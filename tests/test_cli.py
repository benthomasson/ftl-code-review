"""Tests for CLI commands."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from click.testing import CliRunner

from ftl_code_review import ModelReview, ChangeVerdict, Verdict
from ftl_code_review.cli import cli


@pytest.fixture
def runner():
    """Create a CLI runner."""
    return CliRunner()


@pytest.fixture
def mock_diff():
    """Sample diff content."""
    return """diff --git a/src/foo.py b/src/foo.py
index 1234567..abcdefg 100644
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,4 @@
 def hello():
-    print("hello")
+    print("hello world")
+    return True
"""


@pytest.fixture
def mock_review():
    """Create a mock ModelReview."""
    return ModelReview(
        model="claude",
        gate=Verdict.PASS,
        changes=[
            ChangeVerdict(
                change_id="src/foo.py",
                verdict=Verdict.PASS,
                reasoning="Looks good",
            )
        ],
        raw_response="### src/foo.py\nVERDICT: PASS\nREASONING: Looks good",
    )


class TestVersionCommand:
    """Tests for --version flag."""

    def test_version(self, runner):
        """Test that --version works."""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "version" in result.output.lower()


class TestReviewCommand:
    """Tests for the review command."""

    def test_review_no_changes(self, runner):
        """Test review with no changes."""
        with patch("ftl_code_review.cli.get_diff") as mock_get_diff:
            mock_get_diff.return_value = ""
            result = runner.invoke(cli, ["review"])
            assert result.exit_code == 0
            assert "No changes to review" in result.output

    def test_review_missing_model(self, runner, mock_diff):
        """Test review with missing model CLI."""
        with patch("ftl_code_review.cli.get_diff") as mock_get_diff, \
             patch("ftl_code_review.cli.preflight_check") as mock_preflight:
            mock_get_diff.return_value = mock_diff
            mock_preflight.return_value = ["nonexistent-model"]
            result = runner.invoke(cli, ["review", "-m", "nonexistent-model"])
            assert result.exit_code == 1
            assert "Missing CLI tools" in result.output

    def test_review_success(self, runner, mock_diff, mock_review):
        """Test successful review."""
        with patch("ftl_code_review.cli.get_diff") as mock_get_diff, \
             patch("ftl_code_review.cli.preflight_check") as mock_preflight, \
             patch("ftl_code_review.cli.review_with_models") as mock_review_models:
            mock_get_diff.return_value = mock_diff
            mock_preflight.return_value = []
            mock_review_models.return_value = [mock_review]

            result = runner.invoke(cli, ["review", "-m", "claude"])
            assert result.exit_code == 0
            assert "PASS" in result.output


class TestGateCommand:
    """Tests for the gate command."""

    def test_gate_pass_exits_0(self, runner, mock_diff, mock_review):
        """Test gate command exits 0 on PASS."""
        with patch("ftl_code_review.cli.get_diff") as mock_get_diff, \
             patch("ftl_code_review.cli.preflight_check") as mock_preflight, \
             patch("ftl_code_review.cli.review_with_models") as mock_review_models:
            mock_get_diff.return_value = mock_diff
            mock_preflight.return_value = []
            mock_review_models.return_value = [mock_review]

            result = runner.invoke(cli, ["gate", "-m", "claude"])
            assert result.exit_code == 0

    def test_gate_concern_exits_1(self, runner, mock_diff):
        """Test gate command exits 1 on CONCERN."""
        concern_review = ModelReview(
            model="claude",
            gate=Verdict.CONCERN,
            changes=[
                ChangeVerdict(
                    change_id="src/foo.py",
                    verdict=Verdict.CONCERN,
                    reasoning="Needs tests",
                )
            ],
            raw_response="### src/foo.py\nVERDICT: CONCERN\nREASONING: Needs tests",
        )
        with patch("ftl_code_review.cli.get_diff") as mock_get_diff, \
             patch("ftl_code_review.cli.preflight_check") as mock_preflight, \
             patch("ftl_code_review.cli.review_with_models") as mock_review_models:
            mock_get_diff.return_value = mock_diff
            mock_preflight.return_value = []
            mock_review_models.return_value = [concern_review]

            result = runner.invoke(cli, ["gate", "-m", "claude"])
            assert result.exit_code == 1

    def test_gate_block_exits_2(self, runner, mock_diff):
        """Test gate command exits 2 on BLOCK."""
        block_review = ModelReview(
            model="claude",
            gate=Verdict.BLOCK,
            changes=[
                ChangeVerdict(
                    change_id="src/foo.py",
                    verdict=Verdict.BLOCK,
                    reasoning="Security issue",
                )
            ],
            raw_response="### src/foo.py\nVERDICT: BLOCK\nREASONING: Security issue",
        )
        with patch("ftl_code_review.cli.get_diff") as mock_get_diff, \
             patch("ftl_code_review.cli.preflight_check") as mock_preflight, \
             patch("ftl_code_review.cli.review_with_models") as mock_review_models:
            mock_get_diff.return_value = mock_diff
            mock_preflight.return_value = []
            mock_review_models.return_value = [block_review]

            result = runner.invoke(cli, ["gate", "-m", "claude"])
            assert result.exit_code == 2


class TestObserveCommand:
    """Tests for the observe command."""

    def test_observe_missing_model(self, runner, mock_diff):
        """Test observe with missing model CLI."""
        with patch("ftl_code_review.cli.get_diff") as mock_get_diff, \
             patch("ftl_code_review.cli.preflight_check") as mock_preflight:
            mock_get_diff.return_value = mock_diff
            mock_preflight.return_value = ["nonexistent-model"]
            result = runner.invoke(cli, ["observe", "-m", "nonexistent-model"])
            assert result.exit_code == 1


class TestFilesCommand:
    """Tests for the files command."""

    def test_files_nonexistent_path(self, runner, tmp_path):
        """Test files command with nonexistent path."""
        result = runner.invoke(cli, ["files", "/nonexistent/path.py", "-r", str(tmp_path)])
        assert result.exit_code == 1
        assert "No files found" in result.output

    def test_files_with_python_file(self, runner, tmp_path, mock_review):
        """Test files command with Python file."""
        # Create a Python file
        py_file = tmp_path / "test.py"
        py_file.write_text("def foo(): pass")

        with patch("ftl_code_review.cli.preflight_check") as mock_preflight, \
             patch("ftl_code_review.cli.review_with_models") as mock_review_models, \
             patch("ftl_code_review.cli._gather_coverage_lookups") as mock_coverage:
            mock_preflight.return_value = []
            mock_review_models.return_value = [mock_review]
            mock_coverage.return_value = {}

            result = runner.invoke(cli, ["files", str(py_file), "-r", str(tmp_path), "-m", "claude"])
            assert result.exit_code == 0


class TestInstallSkillCommand:
    """Tests for the install-skill command."""

    def test_install_skill(self, runner, tmp_path):
        """Test install-skill creates the skill file."""
        skill_dir = tmp_path / ".claude" / "skills" / "code-review"

        result = runner.invoke(cli, ["install-skill", "--skill-dir", str(skill_dir)])
        assert result.exit_code == 0

        skill_file = skill_dir / "SKILL.md"
        assert skill_file.exists()
        content = skill_file.read_text()
        assert "code-review" in content


class TestGatherCoverageLookups:
    """Tests for _gather_coverage_lookups helper."""

    @pytest.mark.asyncio
    async def test_gather_coverage_lookups_empty(self):
        """Test with empty file list."""
        from ftl_code_review.cli import _gather_coverage_lookups
        result = await _gather_coverage_lookups([], "/tmp")
        assert result == {}

    @pytest.mark.asyncio
    async def test_gather_coverage_lookups_no_coverage_map(self, tmp_path):
        """Test when coverage-map.json doesn't exist."""
        from ftl_code_review.cli import _gather_coverage_lookups

        # No coverage-map.json, so all lookups will return errors
        result = await _gather_coverage_lookups(["foo.py"], str(tmp_path))
        assert result == {}  # Errors are filtered out

    @pytest.mark.asyncio
    async def test_gather_coverage_lookups_with_data(self, tmp_path):
        """Test with coverage-map.json present."""
        from ftl_code_review.cli import _gather_coverage_lookups

        # Create a coverage-map.json with correct format
        coverage_map = {
            "file_to_tests": {
                "src/foo.py": ["tests/test_foo.py::test_one", "tests/test_foo.py::test_two"]
            }
        }
        (tmp_path / "coverage-map.json").write_text(json.dumps(coverage_map))

        result = await _gather_coverage_lookups(["src/foo.py"], str(tmp_path))
        assert len(result) == 1
        assert "coverage_src_foo" in result
        assert result["coverage_src_foo"]["test_count"] == 2
