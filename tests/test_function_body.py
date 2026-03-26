"""Tests for function body extraction and modified function gathering."""

import asyncio
import textwrap

import pytest

from ftl_code_review.observations import (
    function_body,
    gather_function_context,
    OBSERVATION_TOOLS,
)
from ftl_code_review.git_utils import extract_modified_line_ranges


# ---------------------------------------------------------------------------
# extract_modified_line_ranges tests
# ---------------------------------------------------------------------------


class TestExtractModifiedLineRanges:
    def test_single_hunk(self):
        diff = textwrap.dedent("""\
            diff --git a/foo.py b/foo.py
            --- a/foo.py
            +++ b/foo.py
            @@ -10,4 +10,6 @@ def hello():
             unchanged
            +added line 1
            +added line 2
             unchanged
        """)
        result = extract_modified_line_ranges(diff)
        assert result == {"foo.py": [(10, 15)]}

    def test_multiple_hunks_same_file(self):
        diff = textwrap.dedent("""\
            diff --git a/foo.py b/foo.py
            --- a/foo.py
            +++ b/foo.py
            @@ -5,3 +5,4 @@ def a():
             x
            +y
             z
            @@ -20,2 +21,3 @@ def b():
             a
            +b
             c
        """)
        result = extract_modified_line_ranges(diff)
        assert "foo.py" in result
        assert len(result["foo.py"]) == 2

    def test_multiple_files(self):
        diff = textwrap.dedent("""\
            diff --git a/a.py b/a.py
            --- a/a.py
            +++ b/a.py
            @@ -1,3 +1,4 @@
             x
            +y
             z
            diff --git a/b.py b/b.py
            --- a/b.py
            +++ b/b.py
            @@ -10,2 +10,3 @@
             a
            +b
        """)
        result = extract_modified_line_ranges(diff)
        assert "a.py" in result
        assert "b.py" in result

    def test_single_line_hunk_no_count(self):
        """@@ -5 +5 @@ — no comma means count=1."""
        diff = textwrap.dedent("""\
            diff --git a/foo.py b/foo.py
            --- a/foo.py
            +++ b/foo.py
            @@ -5 +5 @@
             changed
        """)
        result = extract_modified_line_ranges(diff)
        assert result == {"foo.py": [(5, 5)]}

    def test_zero_count_hunk_skipped(self):
        """+5,0 means a pure deletion — no lines in new file."""
        diff = textwrap.dedent("""\
            diff --git a/foo.py b/foo.py
            --- a/foo.py
            +++ b/foo.py
            @@ -5,3 +5,0 @@
            -removed1
            -removed2
            -removed3
        """)
        result = extract_modified_line_ranges(diff)
        # count=0 → should be skipped
        assert result == {}

    def test_deleted_file_skipped(self):
        """+++ /dev/null means file was deleted — skip it."""
        diff = textwrap.dedent("""\
            diff --git a/gone.py b/gone.py
            --- a/gone.py
            +++ /dev/null
            @@ -1,5 +0,0 @@
            -line1
            -line2
        """)
        result = extract_modified_line_ranges(diff)
        assert result == {}

    def test_empty_diff(self):
        assert extract_modified_line_ranges("") == {}


# ---------------------------------------------------------------------------
# function_body tests
# ---------------------------------------------------------------------------


