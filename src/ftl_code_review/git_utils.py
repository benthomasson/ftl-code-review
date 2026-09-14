"""Git utilities for extracting diffs."""

import json
import os
import re
import subprocess
from enum import Enum


class Platform(Enum):
    GITHUB = "github"
    GITLAB = "gitlab"


def _detect_platform_from_remote() -> Platform:
    """Detect platform from the current repo's git remote URL."""
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return Platform.GITHUB

    remote_url = result.stdout.strip()
    gitlab_host = os.environ.get("GITLAB_HOST", "gitlab.com")
    if "gitlab" in remote_url.lower() or gitlab_host in remote_url:
        return Platform.GITLAB
    return Platform.GITHUB


def detect_platform(ref: str) -> Platform:
    """Detect whether a PR/MR/issue reference points to GitHub or GitLab.

    Detection order:
        1. URL contains github.com → GITHUB
        2. URL contains /-/merge_requests/ or /-/issues/ → GITLAB
        3. URL contains gitlab.com or GITLAB_HOST → GITLAB
        4. Shorthand or bare number → check git remote
    """
    if "github.com" in ref:
        return Platform.GITHUB

    if "/-/merge_requests/" in ref or "/-/issues/" in ref:
        return Platform.GITLAB

    gitlab_host = os.environ.get("GITLAB_HOST", "gitlab.com")
    if "gitlab.com" in ref or (gitlab_host != "gitlab.com" and gitlab_host in ref):
        return Platform.GITLAB

    if re.match(r"https?://", ref):
        return Platform.GITHUB

    return _detect_platform_from_remote()


# ---------------------------------------------------------------------------
# Local git operations (platform-independent)
# ---------------------------------------------------------------------------


def get_diff(
    ref: str | None = None,
    base: str | None = None,
    cwd: str | None = None,
    context_lines: int = 10,
) -> str:
    """Get git diff for review.

    Args:
        ref: Branch or commit to diff. If None, uses staged changes.
        base: Base branch to diff against (default: main)
        cwd: Working directory to run git in (default: current directory)
        context_lines: Number of context lines around changes (default: 10)

    Returns:
        Git diff output as string

    Raises:
        RuntimeError: If git command fails
    """
    context_arg = f"-U{context_lines}"

    if ref is None:
        cmd = ["git", "diff", "--staged", context_arg]
    else:
        if base is None:
            check = subprocess.run(
                ["git", "rev-parse", "--verify", "origin/main"],
                capture_output=True,
                cwd=cwd,
            )
            base = "origin/main" if check.returncode == 0 else "main"
        cmd = ["git", "diff", context_arg, f"{base}...{ref}"]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)

    if result.returncode != 0:
        raise RuntimeError(f"Git diff failed: {result.stderr}")

    return result.stdout


def read_file_content(path: str) -> str | None:
    """Read file content, returning None if file doesn't exist."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


# ---------------------------------------------------------------------------
# GitHub implementation (private)
# ---------------------------------------------------------------------------


def _parse_github_pr_url(pr_ref: str) -> tuple[str | None, int]:
    """Parse a GitHub PR reference into (repo, number)."""
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/(\d+)", pr_ref)
    if m:
        return m.group(1), int(m.group(2))

    m = re.match(r"([^/]+/[^#]+)#(\d+)", pr_ref)
    if m:
        return m.group(1), int(m.group(2))

    m = re.match(r"(\d+)$", pr_ref)
    if m:
        return None, int(m.group(1))

    raise ValueError(f"Cannot parse PR reference: {pr_ref}")


def _get_github_pr_diff(pr_ref: str) -> tuple[str, str, str]:
    """Get diff for a GitHub PR using gh CLI."""
    repo, pr_number = _parse_github_pr_url(pr_ref)

    cmd = ["gh", "pr", "diff", str(pr_number)]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr diff failed: {result.stderr.strip()}")

    if repo:
        diff_ref = f"{repo}#{pr_number}"
    else:
        info_cmd = ["gh", "pr", "view", str(pr_number), "--json", "url"]
        info_result = subprocess.run(info_cmd, capture_output=True, text=True)
        if info_result.returncode == 0:
            url = json.loads(info_result.stdout).get("url", "")
            m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/\d+", url)
            diff_ref = f"{m.group(1)}#{pr_number}" if m else f"PR #{pr_number}"
        else:
            diff_ref = f"PR #{pr_number}"

    return result.stdout, diff_ref, repo or ""


def _fetch_github_pr_locally(pr_ref: str, cwd: str) -> tuple[str, str, str]:
    """Fetch a GitHub PR's branch into a local repo and check it out."""
    repo, pr_number = _parse_github_pr_url(pr_ref)

    cmd = ["gh", "pr", "view", str(pr_number), "--json", "headRefName,baseRefName,url"]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr view failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    head_branch = data["headRefName"]
    base_branch = data["baseRefName"]

    if repo:
        diff_ref = f"{repo}#{pr_number}"
    else:
        url = data.get("url", "")
        m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/\d+", url)
        diff_ref = f"{m.group(1)}#{pr_number}" if m else f"PR #{pr_number}"

    _checkout_branch(head_branch, cwd)

    return head_branch, base_branch, diff_ref


