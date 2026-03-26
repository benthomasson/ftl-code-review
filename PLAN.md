# Implementation Plan: ftl-code-review

## Overview

Build a CLI tool that runs code reviews through multiple AI models before human review. This reduces human reviewer burden by catching obvious issues and surfacing inter-model disagreements as signals for focused attention.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     code-review CLI                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐              │
│  │  Claude  │    │  Gemini  │    │  Model N │              │
│  └────┬─────┘    └────┬─────┘    └────┬─────┘              │
│       │               │               │                     │
│       └───────────────┼───────────────┘                     │
│                       ▼                                      │
│              ┌─────────────────┐                            │
│              │   Aggregator    │                            │
│              │  (disagreement  │                            │
│              │   detection)    │                            │
│              └────────┬────────┘                            │
│                       ▼                                      │
│              ┌─────────────────┐                            │
│              │     Report      │                            │
│              │   (verdicts,    │                            │
│              │  gate status)   │                            │
│              └─────────────────┘                            │
└─────────────────────────────────────────────────────────────┘
```

## Phase 1: Core Framework

### 1.1 Project Setup

```
ftl-code-review/
├── README.md
├── PLAN.md
├── pyproject.toml
├── src/
│   └── ftl_code_review/
│       ├── __init__.py          # Data structures
│       ├── cli.py               # Command dispatch
│       ├── reviewer.py          # Model invocation
│       ├── prompts/
│       │   ├── __init__.py
│       │   ├── review.py        # General review prompt
│       │   ├── spec_check.py    # Spec compliance prompt
│       │   └── security.py      # Security-focused prompt
│       ├── parser.py            # Parse model outputs
│       ├── aggregator.py        # Combine results, find disagreements
│       └── report.py            # Format output
└── tests/
```

### 1.2 Data Structures

```python
from dataclasses import dataclass
from enum import Enum

class Verdict(Enum):
    PASS = "PASS"
    CONCERN = "CONCERN"
    BLOCK = "BLOCK"

class Correctness(Enum):
    VALID = "VALID"
    QUESTIONABLE = "QUESTIONABLE"
    BROKEN = "BROKEN"

class SpecCompliance(Enum):
    MEETS = "MEETS"
    PARTIAL = "PARTIAL"
    VIOLATES = "VIOLATES"

class TestCoverage(Enum):
    COVERED = "COVERED"
    PARTIAL = "PARTIAL"
    UNTESTED = "UNTESTED"

class Integration(Enum):
    WIRED = "WIRED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"

@dataclass
class ChangeVerdict:
    """Review verdict for a single change (file or function)."""
    change_id: str              # e.g., "src/workflow/router.py:42"
    verdict: Verdict
    correctness: Correctness
    spec_compliance: SpecCompliance | None
    test_coverage: TestCoverage
    integration: Integration
    reasoning: str

@dataclass
class ModelReview:
    """Complete review from one model."""
    model: str
    gate: Verdict               # Overall PASS/BLOCK
    changes: list[ChangeVerdict]
    raw_response: str

@dataclass
class AggregateReview:
    """Combined review across all models."""
    diff_ref: str               # Branch or commit
    spec_file: str | None
    models: list[str]
    reviews: list[ModelReview]
    gate: Verdict               # BLOCK if any model blocks
    disagreements: list[dict]   # Where models differ
```

### 1.3 Model Invocation

Reuse pattern from multi-model-review:

```python
MODEL_COMMANDS = {
    "claude": ["claude", "-p"],
    "gemini": ["gemini", "-p", ""],
}

async def run_model(model: str, prompt: str, timeout: int = 300) -> str:
    """Invoke model via CLI, piping prompt through stdin."""
    cmd = MODEL_COMMANDS[model]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    stdout, stderr = await asyncio.wait_for(
        proc.communicate(prompt.encode()),
        timeout=timeout
    )

    return stdout.decode()
```

## Phase 2: Review Prompts

### 2.1 General Code Review Prompt

```python
CODE_REVIEW_PROMPT = """You are a senior code reviewer. Review the following code changes.

## Spec (if provided)
{spec_content}

## Code Changes
{diff_content}

## Instructions

For each significant change (new file, modified function, etc.), provide a structured verdict:

### <change_id>
VERDICT: PASS | CONCERN | BLOCK
CORRECTNESS: VALID | QUESTIONABLE | BROKEN
SPEC_COMPLIANCE: MEETS | PARTIAL | VIOLATES | N/A
TEST_COVERAGE: COVERED | PARTIAL | UNTESTED
INTEGRATION: WIRED | PARTIAL | MISSING
REASONING: <brief explanation>
---

## Review Criteria

1. **Correctness**: Does the code do what it claims?
2. **Spec Compliance**: Does it meet MUST requirements from the spec?
3. **Test Coverage**: Are there tests for the new/changed code?
4. **Integration**: Are callers updated? Is the feature usable end-to-end?

## Important

- BLOCK for security issues, broken functionality, or spec violations
- CONCERN for missing tests, partial integration, or questionable patterns
- PASS for correct, tested, well-integrated code

Focus on actual issues, not style preferences.
"""
```

### 2.2 Spec Compliance Prompt

```python
SPEC_CHECK_PROMPT = """You are validating code against a specification.

## Specification
{spec_content}

## Implementation
{code_content}

## Instructions

Extract each MUST requirement from the spec and verify implementation:

### REQ-<n>: <requirement summary>
VERDICT: MEETS | PARTIAL | VIOLATES
EVIDENCE: <file:line or "NOT FOUND">
REASONING: <explanation>
---

## Critical Checks

1. Are all MUST requirements implemented?
2. Are integration points wired up (not just interfaces)?
3. Is there a code path that exercises the feature end-to-end?

Remember: A method signature change without caller updates is PARTIAL, not MEETS.
"""
```

### 2.3 Security Review Prompt

```python
SECURITY_PROMPT = """You are a security reviewer. Analyze for vulnerabilities.

## Code Changes
{diff_content}

## Instructions

For each potential security issue:

### SEC-<n>: <vulnerability type>
SEVERITY: CRITICAL | HIGH | MEDIUM | LOW
LOCATION: <file:line>
ISSUE: <description>
RECOMMENDATION: <fix>
---

## Check For
- Injection (SQL, command, XSS)
- Authentication/authorization gaps
- Secrets in code
- Unsafe deserialization
- Path traversal
- SSRF
"""
```

## Phase 3: Aggregation & Disagreement Detection

### 3.1 Aggregation Logic

```python
def aggregate_reviews(reviews: list[ModelReview]) -> AggregateReview:
    """Combine reviews and detect disagreements."""

    # Gate: BLOCK if any model blocks
    gate = Verdict.PASS
    for review in reviews:
        if review.gate == Verdict.BLOCK:
            gate = Verdict.BLOCK
            break

    # Find disagreements
    disagreements = find_disagreements(reviews)

    return AggregateReview(
        diff_ref=diff_ref,
        spec_file=spec_file,
        models=[r.model for r in reviews],
        reviews=reviews,
        gate=gate,
        disagreements=disagreements,
    )

