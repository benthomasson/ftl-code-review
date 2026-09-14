"""Tests for reviewer module - especially response parsing."""

import asyncio
import threading

from ftl_code_review import (
    Correctness,
    Integration,
    SpecCompliance,
    TestCoverage,
    Verdict,
)
from ftl_code_review.reviewer import (
    _run_openai,
    check_model_available,
    parse_correctness,
    parse_integration,
    parse_review_response,
    parse_spec_compliance,
    parse_test_coverage,
    parse_verdict,
    run_model,
)


class TestOpenAIAvailability:
    def test_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert not check_model_available("openai")

    def test_uses_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        assert check_model_available("openai")
        assert check_model_available("openai:gpt-5.6-luna")

    def test_model_spec_is_sent_to_openai(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return b'{"choices":[{"message":{"content":"ok"}}]}'

        def fake_urlopen(request, timeout):
            captured["body"] = request.data
            return Response()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        assert asyncio.run(run_model("openai:gpt-5.6-luna", "prompt", timeout=1)) == "ok"
        assert b'"model": "gpt-5.6-luna"' in captured["body"]

    def test_timeout_cancellation_does_not_wait_for_request(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        request_started = threading.Event()
        release_request = threading.Event()

        def blocking_request(*args, **kwargs):
            request_started.set()
            release_request.wait()
            return object()

        monkeypatch.setattr("urllib.request.urlopen", blocking_request)

        async def run():
            task = asyncio.create_task(_run_openai("prompt", timeout=1))
            await asyncio.to_thread(request_started.wait, 1)
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=0.1)
            except asyncio.CancelledError:
                pass
            finally:
                release_request.set()

        asyncio.run(run())


class TestParseVerdict:
    def test_pass(self):
        assert parse_verdict("PASS") == Verdict.PASS
        assert parse_verdict("pass") == Verdict.PASS
        assert parse_verdict("  PASS  ") == Verdict.PASS

    def test_concern(self):
        assert parse_verdict("CONCERN") == Verdict.CONCERN
        assert parse_verdict("concern") == Verdict.CONCERN

    def test_block(self):
        assert parse_verdict("BLOCK") == Verdict.BLOCK
        assert parse_verdict("block") == Verdict.BLOCK

    def test_unknown_defaults_to_concern(self):
        assert parse_verdict("UNKNOWN") == Verdict.CONCERN
        assert parse_verdict("") == Verdict.CONCERN


class TestParseCorrectness:
    def test_valid(self):
        assert parse_correctness("VALID") == Correctness.VALID

    def test_questionable(self):
        assert parse_correctness("QUESTIONABLE") == Correctness.QUESTIONABLE

    def test_broken(self):
        assert parse_correctness("BROKEN") == Correctness.BROKEN

    def test_unknown_returns_none(self):
        assert parse_correctness("UNKNOWN") is None


class TestParseSpecCompliance:
    def test_meets(self):
        assert parse_spec_compliance("MEETS") == SpecCompliance.MEETS

    def test_partial(self):
        assert parse_spec_compliance("PARTIAL") == SpecCompliance.PARTIAL

    def test_violates(self):
        assert parse_spec_compliance("VIOLATES") == SpecCompliance.VIOLATES

    def test_na(self):
        assert parse_spec_compliance("N/A") == SpecCompliance.NA
        assert parse_spec_compliance("NA") == SpecCompliance.NA


class TestParseTestCoverage:
    def test_covered(self):
        assert parse_test_coverage("COVERED") == TestCoverage.COVERED

    def test_partial(self):
        assert parse_test_coverage("PARTIAL") == TestCoverage.PARTIAL

    def test_untested(self):
        assert parse_test_coverage("UNTESTED") == TestCoverage.UNTESTED


class TestParseIntegration:
    def test_wired(self):
        assert parse_integration("WIRED") == Integration.WIRED

    def test_partial(self):
        assert parse_integration("PARTIAL") == Integration.PARTIAL

    def test_missing(self):
        assert parse_integration("MISSING") == Integration.MISSING


# Sample model outputs for parsing tests
SAMPLE_CLAUDE_RESPONSE = """### src/main.py
VERDICT: PASS
CORRECTNESS: VALID
SPEC_COMPLIANCE: N/A
TEST_COVERAGE: COVERED
INTEGRATION: WIRED
REASONING: Clean implementation with proper error handling.
---

### src/utils.py
VERDICT: CONCERN
CORRECTNESS: QUESTIONABLE
SPEC_COMPLIANCE: N/A
TEST_COVERAGE: UNTESTED
INTEGRATION: WIRED
REASONING: Missing edge case handling for empty inputs.
---

### src/broken.py
VERDICT: BLOCK
CORRECTNESS: BROKEN
SPEC_COMPLIANCE: VIOLATES
TEST_COVERAGE: UNTESTED
INTEGRATION: MISSING
REASONING: Critical security vulnerability in line 42.
---
"""

SAMPLE_GEMINI_RESPONSE = """### pyproject.toml
VERDICT: PASS
CORRECTNESS: VALID
SPEC_COMPLIANCE: N/A
TEST_COVERAGE: UNTESTED
INTEGRATION: WIRED
REASONING: Standard configuration, no issues.
---

### src/api.py
VERDICT: CONCERN
CORRECTNESS: VALID
SPEC_COMPLIANCE: PARTIAL
TEST_COVERAGE: PARTIAL
INTEGRATION: WIRED
REASONING: Missing authentication on one endpoint.
---
"""

# Edge cases that models sometimes produce
SAMPLE_WITH_MARKDOWN_BOLD = """### src/file.py
VERDICT: PASS
CORRECTNESS: VALID
SPEC_COMPLIANCE: N/A
TEST_COVERAGE: COVERED
INTEGRATION: WIRED
REASONING: All good here.
---
"""

SAMPLE_WITH_EXTRA_WHITESPACE = """### src/file.py
VERDICT:   PASS
CORRECTNESS:  VALID
SPEC_COMPLIANCE: N/A
TEST_COVERAGE: COVERED
INTEGRATION: WIRED
REASONING: Looks fine.
---
"""

SAMPLE_MULTILINE_REASONING = """### src/complex.py
VERDICT: CONCERN
CORRECTNESS: QUESTIONABLE
SPEC_COMPLIANCE: N/A
TEST_COVERAGE: UNTESTED
INTEGRATION: WIRED
REASONING: There are several issues here:
1. Missing null check on line 15
2. Potential race condition in the async handler
3. No retry logic for network failures

These should be addressed before merging.
---
"""


class TestParseReviewResponse:
    def test_parse_claude_response(self):
        result = parse_review_response("claude", SAMPLE_CLAUDE_RESPONSE)

        assert result.model == "claude"
        assert result.gate == Verdict.BLOCK  # Has a BLOCK verdict
        assert len(result.changes) == 3

        # Check first change
        assert result.changes[0].change_id == "src/main.py"
        assert result.changes[0].verdict == Verdict.PASS
        assert result.changes[0].correctness == Correctness.VALID
        assert result.changes[0].test_coverage == TestCoverage.COVERED

        # Check second change
        assert result.changes[1].change_id == "src/utils.py"
        assert result.changes[1].verdict == Verdict.CONCERN
        assert result.changes[1].correctness == Correctness.QUESTIONABLE

        # Check third change (BLOCK)
        assert result.changes[2].change_id == "src/broken.py"
        assert result.changes[2].verdict == Verdict.BLOCK
        assert result.changes[2].correctness == Correctness.BROKEN
        assert result.changes[2].spec_compliance == SpecCompliance.VIOLATES

    def test_parse_gemini_response(self):
        result = parse_review_response("gemini", SAMPLE_GEMINI_RESPONSE)

        assert result.model == "gemini"
        assert result.gate == Verdict.CONCERN  # Highest is CONCERN
        assert len(result.changes) == 2

    def test_gate_pass_when_all_pass(self):
        response = """### src/good.py
VERDICT: PASS
CORRECTNESS: VALID
SPEC_COMPLIANCE: N/A
TEST_COVERAGE: COVERED
INTEGRATION: WIRED
REASONING: Perfect.
---
"""
        result = parse_review_response("test", response)
        assert result.gate == Verdict.PASS

    def test_gate_concern_when_any_concern(self):
        response = """### src/good.py
VERDICT: PASS
CORRECTNESS: VALID
SPEC_COMPLIANCE: N/A
TEST_COVERAGE: COVERED
INTEGRATION: WIRED
REASONING: Good.
---

### src/iffy.py
VERDICT: CONCERN
CORRECTNESS: VALID
SPEC_COMPLIANCE: N/A
TEST_COVERAGE: UNTESTED
INTEGRATION: WIRED
REASONING: Needs tests.
---
"""
        result = parse_review_response("test", response)
        assert result.gate == Verdict.CONCERN

    def test_gate_block_when_any_block(self):
        response = """### src/good.py
VERDICT: PASS
CORRECTNESS: VALID
SPEC_COMPLIANCE: N/A
TEST_COVERAGE: COVERED
INTEGRATION: WIRED
REASONING: Good.
---

### src/bad.py
VERDICT: BLOCK
CORRECTNESS: BROKEN
SPEC_COMPLIANCE: N/A
TEST_COVERAGE: UNTESTED
INTEGRATION: MISSING
REASONING: Broken.
---
"""
        result = parse_review_response("test", response)
        assert result.gate == Verdict.BLOCK

    def test_empty_response_defaults_to_concern(self):
        result = parse_review_response("test", "")
        assert result.gate == Verdict.CONCERN
        assert len(result.changes) == 0

    def test_unparseable_response_defaults_to_concern(self):
        result = parse_review_response("test", "This is just text with no structure.")
        assert result.gate == Verdict.CONCERN
        assert len(result.changes) == 0

    def test_multiline_reasoning(self):
        result = parse_review_response("test", SAMPLE_MULTILINE_REASONING)

        assert len(result.changes) == 1
        assert result.changes[0].verdict == Verdict.CONCERN
        assert "race condition" in result.changes[0].reasoning

    def test_extra_whitespace_handled(self):
        result = parse_review_response("test", SAMPLE_WITH_EXTRA_WHITESPACE)

        assert len(result.changes) == 1
        assert result.changes[0].verdict == Verdict.PASS

    def test_issue_compliance_field_parsed(self):
        """ISSUE_COMPLIANCE field between SPEC and BELIEF should not break parsing."""
        response = """### src/fix.py
VERDICT: PASS
CORRECTNESS: VALID
SPEC_COMPLIANCE: N/A
ISSUE_COMPLIANCE: ADDRESSES
BELIEF_COMPLIANCE: CONSISTENT
TEST_COVERAGE: COVERED
INTEGRATION: WIRED
REASONING: The fix correctly addresses the issue.
---
"""
        result = parse_review_response("test", response)
        assert result.gate == Verdict.PASS
        assert len(result.changes) == 1
        assert result.changes[0].verdict == Verdict.PASS
        assert result.changes[0].correctness == Correctness.VALID
        assert result.changes[0].test_coverage == TestCoverage.COVERED
        assert result.changes[0].integration == Integration.WIRED

    def test_raw_response_preserved(self):
        result = parse_review_response("claude", SAMPLE_CLAUDE_RESPONSE)
        assert result.raw_response == SAMPLE_CLAUDE_RESPONSE
