"""Tests for belief keyword filtering."""

from ftl_code_review.beliefs import (
    extract_keywords_from_diff,
    filter_beliefs,
    parse_belief_entries,
)

SAMPLE_BELIEFS = """\
# Belief Registry

## Claims

### planner-builds-recursive-tree [IN] OBSERVATION
Planner builds a recursive tree structure.
- Source: entries/2026/04/07/planner.md

### citation-merging-is-deterministic [IN] DERIVED
Citation merging produces deterministic output.
- Depends on: citation-dedup

### stale-belief-example [STALE] OBSERVATION
This belief is stale.
- Source: old.md

### out-belief-example [OUT] DERIVED
This belief was retracted.
- Depends on: nothing

### evaluator-returns-zero [IN] OBSERVATION
Evaluator returns zero on error.
- Source: entries/2026/04/07/evaluator.md
"""

SAMPLE_DIFF = """\
diff --git a/src/workflow/planner.py b/src/workflow/planner.py
--- a/src/workflow/planner.py
+++ b/src/workflow/planner.py
@@ -10,3 +10,5 @@ class Planner:
+    def build_tree(self):
+        pass
"""


def test_parse_belief_entries():
    header, entries = parse_belief_entries(SAMPLE_BELIEFS)
    assert "Belief Registry" in header
    assert len(entries) == 5
    assert entries[0]["id"] == "planner-builds-recursive-tree"
    assert entries[0]["status"] == "IN"
    assert entries[2]["status"] == "STALE"
    assert entries[3]["status"] == "OUT"


def test_extract_keywords_from_diff():
    keywords = extract_keywords_from_diff(SAMPLE_DIFF)
    assert "planner" in keywords
    assert "workflow" in keywords
    assert "src" not in keywords
    assert "py" not in keywords


def test_extract_keywords_strips_test_prefix():
    diff = """\
diff --git a/tests/test_planner.py b/tests/test_planner.py
--- a/tests/test_planner.py
+++ b/tests/test_planner.py
@@ -1 +1,2 @@
+pass
"""
    keywords = extract_keywords_from_diff(diff)
    assert "planner" in keywords


def test_filter_beliefs_keyword_match():
    filtered, kept, total = filter_beliefs(SAMPLE_BELIEFS, SAMPLE_DIFF)
    assert total == 5
    assert kept >= 1
    assert "planner-builds-recursive-tree" in filtered
    assert "citation-merging-is-deterministic" not in filtered


def test_filter_beliefs_excludes_stale_and_out():
    filtered, kept, total = filter_beliefs(SAMPLE_BELIEFS, SAMPLE_DIFF)
    assert "stale-belief-example" not in filtered
    assert "out-belief-example" not in filtered


def test_filter_beliefs_include_stale():
    stale_diff = """\
diff --git a/src/old.py b/src/old.py
--- a/src/old.py
+++ b/src/old.py
@@ -1 +1,2 @@
+pass
"""
    beliefs = """\
# Registry

### stale-old-thing [STALE] OBSERVATION
Old thing is stale.
- Source: old.md
"""
    filtered, kept, _ = filter_beliefs(beliefs, stale_diff, include_stale=True)
    assert kept == 1
    assert "stale-old-thing" in filtered


def test_filter_beliefs_no_keywords_small_file():
    no_file_diff = """\
diff --git a/a b/a
--- a/a
+++ b/a
@@ -1 +1,2 @@
+x
"""
    filtered, kept, total = filter_beliefs(SAMPLE_BELIEFS, no_file_diff)
    assert kept == total


def test_filter_beliefs_truncation():
    filtered, kept, total = filter_beliefs(SAMPLE_BELIEFS, SAMPLE_DIFF, max_size=50)
    assert len(filtered) <= 120
    assert "truncated" in filtered


def test_filter_beliefs_generic_keyword_dropped():
    beliefs = "# Registry\n\n## Claims\n\n"
    for i in range(100):
        beliefs += f"### belief-{i}-foo [IN] OBSERVATION\nFoo does thing {i}.\n- Source: x.md\n\n"
    beliefs += "### special-bar [IN] OBSERVATION\nBar is special.\n- Source: y.md\n\n"

    diff = """\
diff --git a/src/foo.py b/src/foo.py
+++ b/src/foo.py
@@ -1 +1,2 @@
+pass
diff --git a/src/bar.py b/src/bar.py
+++ b/src/bar.py
@@ -1 +1,2 @@
+pass
"""
    filtered, kept, total = filter_beliefs(beliefs, diff)
    assert kept < total
    assert "special-bar" in filtered