def _post_github_pr_comment(pr_ref: str, body: str) -> None:
    """Post a comment on a GitHub PR using gh CLI."""
    repo, pr_number = _parse_github_pr_url(pr_ref)

    cmd = ["gh", "pr", "comment", str(pr_number), "--body", body]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr comment failed: {result.stderr.strip()}")


def _parse_github_issue_url(issue_ref: str) -> tuple[str | None, int]:
    """Parse a GitHub issue reference into (repo, number)."""
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/issues/(\d+)", issue_ref)
    if m:
        return m.group(1), int(m.group(2))

    m = re.match(r"([^/]+/[^#]+)#(\d+)", issue_ref)
    if m:
        return m.group(1), int(m.group(2))

    m = re.match(r"(\d+)$", issue_ref)
    if m:
        return None, int(m.group(1))

    raise ValueError(f"Cannot parse issue reference: {issue_ref}")


def _get_github_issue_impl(issue_ref: str) -> str:
    """Fetch a GitHub issue's title and body using gh CLI."""
    repo, issue_number = _parse_github_issue_url(issue_ref)

    cmd = ["gh", "issue", "view", str(issue_number), "--json", "title,body"]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh issue view failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    title = data.get("title", "")
    body = data.get("body", "") or ""

    return f"## {title}\n\n{body}"


# ---------------------------------------------------------------------------
# GitLab implementation (private)
# ---------------------------------------------------------------------------


def _parse_gitlab_mr_url(mr_ref: str) -> tuple[str | None, int]:
    """Parse a GitLab MR reference into (repo, number).

    Accepts:
        - Full URL: https://gitlab.com/owner/repo/-/merge_requests/123
        - Full URL with nested groups: https://gitlab.example.com/group/sub/repo/-/merge_requests/5
        - Shorthand: owner/repo#123 (when platform already detected as GitLab)
        - Number only: 123
    """
    m = re.match(r"https?://[^/]+/(.+?)/-/merge_requests/(\d+)", mr_ref)
    if m:
        return m.group(1), int(m.group(2))

    m = re.match(r"([^/]+/[^#]+)#(\d+)", mr_ref)
    if m:
        return m.group(1), int(m.group(2))

    m = re.match(r"(\d+)$", mr_ref)
    if m:
        return None, int(m.group(1))

    raise ValueError(f"Cannot parse MR reference: {mr_ref}")


def _get_gitlab_mr_diff(mr_ref: str) -> tuple[str, str, str]:
    """Get diff for a GitLab MR using glab CLI."""
    repo, mr_number = _parse_gitlab_mr_url(mr_ref)

    cmd = ["glab", "mr", "diff", str(mr_number), "--color=never"]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"glab mr diff failed: {result.stderr.strip()}")

    if repo:
        diff_ref = f"{repo}!{mr_number}"
    else:
        info_cmd = ["glab", "mr", "view", str(mr_number), "--output", "json"]
        info_result = subprocess.run(info_cmd, capture_output=True, text=True)
        if info_result.returncode == 0:
            data = json.loads(info_result.stdout)
            web_url = data.get("web_url", "")
            m = re.match(r"https?://[^/]+/(.+?)/-/merge_requests/\d+", web_url)
            diff_ref = f"{m.group(1)}!{mr_number}" if m else f"MR !{mr_number}"
        else:
            diff_ref = f"MR !{mr_number}"

    return result.stdout, diff_ref, repo or ""


