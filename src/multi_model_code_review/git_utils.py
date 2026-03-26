"""Git utilities for extracting diffs."""

import re
import subprocess


def get_diff(
    ref: str | None = None,
    base: str | None = None,
    cwd: str | None = None,
    context_lines: int = 10,
) -> str:
    """
    Get git diff for review.

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
        # Staged changes
        cmd = ["git", "diff", "--staged", context_arg]
    else:
        # Diff between base and ref
        # Default to origin/main to avoid stale local main issues
        if base is None:
            # Check if origin/main exists, fall back to main
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
    """
    Read file content, returning None if file doesn't exist.

    Args:
        path: Path to file

    Returns:
        File content or None
    """
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def parse_pr_url(pr_ref: str) -> tuple[str | None, int]:
    """
    Parse a PR reference into (repo, number).

    Accepts:
        - Full URL: https://github.com/owner/repo/pull/123
        - Shorthand: owner/repo#123
        - Number only: 123 (repo=None, uses current repo)

    Returns:
        (repo, pr_number) where repo is "owner/repo" or None
    """
    # Full GitHub URL
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/(\d+)", pr_ref)
    if m:
        return m.group(1), int(m.group(2))

    # Shorthand: owner/repo#123
    m = re.match(r"([^/]+/[^#]+)#(\d+)", pr_ref)
    if m:
        return m.group(1), int(m.group(2))

    # Plain number
    m = re.match(r"(\d+)$", pr_ref)
    if m:
        return None, int(m.group(1))

    raise ValueError(f"Cannot parse PR reference: {pr_ref}")


def get_pr_diff(pr_ref: str) -> tuple[str, str, str]:
    """
    Get diff for a GitHub PR using gh CLI.

    Args:
        pr_ref: PR URL, owner/repo#N, or number

    Returns:
        (diff_content, diff_ref, repo) where diff_ref is "owner/repo#N"

    Raises:
        RuntimeError: If gh command fails
    """
    repo, pr_number = parse_pr_url(pr_ref)

    # Build gh command
    cmd = ["gh", "pr", "diff", str(pr_number)]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr diff failed: {result.stderr.strip()}")

    # Build diff_ref label
    if repo:
        diff_ref = f"{repo}#{pr_number}"
    else:
        # Try to get repo name from gh
        info_cmd = ["gh", "pr", "view", str(pr_number), "--json", "url"]
        info_result = subprocess.run(info_cmd, capture_output=True, text=True)
        if info_result.returncode == 0:
            import json
            url = json.loads(info_result.stdout).get("url", "")
            m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/\d+", url)
            diff_ref = f"{m.group(1)}#{pr_number}" if m else f"PR #{pr_number}"
        else:
            diff_ref = f"PR #{pr_number}"

    return result.stdout, diff_ref, repo or ""


def fetch_pr_locally(pr_ref: str, cwd: str) -> tuple[str, str, str]:
    """
    Fetch a PR's branch into a local repo and check it out.

    Args:
        pr_ref: PR URL, owner/repo#N, or number
        cwd: Local repo directory

    Returns:
        (head_branch, base_branch, diff_ref) — ready for get_diff()

    Raises:
        RuntimeError: If gh or git commands fail
    """
    import json

    repo, pr_number = parse_pr_url(pr_ref)

    # Get PR metadata
    cmd = ["gh", "pr", "view", str(pr_number), "--json", "headRefName,baseRefName,url"]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr view failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    head_branch = data["headRefName"]
    base_branch = data["baseRefName"]

    # Build diff_ref label
    if repo:
        diff_ref = f"{repo}#{pr_number}"
    else:
        url = data.get("url", "")
        m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/\d+", url)
        diff_ref = f"{m.group(1)}#{pr_number}" if m else f"PR #{pr_number}"

    # Fetch and checkout the PR branch
    subprocess.run(
        ["git", "fetch", "origin", head_branch],
        cwd=cwd, capture_output=True, text=True,
    )

    # Check if branch exists locally
    check = subprocess.run(
        ["git", "rev-parse", "--verify", head_branch],
        cwd=cwd, capture_output=True,
    )
    if check.returncode == 0:
        # Branch exists, update it
        subprocess.run(
            ["git", "checkout", head_branch],
            cwd=cwd, capture_output=True,
        )
        subprocess.run(
            ["git", "pull", "--ff-only", "origin", head_branch],
            cwd=cwd, capture_output=True,
        )
    else:
        # Create tracking branch
        subprocess.run(
            ["git", "checkout", "-b", head_branch, f"origin/{head_branch}"],
            cwd=cwd, capture_output=True,
        )

    return head_branch, base_branch, diff_ref


def pr_output_dir_name(pr_ref: str) -> str:
    """
    Generate a clean output directory name from a PR reference.

    Examples:
        "https://github.com/owner/repo/pull/123" -> "owner-repo-123"
        "owner/repo#123" -> "owner-repo-123"
        "123" -> "pr-123"
    """
    repo, pr_number = parse_pr_url(pr_ref)
    if repo:
        return f"{repo.replace('/', '-')}-{pr_number}"
    return f"pr-{pr_number}"


def post_pr_comment(pr_ref: str, body: str) -> None:
    """
    Post a comment on a GitHub PR using gh CLI.

    Args:
        pr_ref: PR URL, owner/repo#N, or number
        body: Comment body (markdown)

    Raises:
        RuntimeError: If gh command fails
    """
    repo, pr_number = parse_pr_url(pr_ref)

    cmd = ["gh", "pr", "comment", str(pr_number), "--body", body]
    if repo:
        cmd.extend(["--repo", repo])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr comment failed: {result.stderr.strip()}")


def parse_issue_ref(issue_ref: str) -> tuple[str | None, int]:
    """
    Parse a GitHub issue reference into (repo, number).

    Accepts:
        - Full URL: https://github.com/owner/repo/issues/123
        - Shorthand: owner/repo#123
        - Number only: 123 (repo=None, uses current repo)

    Returns:
        (repo, issue_number) where repo is "owner/repo" or None
    """
    # Full GitHub URL
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/issues/(\d+)", issue_ref)
    if m:
        return m.group(1), int(m.group(2))

    # Shorthand: owner/repo#123
    m = re.match(r"([^/]+/[^#]+)#(\d+)", issue_ref)
    if m:
        return m.group(1), int(m.group(2))

    # Plain number
    m = re.match(r"(\d+)$", issue_ref)
    if m:
        return None, int(m.group(1))

    raise ValueError(f"Cannot parse issue reference: {issue_ref}")


def get_github_issue(issue_ref: str) -> str:
    """
    Fetch a GitHub issue's title and body using gh CLI.

    Args:
        issue_ref: Issue URL, owner/repo#N, or number

    Returns:
        Formatted issue content (title + body)

    Raises:
        RuntimeError: If gh command fails
    """
    import json

    repo, issue_number = parse_issue_ref(issue_ref)

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


def extract_changed_files(diff_content: str) -> list[str]:
    """
    Extract file paths from a git diff.

    Args:
        diff_content: Git diff output

    Returns:
        List of file paths that were changed
    """
    files = []
    # Match "+++ b/path/to/file" lines
    for line in diff_content.split("\n"):
        if line.startswith("+++ b/"):
            path = line[6:]  # Remove "+++ b/" prefix
            if path != "/dev/null":  # Exclude deleted files
                files.append(path)
    return files


def extract_modified_line_ranges(diff_content: str) -> dict[str, list[tuple[int, int]]]:
    """
    Parse a unified diff to extract modified line ranges in the new version of each file.

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
        # Track current file from "+++ b/..." lines
        if line.startswith("+++ b/"):
            path = line[6:]
            current_file = path if path != "/dev/null" else None
            continue

        # Parse hunk headers: @@ -old_start,old_count +new_start,new_count @@
        if current_file and line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                if count > 0:
                    end = start + count - 1
                    result.setdefault(current_file, []).append((start, end))

    return result
