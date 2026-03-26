"""Tests for caller analysis: production/test separation, context extraction, and edge cases.

Covers the new find_callers, enhanced find_usages, is_test_file, and
_extract_enclosing_function functionality added for issue #3.
"""

import pytest

from multi_model_code_review.observations import (
    find_callers,
    find_usages,
    is_test_file,
    _extract_enclosing_function,
)


# ---------------------------------------------------------------------------
# is_test_file edge cases
# ---------------------------------------------------------------------------

class TestIsTestFileEdgeCases:
    """Edge cases beyond the basic patterns tested in test_observations.py."""

    def test_nested_test_directory(self):
        """Path with tests/ deep in the hierarchy."""
        assert is_test_file("src/myapp/tests/unit/helpers.py") is True

    def test_testing_directory(self):
        assert is_test_file("testing/integration/suite.py") is True

    def test_test_in_filename_but_not_prefix_or_suffix(self):
        """'test' appearing in the middle of filename is NOT a test file."""
        assert is_test_file("src/contest.py") is False
        assert is_test_file("src/attestation.py") is False

    def test_test_like_directory_name_not_exact(self):
        """Directories named 'testdata' or 'attest' should not match."""
        assert is_test_file("testdata/fixture.py") is False
        assert is_test_file("src/attest/validator.py") is False

    def test_non_python_test_file(self):
        """test_ prefix on non-Python files — function checks basename only."""
        # is_test_file doesn't filter by extension, so test_foo.js would match
        assert is_test_file("test_foo.js") is True

    def test_empty_string(self):
        assert is_test_file("") is False

    def test_conftest_in_subdirectory(self):
        assert is_test_file("src/tests/nested/conftest.py") is True


# ---------------------------------------------------------------------------
# _extract_enclosing_function edge cases (reviewer issue #1 and #4)
# ---------------------------------------------------------------------------

class TestExtractEnclosingFunction:
    """Direct unit tests for the enclosing function heuristic."""

    def test_match_inside_function(self):
        lines = [
            "def outer():",
            "    x = 1",
            "    target_func()",
            "    return x",
        ]
        result = _extract_enclosing_function(lines, match_line=3)
        assert result["context_function"] == "outer"
        assert "target_func" in result["context_snippet"]

    def test_match_inside_class(self):
        lines = [
            "class MyClass:",
            "    def method(self):",
            "        target_func()",
        ]
        result = _extract_enclosing_function(lines, match_line=3)
        assert result["context_function"] == "method"

    def test_match_at_module_level_no_enclosing(self):
        """When match is before any def/class, context_function should be None."""
        lines = [
            "import os",
            "target_func()",
            "",
            "def later():",
            "    pass",
        ]
        result = _extract_enclosing_function(lines, match_line=2)
        assert result["context_function"] is None

    def test_async_def_detected(self):
        lines = [
            "async def handler():",
            "    await target_func()",
        ]
        result = _extract_enclosing_function(lines, match_line=2)
        assert result["context_function"] == "handler"

    def test_class_scope_reported(self):
        """If the nearest scope is a class (not a method), reports class name."""
        lines = [
            "class Config:",
            "    TARGET = target_func()",
        ]
        result = _extract_enclosing_function(lines, match_line=2)
        assert result["context_function"] == "Config (class)"

    def test_out_of_bounds_line(self):
        lines = ["x = 1"]
        result = _extract_enclosing_function(lines, match_line=99)
        assert result["context_function"] is None
        assert result["context_snippet"] == ""

    def test_negative_line(self):
        lines = ["x = 1"]
        result = _extract_enclosing_function(lines, match_line=0)
        assert result["context_function"] is None

    def test_context_snippet_markers(self):
        """The match line should be marked with >> prefix."""
        lines = [
            "def foo():",
            "    a = 1",
            "    target()",
            "    b = 2",
            "    return b",
        ]
        result = _extract_enclosing_function(lines, match_line=3, context_lines=1)
        assert ">>" in result["context_snippet"]
        # The match line (3) should have >>
        for snippet_line in result["context_snippet"].split("\n"):
            if "target()" in snippet_line:
                assert snippet_line.startswith(">>")

    def test_nested_function_finds_innermost(self):
        """Walk-back should find the nearest def, which is the inner function."""
        lines = [
            "def outer():",
            "    def inner():",
            "        target_func()",
            "    return inner",
        ]
        result = _extract_enclosing_function(lines, match_line=3)
        assert result["context_function"] == "inner"