def _fetch_gitlab_mr_locally(mr_ref: str, cwd: str) -> tuple[str, str, str]:
    """Fetch a GitLab MR's branch into a local repo and check it out."""
    repo, mr_number = _parse_gitlab_mr_url(mr_ref)

    cmd = ["glab", "mr", "view", str(mr_number), "--output", "json"]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"glab mr view failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    head_branch = data["source_branch"]
    base_branch = data["target_branch"]

    if repo:
        diff_ref = f"{repo}!{mr_number}"
    else:
        web_url = data.get("web_url", "")
        m = re.match(r"https?://[^/]+/(.+?)/-/merge_requests/\d+", web_url)
        diff_ref = f"{m.group(1)}!{mr_number}" if m else f"MR !{mr_number}"

    _checkout_branch(head_branch, cwd)

    return head_branch, base_branch, diff_ref


def _post_gitlab_mr_comment(mr_ref: str, body: str) -> None:
    """Post a comment on a GitLab MR using glab CLI."""
    repo, mr_number = _parse_gitlab_mr_url(mr_ref)

    cmd = ["glab", "mr", "note", str(mr_number), "--message", body]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"glab mr note failed: {result.stderr.strip()}")


def _parse_gitlab_issue_url(issue_ref: str) -> tuple[str | None, int]:
    """Parse a GitLab issue reference into (repo, number)."""
    m = re.match(r"https?://[^/]+/(.+?)/-/issues/(\d+)", issue_ref)
    if m:
        return m.group(1), int(m.group(2))

    m = re.match(r"([^/]+/[^#]+)#(\d+)", issue_ref)
    if m:
        return m.group(1), int(m.group(2))

    m = re.match(r"(\d+)$", issue_ref)
    if m:
        return None, int(m.group(1))

    raise ValueError(f"Cannot parse issue reference: {issue_ref}")


def _get_gitlab_issue_impl(issue_ref: str) -> str:
    """Fetch a GitLab issue's title and description using glab CLI."""
    repo, issue_number = _parse_gitlab_issue_url(issue_ref)

    cmd = ["glab", "issue", "view", str(issue_number), "--output", "json"]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"glab issue view failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    title = data.get("title", "")
    description = data.get("description", "") or ""

    return f"## {title}\n\n{description}"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _checkout_branch(branch: str, cwd: str) -> None:
    """Fetch and checkout a branch in a local repo."""
    subprocess.run(
        ["git", "fetch", "origin", branch],
        cwd=cwd, capture_output=True, text=True,
    )

    check = subprocess.run(
        ["git", "rev-parse", "--verify", branch],
        cwd=cwd, capture_output=True,
    )
    if check.returncode == 0:
        subprocess.run(
            ["git", "checkout", branch],
            cwd=cwd, capture_output=True,
        )
        subprocess.run(
            ["git", "pull", "--ff-only", "origin", branch],
            cwd=cwd, capture_output=True,
        )
    else:
        subprocess.run(
            ["git", "checkout", "-b", branch, f"origin/{branch}"],
            cwd=cwd, capture_output=True,
        )


# ---------------------------------------------------------------------------
# Public API (dispatch based on platform detection)
# ---------------------------------------------------------------------------


def parse_pr_url(pr_ref: str) -> tuple[str | None, int]:
    """Parse a PR/MR reference into (repo, number). Works for GitHub and GitLab."""
    platform = detect_platform(pr_ref)
    if platform == Platform.GITLAB:
        return _parse_gitlab_mr_url(pr_ref)
    return _parse_github_pr_url(pr_ref)


def get_pr_diff(pr_ref: str) -> tuple[str, str, str]:
    """Get diff for a GitHub PR or GitLab MR.

    Args:
        pr_ref: PR/MR URL, owner/repo#N, or number

    Returns:
        (diff_content, diff_ref, repo)

    Raises:
        RuntimeError: If CLI command fails
    """
    platform = detect_platform(pr_ref)
    if platform == Platform.GITLAB:
        return _get_gitlab_mr_diff(pr_ref)
    return _get_github_pr_diff(pr_ref)


