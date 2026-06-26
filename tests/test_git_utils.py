"""Tests for git_utils platform detection, parsing, and dispatch."""

from unittest.mock import MagicMock, patch

import pytest

from ftl_code_review.git_utils import (
    Platform,
    _parse_github_pr_url,
    _parse_gitlab_mr_url,
    _parse_github_issue_url,
    _parse_gitlab_issue_url,
    detect_platform,
    get_pr_diff,
    fetch_pr_locally,
    post_pr_comment,
    get_github_issue,
    parse_pr_url,
    parse_issue_ref,
    pr_output_dir_name,
)


# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------


class TestDetectPlatform:
    def test_github_pr_url(self):
        assert detect_platform("https://github.com/owner/repo/pull/123") == Platform.GITHUB

    def test_github_issue_url(self):
        assert detect_platform("https://github.com/owner/repo/issues/42") == Platform.GITHUB

    def test_gitlab_mr_url(self):
        assert detect_platform("https://gitlab.com/owner/repo/-/merge_requests/123") == Platform.GITLAB

    def test_gitlab_issue_url(self):
        assert detect_platform("https://gitlab.com/owner/repo/-/issues/42") == Platform.GITLAB

    def test_gitlab_nested_group_url(self):
        assert detect_platform("https://gitlab.com/group/sub/repo/-/merge_requests/5") == Platform.GITLAB

    def test_self_hosted_gitlab_mr_url(self):
        assert detect_platform("https://git.company.com/team/project/-/merge_requests/99") == Platform.GITLAB

    def test_self_hosted_gitlab_via_env(self):
        with patch.dict("os.environ", {"GITLAB_HOST": "git.internal.co"}):
            assert detect_platform("https://git.internal.co/team/repo/something") == Platform.GITLAB

    def test_unknown_url_defaults_github(self):
        assert detect_platform("https://bitbucket.org/owner/repo/pull-requests/1") == Platform.GITHUB

    @patch("ftl_code_review.git_utils._detect_platform_from_remote")
    def test_bare_number_checks_remote(self, mock_remote):
        mock_remote.return_value = Platform.GITLAB
        assert detect_platform("123") == Platform.GITLAB
        mock_remote.assert_called_once()

    @patch("ftl_code_review.git_utils._detect_platform_from_remote")
    def test_shorthand_checks_remote(self, mock_remote):
        mock_remote.return_value = Platform.GITHUB
        assert detect_platform("owner/repo#42") == Platform.GITHUB
        mock_remote.assert_called_once()


class TestDetectPlatformFromRemote:
    @patch("ftl_code_review.git_utils.subprocess")
    def test_github_remote(self, mock_sub):
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="https://github.com/o/r.git\n")
        from ftl_code_review.git_utils import _detect_platform_from_remote
        assert _detect_platform_from_remote() == Platform.GITHUB

    @patch("ftl_code_review.git_utils.subprocess")
    def test_gitlab_remote(self, mock_sub):
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="git@gitlab.com:group/repo.git\n")
        from ftl_code_review.git_utils import _detect_platform_from_remote
        assert _detect_platform_from_remote() == Platform.GITLAB

    @patch("ftl_code_review.git_utils.subprocess")
    def test_no_remote_defaults_github(self, mock_sub):
        mock_sub.run.return_value = MagicMock(returncode=1, stdout="")
        from ftl_code_review.git_utils import _detect_platform_from_remote
        assert _detect_platform_from_remote() == Platform.GITHUB

    @patch("ftl_code_review.git_utils.subprocess")
    def test_gitlab_host_env(self, mock_sub):
        mock_sub.run.return_value = MagicMock(returncode=0, stdout="git@git.internal.co:team/repo.git\n")
        from ftl_code_review.git_utils import _detect_platform_from_remote
        with patch.dict("os.environ", {"GITLAB_HOST": "git.internal.co"}):
            assert _detect_platform_from_remote() == Platform.GITLAB


