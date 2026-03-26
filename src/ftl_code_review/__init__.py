"""Multi-model code review - AI-first code review using multiple models."""

from dataclasses import dataclass, field
from enum import Enum

__version__ = "0.8.0"


class Verdict(Enum):
    """Overall verdict for a change or review."""

    PASS = "PASS"
    CONCERN = "CONCERN"
    BLOCK = "BLOCK"


class Correctness(Enum):
    """Assessment of code correctness."""

    VALID = "VALID"
    QUESTIONABLE = "QUESTIONABLE"
    BROKEN = "BROKEN"


class SpecCompliance(Enum):
    """Assessment of spec compliance."""

    MEETS = "MEETS"
    PARTIAL = "PARTIAL"
    VIOLATES = "VIOLATES"
    NA = "N/A"


class TestCoverage(Enum):
    """Assessment of test coverage."""

    COVERED = "COVERED"
    PARTIAL = "PARTIAL"
    UNTESTED = "UNTESTED"


class Integration(Enum):
    """Assessment of integration completeness."""

    WIRED = "WIRED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"


@dataclass
class SelfReview:
    """Model's self-assessment of its review quality."""

    limitations: str = ""


@dataclass
class ChangeVerdict:
    """Review verdict for a single change (file or function)."""

    change_id: str  # e.g., "src/workflow/router.py" or "src/workflow/router.py:42"
    verdict: Verdict
    correctness: Correctness | None = None
    spec_compliance: SpecCompliance | None = None
    test_coverage: TestCoverage | None = None
    integration: Integration | None = None
    reasoning: str = ""


@dataclass
class ModelReview:
    """Complete review from one model."""

    model: str
    gate: Verdict  # Overall PASS/BLOCK for this model
    changes: list[ChangeVerdict] = field(default_factory=list)
    raw_response: str = ""
    self_review: SelfReview | None = None
    feature_requests: list[str] = field(default_factory=list)
    observations: dict = field(default_factory=dict)  # Results from observation tools


@dataclass
class AggregateReview:
    """Combined review across all models."""

    diff_ref: str  # Branch or commit reviewed
    spec_file: str | None = None
    models: list[str] = field(default_factory=list)
    reviews: list[ModelReview] = field(default_factory=list)
    gate: Verdict = Verdict.PASS  # BLOCK if any model blocks
    disagreements: list[dict] = field(default_factory=list)


__all__ = [
    "Verdict",
    "Correctness",
    "SpecCompliance",
    "TestCoverage",
    "Integration",
    "SelfReview",
    "ChangeVerdict",
    "ModelReview",
    "AggregateReview",
]