class TestFunctionBody:
    @pytest.mark.asyncio
    async def test_find_by_name(self, tmp_path):
        src = textwrap.dedent("""\
            def greet(name):
                return f"Hello, {name}"

            def farewell(name):
                return f"Goodbye, {name}"
        """)
        f = tmp_path / "sample.py"
        f.write_text(src)

        result = await function_body(str(f), function_name="greet")
        assert result["function"] == "greet"
        assert "Hello" in result["source"]
        assert result["start_line"] == 1
        assert result["end_line"] == 2

    @pytest.mark.asyncio
    async def test_find_by_line_hint(self, tmp_path):
        src = textwrap.dedent("""\
            def first():
                pass

            def second():
                x = 1
                y = 2
                return x + y
        """)
        f = tmp_path / "sample.py"
        f.write_text(src)

        result = await function_body(str(f), line_hint=5)
        assert result["function"] == "second"

    @pytest.mark.asyncio
    async def test_method_in_class(self, tmp_path):
        src = textwrap.dedent("""\
            class MyClass:
                def my_method(self):
                    return 42
        """)
        f = tmp_path / "sample.py"
        f.write_text(src)

        result = await function_body(str(f), function_name="my_method")
        assert result["function"] == "my_method"
        assert result["class_name"] == "MyClass"

    @pytest.mark.asyncio
    async def test_innermost_nested_function(self, tmp_path):
        src = textwrap.dedent("""\
            def outer():
                def inner():
                    return 1
                return inner()
        """)
        f = tmp_path / "sample.py"
        f.write_text(src)

        result = await function_body(str(f), line_hint=3)
        assert result["function"] == "inner"

    @pytest.mark.asyncio
    async def test_decorator_included(self, tmp_path):
        src = textwrap.dedent("""\
            import functools

            @functools.lru_cache
            @staticmethod
            def cached_fn():
                return 42
        """)
        f = tmp_path / "sample.py"
        f.write_text(src)

        result = await function_body(str(f), function_name="cached_fn")
        assert result["start_line"] == 3  # decorator line, not def line
        assert "@functools.lru_cache" in result["source"]
        assert "@staticmethod" in result["source"]
        assert "def cached_fn" in result["source"]

    @pytest.mark.asyncio
    async def test_class_method_decorator(self, tmp_path):
        src = textwrap.dedent("""\
            class Foo:
                @property
                def bar(self):
                    return self._bar
        """)
        f = tmp_path / "sample.py"
        f.write_text(src)

        result = await function_body(str(f), function_name="bar")
        assert result["start_line"] == 2  # @property line
        assert "@property" in result["source"]
        assert result["class_name"] == "Foo"

    @pytest.mark.asyncio
    async def test_truncation_large_function(self, tmp_path):
        lines = ["def big():"]
        for i in range(250):
            lines.append(f"    x_{i} = {i}")
        f = tmp_path / "big.py"
        f.write_text("\n".join(lines) + "\n")

        result = await function_body(str(f), function_name="big")
        assert result["truncated"] is True
        assert result["total_lines"] == 251
        assert result["source"].count("\n") < 201  # max 200 lines

    @pytest.mark.asyncio
    async def test_no_params_returns_error(self):
        result = await function_body("anything.py")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_function_not_found(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("x = 1\n")

        result = await function_body(str(f), function_name="nonexistent")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_line_hint_module_level(self, tmp_path):
        src = textwrap.dedent("""\
            x = 1
            y = 2
        """)
        f = tmp_path / "mod.py"
        f.write_text(src)

        result = await function_body(str(f), line_hint=1)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_syntax_error_file(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("def broken(\n")

        result = await function_body(str(f), function_name="broken")
        assert "error" in result
        assert "Syntax error" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_file(self):
        result = await function_body("/nonexistent/path.py", function_name="foo")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_repo_path_resolution(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        f = sub / "mod.py"
        f.write_text("def hello():\n    pass\n")

        result = await function_body("src/mod.py", function_name="hello", repo_path=str(tmp_path))
        assert result["function"] == "hello"

    @pytest.mark.asyncio
    async def test_async_function(self, tmp_path):
        src = textwrap.dedent("""\
            async def fetch_data():
                return await get()
        """)
        f = tmp_path / "async_mod.py"
        f.write_text(src)

        result = await function_body(str(f), function_name="fetch_data")
        assert result["function"] == "fetch_data"
        assert "async def" in result["source"]


# ---------------------------------------------------------------------------
# gather_function_context tests
# ---------------------------------------------------------------------------


class TestGatherFunctionContext:
    @pytest.mark.asyncio
    async def test_basic_gathering(self, tmp_path):
        """Modified function is found and returned."""
        f = tmp_path / "app.py"
        f.write_text(textwrap.dedent("""\
            def setup():
                pass

            def process(data):
                return data.strip()

            def cleanup():
                pass
        """))

        diff = textwrap.dedent("""\
            diff --git a/app.py b/app.py
            --- a/app.py
            +++ b/app.py
            @@ -4,2 +4,3 @@ def process(data):
             def process(data):
            -    return data
            +    return data.strip()
        """)

        result = await gather_function_context(diff, str(tmp_path))
        assert len(result) >= 1
        keys = list(result.keys())
        assert any("process" in k for k in keys)

    @pytest.mark.asyncio
    async def test_non_python_files_skipped(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# Hello\n")

        diff = textwrap.dedent("""\
            diff --git a/readme.md b/readme.md
            --- a/readme.md
            +++ b/readme.md
            @@ -1 +1,2 @@
             # Hello
            +World
        """)

        result = await gather_function_context(diff, str(tmp_path))
        assert result == {}

    @pytest.mark.asyncio
    async def test_missing_file_skipped(self, tmp_path):
        diff = textwrap.dedent("""\
            diff --git a/gone.py b/gone.py
            --- a/gone.py
            +++ b/gone.py
            @@ -1,2 +1,3 @@
             def x():
            +    pass
        """)

        result = await gather_function_context(diff, str(tmp_path))
        assert result == {}

    @pytest.mark.asyncio
    async def test_deduplication(self, tmp_path):
        """Two hunks in the same function → only one result."""
        f = tmp_path / "dup.py"
        f.write_text(textwrap.dedent("""\
            def big_func():
                a = 1
                b = 2
                c = 3
                d = 4
                e = 5
                f = 6
                g = 7
                h = 8
                i = 9
                return a + b + c
        """))

        diff = textwrap.dedent("""\
            diff --git a/dup.py b/dup.py
            --- a/dup.py
            +++ b/dup.py
            @@ -2,2 +2,2 @@ def big_func():
            -    a = 1
            +    a = 10
            @@ -9,2 +9,2 @@ def big_func():
            -    h = 8
            +    h = 80
        """)

        result = await gather_function_context(diff, str(tmp_path))
        # Should have only one entry for big_func despite two hunks
        assert len(result) == 1
        assert "big_func" in list(result.keys())[0]

    @pytest.mark.asyncio
    async def test_module_level_changes_skipped(self, tmp_path):
        """Changes to module-level code produce no function results."""
        f = tmp_path / "conf.py"
        f.write_text("X = 1\nY = 2\n")

        diff = textwrap.dedent("""\
            diff --git a/conf.py b/conf.py
            --- a/conf.py
            +++ b/conf.py
            @@ -1,2 +1,2 @@
            -X = 1
            +X = 10
             Y = 2
        """)

        result = await gather_function_context(diff, str(tmp_path))
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_diff(self, tmp_path):
        result = await gather_function_context("", str(tmp_path))
        assert result == {}

    @pytest.mark.asyncio
    async def test_class_method_key_format(self, tmp_path):
        f = tmp_path / "models.py"
        f.write_text(textwrap.dedent("""\
            class User:
                def save(self):
                    pass
        """))

        diff = textwrap.dedent("""\
            diff --git a/models.py b/models.py
            --- a/models.py
            +++ b/models.py
            @@ -2,2 +2,3 @@ class User:
                 def save(self):
            -        pass
            +        self.validate()
            +        self.persist()
        """)

        result = await gather_function_context(diff, str(tmp_path))
        keys = list(result.keys())
        assert len(keys) >= 1
        # Key should include class name: "models.py:User.save"
        assert any("User.save" in k for k in keys)


# ---------------------------------------------------------------------------
# Registration test
# ---------------------------------------------------------------------------


def test_function_body_in_observation_tools():
    """function_body should be registered as an observation tool."""
    assert "function_body" in OBSERVATION_TOOLS