# ---------------------------------------------------------------------------
# GitHub URL parsing
# ---------------------------------------------------------------------------


class TestParseGitHubPrUrl:
    def test_full_url(self):
        assert _parse_github_pr_url("https://github.com/owner/repo/pull/123") == ("owner/repo", 123)

    def test_shorthand(self):
        assert _parse_github_pr_url("owner/repo#42") == ("owner/repo", 42)

    def test_number_only(self):
        assert _parse_github_pr_url("7") == (None, 7)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_github_pr_url("not-a-ref")


class TestParseGitHubIssueUrl:
    def test_full_url(self):
        assert _parse_github_issue_url("https://github.com/owner/repo/issues/99") == ("owner/repo", 99)

    def test_shorthand(self):
        assert _parse_github_issue_url("owner/repo#10") == ("owner/repo", 10)

    def test_number_only(self):
        assert _parse_github_issue_url("5") == (None, 5)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_github_issue_url("bad-ref!")


# ---------------------------------------------------------------------------
# GitLab URL parsing
# ---------------------------------------------------------------------------


class TestParseGitLabMrUrl:
    def test_full_url(self):
        assert _parse_gitlab_mr_url("https://gitlab.com/owner/repo/-/merge_requests/123") == ("owner/repo", 123)

    def test_nested_group_url(self):
        assert _parse_gitlab_mr_url("https://gitlab.com/group/subgroup/repo/-/merge_requests/5") == ("group/subgroup/repo", 5)

    def test_self_hosted_url(self):
        assert _parse_gitlab_mr_url("https://git.company.com/team/project/-/merge_requests/99") == ("team/project", 99)

    def test_shorthand(self):
        assert _parse_gitlab_mr_url("owner/repo#42") == ("owner/repo", 42)

    def test_number_only(self):
        assert _parse_gitlab_mr_url("7") == (None, 7)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_gitlab_mr_url("not-a-ref")


class TestParseGitLabIssueUrl:
    def test_full_url(self):
        assert _parse_gitlab_issue_url("https://gitlab.com/owner/repo/-/issues/42") == ("owner/repo", 42)

    def test_nested_group_url(self):
        assert _parse_gitlab_issue_url("https://gitlab.com/group/sub/repo/-/issues/10") == ("group/sub/repo", 10)

    def test_shorthand(self):
        assert _parse_gitlab_issue_url("owner/repo#5") == ("owner/repo", 5)

    def test_number_only(self):
        assert _parse_gitlab_issue_url("3") == (None, 3)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_gitlab_issue_url("not-a-ref")


# ---------------------------------------------------------------------------
# Dispatch: public API routes to correct implementation
# ---------------------------------------------------------------------------


class TestParseDispatch:
    def test_github_url_dispatches(self):
        repo, num = parse_pr_url("https://github.com/owner/repo/pull/123")
        assert repo == "owner/repo"
        assert num == 123

    def test_gitlab_url_dispatches(self):
        repo, num = parse_pr_url("https://gitlab.com/group/repo/-/merge_requests/45")
        assert repo == "group/repo"
        assert num == 45

    def test_issue_github_dispatches(self):
        repo, num = parse_issue_ref("https://github.com/owner/repo/issues/10")
        assert repo == "owner/repo"
        assert num == 10

    def test_issue_gitlab_dispatches(self):
        repo, num = parse_issue_ref("https://gitlab.com/owner/repo/-/issues/10")
        assert repo == "owner/repo"
        assert num == 10


