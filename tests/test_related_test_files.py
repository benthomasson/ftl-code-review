"""Tests for auto-discovery of related test files."""

import asyncio
import os
import textwrap

import pytest

from multi_model_code_review.observations import (
    _extract_modified_symbols,
    _find_test_files_by_naming,
    _find_test_files_by_imports,
    gather_related_test_files,
    related_test_files,
    OBSERVATION_TOOLS,
)


# -- Helpers ------------------------------------------------------------------

def _make_diff(file_path, old_lines, new_lines, context=3):
    """Build a minimal unified diff string for testing."""
    return textwrap.dedent(f"""\
        diff --git a/{file_path} b/{file_path}
        --- a/{file_path}
        +++ b/{file_path}
        @@ -1,{len(old_lines)} +1,{len(new_lines)} @@
    """) + "\n".join(f"+{l}" for l in new_lines) + "\n"


def _setup_repo(tmp_path, files: dict[str, str]):
    """Create a fake repo with given files.

    Args:
        files: mapping of relative path -> content
    Returns:
        repo_path as string
    """
    for rel_path, content in files.items():
        p = tmp_path / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))
    return str(tmp_path)


# -- _extract_modified_symbols ------------------------------------------------

class TestExtractModifiedSymbols:
    def test_finds_modified_function(self, tmp_path):
        source = textwrap.dedent("""\
            def unchanged():
                pass

            def handle_request(data):
                return data.upper()

            def another():
                pass
        """)
        (tmp_path / "proxy.py").write_text(source)
        diff = textwrap.dedent("""\
            diff --git a/proxy.py b/proxy.py
            --- a/proxy.py
            +++ b/proxy.py
            @@ -4,2 +4,2 @@
            -def handle_request(data):
            -    return data.upper()
            +def handle_request(data):
            +    return data.lower()
        """)
        result = _extract_modified_symbols(diff, str(tmp_path))
        assert "proxy.py" in result
        assert "handle_request" in result["proxy.py"]
        assert "unchanged" not in result["proxy.py"]

    def test_finds_modified_class(self, tmp_path):
        source = textwrap.dedent("""\
            class MyClient:
                def connect(self):
                    pass
        """)
        (tmp_path / "client.py").write_text(source)
        diff = textwrap.dedent("""\
            diff --git a/client.py b/client.py
            --- a/client.py
            +++ b/client.py
            @@ -1,3 +1,3 @@
            -class MyClient:
            +class MyClient:
             def connect(self):
            -        pass
            +        return True
        """)
        result = _extract_modified_symbols(diff, str(tmp_path))
        assert "client.py" in result
        # Both the class and the method overlap the modified range
        assert "MyClient" in result["client.py"]

    def test_skips_non_python(self, tmp_path):
        diff = textwrap.dedent("""\
            diff --git a/readme.md b/readme.md
            --- a/readme.md
            +++ b/readme.md
            @@ -1 +1 @@
            -old
            +new
        """)
        result = _extract_modified_symbols(diff, str(tmp_path))
        assert result == {}

    def test_missing_file(self, tmp_path):
        diff = textwrap.dedent("""\
            diff --git a/gone.py b/gone.py
            --- a/gone.py
            +++ b/gone.py
            @@ -1 +1 @@
            -old
            +new
        """)
        result = _extract_modified_symbols(diff, str(tmp_path))
        assert result == {}

    def test_empty_diff(self, tmp_path):
        result = _extract_modified_symbols("", str(tmp_path))
        assert result == {}


# -- _find_test_files_by_naming -----------------------------------------------

