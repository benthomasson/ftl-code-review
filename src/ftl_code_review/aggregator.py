"""Aggregation logic for combining reviews from multiple models."""

from . import AggregateReview, ModelReview, Verdict


def find_disagreements(reviews: list[ModelReview]) -> list[dict]:
    """
    Find changes where models disagree on verdict.

    A disagreement is when one model says PASS and another says BLOCK,
    or any combination where verdicts differ significantly.

    Args:
        reviews: List of ModelReview from different models

    Returns:
        List of disagreement dicts with change_id, verdicts by model, and severity
    """
    if len(reviews) < 2:
        return []

    # Build map of change_id -> {model: verdict}
    change_verdicts: dict[str, dict[str, Verdict]] = {}

    for review in reviews:
        for change in review.changes:
            if change.change_id not in change_verdicts:
                change_verdicts[change.change_id] = {}
            change_verdicts[change.change_id][review.model] = change.verdict

    disagreements = []

    for change_id, verdicts in change_verdicts.items():
        unique_verdicts = set(verdicts.values())

        # Check for disagreement
        if len(unique_verdicts) > 1:
            # Determine severity
            if Verdict.PASS in unique_verdicts and Verdict.BLOCK in unique_verdicts:
                severity = "HIGH"  # Direct conflict
            elif Verdict.BLOCK in unique_verdicts:
                severity = "MEDIUM"  # One blocks, others don't
            else:
                severity = "LOW"  # PASS vs CONCERN

            disagreements.append(
                {
                    "change_id": change_id,
                    "verdicts": {model: v.value for model, v in verdicts.items()},
                    "severity": severity,
                }
            )

    # Sort by severity
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    disagreements.sort(key=lambda d: severity_order[d["severity"]])

    return disagreements


def compute_gate(reviews: list[ModelReview]) -> Verdict:
    """
    Compute overall gate from all reviews.

    Gate logic:
    - BLOCK if any model returns BLOCK
    - CONCERN if any model returns CONCERN (and none BLOCK)
    - PASS only if all models return PASS

    Args:
        reviews: List of ModelReview

    Returns:
        Overall Verdict for the gate
    """
    if not reviews:
        return Verdict.CONCERN  # Conservative default

    gates = [r.gate for r in reviews]

    if Verdict.BLOCK in gates:
        return Verdict.BLOCK
    elif Verdict.CONCERN in gates:
        return Verdict.CONCERN
    else:
        return Verdict.PASS


def aggregate_reviews(
    diff_ref: str,
    reviews: list[ModelReview],
    spec_file: str | None = None,
) -> AggregateReview:
    """
    Combine reviews from multiple models into aggregate result.

    Args:
        diff_ref: Branch or commit being reviewed
        reviews: List of ModelReview from different models
        spec_file: Optional spec file path

    Returns:
        AggregateReview with combined results
    """
    return AggregateReview(
        diff_ref=diff_ref,
        spec_file=spec_file,
        models=[r.model for r in reviews],
        reviews=reviews,
        gate=compute_gate(reviews),
        disagreements=find_disagreements(reviews),
    )
