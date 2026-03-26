"""Tests for new observation tools: class_hierarchy, symbol_migration, generator_info.

Covers the three fixes from the implementation round:
1. generator_info nested function bug (manual worklist instead of ast.walk)
2. symbol_migration word-boundary matching (grep -P portability issue)
3. Factory method guidance in observe.py prompt
"""

import pytest
import sys

from ftl_code_review.observations import (
    class_hierarchy,
    symbol_migration,
    generator_info,
    OBSERVATION_TOOLS,
)


# ---------------------------------------------------------------------------
# class_hierarchy tests
# ---------------------------------------------------------------------------

class TestClassHierarchy:
    """Tests for the class_hierarchy observation tool."""

    @pytest.mark.asyncio
    async def test_single_base_class(self, tmp_path):
        """Basic single inheritance with __init__ signatures."""
        (tmp_path / "models.py").write_text(
            "class Base:\n"
            "    def __init__(self, host: str, port: int = 443):\n"
            "        self.host = host\n"
            "        self.port = port\n"
            "\n"
            "class Child(Base):\n"
            "    def __init__(self, host: str, port: int = 443, timeout: float = 30.0):\n"
            "        super().__init__(host, port)\n"
            "        self.timeout = timeout\n"
        )
        result = await class_hierarchy("Child", "models.py", str(tmp_path))

        assert "error" not in result
        assert result["class_name"] == "Child"
        assert result["bases"] == ["Base"]
        assert "host: str" in result["own_init_signature"]
        assert "timeout" in result["own_init_signature"]
        assert result["base_init_signatures"]["Base"] is not None
        assert "host: str" in result["base_init_signatures"]["Base"]
        assert "multiple_inheritance" not in result

    @pytest.mark.asyncio
    async def test_multiple_inheritance(self, tmp_path):
        """Multiple inheritance should set multiple_inheritance flag."""
        (tmp_path / "mix.py").write_text(
            "class A:\n    pass\n"
            "class B:\n    pass\n"
            "class C(A, B):\n    pass\n"
        )
        result = await class_hierarchy("C", "mix.py", str(tmp_path))

        assert "error" not in result
        assert result["bases"] == ["A", "B"]
        assert result["multiple_inheritance"] is True

    @pytest.mark.asyncio
    async def test_class_not_found(self, tmp_path):
        """Returns error when class doesn't exist in file."""
        (tmp_path / "empty.py").write_text("x = 1\n")
        result = await class_hierarchy("Missing", "empty.py", str(tmp_path))

        assert "error" in result
        assert "Missing" in result["error"]

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path):
        """Returns error with similar_files fallback when file doesn't exist."""
        (tmp_path / "client.py").write_text("class Foo:\n    pass\n")
        result = await class_hierarchy("Foo", "nonexistent.py", str(tmp_path))

        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_no_init(self, tmp_path):
        """Class without __init__ returns None for own_init_signature."""
        (tmp_path / "simple.py").write_text("class NoInit:\n    x = 1\n")
        result = await class_hierarchy("NoInit", "simple.py", str(tmp_path))

        assert "error" not in result
        assert result["own_init_signature"] is None

    @pytest.mark.asyncio
    async def test_dotted_base(self, tmp_path):
        """Dotted base class name (e.g. module.ClassName) is extracted."""
        (tmp_path / "child.py").write_text(
            "import abc\n"
            "class MyABC(abc.ABC):\n"
            "    pass\n"
        )
        result = await class_hierarchy("MyABC", "child.py", str(tmp_path))

        assert "error" not in result
        assert "abc.ABC" in result["bases"]

    @pytest.mark.asyncio
    async def test_base_in_same_file(self, tmp_path):
        """Base class __init__ resolved from the same file."""
        (tmp_path / "same.py").write_text(
            "class Parent:\n"
            "    def __init__(self, name: str):\n"
            "        self.name = name\n"
            "\n"
            "class Kid(Parent):\n"
            "    def __init__(self, name: str, age: int):\n"
            "        super().__init__(name)\n"
            "        self.age = age\n"
        )
        result = await class_hierarchy("Kid", "same.py", str(tmp_path))

        assert result["base_init_signatures"]["Parent"] is not None
        assert "name: str" in result["base_init_signatures"]["Parent"]


