"""Tests for check_test_discoverability() and TestDiscoverabilityResult."""

import subprocess
import textwrap

import pytest

from ftl_code_review.lint import (
    TestDiscoverabilityResult,
    check_test_discoverability,
)


# -- Helpers ------------------------------------------------------------------


def _setup_git_repo(tmp_path, files: dict[str, str], new_files: list[str] | None = None) -> str:
    """Create a git repo with files. Optionally mark some as 'new' (untracked)."""
    # Init git repo
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True,
    )

    # Create all files
    for rel_path, content in files.items():
        p = tmp_path / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content))

    # Commit all files that are NOT in new_files
    new_files = new_files or []
    existing = [f for f in files if f not in new_files]
    if existing:
        for f in existing:
            subprocess.run(["git", "add", f], cwd=str(tmp_path), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=str(tmp_path), capture_output=True,
        )

    return str(tmp_path)


# -- TestDiscoverabilityResult ------------------------------------------------


class TestTestDiscoverabilityResult:
    def test_passed_when_no_warnings(self):
        result = TestDiscoverabilityResult(warnings=[])
        assert result.passed is True
        assert "discoverable" in result.summary.lower()

    def test_failed_when_warnings(self):
        result = TestDiscoverabilityResult(warnings=["⚠ bad file"])
        assert result.passed is False
        assert "bad file" in result.summary

    def test_summary_joins_warnings(self):
        result = TestDiscoverabilityResult(warnings=["warn1", "warn2"])
        assert "warn1" in result.summary
        assert "warn2" in result.summary


# -- check_test_discoverability: no test files --------------------------------


class TestDiscoverabilityNoTestFiles:
    def test_no_test_files_returns_passed(self, tmp_path):
        """Non-test files are ignored."""
        repo = _setup_git_repo(tmp_path, {
            "src/foo.py": "def foo(): pass\n",
        })
        result = check_test_discoverability(["src/foo.py"], repo, new_files_only=False)
        assert result.passed is True

    def test_empty_file_list(self, tmp_path):
        repo = _setup_git_repo(tmp_path, {})
        result = check_test_discoverability([], repo, new_files_only=False)
        assert result.passed is True


# -- check_test_discoverability: testpaths check ------------------------------


class TestDiscoverabilityTestpaths:
    def test_file_under_testpaths_passes(self, tmp_path):
        """Test file under configured testpaths passes."""
        repo = _setup_git_repo(
            tmp_path,
            {
                "pyproject.toml": """\
                    [tool.pytest.ini_options]
                    testpaths = ["tests"]
                """,
                "tests/test_foo.py": "def test_it(): pass\n",
            },
        )
        result = check_test_discoverability(
            ["tests/test_foo.py"], repo, new_files_only=False
        )
        # Should not warn about testpaths
        testpath_warnings = [w for w in result.warnings if "testpaths" in w]
        assert len(testpath_warnings) == 0

    def test_file_outside_testpaths_warns(self, tmp_path):
        """Test file outside configured testpaths triggers warning."""
        repo = _setup_git_repo(
            tmp_path,
            {
                "pyproject.toml": """\
                    [tool.pytest.ini_options]
                    testpaths = ["tests"]
                """,
                "other/test_bar.py": "def test_it(): pass\n",
            },
        )
        result = check_test_discoverability(
            ["other/test_bar.py"], repo, new_files_only=False
        )
        assert not result.passed
        assert any("testpaths" in w for w in result.warnings)

    def test_no_testpaths_configured_no_warning(self, tmp_path):
        """Without testpaths in pyproject.toml, no testpaths warning."""
        repo = _setup_git_repo(
            tmp_path,
            {
                "pyproject.toml": """\
                    [project]
                    name = "test"
                """,
                "anywhere/test_foo.py": "def test_it(): pass\n",
            },
        )
        result = check_test_discoverability(
            ["anywhere/test_foo.py"], repo, new_files_only=False
        )
        testpath_warnings = [w for w in result.warnings if "testpaths" in w]
        assert len(testpath_warnings) == 0


# -- check_test_discoverability: python_files patterns ------------------------