class TestFindTestFilesByNaming:
    @pytest.mark.asyncio
    async def test_finds_test_prefix(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_proxy.py").write_text("# test")
        result = await _find_test_files_by_naming("proxy", str(tmp_path))
        assert "tests/test_proxy.py" in result

    @pytest.mark.asyncio
    async def test_finds_test_suffix(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "proxy_test.py").write_text("# test")
        result = await _find_test_files_by_naming("proxy", str(tmp_path))
        assert "tests/proxy_test.py" in result

    @pytest.mark.asyncio
    async def test_searches_multiple_dirs(self, tmp_path):
        for d in ["tests", "tests/unit", "tests/integration"]:
            (tmp_path / d).mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests" / "test_proxy.py").write_text("# a")
        (tmp_path / "tests" / "unit" / "test_proxy.py").write_text("# b")
        result = await _find_test_files_by_naming("proxy", str(tmp_path))
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_no_match(self, tmp_path):
        (tmp_path / "tests").mkdir()
        result = await _find_test_files_by_naming("proxy", str(tmp_path))
        assert result == []

    @pytest.mark.asyncio
    async def test_deduplicates(self, tmp_path):
        # A file in root "." and also matching pattern
        (tmp_path / "test_proxy.py").write_text("# test")
        result = await _find_test_files_by_naming("proxy", str(tmp_path))
        # Should only appear once even though "." is searched
        assert result.count("test_proxy.py") == 1


# -- _find_test_files_by_imports ----------------------------------------------

class TestFindTestFilesByImports:
    @pytest.mark.asyncio
    async def test_finds_import(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_auth.py").write_text(
            "from auth import login\n\ndef test_login():\n    pass\n"
        )
        result = await _find_test_files_by_imports("auth", "src/auth.py", str(tmp_path))
        assert any("test_auth.py" in p for p in result)

    @pytest.mark.asyncio
    async def test_ignores_non_test_files(self, tmp_path):
        (tmp_path / "helper.py").write_text("from auth import login\n")
        result = await _find_test_files_by_imports("auth", "src/auth.py", str(tmp_path))
        assert result == []

    @pytest.mark.asyncio
    async def test_dotted_import(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_client.py").write_text(
            "from src.auth.client import Client\n\ndef test_it():\n    pass\n"
        )
        result = await _find_test_files_by_imports("client", "src/auth/client.py", str(tmp_path))
        assert any("test_client.py" in p for p in result)


# -- gather_related_test_files ------------------------------------------------

class TestGatherRelatedTestFiles:
    @pytest.mark.asyncio
    async def test_full_discovery(self, tmp_path):
        repo = _setup_repo(tmp_path, {
            "src/proxy.py": """\
                def handle_request(data):
                    return data.upper()
            """,
            "tests/test_proxy.py": """\
                from proxy import handle_request

                def test_handle_request():
                    assert handle_request("a") == "A"
            """,
        })
        diff = textwrap.dedent("""\
            diff --git a/src/proxy.py b/src/proxy.py
            --- a/src/proxy.py
            +++ b/src/proxy.py
            @@ -1,2 +1,2 @@
             def handle_request(data):
            -    return data.upper()
            +    return data.lower()
        """)
        result = await gather_related_test_files(diff, repo)
        assert result["test_file_count"] >= 1
        paths = [tf["path"] for tf in result["test_files"]]
        assert "tests/test_proxy.py" in paths

    @pytest.mark.asyncio
    async def test_includes_content(self, tmp_path):
        repo = _setup_repo(tmp_path, {
            "src/proxy.py": """\
                def handle_request(data):
                    return data
            """,
            "tests/test_proxy.py": """\
                def test_handle_request():
                    pass
            """,
        })
        diff = textwrap.dedent("""\
            diff --git a/src/proxy.py b/src/proxy.py
            --- a/src/proxy.py
            +++ b/src/proxy.py
            @@ -1,2 +1,2 @@
             def handle_request(data):
            -    return data
            +    return data.strip()
        """)
        result = await gather_related_test_files(diff, repo)
        assert result["test_files"][0]["content"]
        assert "test_handle_request" in result["test_files"][0]["content"]

    @pytest.mark.asyncio
    async def test_symbol_annotation(self, tmp_path):
        repo = _setup_repo(tmp_path, {
            "src/proxy.py": """\
                def handle_request(data):
                    return data
                def other():
                    pass
            """,
            "tests/test_proxy.py": """\
                def test_handle_request():
                    handle_request("x")
            """,
        })
        diff = textwrap.dedent("""\
            diff --git a/src/proxy.py b/src/proxy.py
            --- a/src/proxy.py
            +++ b/src/proxy.py
            @@ -1,2 +1,2 @@
             def handle_request(data):
            -    return data
            +    return data.strip()
        """)
        result = await gather_related_test_files(diff, repo)
        tf = result["test_files"][0]
        assert "handle_request" in tf["symbols_referenced"]

    @pytest.mark.asyncio
    async def test_duplicate_coverage_detection(self, tmp_path):
        repo = _setup_repo(tmp_path, {
            "src/proxy.py": """\
                def handle_request(data):
                    return data
            """,
            "tests/test_proxy.py": """\
                def test_a():
                    pass
            """,
            "tests/unit/test_proxy.py": """\
                def test_b():
                    pass
            """,
        })
        diff = textwrap.dedent("""\
            diff --git a/src/proxy.py b/src/proxy.py
            --- a/src/proxy.py
            +++ b/src/proxy.py
            @@ -1,2 +1,2 @@
             def handle_request(data):
            -    return data
            +    return data.strip()
        """)
        result = await gather_related_test_files(diff, repo)
        assert result["duplicate_coverage"]
        assert "src/proxy.py" in result["duplicate_coverage"]

    @pytest.mark.asyncio
    async def test_no_python_files(self, tmp_path):
        repo = _setup_repo(tmp_path, {})
        diff = textwrap.dedent("""\
            diff --git a/README.md b/README.md
            --- a/README.md
            +++ b/README.md
            @@ -1 +1 @@
            -old
            +new
        """)
        result = await gather_related_test_files(diff, repo)
        assert result["test_files"] == []
        assert "No non-test Python files modified" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_skips_test_files_as_source(self, tmp_path):
        repo = _setup_repo(tmp_path, {
            "tests/test_proxy.py": """\
                def test_it():
                    pass
            """,
        })
        diff = textwrap.dedent("""\
            diff --git a/tests/test_proxy.py b/tests/test_proxy.py
            --- a/tests/test_proxy.py
            +++ b/tests/test_proxy.py
            @@ -1,2 +1,2 @@
             def test_it():
            -    pass
            +    assert True
        """)
        result = await gather_related_test_files(diff, repo)
        assert result["test_files"] == []

    @pytest.mark.asyncio
    async def test_max_lines_per_file(self, tmp_path):
        big_test = "\n".join(f"def test_{i}(): pass" for i in range(100))
        repo = _setup_repo(tmp_path, {
            "src/proxy.py": "def f(): pass\n",
            "tests/test_proxy.py": big_test,
        })
        diff = textwrap.dedent("""\
            diff --git a/src/proxy.py b/src/proxy.py
            --- a/src/proxy.py
            +++ b/src/proxy.py
            @@ -1 +1 @@
            -def f(): pass
            +def f(): return 1
        """)
        result = await gather_related_test_files(diff, repo, max_lines_per_file=10)
        tf = result["test_files"][0]
        assert tf["truncated"] is True
        assert tf["content"].count("\n") < 15  # capped near 10

    @pytest.mark.asyncio
    async def test_max_total_lines(self, tmp_path):
        repo = _setup_repo(tmp_path, {
            "src/proxy.py": "def f(): pass\n",
            "tests/test_proxy.py": "\n".join(f"# line {i}" for i in range(50)),
            "tests/unit/test_proxy.py": "\n".join(f"# line {i}" for i in range(50)),
        })
        diff = textwrap.dedent("""\
            diff --git a/src/proxy.py b/src/proxy.py
            --- a/src/proxy.py
            +++ b/src/proxy.py
            @@ -1 +1 @@
            -def f(): pass
            +def f(): return 1
        """)
        result = await gather_related_test_files(diff, repo, max_total_lines=30)
        assert result["total_lines_included"] <= 30


# -- related_test_files (observation tool) ------------------------------------

class TestRelatedTestFilesTool:
    @pytest.mark.asyncio
    async def test_discovers_by_naming(self, tmp_path):
        repo = _setup_repo(tmp_path, {
            "src/proxy.py": "def handle(): pass\n",
            "tests/test_proxy.py": "def test_handle(): pass\n",
        })
        result = await related_test_files("src/proxy.py", repo)
        assert result["source_file"] == "src/proxy.py"
        assert result["test_count"] >= 1
        paths = [t["path"] for t in result["test_files"]]
        assert "tests/test_proxy.py" in paths

    @pytest.mark.asyncio
    async def test_reports_existence(self, tmp_path):
        repo = _setup_repo(tmp_path, {
            "src/proxy.py": "def handle(): pass\n",
            "tests/test_proxy.py": "def test_handle(): pass\n",
        })
        result = await related_test_files("src/proxy.py", repo)
        for t in result["test_files"]:
            assert "exists" in t
            assert t["exists"] is True


# -- Registry -----------------------------------------------------------------

def test_related_test_files_in_registry():
    """Verify the new tool is registered in OBSERVATION_TOOLS."""
    assert "related_test_files" in OBSERVATION_TOOLS
