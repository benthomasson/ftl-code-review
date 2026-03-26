"""Spec compliance checking prompt."""

SPEC_CHECK_PROMPT = """You are a specification compliance checker. Review the code changes against the specification.

## Specification

```markdown
{spec_content}
```

## Code Changes

```diff
{diff_content}
```

## Instructions

Check each MUST requirement in the spec. For each requirement, determine if the code:
1. **MEETS** - Code satisfies the requirement
2. **PARTIAL** - Code partially addresses the requirement
3. **VIOLATES** - Code contradicts or ignores the requirement
4. **UNTESTED** - Requirement exists but no test coverage

Use this exact format for each requirement:

### MUST: <requirement text>
STATUS: MEETS | PARTIAL | VIOLATES | UNTESTED
EVIDENCE: <specific code reference or explanation>
---

## Summary

After reviewing all requirements, provide:

OVERALL: COMPLIANT | NON_COMPLIANT | PARTIAL
MISSING: <list any MUST requirements not addressed>
CONCERNS: <any implementation concerns>
"""


def build_spec_check_prompt(
    diff_content: str,
    spec_content: str,
) -> str:
    """
    Build the spec check prompt.

    Args:
        diff_content: Git diff to review
        spec_content: Spec file content (required for this prompt)

    Returns:
        Complete prompt for model
    """
    return SPEC_CHECK_PROMPT.format(
        spec_content=spec_content,
        diff_content=diff_content,
    )