class TestDiscoverabilityPythonFilesPatterns:
    def test_default_pattern_matches_test_prefix(self, tmp_path):
        """Default pattern test_*.py matches test_foo.py."""
        repo = _setup_git_repo(
            tmp_path,
            {
                "tests/test_foo.py": "def test_it(): pass\n",
            },
        )
        result = check_test_discoverability(
            ["tests/test_foo.py"], repo, new_files_only=False
        )
        pattern_warnings = [w for w in result.warnings if "python_files" in w]
        assert len(pattern_warnings) == 0

    def test_custom_pattern_warns_on_mismatch(self, tmp_path):
        """Custom python_files pattern warns if filename doesn't match."""
        repo = _setup_git_repo(
            tmp_path,
            {
                "pyproject.toml": """\
                    [tool.pytest.ini_options]
                    python_files = ["check_*.py"]
                """,
                "tests/test_foo.py": "def test_it(): pass\n",
            },
        )
        result = check_test_discoverability(
            ["tests/test_foo.py"], repo, new_files_only=False
        )
        assert any("python_files" in w for w in result.warnings)


# -- check_test_discoverability: sys.path anti-pattern ------------------------


class TestDiscoverabilitySysPath:
    def test_sys_path_insert_flagged(self, tmp_path):
        """sys.path.insert() in test files is flagged."""
        repo = _setup_git_repo(
            tmp_path,
            {
                "tests/test_hack.py": """\
                    import sys
                    sys.path.insert(0, "/some/path")

                    def test_it(): pass
                """,
            },
        )
        result = check_test_discoverability(
            ["tests/test_hack.py"], repo, new_files_only=False
        )
        assert any("sys.path.insert" in w for w in result.warnings)

    def test_sys_path_append_flagged(self, tmp_path):
        """sys.path.append() in test files is flagged."""
        repo = _setup_git_repo(
            tmp_path,
            {
                "tests/test_hack2.py": """\
                    import sys
                    sys.path.append("/some/path")

                    def test_it(): pass
                """,
            },
        )
        result = check_test_discoverability(
            ["tests/test_hack2.py"], repo, new_files_only=False
        )
        assert any("sys.path.append" in w for w in result.warnings)

    def test_commented_sys_path_not_flagged(self, tmp_path):
        """Commented-out sys.path lines are not flagged."""
        repo = _setup_git_repo(
            tmp_path,
            {
                "tests/test_clean.py": """\
                    # sys.path.insert(0, "/old/path")

                    def test_it(): pass
                """,
            },
        )
        result = check_test_discoverability(
            ["tests/test_clean.py"], repo, new_files_only=False
        )
        syspath_warnings = [w for w in result.warnings if "sys.path" in w]
        assert len(syspath_warnings) == 0


# -- check_test_discoverability: new_files_only filter ------------------------


class TestDiscoverabilityNewFilesOnly:
    def test_existing_file_skipped_in_new_files_only_mode(self, tmp_path):
        """Already-committed test files are skipped in new_files_only mode."""
        repo = _setup_git_repo(
            tmp_path,
            {
                "tests/test_old.py": """\
                    import sys
                    sys.path.insert(0, "/bad")
                    def test_it(): pass
                """,
            },
            new_files=[],  # all files committed
        )
        result = check_test_discoverability(
            ["tests/test_old.py"], repo, new_files_only=True
        )
        # Should pass because the file is not new
        assert result.passed is True

    def test_untracked_file_checked_in_new_files_only_mode(self, tmp_path):
        """Untracked test files ARE checked in new_files_only mode."""
        repo = _setup_git_repo(
            tmp_path,
            {
                "tests/test_new.py": """\
                    import sys
                    sys.path.insert(0, "/bad")
                    def test_it(): pass
                """,
            },
            new_files=["tests/test_new.py"],  # not committed
        )
        result = check_test_discoverability(
            ["tests/test_new.py"], repo, new_files_only=True
        )
        assert any("sys.path.insert" in w for w in result.warnings)


# -- check_test_discoverability: no pyproject.toml ----------------------------


class TestDiscoverabilityNoPyproject:
    def test_no_pyproject_uses_defaults(self, tmp_path):
        """Without pyproject.toml, uses default test_*.py pattern."""
        repo = _setup_git_repo(
            tmp_path,
            {
                "tests/test_foo.py": "def test_it(): pass\n",
            },
        )
        result = check_test_discoverability(
            ["tests/test_foo.py"], repo, new_files_only=False
        )
        # Default pattern matches test_*.py, so no warnings
        assert result.passed is True
