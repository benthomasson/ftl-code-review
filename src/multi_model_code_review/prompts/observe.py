"""Observation-only prompt for first pass."""

OBSERVE_PROMPT = """You are a senior code reviewer preparing to review code changes.

## Code Changes

```diff
{diff_content}
```

## Your Task

Analyze the diff and identify what additional information you need to render confident verdicts.
Do NOT render verdicts yet. Only request observations.

## Available Observation Tools

| Tool | Purpose | When to use |
|------|---------|-------------|
| `exception_hierarchy` | Show exception MRO and subclasses | Retry logic, exception handling |
| `raises_analysis` | What exceptions a function raises | New function calls, error paths |
| `call_graph` | What a function calls | Impact analysis |
| `find_usages` | Where a symbol is used | Integration verification |
| `test_coverage` | Find tests for a file (uses coverage-map if available) | Test coverage claims |
| `coverage_map_tests` | Find tests covering a file (from coverage-map.json) | Precise test coverage from actual execution |
| `coverage_map_files` | Find files covered by tests matching a pattern | Impact analysis for test changes |
| `function_body` | Full source of a function/method | Need complete function context beyond diff hunks |
| `file_imports` | Extract imports from a file | Verify import changes, check dependencies |
| `project_dependencies` | Get pyproject.toml/requirements.txt | Verify new imports have dependencies |
| `related_test_files` | Find test files for a source file | Discover tests by naming, imports, and coverage map |

## What to Look For

1. **Exception handling**: Any `retry_if_exception_type`, `except`, or exception class references
2. **New dependencies**: Calls to external libraries where you don't know the error behavior
3. **Behavioral changes**: Modified logic where you need to verify callers/callees
4. **Test claims**: References to tests you can't see in the diff

## Output Format

Output a JSON array of observation requests:

```json
[
  {{"name": "descriptive_name", "tool": "tool_name", "params": {{"param": "value"}}}},
  ...
]
```

If you don't need any observations (simple changes, all context is in the diff), output:

```json
[]
```

## Examples

For a diff containing `retry_if_exception_type((OSError, httpx.TransportError))`:
```json
[
  {{"name": "oserror_subclasses", "tool": "exception_hierarchy", "params": {{"class_name": "builtins.OSError"}}}},
  {{"name": "transport_errors", "tool": "exception_hierarchy", "params": {{"class_name": "httpx.TransportError"}}}}
]
```

For a diff adding a new function that calls `oauth_client.get_access_token()`:
```json
[
  {{"name": "oauth_exceptions", "tool": "raises_analysis", "params": {{"file_path": "src/auth/oauth.py", "function_name": "get_access_token"}}}}
]
```

For a diff modifying a method but you need the full function to verify:
```json
[
  {{"name": "full_getattr", "tool": "function_body", "params": {{"file_path": "src/proxy.py", "function_name": "__getattr__"}}}}
]
```

For a diff adding new imports (e.g., `import httpx`):
```json
[
  {{"name": "file_imports", "tool": "file_imports", "params": {{"file_path": "src/client.py"}}}},
  {{"name": "project_deps", "tool": "project_dependencies", "params": {{}}}}
]
```

Now analyze the diff above and output your observation requests as JSON:
"""


def build_observe_prompt(diff_content: str) -> str:
    """Build the observation-gathering prompt."""
    return OBSERVE_PROMPT.format(diff_content=diff_content)
