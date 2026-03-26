"""General code review prompt."""

CODE_REVIEW_PROMPT = """You are a senior code reviewer. Review the following code changes.

{spec_section}

{beliefs_section}

{issue_section}

## Code Changes

```diff
{diff_content}
```

{observations_section}

## Instructions

For each significant change (new file, modified function, etc.), provide a structured verdict.

Use this exact format for each change:

### <file_path or file_path:function_name>
VERDICT: PASS | CONCERN | BLOCK
CORRECTNESS: VALID | QUESTIONABLE | BROKEN
SPEC_COMPLIANCE: MEETS | PARTIAL | VIOLATES | N/A
ISSUE_COMPLIANCE: ADDRESSES | PARTIAL | UNRELATED | N/A
BELIEF_COMPLIANCE: CONSISTENT | VIOLATES | N/A
TEST_COVERAGE: COVERED | PARTIAL | UNTESTED
INTEGRATION: WIRED | PARTIAL | MISSING
REASONING: <brief explanation of your assessment>
---

## Review Criteria

1. **CORRECTNESS**: Does the code do what it claims? Is the logic sound?
   - VALID: Logic is correct, no bugs apparent
   - QUESTIONABLE: Logic may have edge cases or unclear behavior
   - BROKEN: Clear bugs or incorrect behavior

2. **SPEC_COMPLIANCE**: Does it meet MUST requirements from the spec?
   - MEETS: All relevant spec requirements satisfied
   - PARTIAL: Some requirements met, others missing or incomplete
   - VIOLATES: Contradicts spec requirements
   - N/A: No spec provided or not applicable

3. **ISSUE_COMPLIANCE** (only when an issue is provided): Do the changes address the problem or feature described in the issue?
   - ADDRESSES: Changes directly solve the issue's stated problem or implement the requested feature
   - PARTIAL: Changes partially address the issue but leave some aspects unresolved
   - UNRELATED: Changes do not appear related to the issue
   - N/A: No issue provided

4. **TEST_COVERAGE**: Are there tests for the new/changed code?
   - COVERED: Tests exist and cover the changes
   - PARTIAL: Some tests exist but coverage is incomplete
   - UNTESTED: No tests for the changes

5. **INTEGRATION**: Are callers updated? Is the feature usable end-to-end?
   - WIRED: Feature is fully integrated and usable
   - PARTIAL: Interface exists but callers not updated, or integration incomplete
   - MISSING: No integration with existing code

6. **BELIEF_COMPLIANCE** (only when beliefs are provided): Do the changes respect known architectural invariants, contracts, and rules?
   - CONSISTENT: Changes align with or reinforce known beliefs
   - VIOLATES: Changes contradict a specific belief — cite the belief ID
   - N/A: No beliefs provided or no relevant beliefs apply

## Verdict Guidelines

- **BLOCK**: Security issues, broken functionality, spec violations, or missing critical integration
- **CONCERN**: Missing tests, partial integration, questionable patterns, or unclear logic
- **PASS**: Correct, tested, well-integrated code

## Important

- Full function bodies for modified functions may be available in the observations section — use them to verify the complete logic, not just the diff hunks
- Focus on actual issues, not style preferences
- If a method signature is added but callers aren't updated, that's PARTIAL integration
- Be specific in reasoning - reference line numbers or function names
- When in doubt, use CONCERN rather than PASS

## Self-Review

After completing your review, add a brief self-assessment:

### SELF_REVIEW
LIMITATIONS: <what context were you missing that affected review quality?>
---

Examples of limitations:
- "Could not see full class to verify no other methods access the modified field"
- "Test file not included in diff - cannot verify coverage claims"
- "Spec file referenced but not provided"


## Feature Requests

If this review tool could be improved to help you do a better job, suggest features:

### FEATURE_REQUESTS
- <suggestion 1>
- <suggestion 2>
---

Examples:
- "Include full file context for modified functions, not just diff hunks"
- "Show callers of modified methods to verify integration"
- "Include test file alongside implementation changes"

Only include this section if you have specific suggestions. Skip if none.
"""


def build_review_prompt(
    diff_content: str,
    spec_content: str | None = None,
    observations: dict | None = None,
    beliefs_content: str | None = None,
    issue_content: str | None = None,
) -> str:
    """
    Build the review prompt with diff, optional spec, beliefs, issue, and observation results.

    Args:
        diff_content: Git diff to review
        spec_content: Optional spec file content
        observations: Optional dict of observation results from previous iteration
        beliefs_content: Optional beliefs file content (from code-expert knowledge base)
        issue_content: Optional issue description to check changes against

    Returns:
        Complete prompt for model
    """
    import json

    if spec_content:
        spec_section = f"""## Specification

Review the code against this specification. Flag any MUST requirements that are not met.

```markdown
{spec_content}
```
"""
    else:
        spec_section = "## Specification\n\nNo specification provided. Focus on correctness, tests, and integration."

    if beliefs_content:
        beliefs_section = f"""## Known Beliefs (Architecture & Contracts)

The following beliefs are established facts about this codebase, extracted from expert analysis.
Check each code change against these beliefs. If a change VIOLATES a belief, flag it with BELIEF_COMPLIANCE: VIOLATES and cite the belief ID.
If a change is CONSISTENT with a belief, note it briefly. If no beliefs are relevant to a change, use BELIEF_COMPLIANCE: N/A.

```markdown
{beliefs_content}
```
"""
    else:
        beliefs_section = ""

    if issue_content:
        issue_section = f"""## Issue Description

Review the code changes against this issue. Determine whether the changes address the problem or feature request described.

```markdown
{issue_content}
```
"""
    else:
        issue_section = ""

    if observations:
        observations_section = f"""## Observation Results

You previously requested observations. Here are the results:

```json
{json.dumps(observations, indent=2, default=str)}
```

Use these results to inform your review. Do not request the same observations again.
"""
    else:
        observations_section = ""

    return CODE_REVIEW_PROMPT.format(
        spec_section=spec_section,
        diff_content=diff_content,
        observations_section=observations_section,
        beliefs_section=beliefs_section,
        issue_section=issue_section,
    )
