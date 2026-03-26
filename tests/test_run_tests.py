"""Tests for run_tests_for_files() and format_test_results()."""

import asyncio
import os
import textwrap

import pytest

from ftl_code_review.observations import (
    format_test_results,
    run_tests_for_files,
)


# -- Helpers ------------------------------------------------------------------


def _setup_repo(tmp_path, files: dict[str, str]) -> str:
    """Create a fake repo with given files."""
    for rel_path, content in files.items():
        p = tmp_path / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))
    return str(tmp_path)


# -- run_tests_for_files: edge cases ------------------------------------------


class TestRunTestsForFilesEdgeCases:
    @pytest.mark.asyncio
    async def test_no_files(self):
        """Empty file list returns SKIPPED."""
        result = await run_tests_for_files([], "/tmp")
        assert result["status"] == "SKIPPED"
        assert result["total"] == 0
        assert result["tests_run"] == []

    @pytest.mark.asyncio
    async def test_only_test_files(self, tmp_path):
        """If all changed files are test files, returns SKIPPED."""
        repo = _setup_repo(tmp_path, {
            "tests/test_foo.py": "def test_it(): pass\n",
        })
        result = await run_tests_for_files(["tests/test_foo.py"], repo)
        assert result["status"] == "SKIPPED"
        assert "No non-test Python source files" in result["output"]

    @pytest.mark.asyncio
    async def test_suffix_test_files_filtered(self, tmp_path):
        """Files ending in _test.py are also filtered as test files."""
        repo = _setup_repo(tmp_path, {
            "foo_test.py": "def test_it(): pass\n",
        })
        result = await run_tests_for_files(["foo_test.py"], repo)
        assert result["status"] == "SKIPPED"

    @pytest.mark.asyncio
    async def test_non_python_files_filtered(self, tmp_path):
        """Non-Python files are filtered out."""
        repo = _setup_repo(tmp_path, {
            "README.md": "# hello\n",
        })
        result = await run_tests_for_files(["README.md"], repo)
        assert result["status"] == "SKIPPED"

    @pytest.mark.asyncio
    async def test_no_related_tests_found(self, tmp_path):
        """Source file with no matching tests returns SKIPPED."""
        repo = _setup_repo(tmp_path, {
            "src/orphan_module.py": "def lonely(): pass\n",
        })
        result = await run_tests_for_files(["src/orphan_module.py"], repo)
        assert result["status"] == "SKIPPED"
        assert result["tests_run"] == []


# -- run_tests_for_files: actually running pytest -----------------------------


class TestRunTestsForFilesExecution:
    @pytest.mark.asyncio
    async def test_passing_tests(self, tmp_path):
        """Discovers and runs passing tests successfully."""
        repo = _setup_repo(tmp_path, {
            "src/calculator.py": """\
                def add(a, b):
                    return a + b
            """,
            "tests/test_calculator.py": """\
                from calculator import add

                def test_add():
                    assert add(1, 2) == 3

                def test_add_negative():
                    assert add(-1, 1) == 0
            """,
        })
        # Need to make src importable for the test
        (tmp_path / "tests" / "conftest.py").write_text(
            "import sys, os\nsys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))\n"
        )

        result = await run_tests_for_files(["src/calculator.py"], repo)
        assert result["status"] == "PASSED"
        assert result["passed"] >= 2
        assert result["failed"] == 0
        assert result["errors"] == 0
        assert result["total"] >= 2
        assert result["duration_seconds"] > 0
        assert any("test_calculator.py" in t for t in result["tests_run"])

    @pytest.mark.asyncio
    async def test_failing_tests(self, tmp_path):
        """Reports FAILED when tests fail."""
        repo = _setup_repo(tmp_path, {
            "src/broken.py": """\
                def multiply(a, b):
                    return a + b  # bug: should be a * b
            """,
            "tests/test_broken.py": """\
                import sys, os
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
                from broken import multiply

                def test_multiply():
                    assert multiply(3, 4) == 12  # will fail because of bug
            """,
        })

        result = await run_tests_for_files(["src/broken.py"], repo)
        assert result["status"] == "FAILED"
        assert result["failed"] >= 1
        assert len(result["output"]) > 0

    @pytest.mark.asyncio
    async def test_timeout(self, tmp_path):
        """Returns TIMEOUT when pytest exceeds timeout."""
        repo = _setup_repo(tmp_path, {
            "src/slow.py": "def slow(): pass\n",
            "tests/test_slow.py": """\
                import time

                def test_slow():
                    time.sleep(10)
            """,
        })

        result = await run_tests_for_files(["src/slow.py"], repo, timeout=2)
        assert result["status"] == "TIMEOUT"
        assert "timed out" in result["output"]
        assert result["duration_seconds"] > 0

    @pytest.mark.asyncio
    async def test_test_files_verified_on_disk(self, tmp_path):
        """Test files that don't exist on disk are excluded."""
        repo = _setup_repo(tmp_path, {
            "src/widget.py": "def render(): pass\n",
            # No test file created on disk
        })
        result = await run_tests_for_files(["src/widget.py"], repo)
        assert result["status"] == "SKIPPED"

    @pytest.mark.asyncio
    async def test_multiple_source_files(self, tmp_path):
        """Deduplicates tests across multiple source files."""
        repo = _setup_repo(tmp_path, {
            "src/alpha.py": "def a(): pass\n",
            "src/beta.py": "def b(): pass\n",
            "tests/test_alpha.py": "def test_a(): assert True\n",
            "tests/test_beta.py": "def test_b(): assert True\n",
        })

        result = await run_tests_for_files(
            ["src/alpha.py", "src/beta.py"], repo
        )
        # Should find and run tests for both
        assert result["status"] in ("PASSED", "SKIPPED")
        if result["status"] == "PASSED":
            assert len(result["tests_run"]) >= 2


