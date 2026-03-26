---
name: code-review
description: Run multi-model AI code reviews with lint checking
argument-hint: "[review|lint|gate|compare|check-spec|models] [options]"
allowed-tools: Bash(code-review *), Bash(uv run code-review *), Bash(uvx *code-review*), Read
---

You are running AI-powered code reviews using the `code-review` CLI tool. This tool runs reviews with multiple models (Claude + Gemini by default) and aggregates their verdicts.

## Why Use This Tool

**Multi-model consensus catches more issues.** A single model may miss bugs or have blind spots. Running Claude and Gemini together surfaces disagreements and provides higher confidence when they agree.

**Structured verdicts, not prose.** Each change gets a verdict (PASS/CONCERN/BLOCK) with axes for correctness, spec compliance, test coverage, and integration. This makes reviews actionable.

**Lint as pre-model gate.** Run black, isort, and ruff checks before invoking expensive model APIs. Fail fast on formatting issues.

## How to Run

Try these in order until one works:
1. `code-review $ARGUMENTS` (if installed via uv/pip)
2. `uv run code-review $ARGUMENTS` (if in the repo with pyproject.toml)
3. `uvx --from git+https://github.com/benthomasson/ftl-code-review code-review $ARGUMENTS` (fallback)

## Common Commands

### Review a branch
```bash
code-review review -b feature-branch --base main
```

### Review with repo flag (run from anywhere)
```bash
code-review review --repo ~/git/my-project -b feature-branch
```

### Run lint checks only
```bash
code-review lint --repo ~/git/my-project -b feature-branch
```

### Fix lint issues automatically
```bash
code-review lint --repo ~/git/my-project -b feature-branch --fix
```

### Gate check (exit code based on result)
```bash
code-review gate -b feature-branch
# Exit 0 = PASS, 1 = CONCERN, 2 = BLOCK
```

### Review with lint pre-check
```bash
code-review review -b feature-branch --lint
```

### Review with auto-fix then lint
```bash
code-review review -b feature-branch --fix-lint
```

### Compare model disagreements only
```bash
code-review compare -b feature-branch
```

### Check against a spec file
```bash
code-review check-spec spec.md -b feature-branch
```

### List available models
```bash
code-review models
```

## Command Reference

### `review`
Run full code review with multiple models.

Options:
- `-b, --branch` - Branch to review (default: staged changes)
- `--base` - Base branch to diff against (default: main)
- `-r, --repo` - Repository directory (default: current directory)
- `-m, --model` - Models to use (repeatable, default: claude, gemini)
- `-s, --spec` - Path to spec file for compliance checking
- `-o, --output` - Output format: full or summary
- `-d, --output-dir` - Save reports and raw responses to directory
- `--lint/--no-lint` - Run lint checks before model review
- `--fix-lint` - Auto-fix lint issues before review

### `lint`
Run lint checks (black, isort, ruff) on changed files.

Options:
- `-b, --branch` - Branch to check (default: staged changes)
- `--base` - Base branch to diff against (default: main)
- `-r, --repo` - Repository directory
- `--fix` - Auto-fix lint issues

### `gate`
Run review and exit with code based on result.
- Exit 0: PASS (all models pass)
- Exit 1: CONCERN (at least one concern, no blocks)
- Exit 2: BLOCK (at least one model blocks)

### `compare`
Show only disagreements between models.

### `check-spec`
Check code changes against a specification file.

### `models`
List available models and their status.

### `install-skill`
Install this skill file to `.claude/skills/code-review/SKILL.md`.

## Understanding Verdicts

Each change is assessed on:
- **CORRECTNESS**: VALID / QUESTIONABLE / BROKEN
- **SPEC_COMPLIANCE**: MEETS / PARTIAL / VIOLATES / N/A
- **TEST_COVERAGE**: COVERED / PARTIAL / UNTESTED
- **INTEGRATION**: WIRED / PARTIAL / MISSING

Overall verdict:
- **PASS**: Correct, tested, well-integrated
- **CONCERN**: Missing tests, partial integration, questionable patterns
- **BLOCK**: Security issues, broken functionality, spec violations

## Self-Review and Feature Requests

Models provide self-assessment after each review:
- **Limitations**: What context was missing
- **Feature Requests**: Suggestions for improving the tool

## After Any Command

- If the review completed, summarize the gate result and key findings
- If models disagree, highlight the disagreements
- If lint fails, show what needs fixing