# ---------------------------------------------------------------------------
# find_usages edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_usages_backward_compat_keys(tmp_path):
    """Verify backward-compatible 'usages' key is present alongside new keys."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("x = my_symbol\n")

    result = await find_usages("my_symbol", str(tmp_path))

    # All expected keys present
    for key in ("usages", "production_usages", "test_usages",
                "production_count", "test_count", "total_count", "symbol"):
        assert key in result, f"Missing key: {key}"

    # usages should be union of prod + test
    assert result["total_count"] == result["production_count"] + result["test_count"]


@pytest.mark.asyncio
async def test_find_usages_no_matches(tmp_path):
    """Symbol not found should return zero counts, no error."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "empty.py").write_text("x = 1\n")

    result = await find_usages("nonexistent_xyz_abc", str(tmp_path))

    assert "error" not in result
    assert result["total_count"] == 0
    assert result["production_usages"] == []
    assert result["test_usages"] == []


@pytest.mark.asyncio
async def test_find_usages_definition_included(tmp_path):
    """Reviewer issue #2: grep matches definitions too. Verify this behavior."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "module.py").write_text(
        "def my_func():\n"
        "    return 42\n"
        "\n"
        "result = my_func()\n"
    )

    result = await find_usages("my_func", str(tmp_path))

    # Both the definition and the call site are in the results
    texts = [u["text"] for u in result["usages"]]
    assert any("def my_func" in t for t in texts), "Definition should appear in usages"
    assert any("result = my_func" in t for t in texts), "Call site should appear"


@pytest.mark.asyncio
async def test_find_usages_only_test_files(tmp_path):
    """All usages in test files — production list should be empty."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("assert my_func() == 1\n")
    (tests / "test_b.py").write_text("val = my_func()\n")

    result = await find_usages("my_func", str(tmp_path))

    assert result["production_count"] == 0
    assert result["test_count"] >= 2


@pytest.mark.asyncio
async def test_find_usages_only_prod_files(tmp_path):
    """All usages in production files — test list should be empty."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("my_func()\n")
    (src / "b.py").write_text("my_func()\n")

    result = await find_usages("my_func", str(tmp_path))

    assert result["test_count"] == 0
    assert result["production_count"] >= 2


# ---------------------------------------------------------------------------
# find_callers edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_callers_context_extraction(tmp_path):
    """Verify context_function and context_snippet are populated."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "service.py").write_text(
        "def process():\n"
        "    data = fetch()\n"
        "    result = transform(data)\n"
        "    return result\n"
        "\n"
        "def other():\n"
        "    transform(None)\n"
    )

    result = await find_callers("transform", str(tmp_path))

    assert result["production_count"] >= 2
    for caller in result["production_callers"]:
        assert "context_function" in caller
        assert "context_snippet" in caller


@pytest.mark.asyncio
async def test_find_callers_no_context(tmp_path):
    """When include_context=False, no context fields should be added."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def run():\n    do_thing()\n")

    result = await find_callers("do_thing", str(tmp_path), include_context=False)

    for caller in result["production_callers"]:
        assert "context_function" not in caller
        assert "context_snippet" not in caller


@pytest.mark.asyncio
async def test_find_callers_mixed_prod_and_test(tmp_path):
    """Callers split correctly between prod and test."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "api.py").write_text("def handler():\n    target()\n")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_api.py").write_text("def test_it():\n    target()\n")

    result = await find_callers("target", str(tmp_path))

    assert result["production_count"] >= 1
    assert result["test_count"] >= 1
    prod_files = [c["file"] for c in result["production_callers"]]
    test_files = [c["file"] for c in result["test_callers"]]
    assert any("api.py" in f for f in prod_files)
    assert any("test_api" in f for f in test_files)


