"""Fixer module for auto-fixing BLOCK verdicts."""

import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .reviewer import run_model


FIX_PROMPT = """The following BLOCK was identified in a code review:

File: {file_path}
Verdict: BLOCK
Concern: {concern}

Current file content:
```
{file_content}
```

Generate a unified diff that fixes this concern.
Output ONLY the diff, no explanation. Start with:
--- a/{file_path}
+++ b/{file_path}
"""


async def generate_fix(model: str, file_path: str, concern: str, repo_path: str) -> str:
    """Ask model to generate a unified diff to fix the concern."""
    full_path = Path(repo_path) / file_path
    file_content = full_path.read_text()

    prompt = FIX_PROMPT.format(
        file_path=file_path,
        concern=concern,
        file_content=file_content,
    )

    response = await run_model(model, prompt)

    # Extract diff from response (model might wrap in markdown)
    lines = response.strip().split("\n")
    diff_lines = []
    in_diff = False
    for line in lines:
        if line.startswith("--- a/") or line.startswith("diff --git"):
            in_diff = True
        if in_diff:
            # Stop at markdown fence or explanation
            if line.startswith("```") and diff_lines:
                break
            diff_lines.append(line)

    return "\n".join(diff_lines)


def apply_patch(patch: str, repo_path: str, dry_run: bool = False) -> tuple[bool, str]:
    """Apply a unified diff patch.

    Returns (success, message).
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
        f.write(patch)
        f.write("\n")  # Ensure trailing newline
        patch_file = f.name

    try:
        cmd = ["git", "apply"]
        if dry_run:
            cmd.append("--check")
        cmd.append(patch_file)

        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            return True, "Patch applied successfully" if not dry_run else "Patch is valid"
        else:
            return False, result.stderr
    finally:
        Path(patch_file).unlink()


async def fix_block(
    file_path: str,
    concern: str,
    model: str,
    repo_path: str,
) -> dict[str, Any]:
    """Attempt to fix a single BLOCK verdict.

    Returns {"file": str, "status": "fixed"|"failed", "patch": str|None, "error": str|None}
    """
    # Generate fix
    try:
        patch = await generate_fix(model, file_path, concern, repo_path)
    except Exception as e:
        return {
            "file": file_path,
            "status": "failed",
            "patch": None,
            "error": f"Failed to generate fix: {e}",
        }

    if not patch.strip():
        return {
            "file": file_path,
            "status": "failed",
            "patch": None,
            "error": "Model returned empty patch",
        }

    # Validate patch
    valid, msg = apply_patch(patch, repo_path, dry_run=True)
    if not valid:
        return {
            "file": file_path,
            "status": "failed",
            "patch": patch,
            "error": f"Invalid patch: {msg}",
        }

    # Apply patch
    success, msg = apply_patch(patch, repo_path, dry_run=False)
    return {
        "file": file_path,
        "status": "fixed" if success else "failed",
        "patch": patch,
        "error": None if success else msg,
    }


async def fix_blocks(
    blocks: list[dict],  # [{"file": str, "concern": str}, ...]
    model: str,
    repo_path: str,
) -> list[dict]:
    """Attempt to fix all BLOCK verdicts.

    Returns list of {"file": str, "status": "fixed"|"failed", "patch": str|None, "error": str|None}
    """
    tasks = [
        fix_block(block["file"], block["concern"], model, repo_path)
        for block in blocks
    ]
    return await asyncio.gather(*tasks)