# ---------------------------------------------------------------------------
# symbol_migration tests
# ---------------------------------------------------------------------------

class TestSymbolMigration:
    """Tests for the symbol_migration observation tool."""

    @pytest.mark.asyncio
    async def test_complete_migration(self, tmp_path):
        """Migration is complete when old name has no production usages."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "client.py").write_text("class NewClient:\n    pass\n")
        result = await symbol_migration("OldClient", "NewClient", str(tmp_path))

        assert "error" not in result
        assert result["migration_complete"] is True
        assert result["stale_count"] == 0

    @pytest.mark.asyncio
    async def test_incomplete_migration(self, tmp_path):
        """Stale references detected in production code."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "client.py").write_text("class NewClient:\n    pass\n")
        (src / "handler.py").write_text("from client import OldClient\nclient = OldClient()\n")
        result = await symbol_migration("OldClient", "NewClient", str(tmp_path))

        assert result["migration_complete"] is False
        assert result["stale_count"] > 0
        stale_files = [r["file"] for r in result["stale_references"]]
        assert any("handler" in f for f in stale_files)

    @pytest.mark.asyncio
    async def test_comments_excluded(self, tmp_path):
        """Lines starting with # are not counted as stale references."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "note.py").write_text("# OldClient was removed in v2\nx = 1\n")
        result = await symbol_migration("OldClient", repo_path=str(tmp_path))

        assert result["migration_complete"] is True

    @pytest.mark.asyncio
    async def test_test_files_excluded(self, tmp_path):
        """Test files are not counted as stale references."""
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_client.py").write_text("from client import OldClient\n")
        result = await symbol_migration("OldClient", repo_path=str(tmp_path))

        assert result["migration_complete"] is True

    @pytest.mark.asyncio
    async def test_word_boundary_matching(self, tmp_path):
        """Searching for 'Client' must NOT match 'ClientFactory' or 'HTTPClient'."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "factory.py").write_text(
            "class ClientFactory:\n"
            "    pass\n"
            "\n"
            "class HTTPClient:\n"
            "    pass\n"
        )
        result = await symbol_migration("Client", repo_path=str(tmp_path))

        # "Client" should not appear as a stale reference since only
        # "ClientFactory" and "HTTPClient" exist (no bare "Client")
        assert result["migration_complete"] is True, (
            f"Expected migration_complete=True (no bare 'Client'), "
            f"got stale_references={result.get('stale_references')}"
        )

    @pytest.mark.asyncio
    async def test_no_repo_path(self):
        """Missing repo_path returns error."""
        result = await symbol_migration("Foo", repo_path="")

        assert "error" in result

    @pytest.mark.asyncio
    async def test_old_name_only(self, tmp_path):
        """Works with only old_name (no new_name)."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "code.py").write_text("x = 1\n")
        result = await symbol_migration("OldThing", repo_path=str(tmp_path))

        assert "error" not in result
        assert "new_name" not in result
        assert result["migration_complete"] is True


# ---------------------------------------------------------------------------
# generator_info tests
# ---------------------------------------------------------------------------

class TestGeneratorInfo:
    """Tests for the generator_info observation tool."""

    @pytest.mark.asyncio
    async def test_simple_generator(self, tmp_path):
        """Function with yield is detected as generator."""
        (tmp_path / "gen.py").write_text(
            "def items():\n"
            "    yield 1\n"
            "    yield 2\n"
            "    yield 3\n"
        )
        result = await generator_info(str(tmp_path / "gen.py"), "items")

        assert result["is_generator"] is True
        assert result["is_async_generator"] is False
        assert result["yield_count"] == 3

    @pytest.mark.asyncio
    async def test_non_generator(self, tmp_path):
        """Function without yield is not a generator."""
        (tmp_path / "plain.py").write_text(
            "def add(a, b):\n"
            "    return a + b\n"
        )
        result = await generator_info(str(tmp_path / "plain.py"), "add")

        assert result["is_generator"] is False
        assert result["yield_count"] == 0

    @pytest.mark.asyncio
    async def test_nested_generator_not_propagated(self, tmp_path):
        """Outer function is NOT a generator when only inner function yields.

        This is the key bug fix: ast.walk would descend into the nested
        function and see its yield, incorrectly marking the outer function
        as a generator.
        """
        (tmp_path / "nested.py").write_text(
            "def outer():\n"
            "    def inner():\n"
            "        yield 1\n"
            "        yield 2\n"
            "    return inner\n"
        )
        result = await generator_info(str(tmp_path / "nested.py"), "outer")

        assert result["is_generator"] is False, (
            "outer() should NOT be a generator — only inner() yields"
        )
        assert result["yield_count"] == 0
        assert result["has_return_value"] is True

    @pytest.mark.asyncio
    async def test_nested_generator_inner_detected(self, tmp_path):
        """Inner generator function is correctly detected when targeted."""
        (tmp_path / "nested2.py").write_text(
            "def outer():\n"
            "    def inner():\n"
            "        yield 1\n"
            "    return inner\n"
        )
        result = await generator_info(str(tmp_path / "nested2.py"), "inner")

        assert result["is_generator"] is True
        assert result["yield_count"] == 1

    @pytest.mark.asyncio
    async def test_async_generator(self, tmp_path):
        """Async function with yield is async generator."""
        (tmp_path / "agen.py").write_text(
            "async def stream():\n"
            "    yield 'a'\n"
            "    yield 'b'\n"
        )
        result = await generator_info(str(tmp_path / "agen.py"), "stream")

        assert result["is_generator"] is False
        assert result["is_async_generator"] is True
        assert result["yield_count"] == 2

    @pytest.mark.asyncio
    async def test_return_annotation(self, tmp_path):
        """Return annotation is extracted."""
        (tmp_path / "typed.py").write_text(
            "from typing import Iterator\n"
            "def items() -> Iterator[int]:\n"
            "    yield 1\n"
        )
        result = await generator_info(str(tmp_path / "typed.py"), "items")

        assert result["return_annotation"] == "Iterator[int]"

    @pytest.mark.asyncio
    async def test_generator_with_return_value(self, tmp_path):
        """Generator that also returns a value (StopIteration value)."""
        (tmp_path / "retgen.py").write_text(
            "def gen():\n"
            "    yield 1\n"
            "    return 'done'\n"
        )
        result = await generator_info(str(tmp_path / "retgen.py"), "gen")

        assert result["is_generator"] is True
        assert result["has_return_value"] is True

    @pytest.mark.asyncio
    async def test_function_not_found(self, tmp_path):
        """Returns error for nonexistent function."""
        (tmp_path / "empty.py").write_text("x = 1\n")
        result = await generator_info(str(tmp_path / "empty.py"), "missing")

        assert "error" in result
        assert "missing" in result["error"]

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path):
        """Returns error with similar_files hint for missing file."""
        result = await generator_info(
            "nonexistent.py", "foo", repo_path=str(tmp_path)
        )

        assert "error" in result

    @pytest.mark.asyncio
    async def test_yield_from(self, tmp_path):
        """yield from is counted as a yield."""
        (tmp_path / "delegate.py").write_text(
            "def delegator():\n"
            "    yield from range(10)\n"
        )
        result = await generator_info(str(tmp_path / "delegate.py"), "delegator")

        assert result["is_generator"] is True
        assert result["yield_count"] == 1


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestNewToolsRegistry:
    """Verify new tools are registered in OBSERVATION_TOOLS."""

    def test_class_hierarchy_registered(self):
        assert "class_hierarchy" in OBSERVATION_TOOLS

    def test_symbol_migration_registered(self):
        assert "symbol_migration" in OBSERVATION_TOOLS

    def test_generator_info_registered(self):
        assert "generator_info" in OBSERVATION_TOOLS


# ---------------------------------------------------------------------------
# Factory method prompt test
# ---------------------------------------------------------------------------

class TestObservePromptFactoryGuidance:
    """Verify observe.py prompt includes factory method guidance."""

    def test_factory_method_in_prompt(self):
        from ftl_code_review.prompts.observe import OBSERVE_PROMPT
        assert "factory method" in OBSERVE_PROMPT.lower() or "Factory method" in OBSERVE_PROMPT

    def test_error_result_example_in_prompt(self):
        from ftl_code_review.prompts.observe import OBSERVE_PROMPT
        assert "error_result" in OBSERVE_PROMPT