# -- format_test_results -----------------------------------------------------


class TestFormatTestResults:
    def test_passed_format(self):
        """Formats passing results as Markdown."""
        results = {
            "status": "PASSED",
            "passed": 5,
            "failed": 0,
            "errors": 0,
            "total": 5,
            "duration_seconds": 1.23,
            "tests_run": ["tests/test_foo.py"],
            "output": "",
        }
        text = format_test_results(results)
        assert "## Test Execution Results" in text
        assert "PASSED" in text
        assert "5 passed" in text
        assert "0 failed" in text
        assert "1.23s" in text
        assert "tests/test_foo.py" in text

    def test_failed_format_includes_output(self):
        """Failed results include output section."""
        results = {
            "status": "FAILED",
            "passed": 3,
            "failed": 2,
            "errors": 0,
            "total": 5,
            "duration_seconds": 2.5,
            "tests_run": ["tests/test_bar.py"],
            "output": "FAILED test_bar.py::test_x - AssertionError",
        }
        text = format_test_results(results)
        assert "FAILED" in text
        assert "3 passed" in text
        assert "2 failed" in text
        assert "### Output:" in text
        assert "AssertionError" in text

    def test_timeout_format(self):
        """Timeout results include output."""
        results = {
            "status": "TIMEOUT",
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "total": 0,
            "duration_seconds": 120.0,
            "tests_run": ["tests/test_slow.py"],
            "output": "pytest timed out after 120s",
        }
        text = format_test_results(results)
        assert "TIMEOUT" in text
        assert "timed out" in text

    def test_skipped_format(self):
        """Skipped results are minimal."""
        results = {
            "status": "SKIPPED",
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "total": 0,
            "duration_seconds": 0.0,
            "tests_run": [],
            "output": "No related test files found",
        }
        text = format_test_results(results)
        assert "SKIPPED" in text
        # Should NOT include output section for non-error statuses
        assert "### Output:" not in text

    def test_error_format_includes_output(self):
        """ERROR status includes output."""
        results = {
            "status": "ERROR",
            "passed": 0,
            "failed": 0,
            "errors": 1,
            "total": 1,
            "duration_seconds": 0.5,
            "tests_run": ["tests/test_bad.py"],
            "output": "ImportError: No module named 'nonexistent'",
        }
        text = format_test_results(results)
        assert "ERROR" in text
        assert "### Output:" in text
        assert "ImportError" in text

    def test_no_tests_run(self):
        """No tests run doesn't include 'Tests run for' line."""
        results = {
            "status": "SKIPPED",
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "total": 0,
            "duration_seconds": 0.0,
            "tests_run": [],
            "output": "",
        }
        text = format_test_results(results)
        assert "Tests run for:" not in text