def find_disagreements(reviews: list[ModelReview]) -> list[dict]:
    """Find changes where models gave different verdicts."""
    # Build map: change_id -> {model: verdict}
    verdict_map: dict[str, dict[str, Verdict]] = {}

    for review in reviews:
        for change in review.changes:
            if change.change_id not in verdict_map:
                verdict_map[change.change_id] = {}
            verdict_map[change.change_id][review.model] = change.verdict

    # Find disagreements
    disagreements = []
    for change_id, model_verdicts in verdict_map.items():
        verdicts = set(model_verdicts.values())
        if len(verdicts) > 1:
            disagreements.append({
                "change_id": change_id,
                "verdicts": model_verdicts,
            })

    return disagreements
```

## Phase 4: CLI Commands

### 4.1 Command Structure

```python
@click.group()
def cli():
    """Multi-model code review CLI."""
    pass

@cli.command()
@click.option("--spec", type=click.Path(exists=True), help="Spec file")
@click.option("--diff", required=True, help="Branch or commit to review")
@click.option("--models", default="claude,gemini", help="Comma-separated models")
def review(spec, diff, models):
    """Full review with all verdicts."""
    pass

@cli.command()
@click.option("--spec", type=click.Path(exists=True), help="Spec file")
@click.option("--diff", required=True, help="Branch or commit to review")
@click.option("--models", default="claude,gemini", help="Comma-separated models")
def compare(spec, diff, models):
    """Review highlighting disagreements."""
    pass

@cli.command()
@click.option("--spec", type=click.Path(exists=True), help="Spec file")
@click.option("--diff", required=True, help="Branch or commit to review")
@click.option("--models", default="claude,gemini", help="Comma-separated models")
def gate(spec, diff, models):
    """CI gate - exit 0=PASS, 2=BLOCK."""
    pass

@cli.command()
@click.option("--spec", required=True, type=click.Path(exists=True))
@click.option("--code", required=True, type=click.Path(exists=True))
@click.option("--models", default="claude,gemini")
def check_spec(spec, code, models):
    """Validate code against spec requirements."""
    pass
```

## Phase 5: Git Integration

### 5.1 Diff Extraction

```python
def get_diff(ref: str, base: str = "main") -> str:
    """Get diff between ref and base."""
    result = subprocess.run(
        ["git", "diff", f"{base}...{ref}"],
        capture_output=True,
        text=True,
    )
    return result.stdout

def get_changed_files(ref: str, base: str = "main") -> list[str]:
    """List files changed between ref and base."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{ref}"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().split("\n")
```

### 5.2 File Content Extraction

```python
def get_file_content(path: str, ref: str = "HEAD") -> str:
    """Get file content at specific ref."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        capture_output=True,
        text=True,
    )
    return result.stdout
```

## Phase 6: CI Integration

### 6.1 GitHub Actions

```yaml
name: Code Review Gate

on:
  pull_request:
    types: [opened, synchronize]

jobs:
  ai-review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install tools
        run: |
          uv tool install git+https://github.com/benthomasson/ftl-code-review

      - name: Run AI review gate
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}
        run: |
          code-review gate \
            --diff ${{ github.head_ref }} \
            --spec specs/relevant.spec.md
```

### 6.2 GitLab CI

```yaml
ai-review:
  stage: review
  script:
    - uv tool install git+https://github.com/benthomasson/ftl-code-review
    - code-review gate --diff $CI_MERGE_REQUEST_SOURCE_BRANCH_NAME
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
```

## Timeline

| Phase | Description | Estimate |
|-------|-------------|----------|
| 1 | Core framework & data structures | Day 1 |
| 2 | Review prompts | Day 1 |
| 3 | Aggregation & disagreement | Day 1-2 |
| 4 | CLI commands | Day 2 |
| 5 | Git integration | Day 2 |
| 6 | CI integration examples | Day 2-3 |

## Success Criteria

1. `code-review gate` returns 0/2 based on review
2. Multiple models run and verdicts aggregate correctly
3. Disagreements are detected and highlighted
4. Spec compliance checking works end-to-end
5. CI examples work in GitHub Actions and GitLab CI

## Future Enhancements

- [ ] Incremental review (only re-review changed files)
- [ ] Review comment posting to PR/MR
- [ ] Custom prompt templates
- [ ] Model weighting (trust some models more)
- [ ] Learning from human overrides
- [ ] Integration with existing linters/SAST tools
