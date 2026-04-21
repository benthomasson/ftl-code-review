"""Keyword-based belief filtering for code review prompts."""

from __future__ import annotations

import re
from collections import Counter

STOP_WORDS = frozenset({
    "src", "tests", "test", "unit", "integration", "e2e", "functional",
    "lib", "pkg", "packages", "scripts", "docs", "bin",
    "py", "js", "ts", "md", "json", "yaml", "yml", "toml",
    "init", "__init__", "utils", "helpers", "common", "types",
    "main", "index", "app", "core", "base", "models", "api",
    "conftest", "fixtures", "setup", "config",
})

ENTRY_HEADER = re.compile(r"^### (.+)$", re.MULTILINE)
STATUS_RE = re.compile(r"\[(\w+)\]")
SOURCE_LINE = re.compile(r"^- Source.*$", re.MULTILINE)


def parse_belief_entries(beliefs_content: str) -> tuple[str, list[dict]]:
    """Parse beliefs markdown into header + individual entries.

    Returns (header_text, entries) where each entry has keys:
      id, status, text (full entry including header), claim (first paragraph)
    """
    splits = ENTRY_HEADER.split(beliefs_content)
    header = splits[0]
    entries = []
    for i in range(1, len(splits), 2):
        raw_id_line = splits[i]
        body = splits[i + 1] if i + 1 < len(splits) else ""
        status_m = STATUS_RE.search(raw_id_line)
        status = status_m.group(1) if status_m else "UNKNOWN"
        entry_text = f"### {raw_id_line}\n{body}"
        clean_id = re.sub(r"\s*\[.*", "", raw_id_line).strip().strip("`")
        claim = SOURCE_LINE.sub("", body).strip().split("\n")[0] if body.strip() else ""
        entries.append({
            "id": clean_id,
            "status": status,
            "text": entry_text,
            "claim": claim,
        })
    return header, entries


def extract_keywords_from_diff(diff_content: str) -> set[str]:
    """Extract meaningful keywords from a diff's changed file paths.

    Uses only the last two specific path components (directory + file stem)
    to avoid matching on generic project-level package names.
    """
    keywords: set[str] = set()
    for line in diff_content.split("\n"):
        if not line.startswith("+++ b/"):
            continue
        path = line[6:]
        if path == "/dev/null":
            continue
        parts = path.replace("\\", "/").split("/")
        specific = [p for p in parts if p.rsplit(".", 1)[0].lower() not in STOP_WORDS]
        for part in specific[-3:]:
            stem = part.rsplit(".", 1)[0]
            stem = stem.removeprefix("test_")
            if len(stem) >= 3:
                keywords.add(stem.lower())
    return keywords


def _match_score(entry: dict, keywords: set[str]) -> int:
    """Score how well an entry matches the keywords. 0 = no match."""
    id_words = set(entry["id"].replace("-", " ").replace("_", " ").lower().split())
    claim_lower = entry["claim"].lower()
    score = 0
    for kw in keywords:
        if kw in id_words:
            score += 2
        elif kw in claim_lower:
            score += 1
    return score


def filter_beliefs(
    beliefs_content: str,
    diff_content: str,
    *,
    max_size: int = 200_000,
    include_stale: bool = False,
) -> tuple[str, int, int]:
    """Filter beliefs to those relevant to the changed files.

    Matches keywords against belief IDs and claim text only (not Source metadata),
    then drops keywords that are too generic (match >25% of active beliefs).

    Returns (filtered_content, kept_count, total_count).
    """
    header, entries = parse_belief_entries(beliefs_content)
    keywords = extract_keywords_from_diff(diff_content)
    total = len(entries)

    if not keywords:
        if len(beliefs_content) <= max_size:
            return beliefs_content, total, total
        return _truncate(beliefs_content, max_size), total, total

    active = [e for e in entries if include_stale or e["status"] not in ("OUT", "STALE")]

    hit_counts: Counter[str] = Counter()
    for entry in active:
        id_text = entry["id"].replace("-", " ").replace("_", " ").lower()
        claim_lower = entry["claim"].lower()
        for kw in keywords:
            if kw in id_text or kw in claim_lower:
                hit_counts[kw] += 1

    threshold = max(len(active) * 0.25, 10)
    effective_keywords = {kw for kw in keywords if hit_counts.get(kw, 0) <= threshold}

    if not effective_keywords:
        effective_keywords = keywords

    scored = []
    for entry in active:
        s = _match_score(entry, effective_keywords)
        if s > 0:
            scored.append((s, entry))

    scored.sort(key=lambda x: -x[0])
    kept_entries = [entry["text"] for _, entry in scored]

    result = header.rstrip() + "\n\n" + "".join(kept_entries)

    if len(result) > max_size:
        result = _truncate(result, max_size)

    return result, len(kept_entries), total


def _truncate(text: str, max_size: int) -> str:
    cut = text[:max_size]
    last_header = cut.rfind("\n### ")
    if last_header > 0:
        cut = cut[:last_header]
    return cut + f"\n\n<!-- truncated at {max_size // 1024}KB -->\n"
