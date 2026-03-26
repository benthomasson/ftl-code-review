"""Report generation for code review results."""

from . import AggregateReview, ModelReview, Verdict


def format_verdict_badge(verdict: Verdict) -> str:
    """Get text badge for verdict."""
    if verdict == Verdict.PASS:
        return "[PASS]"
    elif verdict == Verdict.CONCERN:
        return "[CONCERN]"
    else:
        return "[BLOCK]"


def format_model_review(review: ModelReview) -> str:
    """Format a single model's review."""
    lines = [
        f"## {review.model} {format_verdict_badge(review.gate)}",
        "",
    ]

    for change in review.changes:
        lines.append(f"### {change.change_id}")
        lines.append(f"**Verdict:** {change.verdict.value}")

        if change.correctness:
            lines.append(f"**Correctness:** {change.correctness.value}")
        if change.spec_compliance:
            lines.append(f"**Spec Compliance:** {change.spec_compliance.value}")
        if change.test_coverage:
            lines.append(f"**Test Coverage:** {change.test_coverage.value}")
        if change.integration:
            lines.append(f"**Integration:** {change.integration.value}")

        if change.reasoning:
            lines.append(f"\n{change.reasoning}")

        lines.append("")

    # Add self-review section if present
    if review.self_review:
        lines.append("### Self-Review")
        if review.self_review.limitations:
            lines.append(f"**Limitations:** {review.self_review.limitations}")
        lines.append("")

    # Add feature requests section if present
    if review.feature_requests:
        lines.append("### Feature Requests")
        for req in review.feature_requests:
            lines.append(f"- {req}")
        lines.append("")

    return "\n".join(lines)


def format_disagreements(disagreements: list[dict]) -> str:
    """Format disagreement section."""
    if not disagreements:
        return ""

    lines = [
        "## Disagreements",
        "",
        "The following changes have different verdicts across models:",
        "",
    ]

    for d in disagreements:
        severity = d["severity"]
        change_id = d["change_id"]
        verdicts = d["verdicts"]

        verdict_str = ", ".join(f"{m}: {v}" for m, v in verdicts.items())
        lines.append(f"- **{change_id}** [{severity}]: {verdict_str}")

    lines.append("")
    return "\n".join(lines)


def format_aggregate_review(review: AggregateReview) -> str:
    """
    Format aggregate review for display.

    Args:
        review: AggregateReview to format

    Returns:
        Formatted markdown string
    """
    lines = [
        "# Code Review Report",
        "",
        f"**Branch:** {review.diff_ref}",
        f"**Models:** {', '.join(review.models)}",
        f"**Gate:** {format_verdict_badge(review.gate)} {review.gate.value}",
    ]

    if review.spec_file:
        lines.append(f"**Spec:** {review.spec_file}")

    lines.append("")

    # Disagreements first (most important)
    if review.disagreements:
        lines.append(format_disagreements(review.disagreements))

    # Then each model's review
    for model_review in review.reviews:
        lines.append(format_model_review(model_review))

    return "\n".join(lines)


def format_summary(review: AggregateReview) -> str:
    """
    Format a brief summary for terminal output.

    Args:
        review: AggregateReview to summarize

    Returns:
        Brief summary string
    """
    lines = [
        f"Gate: {review.gate.value}",
        f"Models: {', '.join(review.models)}",
    ]

    if review.disagreements:
        high = sum(1 for d in review.disagreements if d["severity"] == "HIGH")
        med = sum(1 for d in review.disagreements if d["severity"] == "MEDIUM")
        low = sum(1 for d in review.disagreements if d["severity"] == "LOW")
        lines.append(f"Disagreements: {high} high, {med} medium, {low} low")

    # Count verdicts across all models
    blocks = 0
    concerns = 0
    passes = 0

    for mr in review.reviews:
        for change in mr.changes:
            if change.verdict == Verdict.BLOCK:
                blocks += 1
            elif change.verdict == Verdict.CONCERN:
                concerns += 1
            else:
                passes += 1

    lines.append(f"Verdicts: {passes} pass, {concerns} concern, {blocks} block")

    return "\n".join(lines)
