# ftl-code-review

AI-first code review using multiple models to surface issues before human review.

## Installation

```bash
# Run directly with uvx (no install needed)
uvx --from "git+https://github.com/benthomasson/ftl-code-review" code-review --help

# Or install as a tool
uv tool install "git+https://github.com/benthomasson/ftl-code-review"
```

## Quick Start

```bash
# Review a branch (recommended - uses observe/review loop)
code-review review-loop -b feature-branch

# Review staged changes
code-review review-loop
```

## Why Multi-Model?

As AI-assisted development scales, human reviewers become the bottleneck. This tool runs reviews through multiple AI models (Claude, Gemini) to:

- **Catch issues** before human review
- **Surface disagreements** between models as signals for attention
- **Validate against specs** for compliance checking
- **Provide CI gates** for automated quality control

If models disagree, humans should look closer.

## Commands

### review-loop (Recommended)

Run the full observe → review loop with automatic context gathering.

```bash
code-review review-loop -b feature-branch
code-review review-loop -b feature-branch --base main
code-review review-loop -b feature-branch --max-iterations 3
```

This:
1. Auto-looks up test coverage from `coverage-map.json` (if present)
2. Gathers observations (exception hierarchies, call graphs, etc.)
3. Runs review with observations as context
4. Saves all artifacts to `reviews/<branch>/<timestamp>/`

### review

Run a single-pass review without observation gathering.

```bash
code-review review -b feature-branch
code-review review -b feature-branch --spec spec.md
code-review review --observations obs.json  # With pre-gathered observations
```

### observe

Gather observations without running the review (for debugging).

```bash
code-review observe -b feature-branch -o observations.json
```

### gate

CI-friendly command with exit codes.

```bash
code-review gate -b feature-branch
# Exit 0 = PASS, Exit 1 = CONCERN, Exit 2 = BLOCK
```

### lint

Run lint checks on changed files.

```bash
code-review lint -b feature-branch
code-review lint -b feature-branch --fix
```

### compare

Show only disagreements between models.

```bash
code-review compare -b feature-branch
```

### check-spec

Validate implementation against a specification.

```bash
code-review check-spec spec.md -b feature-branch
```

### files

Review specific files directly (not diffs). Useful for reviewing existing code or entire modules.

```bash
# Review a single file
code-review files src/auth/client.py

# Review multiple files
code-review files src/auth/client.py src/auth/oauth.py

# Review all Python files in a directory
code-review files src/auth/

# Review with glob patterns
code-review files "src/**/*.py" --glob
```

## Observation Tools

The review system can request additional context via observation tools:

| Tool | Purpose |
|------|---------|
| `exception_hierarchy` | Show exception MRO and subclasses |
| `raises_analysis` | What exceptions a function raises |
| `call_graph` | What a function calls |
| `find_usages` | Where a symbol is used |
| `git_blame` | Who changed specific lines |
| `test_coverage` | Find tests for a file |
| `coverage_map_tests` | Find tests from coverage-map.json |
| `coverage_map_files` | Find files covered by tests |
| `file_imports` | Extract imports from a file |
| `project_dependencies` | Get pyproject.toml/requirements.txt |

## Coverage Map Integration

If you generate a `coverage-map.json` with [coverage-map](https://github.com/benthomasson/coverage-map), reviews automatically include test coverage:

```bash
# Generate coverage map (one-time, or after test changes)
uvx --from "git+https://github.com/benthomasson/coverage-map" \
  coverage-map collect --source src --tests tests

# Run review (auto-detects coverage-map.json)
code-review review-loop -b feature-branch
```

Output:
```
Auto-lookup: 2 Python file(s) changed
  src/auth/client.py: 13 tests
  src/utils/logger.py: 91 tests
Auto-lookup found tests for 2 file(s)
```

## Review Dimensions

Each change is assessed on multiple axes:

| Dimension | Verdicts |
|-----------|----------|
| Correctness | VALID / QUESTIONABLE / BROKEN |
| Spec Compliance | MEETS / PARTIAL / VIOLATES / N/A |
| Test Coverage | COVERED / PARTIAL / UNTESTED |
| Integration | WIRED / PARTIAL / MISSING |

## Output Example

```
## Review: feat/oauth-retry

### src/auth/client.py
VERDICT: PASS
CORRECTNESS: VALID
TEST_COVERAGE: COVERED
REASONING: Retry logic correctly handles OSError and TransportError.
           13 tests verify the behavior.

### src/utils/logger.py
VERDICT: CONCERN
CORRECTNESS: VALID
INTEGRATION: PARTIAL
REASONING: New log_with_context() added but not called from client.py.

---

## Model Agreement
- claude: 8P / 1C / 0B
- gemini: 7P / 2C / 0B

## Disagreements
- src/utils/logger.py: claude=PASS, gemini=CONCERN

GATE: CONCERN (no BLOCKs)
```

## Output Directory

The `review-loop` command saves artifacts to `reviews/<branch>/<timestamp>/`:

```
reviews/feat-oauth-retry/2026-02-26_17-36-02/
├── 00-auto-coverage.json    # Auto-looked up coverage
├── 01-observe-prompt.txt    # Observation prompt
├── 01-observe-response.txt  # Model's observation requests
├── 01-observations.json     # Observation results
├── 01-review-prompt.txt     # Review prompt
├── 01-claude-response.txt   # Claude's review
├── 01-gemini-response.txt   # Gemini's review
├── observations.json        # All observations combined
└── report.md                # Final report
```

## Requirements

- Python 3.11+
- `claude` CLI installed and authenticated
- `gemini` CLI installed and authenticated

Check availability:
```bash
code-review models
```

## Related

- [coverage-map](https://github.com/benthomasson/coverage-map) - Map source files to tests
- [multi-model-review](https://github.com/benthomasson/multi-model-review) - Paper review (inspiration)

## License

MIT
