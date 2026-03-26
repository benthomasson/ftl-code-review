"""Tests for observation tools."""

import pytest

from multi_model_code_review.observations import (
    exception_hierarchy,
    raises_analysis,
    call_graph,
    find_usages,
    find_callers,
    is_test_file,
    run_observations,
    OBSERVATION_TOOLS,
)


@pytest.mark.asyncio
async def test_exception_hierarchy_httpx():
    """Test exception hierarchy for httpx.TransportError."""
    # Skip if httpx not installed
    try:
        import httpx
    except ImportError:
        pytest.skip("httpx not installed")

    result = await exception_hierarchy("httpx.TransportError")

    assert "error" not in result
    assert result["class"] == "httpx.TransportError"
    assert "mro" in result
    assert "subclasses" in result

    # TransportError should have these subclasses
    subclass_names = [s.split(".")[-1] for s in result["subclasses"]]
    assert "ConnectError" in subclass_names
    assert "ReadError" in subclass_names
    assert "WriteError" in subclass_names


@pytest.mark.asyncio
async def test_exception_hierarchy_builtin():
    """Test exception hierarchy for built-in exceptions."""
    result = await exception_hierarchy("builtins.OSError")

    assert "error" not in result
    assert "ConnectionError" in str(result["subclasses"])


@pytest.mark.asyncio
async def test_exception_hierarchy_bare_builtin():
    """Test exception hierarchy for bare builtin name (no module prefix)."""
    result = await exception_hierarchy("ValueError")

    assert "error" not in result
    assert result["class"] == "ValueError"
    assert "mro" in result


@pytest.mark.asyncio
async def test_exception_hierarchy_invalid():
    """Test exception hierarchy with invalid class."""
    result = await exception_hierarchy("nonexistent.FakeError")

    assert "error" in result


@pytest.mark.asyncio
async def test_raises_analysis(tmp_path):
    """Test raises analysis on a sample file."""
    # Create a test file
    test_file = tmp_path / "sample.py"
    test_file.write_text("""
def my_function():
    if something:
        raise ValueError("bad value")
    try:
        do_something()
    except Exception:
        raise RuntimeError("failed")
""")

    result = await raises_analysis(str(test_file), "my_function")

    assert "error" not in result
    assert "ValueError" in result["explicit_raises"]
    assert "RuntimeError" in result["explicit_raises"]
    assert "do_something" in result["calls"]


@pytest.mark.asyncio
async def test_call_graph(tmp_path):
    """Test call graph analysis."""
    test_file = tmp_path / "sample.py"
    test_file.write_text("""
def my_function():
    helper()
    self.method()
    module.function()
    result = compute(x, y)
    return result
""")

    result = await call_graph(str(test_file), "my_function")

    assert "error" not in result
    call_names = [c["name"] for c in result["calls"]]
    assert "helper" in call_names
    assert "self.method" in call_names
    assert "module.function" in call_names
    assert "compute" in call_names


@pytest.mark.asyncio
async def test_run_observations():
    """Test running multiple observations."""
    observations = [
        {
            "name": "oserror_hierarchy",
            "tool": "exception_hierarchy",
            "params": {"class_name": "builtins.OSError"},
        },
    ]

    results = await run_observations(observations, "/tmp")

    assert "oserror_hierarchy" in results
    assert "error" not in results["oserror_hierarchy"]


@pytest.mark.asyncio
async def test_run_observations_invalid_tool():
    """Test handling of invalid tool name."""
    observations = [
        {
            "name": "bad_obs",
            "tool": "nonexistent_tool",
            "params": {},
        },
    ]

    results = await run_observations(observations, "/tmp")

    assert "bad_obs" in results
    assert "error" in results["bad_obs"]


class TestIsTestFile:
    """Tests for is_test_file helper."""

    def test_test_prefix(self):
        assert is_test_file("test_foo.py") is True
        assert is_test_file("tests/test_bar.py") is True

    def test_test_suffix(self):
        assert is_test_file("foo_test.py") is True
        assert is_test_file("src/bar_test.py") is True

    def test_conftest(self):
        assert is_test_file("conftest.py") is True
        assert is_test_file("tests/conftest.py") is True

    def test_test_directory(self):
        assert is_test_file("tests/helpers.py") is True
        assert is_test_file("test/utils.py") is True
        assert is_test_file("testing/fixtures.py") is True

    def test_production_files(self):
        assert is_test_file("src/auth/client.py") is False
        assert is_test_file("observations.py") is False
        assert is_test_file("src/utils/helpers.py") is False


@pytest.mark.asyncio
async def test_find_usages_prod_test_split(tmp_path):
    """Test that find_usages separates production and test usages."""
    # Create a mini repo with prod and test files
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "client.py").write_text("def my_func():\n    return 42\n")
    (src_dir / "handler.py").write_text("from client import my_func\nresult = my_func()\n")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_client.py").write_text("from client import my_func\ndef test_it():\n    assert my_func() == 42\n")

    result = await find_usages("my_func", str(tmp_path))

    assert "error" not in result
    assert result["total_count"] > 0
    # Should have both flat list and split lists
    assert "usages" in result
    assert "production_usages" in result
    assert "test_usages" in result
    assert "production_count" in result
    assert "test_count" in result
    # Test files should be in test_usages
    test_files = [u["file"] for u in result["test_usages"]]
    prod_files = [u["file"] for u in result["production_usages"]]
    assert any("test_client" in f for f in test_files)
    assert any("handler" in f or "client" in f for f in prod_files)


@pytest.mark.asyncio
async def test_find_callers_with_context(tmp_path):
    """Test find_callers returns context for each call site."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "core.py").write_text(
        "def target_func():\n"
        "    return 1\n"
        "\n"
        "def caller_one():\n"
        "    x = 1\n"
        "    result = target_func()\n"
        "    return result\n"
    )

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_core.py").write_text(
        "def test_target():\n"
        "    val = target_func()\n"
        "    assert val == 1\n"
    )

    result = await find_callers("target_func", str(tmp_path))

    assert "error" not in result
    assert result["total_count"] > 0
    assert len(result["production_callers"]) > 0
    assert len(result["test_callers"]) > 0

    # Check context extraction for a production caller
    prod_caller = next(
        (c for c in result["production_callers"] if "context_function" in c and c["context_function"]),
        None,
    )
    if prod_caller:
        assert prod_caller["context_function"] is not None

    # Check test caller
    test_caller = result["test_callers"][0]
    assert "test_core" in test_caller["file"]


@pytest.mark.asyncio
async def test_find_callers_symbol_not_found(tmp_path):
    """Test find_callers with a symbol that doesn't exist."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "empty.py").write_text("x = 1\n")

    result = await find_callers("nonexistent_symbol_xyz", str(tmp_path))

    assert "error" not in result
    assert result["total_count"] == 0
    assert result["production_callers"] == []
    assert result["test_callers"] == []


def test_observation_tools_registry():
    """Test that all expected tools are registered."""
    expected_tools = [
        "exception_hierarchy",
        "raises_analysis",
        "call_graph",
        "find_usages",
        "find_callers",
        "git_blame",
        "test_coverage",
    ]

    for tool in expected_tools:
        assert tool in OBSERVATION_TOOLS