@pytest.mark.asyncio
async def test_find_callers_empty_repo(tmp_path):
    """No Python files at all."""
    result = await find_callers("anything", str(tmp_path))

    assert "error" not in result
    assert result["total_count"] == 0


@pytest.mark.asyncio
async def test_find_callers_constructor_usage(tmp_path):
    """Verify find_callers works for class constructor calls."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "models.py").write_text(
        "class Config:\n"
        "    def __init__(self, x):\n"
        "        self.x = x\n"
    )
    (src / "app.py").write_text(
        "from models import Config\n"
        "\n"
        "def setup():\n"
        "    cfg = Config(42)\n"
        "    return cfg\n"
    )

    result = await find_callers("Config", str(tmp_path))

    assert result["production_count"] >= 1
    # At least the app.py call site
    texts = [c["text"] for c in result["production_callers"]]
    assert any("Config" in t for t in texts)


@pytest.mark.asyncio
async def test_find_callers_destructuring_return(tmp_path):
    """Verify callers that destructure return values are found."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "utils.py").write_text(
        "def get_pair():\n"
        "    return 1, 2\n"
    )
    (src / "consumer.py").write_text(
        "from utils import get_pair\n"
        "\n"
        "def use_it():\n"
        "    a, b = get_pair()\n"
        "    print(a, b)\n"
    )

    result = await find_callers("get_pair", str(tmp_path))

    assert result["production_count"] >= 1
    texts = [c["text"] for c in result["production_callers"]]
    assert any("a, b = get_pair" in t for t in texts)


@pytest.mark.asyncio
async def test_find_callers_max_context_limit(tmp_path):
    """Context extraction should stop after max_context (20) entries.

    Reviewer issue #4: context_count increments even for None results.
    We verify the limit is enforced.
    """
    src = tmp_path / "src"
    src.mkdir()

    # Create a file with 25 call sites
    lines = []
    for i in range(25):
        lines.append(f"def func_{i}():\n    target()\n\n")
    (src / "many_callers.py").write_text("".join(lines))

    result = await find_callers("target", str(tmp_path))

    # All 25 should be found
    assert result["total_count"] >= 25

    # But only the first 20 should have context
    callers_with_context = [
        c for c in result["production_callers"]
        if "context_function" in c
    ]
    callers_without_context = [
        c for c in result["production_callers"]
        if "context_function" not in c
    ]
    assert len(callers_with_context) <= 20
    assert len(callers_without_context) >= 5


@pytest.mark.asyncio
async def test_find_callers_large_file_skipped(tmp_path):
    """Files >5000 lines should be skipped for context extraction."""
    src = tmp_path / "src"
    src.mkdir()

    # Create a file with >5000 lines
    big_lines = ["# padding\n"] * 5001
    big_lines[2500] = "def middle():\n"
    big_lines[2501] = "    target_symbol()\n"
    (src / "huge.py").write_text("".join(big_lines))

    result = await find_callers("target_symbol", str(tmp_path))

    assert result["total_count"] >= 1
    # The caller should be found but without context (file too large)
    caller = result["production_callers"][0]
    assert "context_function" not in caller or caller.get("context_function") is None


@pytest.mark.asyncio
async def test_find_callers_result_count_consistency(tmp_path):
    """total_count should equal production_count + test_count."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("my_fn()\n")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("my_fn()\n")

    result = await find_callers("my_fn", str(tmp_path))

    assert result["total_count"] == result["production_count"] + result["test_count"]