class TestGetPrDiffDispatch:
    @patch("ftl_code_review.git_utils._get_github_pr_diff")
    def test_github_url(self, mock_gh):
        mock_gh.return_value = ("diff", "o/r#1", "o/r")
        result = get_pr_diff("https://github.com/o/r/pull/1")
        mock_gh.assert_called_once_with("https://github.com/o/r/pull/1")
        assert result == ("diff", "o/r#1", "o/r")

    @patch("ftl_code_review.git_utils._get_gitlab_mr_diff")
    def test_gitlab_url(self, mock_gl):
        mock_gl.return_value = ("diff", "o/r!1", "o/r")
        result = get_pr_diff("https://gitlab.com/o/r/-/merge_requests/1")
        mock_gl.assert_called_once_with("https://gitlab.com/o/r/-/merge_requests/1")
        assert result == ("diff", "o/r!1", "o/r")


class TestFetchPrLocallyDispatch:
    @patch("ftl_code_review.git_utils._fetch_github_pr_locally")
    def test_github_url(self, mock_gh):
        mock_gh.return_value = ("feature", "main", "o/r#1")
        result = fetch_pr_locally("https://github.com/o/r/pull/1", "/tmp")
        mock_gh.assert_called_once_with("https://github.com/o/r/pull/1", "/tmp")
        assert result == ("feature", "main", "o/r#1")

    @patch("ftl_code_review.git_utils._fetch_gitlab_mr_locally")
    def test_gitlab_url(self, mock_gl):
        mock_gl.return_value = ("feature", "main", "o/r!1")
        result = fetch_pr_locally("https://gitlab.com/o/r/-/merge_requests/1", "/tmp")
        mock_gl.assert_called_once_with("https://gitlab.com/o/r/-/merge_requests/1", "/tmp")
        assert result == ("feature", "main", "o/r!1")


class TestPostPrCommentDispatch:
    @patch("ftl_code_review.git_utils._post_github_pr_comment")
    def test_github_url(self, mock_gh):
        post_pr_comment("https://github.com/o/r/pull/1", "review text")
        mock_gh.assert_called_once_with("https://github.com/o/r/pull/1", "review text")

    @patch("ftl_code_review.git_utils._post_gitlab_mr_comment")
    def test_gitlab_url(self, mock_gl):
        post_pr_comment("https://gitlab.com/o/r/-/merge_requests/1", "review text")
        mock_gl.assert_called_once_with("https://gitlab.com/o/r/-/merge_requests/1", "review text")


class TestGetIssueDispatch:
    @patch("ftl_code_review.git_utils._get_github_issue_impl")
    def test_github_url(self, mock_gh):
        mock_gh.return_value = "## Title\n\nbody"
        result = get_github_issue("https://github.com/o/r/issues/1")
        mock_gh.assert_called_once_with("https://github.com/o/r/issues/1")
        assert result == "## Title\n\nbody"

    @patch("ftl_code_review.git_utils._get_gitlab_issue_impl")
    def test_gitlab_url(self, mock_gl):
        mock_gl.return_value = "## Title\n\ndescription"
        result = get_github_issue("https://gitlab.com/o/r/-/issues/1")
        mock_gl.assert_called_once_with("https://gitlab.com/o/r/-/issues/1")
        assert result == "## Title\n\ndescription"


# ---------------------------------------------------------------------------
# pr_output_dir_name
# ---------------------------------------------------------------------------


class TestPrOutputDirName:
    def test_github_url(self):
        assert pr_output_dir_name("https://github.com/owner/repo/pull/123") == "owner-repo-123"

    def test_gitlab_url(self):
        assert pr_output_dir_name("https://gitlab.com/owner/repo/-/merge_requests/123") == "owner-repo-123"

    def test_gitlab_nested_group(self):
        assert pr_output_dir_name("https://gitlab.com/group/sub/repo/-/merge_requests/5") == "group-sub-repo-5"

    @patch("ftl_code_review.git_utils._detect_platform_from_remote", return_value=Platform.GITHUB)
    def test_github_bare_number(self, _):
        assert pr_output_dir_name("42") == "pr-42"

    @patch("ftl_code_review.git_utils._detect_platform_from_remote", return_value=Platform.GITLAB)
    def test_gitlab_bare_number(self, _):
        assert pr_output_dir_name("42") == "mr-42"
