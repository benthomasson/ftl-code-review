# Daily Summary - Feb 26

**Date:** 2026-02-26
**Time:** 19:52

## Overview

Shipped coverage-map v0.5.0, ftl-code-review v0.8.0 with `--fix-blocks` prototype, and 4 MRs to agents-python.

## coverage-map v0.5.0

Built and released coverage-map tool for mapping source files to their tests.

## agents-python MRs

- [!804](https://gitlab.com/redhat/clt/agents-python/-/merge_requests/804) - refactor: extract sync_collection into shared utility module (open)
- [!810](https://gitlab.com/redhat/clt/agents-python/-/merge_requests/810) - refactor: replace manual OAuth retry loop with tenacity (open)
- [!812](https://gitlab.com/redhat/clt/agents-python/-/merge_requests/812) - fix: log clear HTTP errors for Pathfinder MCP 401s (open)
- [!814](https://gitlab.com/redhat/clt/agents-python/-/merge_requests/814) - fix: skip Pathfinder MCP calls when using AGGREGATE path (merged)

## ftl-code-review v0.8.0

Major improvements culminating in v0.8.0 with a new `--fix-blocks` prototype.

## Changes

### Async Observations
- Converted `subprocess.run` to `asyncio.create_subprocess_exec` in observations.py
- `find_usages`, `git_blame`, `test_coverage` now non-blocking
- `run_observations` uses `asyncio.gather` for parallel execution
- Coverage lookups in `auto` and `files` commands now run in parallel via `_gather_coverage_lookups()` helper

### Bug Fixes
- Fixed inconsistent `--repo` defaults (all now use "." instead of mix of None/".")
- Fixed confusing `if not preflight_check([model]) == []:` to `if preflight_check([model]) != []:`
- Fixed `exception_hierarchy` for bare builtin names like "ValueError" (was failing to resolve)
- Fixed filename display for files with underscores (was reconstructing incorrectly)
- Fixed `find_usages` to use `grep -Frn` (literal matching)
- Rewrote `raises_analysis` with `_RaisesVisitor` class that tracks caught exceptions via `caught_stack`

### New Features
- **`--fix-blocks` prototype**: When a review returns BLOCK verdict, the model generates a unified diff to fix the issue, validates with `git apply --check`, applies the patch, and saves to `output_dir/patches/`
- Added CLI tests with Click CliRunner (14 tests covering version, review, gate, observe, files, install-skill)

### Cleanup
- Removed `uv.lock` from version control (added to .gitignore)
- Removed `do_review.sh`

## Commits

```
648b05a Remove do_review.sh
0a0cddb Remove uv.lock from version control
c19dbd8 Update lockfile for v0.8.0
9965a01 Add --fix-blocks prototype to files command
9f8f38f Fix filename display for files with underscores
77063e2 Fix exception_hierarchy for bare builtin names
e87bf81 Add CLI tests with Click CliRunner
201a2da Run coverage lookups in parallel with asyncio.gather
4f036d8 Fix find_usages literal match and raises_analysis caught tracking
d5fa063 Convert observations to async for parallel execution
```

## Remaining Concerns (from self-review)

| File | Concern |
|------|---------|
| cli.py | Code duplication across commands, `auto` UnboundLocalError if max_iterations=0 |
| fixer.py | No tests, concurrent `git apply` risk |
| observations.py | No `ast.TryStar` support, `grep -F` substring matches |
| reviewer.py | Regex fragility (field order dependent) |

## Next Steps

- Add tests for fixer.py
- Fix `auto` command UnboundLocalError
- Consider making `fix_blocks` sequential to avoid concurrent patch conflicts

