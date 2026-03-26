"""Tests for aggregator module - disagreement detection and gate logic."""

from ftl_code_review import (
    ChangeVerdict,
    ModelReview,
    Verdict,
)
from ftl_code_review.aggregator import (
    aggregate_reviews,
    compute_gate,
    find_disagreements,
)


def make_review(model: str, changes: list[tuple[str, Verdict]]) -> ModelReview:
    """Helper to create ModelReview from (change_id, verdict) tuples."""
    change_verdicts = [ChangeVerdict(change_id=cid, verdict=v) for cid, v in changes]
    # Compute gate from changes
    gate = Verdict.PASS
    for cv in change_verdicts:
        if cv.verdict == Verdict.BLOCK:
            gate = Verdict.BLOCK
            break
        elif cv.verdict == Verdict.CONCERN:
            gate = Verdict.CONCERN
    return ModelReview(model=model, gate=gate, changes=change_verdicts)


class TestComputeGate:
    def test_empty_reviews_returns_concern(self):
        assert compute_gate([]) == Verdict.CONCERN

    def test_all_pass_returns_pass(self):
        reviews = [
            ModelReview(model="a", gate=Verdict.PASS, changes=[]),
            ModelReview(model="b", gate=Verdict.PASS, changes=[]),
        ]
        assert compute_gate(reviews) == Verdict.PASS

    def test_any_concern_returns_concern(self):
        reviews = [
            ModelReview(model="a", gate=Verdict.PASS, changes=[]),
            ModelReview(model="b", gate=Verdict.CONCERN, changes=[]),
        ]
        assert compute_gate(reviews) == Verdict.CONCERN

    def test_any_block_returns_block(self):
        reviews = [
            ModelReview(model="a", gate=Verdict.PASS, changes=[]),
            ModelReview(model="b", gate=Verdict.CONCERN, changes=[]),
            ModelReview(model="c", gate=Verdict.BLOCK, changes=[]),
        ]
        assert compute_gate(reviews) == Verdict.BLOCK

    def test_block_overrides_concern(self):
        reviews = [
            ModelReview(model="a", gate=Verdict.CONCERN, changes=[]),
            ModelReview(model="b", gate=Verdict.BLOCK, changes=[]),
        ]
        assert compute_gate(reviews) == Verdict.BLOCK


class TestFindDisagreements:
    def test_no_disagreements_when_single_model(self):
        reviews = [make_review("claude", [("file.py", Verdict.PASS)])]
        assert find_disagreements(reviews) == []

    def test_no_disagreements_when_models_agree(self):
        reviews = [
            make_review("claude", [("file.py", Verdict.PASS)]),
            make_review("gemini", [("file.py", Verdict.PASS)]),
        ]
        assert find_disagreements(reviews) == []

    def test_high_severity_pass_vs_block(self):
        reviews = [
            make_review("claude", [("file.py", Verdict.PASS)]),
            make_review("gemini", [("file.py", Verdict.BLOCK)]),
        ]
        disagreements = find_disagreements(reviews)

        assert len(disagreements) == 1
        assert disagreements[0]["change_id"] == "file.py"
        assert disagreements[0]["severity"] == "HIGH"
        assert disagreements[0]["verdicts"]["claude"] == "PASS"
        assert disagreements[0]["verdicts"]["gemini"] == "BLOCK"

    def test_medium_severity_concern_vs_block(self):
        reviews = [
            make_review("claude", [("file.py", Verdict.CONCERN)]),
            make_review("gemini", [("file.py", Verdict.BLOCK)]),
        ]
        disagreements = find_disagreements(reviews)

        assert len(disagreements) == 1
        assert disagreements[0]["severity"] == "MEDIUM"

    def test_low_severity_pass_vs_concern(self):
        reviews = [
            make_review("claude", [("file.py", Verdict.PASS)]),
            make_review("gemini", [("file.py", Verdict.CONCERN)]),
        ]
        disagreements = find_disagreements(reviews)

        assert len(disagreements) == 1
        assert disagreements[0]["severity"] == "LOW"

    def test_multiple_disagreements_sorted_by_severity(self):
        reviews = [
            make_review(
                "claude",
                [
                    ("high.py", Verdict.PASS),
                    ("low.py", Verdict.PASS),
                    ("medium.py", Verdict.CONCERN),
                ],
            ),
            make_review(
                "gemini",
                [
                    ("high.py", Verdict.BLOCK),
                    ("low.py", Verdict.CONCERN),
                    ("medium.py", Verdict.BLOCK),
                ],
            ),
        ]
        disagreements = find_disagreements(reviews)

        assert len(disagreements) == 3
        # Should be sorted: HIGH first, then MEDIUM, then LOW
        assert disagreements[0]["severity"] == "HIGH"
        assert disagreements[0]["change_id"] == "high.py"
        assert disagreements[1]["severity"] == "MEDIUM"
        assert disagreements[2]["severity"] == "LOW"

    def test_partial_overlap_only_reports_shared_changes(self):
        reviews = [
            make_review("claude", [("shared.py", Verdict.PASS), ("only_claude.py", Verdict.PASS)]),
            make_review("gemini", [("shared.py", Verdict.BLOCK), ("only_gemini.py", Verdict.PASS)]),
        ]
        disagreements = find_disagreements(reviews)

        # Only shared.py should be a disagreement
        assert len(disagreements) == 1
        assert disagreements[0]["change_id"] == "shared.py"


class TestAggregateReviews:
    def test_aggregates_basic_info(self):
        reviews = [
            make_review("claude", [("file.py", Verdict.PASS)]),
            make_review("gemini", [("file.py", Verdict.CONCERN)]),
        ]
        result = aggregate_reviews("feature-branch", reviews, spec_file="spec.md")

        assert result.diff_ref == "feature-branch"
        assert result.spec_file == "spec.md"
        assert result.models == ["claude", "gemini"]
        assert len(result.reviews) == 2

    def test_computes_gate(self):
        reviews = [
            make_review("claude", [("file.py", Verdict.PASS)]),
            make_review("gemini", [("file.py", Verdict.BLOCK)]),
        ]
        result = aggregate_reviews("main", reviews)

        assert result.gate == Verdict.BLOCK

    def test_finds_disagreements(self):
        reviews = [
            make_review("claude", [("file.py", Verdict.PASS)]),
            make_review("gemini", [("file.py", Verdict.BLOCK)]),
        ]
        result = aggregate_reviews("main", reviews)

        assert len(result.disagreements) == 1
        assert result.disagreements[0]["severity"] == "HIGH"