def fetch_pr_locally(pr_ref: str, cwd: str) -> tuple[str, str, str]:
    """Fetch a PR/MR's branch into a local repo and check it out.

    Args:
        pr_ref: PR/MR URL, owner/repo#N, or number
        cwd: Local repo directory

    Returns:
        (head_branch, base_branch, diff_ref)

    Raises:
        RuntimeError: If CLI or git commands fail
    """
    platform = detect_platform(pr_ref)
    if platform == Platform.GITLAB:
        return _fetch_gitlab_mr_locally(pr_ref, cwd)
    return _fetch_github_pr_locally(pr_ref, cwd)


def post_pr_comment(pr_ref: str, body: str) -> None:
    """Post a comment on a GitHub PR or GitLab MR.

    Args:
        pr_ref: PR/MR URL, owner/repo#N, or number
        body: Comment body (markdown)

    Raises:
        RuntimeError: If CLI command fails
    """
    platform = detect_platform(pr_ref)
    if platform == Platform.GITLAB:
        _post_gitlab_mr_comment(pr_ref, body)
    else:
        _post_github_pr_comment(pr_ref, body)


def parse_issue_ref(issue_ref: str) -> tuple[str | None, int]:
    """Parse an issue reference into (repo, number). Works for GitHub and GitLab."""
    platform = detect_platform(issue_ref)
    if platform == Platform.GITLAB:
        return _parse_gitlab_issue_url(issue_ref)
    return _parse_github_issue_url(issue_ref)


def get_github_issue(issue_ref: str) -> str:
    """Fetch an issue's title and body. Works for GitHub and GitLab.

    Args:
        issue_ref: Issue URL, owner/repo#N, or number

    Returns:
        Formatted issue content (title + body)

    Raises:
        RuntimeError: If CLI command fails
    """
    platform = detect_platform(issue_ref)
    if platform == Platform.GITLAB:
        return _get_gitlab_issue_impl(issue_ref)
    return _get_github_issue_impl(issue_ref)


def pr_output_dir_name(pr_ref: str) -> str:
    """Generate a clean output directory name from a PR/MR reference.

    Examples:
        "https://github.com/owner/repo/pull/123" -> "owner-repo-123"
        "https://gitlab.com/owner/repo/-/merge_requests/123" -> "owner-repo-123"
        "123" -> "pr-123" (GitHub) or "mr-123" (GitLab)
    """
    platform = detect_platform(pr_ref)
    if platform == Platform.GITLAB:
        repo, mr_number = _parse_gitlab_mr_url(pr_ref)
        if repo:
            return f"{repo.replace('/', '-')}-{mr_number}"
        return f"mr-{mr_number}"
    repo, pr_number = _parse_github_pr_url(pr_ref)
    if repo:
        return f"{repo.replace('/', '-')}-{pr_number}"
    return f"pr-{pr_number}"


# ---------------------------------------------------------------------------
# Diff parsing utilities (platform-independent)
# ---------------------------------------------------------------------------


def extract_changed_files(diff_content: str) -> list[str]:
    """Extract file paths from a git diff.

    Args:
        diff_content: Git diff output

    Returns:
        List of file paths that were changed
    """
    files = []
    for line in diff_content.split("\n"):
        if line.startswith("+++ b/"):
            path = line[6:]
            if path != "/dev/null":
                files.append(path)
    return files


def extract_modified_line_ranges(diff_content: str) -> dict[str, list[tuple[int, int]]]:
    """Parse a unified diff to extract modified line ranges in the new version of each file.

    Parses ``@@ -a,b +c,d @@`` hunk headers to determine which lines were
    touched.  Returns ranges in the **new** file (the ``+`` side).

    Args:
        diff_content: Unified diff output (e.g. from ``git diff``)

    Returns:
        Dict mapping file paths to lists of ``(start_line, end_line)`` tuples
        representing modified regions in the new file.

    Example::

        >>> diff = '''diff --git a/foo.py b/foo.py
        ... --- a/foo.py
        ... +++ b/foo.py
        ... @@ -10,4 +10,6 @@ def hello():
        ...  unchanged
        ... +added line 1
        ... +added line 2
        ...  unchanged
        ... '''
        >>> extract_modified_line_ranges(diff)
        {'foo.py': [(10, 15)]}
    """
    result: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None

    for line in diff_content.split("\n"):
        if line.startswith("+++ b/"):
            path = line[6:]
            current_file = path if path != "/dev/null" else None
            continue

        if current_file and line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                if count > 0:
                    end = start + count - 1
                    result.setdefault(current_file, []).append((start, end))

    return result
