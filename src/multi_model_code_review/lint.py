"""Linting checks as pre-model gate."""

import subprocess
import sys
from dataclasses import dataclass


@dataclass
class LintResult:
    """Result of running lint checks."""

    passed: bool
    black_output: str = ""
    isort_output: str = ""
    ruff_output: str = ""

    @property
    def summary(self) -> str:
        """Get summary of lint failures."""
        if self.passed:
            return "All lint checks passed"

        parts = []
        if self.black_output:
            parts.append(f"black: {self.black_output.strip()}")
        if self.isort_output:
            parts.append(f"isort: {self.isort_output.strip()}")
        if self.ruff_output:
            parts.append(f"ruff: {self.ruff_output.strip()}")
        return "\n".join(parts)


def check_linter_available(name: str, cwd: str | None = None) -> bool:
    """Check if a linter module is available via python -m."""
    result = subprocess.run(
        [sys.executable, "-m", name, "--version"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return result.returncode == 0


def run_black_check(paths: list[str], cwd: str | None = None) -> tuple[bool, str]:
    """Run black --check on paths."""
    if not paths:
        return True, ""

    if not check_linter_available("black", cwd):
        return True, ""  # Skip if not installed

    result = subprocess.run(
        [sys.executable, "-m", "black", "--check", "--quiet"] + paths,
        capture_output=True,
        text=True,
        cwd=cwd,
    )

    if result.returncode == 0:
        return True, ""
    else:
        # Get list of files that would be reformatted
        result_verbose = subprocess.run(
            [sys.executable, "-m", "black", "--check"] + paths,
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        return False, result_verbose.stderr or result_verbose.stdout


def run_isort_check(paths: list[str], cwd: str | None = None) -> tuple[bool, str]:
    """Run isort --check on paths."""
    if not paths:
        return True, ""

    if not check_linter_available("isort", cwd):
        return True, ""  # Skip if not installed

    result = subprocess.run(
        [sys.executable, "-m", "isort", "--check-only", "--quiet"] + paths,
        capture_output=True,
        text=True,
        cwd=cwd,
    )

    if result.returncode == 0:
        return True, ""
    else:
        # Get list of files that would be reformatted
        result_verbose = subprocess.run(
            [sys.executable, "-m", "isort", "--check-only", "--diff"] + paths,
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        return False, result_verbose.stdout or result_verbose.stderr


def run_ruff_check(paths: list[str], cwd: str | None = None) -> tuple[bool, str]:
    """Run ruff check on paths."""
    if not paths:
        return True, ""

    if not check_linter_available("ruff", cwd):
        return True, ""  # Skip if not installed

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check"] + paths,
        capture_output=True,
        text=True,
        cwd=cwd,
    )

    if result.returncode == 0:
        return True, ""
    else:
        return False, result.stdout or result.stderr


def run_lint_checks(paths: list[str], cwd: str | None = None) -> LintResult:
    """
    Run all lint checks on the given paths.

    Args:
        paths: List of file/directory paths to check
        cwd: Working directory to run linters in (default: current directory)

    Returns:
        LintResult with pass/fail status and outputs
    """
    black_passed, black_output = run_black_check(paths, cwd)
    isort_passed, isort_output = run_isort_check(paths, cwd)
    ruff_passed, ruff_output = run_ruff_check(paths, cwd)

    return LintResult(
        passed=black_passed and isort_passed and ruff_passed,
        black_output=black_output if not black_passed else "",
        isort_output=isort_output if not isort_passed else "",
        ruff_output=ruff_output if not ruff_passed else "",
    )


@dataclass
class FixResult:
    """Result of running lint fixes."""

    black_fixed: int = 0
    isort_fixed: int = 0
    ruff_fixed: int = 0

    @property
    def total_fixed(self) -> int:
        return self.black_fixed + self.isort_fixed + self.ruff_fixed

    @property
    def summary(self) -> str:
        parts = []
        if self.black_fixed:
            parts.append(f"black: {self.black_fixed} files reformatted")
        if self.isort_fixed:
            parts.append(f"isort: {self.isort_fixed} files fixed")
        if self.ruff_fixed:
            parts.append(f"ruff: {self.ruff_fixed} fixes applied")
        return "\n".join(parts) if parts else "No fixes needed"


def run_black_fix(paths: list[str], cwd: str | None = None) -> int:
    """Run black to fix formatting issues."""
    if not paths or not check_linter_available("black", cwd):
        return 0

    result = subprocess.run(
        [sys.executable, "-m", "black"] + paths,
        capture_output=True,
        text=True,
        cwd=cwd,
    )

    # Count reformatted files from output
    output = result.stderr or result.stdout
    count = output.count("reformatted")
    return count


def run_isort_fix(paths: list[str], cwd: str | None = None) -> int:
    """Run isort to fix import ordering."""
    if not paths or not check_linter_available("isort", cwd):
        return 0

    # First check how many need fixing
    check_result = subprocess.run(
        [sys.executable, "-m", "isort", "--check-only"] + paths,
        capture_output=True,
        text=True,
        cwd=cwd,
    )

    if check_result.returncode == 0:
        return 0

    # Count files that would be changed
    diff_result = subprocess.run(
        [sys.executable, "-m", "isort", "--check-only", "--diff"] + paths,
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    # Count unique file headers in diff output
    count = diff_result.stdout.count("---")

    # Now fix them
    subprocess.run(
        [sys.executable, "-m", "isort"] + paths,
        capture_output=True,
        text=True,
        cwd=cwd,
    )

    return count


def run_ruff_fix(paths: list[str], cwd: str | None = None) -> int:
    """Run ruff --fix to auto-fix linting issues."""
    if not paths or not check_linter_available("ruff", cwd):
        return 0

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--fix"] + paths,
        capture_output=True,
        text=True,
        cwd=cwd,
    )

    # Parse "Found X errors (Y fixed, Z remaining)"
    output = result.stdout or result.stderr
    if "fixed" in output.lower():
        import re

        match = re.search(r"(\d+) fixed", output)
        if match:
            return int(match.group(1))
    return 0


def run_lint_fixes(paths: list[str], cwd: str | None = None) -> FixResult:
    """
    Run all lint fixers on the given paths.

    Order: isort (imports) → ruff (remove unused, etc) → black (final formatting).
    Black runs last to clean up any artifacts from other fixers.

    Args:
        paths: List of file/directory paths to fix
        cwd: Working directory to run linters in (default: current directory)

    Returns:
        FixResult with counts of fixes applied
    """
    isort_fixed = run_isort_fix(paths, cwd)
    ruff_fixed = run_ruff_fix(paths, cwd)
    black_fixed = run_black_fix(paths, cwd)

    return FixResult(
        black_fixed=black_fixed,
        isort_fixed=isort_fixed,
        ruff_fixed=ruff_fixed,
    )


def get_changed_python_files(
    ref: str | None = None, base: str | None = None, cwd: str | None = None
) -> list[str]:
    """
    Get list of changed Python files from git diff.

    Args:
        ref: Branch or commit to diff. If None, uses staged changes.
        base: Base branch to diff against (default: main)
        cwd: Working directory to run git in (default: current directory)

    Returns:
        List of changed .py file paths
    """
    if ref is None:
        cmd = ["git", "diff", "--staged", "--name-only", "--diff-filter=ACMR"]
    else:
        base = base or "main"
        cmd = ["git", "diff", f"{base}...{ref}", "--name-only", "--diff-filter=ACMR"]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)

    if result.returncode != 0:
        return []

    files = [f for f in result.stdout.strip().split("\n") if f and f.endswith(".py")]
    return files


@dataclass
class TestDiscoverabilityResult:
    """Result of checking test file discoverability."""

    warnings: list[str]

    @property
    def passed(self) -> bool:
        return len(self.warnings) == 0

    @property
    def summary(self) -> str:
        if self.passed:
            return "All test files are discoverable by pytest"
        return "\n".join(self.warnings)


def check_test_discoverability(
    changed_files: list[str],
    repo_path: str,
    new_files_only: bool = True,
) -> TestDiscoverabilityResult:
    """Check that test files match pytest discovery paths and conventions.

    Examines changed (or new) test files to verify they'll be found by pytest,
    and flags anti-patterns like ``sys.path.insert``.

    Args:
        changed_files: List of changed file paths (relative to repo_path).
        repo_path: Repository root path.
        new_files_only: If True, only check newly added files (via git status).
            If False, check all changed test files.

    Returns:
        TestDiscoverabilityResult with any warnings found.

    Example::

        >>> result = check_test_discoverability(["tests/test_foo.py"], "/repo")
        >>> print(result.passed)
        True
    """
    from pathlib import Path

    warnings: list[str] = []
    repo = Path(repo_path)

    # Identify test files among changed files
    test_files = [
        f for f in changed_files
        if Path(f).name.startswith("test_") or f.endswith("_test.py")
    ]

    if not test_files:
        return TestDiscoverabilityResult(warnings=[])

    # If new_files_only, filter to untracked/added files
    if new_files_only:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=A", "HEAD"],
            capture_output=True, text=True, cwd=repo_path,
        )
        new_files = set(result.stdout.strip().split("\n")) if result.returncode == 0 else set()
        # Also check untracked
        result2 = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, cwd=repo_path,
        )
        if result2.returncode == 0:
            new_files.update(result2.stdout.strip().split("\n"))
        test_files = [f for f in test_files if f in new_files]

    if not test_files:
        return TestDiscoverabilityResult(warnings=[])

    # Read pytest config from pyproject.toml
    testpaths: list[str] = []
    python_files_patterns: list[str] = ["test_*.py"]
    pyproject_path = repo / "pyproject.toml"

    if pyproject_path.exists():
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redefine]
            except ImportError:
                tomllib = None  # type: ignore[assignment]

        if tomllib is not None:
            try:
                with open(pyproject_path, "rb") as f:
                    pyproject = tomllib.load(f)
                pytest_opts = pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {})
                testpaths = pytest_opts.get("testpaths", [])
                python_files_patterns = pytest_opts.get("python_files", ["test_*.py"])
                if isinstance(python_files_patterns, str):
                    python_files_patterns = [python_files_patterns]
            except Exception:
                pass

    for test_file in test_files:
        test_path = Path(test_file)

        # Check 1: If testpaths configured, verify the file is under one of them
        if testpaths:
            under_testpath = any(
                test_file.startswith(tp.rstrip("/") + "/") or test_file == tp
                for tp in testpaths
            )
            if not under_testpath:
                warnings.append(
                    f"⚠ {test_file}: not under configured testpaths {testpaths} — "
                    f"pytest may not discover this file"
                )

        # Check 2: Verify filename matches python_files patterns
        import fnmatch
        name_matches = any(
            fnmatch.fnmatch(test_path.name, pat) for pat in python_files_patterns
        )
        if not name_matches:
            warnings.append(
                f"⚠ {test_file}: filename doesn't match pytest python_files "
                f"patterns {python_files_patterns}"
            )

        # Check 3: Flag sys.path.insert / sys.path.append anti-patterns
        full_path = repo / test_file
        if full_path.exists():
            try:
                content = full_path.read_text()
                for i, line in enumerate(content.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if "sys.path.insert" in stripped or "sys.path.append" in stripped:
                        warnings.append(
                            f"⚠ {test_file}:{i}: uses {stripped.split('(')[0].strip()}() — "
                            f"prefer proper pytest discovery (conftest.py or package install)"
                        )
                        break  # One warning per file is enough
            except Exception:
                pass

    return TestDiscoverabilityResult(warnings=warnings)
