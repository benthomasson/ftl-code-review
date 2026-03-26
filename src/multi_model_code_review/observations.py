"""
Observation tools for code review.

Inspired by ftl2-ai-loop's observe pattern, these tools allow reviewers
to request additional information about the code being reviewed.

The reviewer can include an "observe" field in their response:
    {
        "verdict": "CONCERN",
        "reasoning": "Need to verify exception coverage",
        "observe": [
            {"name": "httpx_errors", "tool": "exception_hierarchy", "params": {"class_name": "httpx.TransportError"}}
        ]
    }

The review loop will run the requested observations and re-invoke the
reviewer with the additional context.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import sys
from pathlib import Path
from typing import Any


def is_test_file(file_path: str) -> bool:
    """Classify whether a file path belongs to test code.

    Checks filename patterns and path components commonly used for tests.

    Args:
        file_path: Relative or absolute file path.

    Returns:
        True if the file is a test file, False otherwise.

    Examples:
        >>> is_test_file("tests/test_foo.py")
        True
        >>> is_test_file("src/auth/client.py")
        False
        >>> is_test_file("test/unit/bar_test.py")
        True
        >>> is_test_file("conftest.py")
        True
    """
    from pathlib import PurePosixPath

    path = PurePosixPath(file_path)
    basename = path.name

    # Filename patterns
    if basename.startswith("test_") or basename.endswith("_test.py"):
        return True
    if basename == "conftest.py":
        return True

    # Path component patterns
    test_dirs = {"tests", "test", "testing"}
    for part in path.parts:
        if part in test_dirs:
            return True

    return False


async def exception_hierarchy(class_name: str, repo_path: str | None = None) -> dict[str, Any]:
    """
    Show exception class hierarchy (MRO and subclasses).

    Args:
        class_name: Fully qualified class name (e.g., "httpx.TransportError")
        repo_path: Optional repo path (unused, for interface consistency)

    Returns:
        Dict with class name, MRO, and all subclasses (recursive)
    """
    try:
        # Handle bare names like "ValueError" by assuming builtins
        if "." in class_name:
            module_name, cls_name = class_name.rsplit(".", 1)
            module = importlib.import_module(module_name)
            cls = getattr(module, cls_name)
        else:
            # Try builtins first
            import builtins
            cls = getattr(builtins, class_name)
            cls_name = class_name

        def get_all_subclasses(c: type) -> list[str]:
            """Recursively get all subclasses."""
            result = []
            for sub in c.__subclasses__():
                result.append(f"{sub.__module__}.{sub.__name__}")
                result.extend(get_all_subclasses(sub))
            return result

        return {
            "class": class_name,
            "mro": [f"{c.__module__}.{c.__name__}" for c in cls.__mro__ if c is not object],
            "subclasses": get_all_subclasses(cls),
            "doc": cls.__doc__[:200] if cls.__doc__ else None,
        }
    except Exception as e:
        return {"error": str(e), "class": class_name}


def _get_exception_name(exc_node: ast.expr) -> str | None:
    """Extract exception name from a raise or except node."""
    if isinstance(exc_node, ast.Call):
        if isinstance(exc_node.func, ast.Name):
            return exc_node.func.id
        elif isinstance(exc_node.func, ast.Attribute):
            return exc_node.func.attr
    elif isinstance(exc_node, ast.Name):
        return exc_node.id
    return None


def _get_caught_exceptions(handlers: list[ast.ExceptHandler]) -> set[str]:
    """Get the set of exception names caught by handlers."""
    caught: set[str] = set()
    for handler in handlers:
        if handler.type is None:
            # Bare except catches everything
            caught.add("*")
        elif isinstance(handler.type, ast.Name):
            caught.add(handler.type.id)
        elif isinstance(handler.type, ast.Tuple):
            for elt in handler.type.elts:
                if isinstance(elt, ast.Name):
                    caught.add(elt.id)
    return caught


class _RaisesVisitor(ast.NodeVisitor):
    """Visitor that tracks raises not caught by local try/except."""

    def __init__(self):
        self.raises: list[str] = []
        self.calls: list[str] = []
        self.caught_stack: list[set[str]] = []

    def visit_Try(self, node: ast.Try):
        caught = _get_caught_exceptions(node.handlers)
        self.caught_stack.append(caught)
        for child in node.body:
            self.visit(child)
        self.caught_stack.pop()
        # Visit else and finalbody without the caught context
        for child in node.orelse:
            self.visit(child)
        for child in node.finalbody:
            self.visit(child)
        # Visit handlers themselves
        for handler in node.handlers:
            for child in handler.body:
                self.visit(child)

    def visit_Raise(self, node: ast.Raise):
        if node.exc:
            exc_name = _get_exception_name(node.exc)
            if exc_name:
                # Check if caught by any enclosing try/except
                is_caught = False
                for caught in self.caught_stack:
                    if "*" in caught or exc_name in caught or "Exception" in caught or "BaseException" in caught:
                        is_caught = True
                        break
                if not is_caught:
                    self.raises.append(exc_name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Name):
            self.calls.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            self.calls.append(node.func.attr)
        self.generic_visit(node)


async def raises_analysis(file_path: str, function_name: str, repo_path: str | None = None) -> dict[str, Any]:
    """
    Static analysis of what exceptions a function might raise.

    Ignores exceptions that are caught by local try/except blocks.

    Args:
        file_path: Path to the Python file (relative to repo_path or absolute)
        function_name: Name of the function to analyze
        repo_path: Base path for relative file paths

    Returns:
        Dict with explicit raises and called functions that might raise
    """
    try:
        if repo_path and not Path(file_path).is_absolute():
            full_path = Path(repo_path) / file_path
        else:
            full_path = Path(file_path)

        source = full_path.read_text()
        tree = ast.parse(source)

        # Find the function
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    visitor = _RaisesVisitor()
                    visitor.visit(node)
                    return {
                        "function": function_name,
                        "file": str(file_path),
                        "explicit_raises": list(set(visitor.raises)),
                        "calls": list(set(visitor.calls))[:20],
                    }

        return {
            "function": function_name,
            "file": str(file_path),
            "explicit_raises": [],
            "calls": [],
            "error": f"Function '{function_name}' not found",
        }
    except Exception as e:
        return {"error": str(e), "function": function_name}


async def call_graph(file_path: str, function_name: str, repo_path: str | None = None) -> dict[str, Any]:
    """
    Build call graph for a function.

    Args:
        file_path: Path to the Python file
        function_name: Name of the function to analyze
        repo_path: Base path for relative file paths

    Returns:
        Dict with functions called by this function
    """
    try:
        if repo_path and not Path(file_path).is_absolute():
            full_path = Path(repo_path) / file_path
        else:
            full_path = Path(file_path)

        source = full_path.read_text()
        tree = ast.parse(source)

        calls: list[dict[str, Any]] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            call_info: dict[str, Any] = {"line": child.lineno}

                            if isinstance(child.func, ast.Name):
                                call_info["name"] = child.func.id
                            elif isinstance(child.func, ast.Attribute):
                                # e.g., self.method() or module.func()
                                if isinstance(child.func.value, ast.Name):
                                    call_info["name"] = f"{child.func.value.id}.{child.func.attr}"
                                else:
                                    call_info["name"] = child.func.attr

                            if "name" in call_info:
                                calls.append(call_info)

        # Deduplicate by name, keep first occurrence
        seen = set()
        unique_calls = []
        for call in calls:
            if call["name"] not in seen:
                seen.add(call["name"])
                unique_calls.append(call)

        return {
            "function": function_name,
            "file": str(file_path),
            "calls": unique_calls,
        }
    except Exception as e:
        return {"error": str(e), "function": function_name}


async def find_usages(symbol: str, repo_path: str) -> dict[str, Any]:
    """
    Find usages of a symbol in the codebase.

    Args:
        symbol: Symbol to search for (class name, function name, etc.)
        repo_path: Repository path to search in

    Returns:
        Dict with files and line numbers where the symbol is used
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "grep", "-Frn", "--include=*.py", symbol, repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return {"error": "grep timed out", "symbol": symbol}

        usages = []
        production_usages = []
        test_usages = []
        for line in stdout.decode().strip().split("\n"):
            if line and ":" in line:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    rel_file = parts[0].replace(repo_path + "/", "")
                    entry = {
                        "file": rel_file,
                        "line": int(parts[1]),
                        "text": parts[2].strip()[:100],
                    }
                    usages.append(entry)
                    if is_test_file(rel_file):
                        test_usages.append(entry)
                    else:
                        production_usages.append(entry)

        return {
            "symbol": symbol,
            "usages": usages[:30],  # Limit results (backward compat)
            "production_usages": production_usages[:30],
            "test_usages": test_usages[:30],
            "production_count": len(production_usages),
            "test_count": len(test_usages),
            "total_count": len(usages),
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def _extract_enclosing_function(
    file_lines: list[str], match_line: int, context_lines: int = 3
) -> dict[str, Any]:
    """Find the enclosing function/method for a given line number.

    Walks backwards from match_line to find the nearest ``def`` or ``class``
    statement. Returns the enclosing scope name and surrounding context.

    Args:
        file_lines: All lines of the file (0-indexed list).
        match_line: 1-based line number of the match.
        context_lines: Number of lines to show before and after the match.

    Returns:
        Dict with ``context_function`` (name or None) and ``context_snippet``
        (surrounding lines as a string).
    """
    import re

    idx = match_line - 1  # convert to 0-based
    if idx < 0 or idx >= len(file_lines):
        return {"context_function": None, "context_snippet": ""}

    # Walk backwards to find enclosing def/class
    func_name = None
    for i in range(idx - 1, -1, -1):
        stripped = file_lines[i].lstrip()
        m = re.match(r"(?:async\s+)?def\s+(\w+)", stripped)
        if m:
            func_name = m.group(1)
            break
        m = re.match(r"class\s+(\w+)", stripped)
        if m:
            func_name = f"{m.group(1)} (class)"
            break

    # Extract surrounding context
    start = max(0, idx - context_lines)
    end = min(len(file_lines), idx + context_lines + 1)
    snippet_lines = []
    for i in range(start, end):
        prefix = ">> " if i == idx else "   "
        snippet_lines.append(f"{prefix}{i + 1}: {file_lines[i]}")

    return {
        "context_function": func_name,
        "context_snippet": "\n".join(snippet_lines),
    }


async def find_callers(
    symbol: str, repo_path: str, include_context: bool = True
) -> dict[str, Any]:
    """Find callers of a symbol with production/test separation and calling context.

    Combines usage finding with caller classification and enclosing function
    extraction. Purpose-built for integration verification during code review.

    Args:
        symbol: Function, method, or class name to search for.
        repo_path: Repository path to search in.
        include_context: Whether to extract enclosing function context for each
            call site. Defaults to True.

    Returns:
        Dict with ``production_callers`` and ``test_callers`` lists, each entry
        containing file, line, text, and optionally context_function and
        context_snippet fields. Also includes counts and the symbol searched.

    Example::

        >>> import asyncio
        >>> result = asyncio.run(find_callers("handle_request", "/path/to/repo"))
        >>> for caller in result["production_callers"]:
        ...     print(caller["context_function"], caller["file"], caller["line"])
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "grep", "-Frn", "--include=*.py", symbol, repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return {"error": "grep timed out", "symbol": symbol}

        production_callers: list[dict[str, Any]] = []
        test_callers: list[dict[str, Any]] = []
        file_cache: dict[str, list[str]] = {}
        context_count = 0
        max_context = 20

        for line in stdout.decode().strip().split("\n"):
            if not line or ":" not in line:
                continue
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue

            rel_file = parts[0].replace(repo_path + "/", "")
            line_no = int(parts[1])
            text = parts[2].strip()[:100]

            entry: dict[str, Any] = {
                "file": rel_file,
                "line": line_no,
                "text": text,
            }

            # Add calling context if requested (up to max_context entries)
            if include_context and context_count < max_context:
                abs_path = parts[0]
                if abs_path not in file_cache:
                    try:
                        file_content = Path(abs_path).read_text()
                        file_lines = file_content.splitlines()
                        # Skip very large files
                        if len(file_lines) <= 5000:
                            file_cache[abs_path] = file_lines
                    except Exception:
                        pass

                if abs_path in file_cache:
                    ctx = _extract_enclosing_function(
                        file_cache[abs_path], line_no
                    )
                    entry["context_function"] = ctx["context_function"]
                    entry["context_snippet"] = ctx["context_snippet"]
                    context_count += 1

            if is_test_file(rel_file):
                test_callers.append(entry)
            else:
                production_callers.append(entry)

        return {
            "symbol": symbol,
            "production_callers": production_callers[:30],
            "test_callers": test_callers[:30],
            "production_count": len(production_callers),
            "test_count": len(test_callers),
            "total_count": len(production_callers) + len(test_callers),
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


async def git_blame(file_path: str, start_line: int, end_line: int, repo_path: str) -> dict[str, Any]:
    """
    Get git blame information for a range of lines.

    Args:
        file_path: Path to the file (relative to repo)
        start_line: Starting line number
        end_line: Ending line number
        repo_path: Repository path

    Returns:
        Dict with blame information per line
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", repo_path, "blame", "-L", f"{start_line},{end_line}", "--porcelain", file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return {"error": "git blame timed out", "file": file_path}

        if proc.returncode != 0:
            return {"error": stderr.decode(), "file": file_path}

        # Parse porcelain format
        lines = stdout.decode().strip().split("\n")
        blame_info = []
        current_commit = None
        current_author = None

        for line in lines:
            if line.startswith("author "):
                current_author = line[7:]
            elif line.startswith("\t"):
                if current_commit and current_author:
                    blame_info.append({
                        "commit": current_commit[:8],
                        "author": current_author,
                        "code": line[1:][:60],
                    })
            elif " " in line and len(line.split()[0]) == 40:
                current_commit = line.split()[0]

        return {
            "file": file_path,
            "range": f"{start_line}-{end_line}",
            "blame": blame_info,
        }
    except Exception as e:
        return {"error": str(e), "file": file_path}


async def test_coverage(file_path: str, repo_path: str) -> dict[str, Any]:
    """
    Find tests that cover a given file.

    Uses coverage-map.json if available (precise coverage data),
    otherwise falls back to naming conventions.

    Args:
        file_path: Path to the source file
        repo_path: Repository path

    Returns:
        Dict with test files/functions that cover this module
    """
    try:
        # Try coverage-map.json first for precise data
        coverage_map_result = await coverage_map_tests(file_path, repo_path)
        if "error" not in coverage_map_result and coverage_map_result.get("tests"):
            return coverage_map_result

        # Fall back to naming conventions
        path = Path(file_path)
        module_name = path.stem  # e.g., "client" from "client.py"

        # Search for test files
        test_patterns = [
            f"test_{module_name}.py",
            f"{module_name}_test.py",
            f"test_{module_name}*.py",
        ]

        tests_found = []
        for pattern in test_patterns:
            proc = await asyncio.create_subprocess_exec(
                "find", repo_path, "-name", pattern, "-path", "*/tests/*",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                continue
            for test_file in stdout.decode().strip().split("\n"):
                if test_file:
                    tests_found.append(test_file.replace(repo_path + "/", ""))

        return {
            "source_file": file_path,
            "test_files": list(set(tests_found)),
            "method": "naming_convention",
        }
    except Exception as e:
        return {"error": str(e), "file": file_path}


async def coverage_map_tests(file_path: str, repo_path: str) -> dict[str, Any]:
    """
    Find tests that cover a file using coverage-map.json.

    Args:
        file_path: Path to the source file (relative or absolute)
        repo_path: Repository path where coverage-map.json is located

    Returns:
        Dict with tests that cover the file (from coverage-map data)
    """
    import json

    try:
        coverage_map_path = Path(repo_path) / "coverage-map.json"
        if not coverage_map_path.exists():
            return {"error": "coverage-map.json not found", "file": file_path}

        with open(coverage_map_path) as f:
            data = json.load(f)

        file_to_tests = data.get("file_to_tests", {})

        # Normalize file path for matching
        # Strip leading repo path if present
        normalized_path = file_path
        if repo_path and file_path.startswith(repo_path):
            normalized_path = file_path[len(repo_path):].lstrip("/")

        # Try exact match first
        tests = file_to_tests.get(normalized_path, [])

        # If not found, try partial match
        if not tests:
            for mapped_file, mapped_tests in file_to_tests.items():
                if normalized_path in mapped_file or mapped_file.endswith(normalized_path):
                    tests = mapped_tests
                    normalized_path = mapped_file
                    break

        return {
            "source_file": normalized_path,
            "tests": tests,
            "test_count": len(tests),
            "method": "coverage_map",
        }
    except Exception as e:
        return {"error": str(e), "file": file_path}


async def coverage_map_files(test_pattern: str, repo_path: str) -> dict[str, Any]:
    """
    Find files covered by tests matching a pattern using coverage-map.json.

    Args:
        test_pattern: Test name or pattern to match
        repo_path: Repository path where coverage-map.json is located

    Returns:
        Dict with files covered by matching tests
    """
    import json

    try:
        coverage_map_path = Path(repo_path) / "coverage-map.json"
        if not coverage_map_path.exists():
            return {"error": "coverage-map.json not found", "pattern": test_pattern}

        with open(coverage_map_path) as f:
            data = json.load(f)

        test_to_files = data.get("test_to_files", {})

        # Find all matching tests and aggregate files
        matched_tests = []
        all_files: set[str] = set()

        for test_name, test_files in test_to_files.items():
            if test_pattern in test_name:
                matched_tests.append(test_name)
                all_files.update(test_files)

        return {
            "pattern": test_pattern,
            "matched_tests": len(matched_tests),
            "files": sorted(all_files),
            "file_count": len(all_files),
            "method": "coverage_map",
        }
    except Exception as e:
        return {"error": str(e), "pattern": test_pattern}


async def function_body(
    file_path: str,
    line_hint: int | None = None,
    function_name: str | None = None,
    repo_path: str | None = None,
) -> dict[str, Any]:
    """
    Extract the full source of a function or method from a Python file.

    Finds the function either by name or by a line number that falls within it.
    For methods, includes the enclosing class name for context.

    Args:
        file_path: Path to the Python file (relative to repo_path or absolute).
        line_hint: A line number inside the target function. The innermost
            function/method containing this line is returned.
        function_name: Name of the function to find (searched depth-first).
            Ignored when *line_hint* is provided.
        repo_path: Base path for resolving relative *file_path* values.

    Returns:
        Dict with keys ``function``, ``file``, ``start_line``, ``end_line``,
        ``class_name`` (if a method), and ``source`` (full function text).
        On error, returns a dict with an ``error`` key.

    Example::

        >>> import asyncio
        >>> result = asyncio.run(function_body("cli.py", function_name="review_loop"))
        >>> result["start_line"]
        945
    """
    if line_hint is None and function_name is None:
        return {"error": "Provide either line_hint or function_name", "file": file_path}

    try:
        if repo_path and not Path(file_path).is_absolute():
            full_path = Path(repo_path) / file_path
        else:
            full_path = Path(file_path)

        source = full_path.read_text()
        tree = ast.parse(source)
        source_lines = source.splitlines()

        # Collect all function/method nodes with their class context
        functions: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str | None]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        functions.append((child, node.name))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Top-level functions (avoid double-adding methods)
                if not any(fn is node for fn, _ in functions):
                    functions.append((node, None))

        target: ast.FunctionDef | ast.AsyncFunctionDef | None = None
        class_name: str | None = None

        if line_hint is not None:
            # Find the innermost function containing this line
            best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
            best_class: str | None = None
            for fn, cls in functions:
                if fn.lineno <= line_hint <= (fn.end_lineno or fn.lineno):
                    if best is None or fn.lineno >= best.lineno:
                        best = fn
                        best_class = cls
            target = best
            class_name = best_class
        else:
            # Find by name (first match, depth-first)
            for fn, cls in functions:
                if fn.name == function_name:
                    target = fn
                    class_name = cls
                    break

        if target is None:
            search = f"line {line_hint}" if line_hint else f"'{function_name}'"
            return {
                "error": f"No function found at {search}",
                "file": str(file_path),
            }

        start = target.lineno
        # Include decorators (e.g. @property, @retry) — they affect behavior
        if target.decorator_list:
            start = target.decorator_list[0].lineno
        end = target.end_lineno or start
        # Cap very large functions to avoid blowing up context
        max_lines = 200
        body_lines = source_lines[start - 1 : end]
        truncated = len(body_lines) > max_lines
        if truncated:
            body_lines = body_lines[:max_lines]

        result: dict[str, Any] = {
            "function": target.name,
            "file": str(file_path),
            "start_line": start,
            "end_line": end,
            "source": "\n".join(body_lines),
        }
        if class_name:
            result["class_name"] = class_name
        if truncated:
            result["truncated"] = True
            result["total_lines"] = end - start + 1
        return result

    except SyntaxError as e:
        return {"error": f"Syntax error parsing {file_path}: {e}", "file": str(file_path)}
    except Exception as e:
        return {"error": str(e), "file": str(file_path)}


async def gather_function_context(
    diff_content: str,
    repo_path: str,
) -> dict[str, Any]:
    """
    Automatically find all modified Python functions and return their full bodies.

    Combines diff parsing (to find touched line ranges) with AST-based function
    extraction.  This is the main entry point for the "show full function bodies"
    feature — it requires no reviewer action.

    Args:
        diff_content: Unified diff output.
        repo_path: Repository root so relative paths can be resolved.

    Returns:
        Dict mapping descriptive keys (``"file.py:function_name"``) to
        ``function_body()`` results.
    """
    from .git_utils import extract_modified_line_ranges

    ranges = extract_modified_line_ranges(diff_content)
    results: dict[str, Any] = {}
    tasks: list[tuple[str, asyncio.Task]] = []

    for file_path, line_ranges in ranges.items():
        if not file_path.endswith(".py"):
            continue

        full_path = Path(repo_path) / file_path
        if not full_path.exists():
            continue

        # For each touched range, find the enclosing function
        seen_functions: set[str] = set()
        for start, end in line_ranges:
            # Use the midpoint as a hint — function_body finds the innermost
            # function containing the line
            for line in (start, end):
                key = f"{file_path}:{line}"
                if key not in seen_functions:
                    seen_functions.add(key)
                    tasks.append((
                        file_path,
                        asyncio.ensure_future(
                            function_body(file_path, line_hint=line, repo_path=repo_path)
                        ),
                    ))

    if not tasks:
        return results

    awaited = await asyncio.gather(*(t for _, t in tasks), return_exceptions=True)

    # Deduplicate by (file, function_name, start_line)
    seen: set[tuple[str, str, int]] = set()
    for (file_path, _), result in zip(tasks, awaited):
        if isinstance(result, Exception):
            continue
        if "error" in result:
            continue
        fn_name = result.get("function", "")
        start = result.get("start_line", 0)
        dedup_key = (file_path, fn_name, start)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        class_prefix = f"{result['class_name']}." if result.get("class_name") else ""
        obs_key = f"{file_path}:{class_prefix}{fn_name}"
        results[obs_key] = result

    return results


def _extract_modified_symbols(diff_content: str, repo_path: str) -> dict[str, set[str]]:
    """Extract function/class names modified in each file from a diff.

    Parses the diff to find modified line ranges, then uses AST to identify
    which functions or classes those lines belong to.

    Args:
        diff_content: Unified diff output.
        repo_path: Repository root for resolving file paths.

    Returns:
        Dict mapping file paths to sets of modified symbol names
        (e.g. ``{"src/proxy.py": {"handle_request", "ProxyClient"}}``).
    """
    from .git_utils import extract_modified_line_ranges

    ranges = extract_modified_line_ranges(diff_content)
    result: dict[str, set[str]] = {}

    for file_path, line_ranges in ranges.items():
        if not file_path.endswith(".py"):
            continue
        full_path = Path(repo_path) / file_path
        if not full_path.exists():
            continue

        try:
            source = full_path.read_text()
            tree = ast.parse(source)
        except Exception:
            continue

        symbols: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                node_start = node.lineno
                node_end = node.end_lineno or node_start
                for start, end in line_ranges:
                    if node_start <= end and node_end >= start:
                        symbols.add(node.name)
                        break

        if symbols:
            result[file_path] = symbols

    return result


async def _find_test_files_by_naming(module_name: str, repo_path: str) -> list[str]:
    """Find test files by naming convention.

    Searches common test directory patterns for files matching
    ``test_{module}.py`` or ``{module}_test.py``.

    Args:
        module_name: Module stem (e.g. "proxy" from "proxy.py").
        repo_path: Repository root path.

    Returns:
        List of relative file paths to discovered test files.
    """
    test_patterns = [
        f"test_{module_name}.py",
        f"{module_name}_test.py",
    ]
    search_dirs = ["tests", "test", "tests/unit", "tests/integration", "."]

    found: list[str] = []
    repo = Path(repo_path)

    for search_dir in search_dirs:
        dir_path = repo / search_dir
        if not dir_path.is_dir():
            continue
        for pattern in test_patterns:
            for match in dir_path.glob(pattern):
                rel = str(match.relative_to(repo))
                if rel not in found:
                    found.append(rel)

    return found


async def _find_test_files_by_imports(
    module_name: str, source_file: str, repo_path: str
) -> list[str]:
    """Find test files that import from a given module.

    Searches for ``from {module} import`` or ``import {module}`` patterns
    in Python test files across the repository.

    Args:
        module_name: Module stem to search for in import statements.
        source_file: Dotted module path (e.g. "src.auth.client").
        repo_path: Repository root path.

    Returns:
        List of relative file paths that import from the module.
    """
    # Build patterns to search for
    patterns = [
        f"from {module_name} import",
        f"import {module_name}",
    ]
    # Also try dotted module path from source file
    dotted = source_file.replace("/", ".").replace(".py", "")
    if dotted != module_name:
        patterns.append(f"from {dotted} import")
        patterns.append(f"import {dotted}")

    # Also try partial dotted paths (e.g. "auth.client" from "src/auth/client.py")
    parts = dotted.split(".")
    if len(parts) > 1:
        for i in range(len(parts) - 1):
            partial = ".".join(parts[i:])
            patterns.append(f"from {partial} import")

    # Search test files using grep
    grep_pattern = "|".join(patterns)
    try:
        proc = await asyncio.create_subprocess_exec(
            "grep", "-rl", "--include=*.py", "-E", grep_pattern, repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return []

        found: list[str] = []
        for line in stdout.decode().strip().split("\n"):
            if not line:
                continue
            rel = line.replace(repo_path + "/", "").replace(repo_path, "")
            rel = rel.lstrip("/")
            # Only include test files
            basename = Path(rel).name
            if basename.startswith("test_") or basename.endswith("_test.py"):
                if rel not in found:
                    found.append(rel)
        return found
    except Exception:
        return []


async def gather_related_test_files(
    diff_content: str,
    repo_path: str,
    max_lines_per_file: int = 500,
    max_total_lines: int = 2000,
) -> dict[str, Any]:
    """Auto-discover test files related to modified code and return their content.

    Given a diff, finds test files that cover modified source files using three
    strategies:
    1. **Naming convention**: ``test_{module}.py`` / ``{module}_test.py`` in
       common locations (``tests/``, ``test/``, same directory).
    2. **Coverage map**: Precise test mapping from ``coverage-map.json`` if available.
    3. **Import scanning**: Grep for ``from {module} import`` across test files.

    Reads full test file content (capped) and filters to tests that reference
    actually-modified symbols. Also flags duplicate coverage when multiple test
    files cover the same source file.

    Args:
        diff_content: Unified diff output.
        repo_path: Repository root path.
        max_lines_per_file: Maximum lines to include per test file (default 500).
        max_total_lines: Maximum total lines across all test files (default 2000).

    Returns:
        Dict with ``test_files`` (list of file info dicts), ``modified_symbols``
        (symbols found in the diff), ``duplicate_coverage`` (files covered by
        multiple test files), and summary statistics.

    Example::

        >>> result = asyncio.run(gather_related_test_files(diff, "/path/to/repo"))
        >>> for tf in result["test_files"]:
        ...     print(tf["path"], tf["symbols_referenced"])
    """
    from .git_utils import extract_changed_files

    changed_files = extract_changed_files(diff_content)
    source_files = [
        f for f in changed_files
        if f.endswith(".py")
        and not Path(f).name.startswith("test_")
        and not f.endswith("_test.py")
    ]

    if not source_files:
        return {"test_files": [], "modified_symbols": {}, "message": "No non-test Python files modified"}

    # Extract modified symbols from the diff
    modified_symbols = _extract_modified_symbols(diff_content, repo_path)

    # Discover test files for each source file (parallel)
    all_test_files: dict[str, list[str]] = {}  # test_path -> list of source files it covers
    source_to_tests: dict[str, list[str]] = {}

    discovery_tasks = []
    for source_file in source_files:
        module_name = Path(source_file).stem
        discovery_tasks.append((source_file, module_name))

    for source_file, module_name in discovery_tasks:
        tests: set[str] = set()

        # Strategy 1: Naming convention
        naming_results = await _find_test_files_by_naming(module_name, repo_path)
        tests.update(naming_results)

        # Strategy 2: Coverage map
        try:
            cov_result = await coverage_map_tests(source_file, repo_path)
            if "error" not in cov_result and cov_result.get("tests"):
                for test_entry in cov_result["tests"]:
                    # coverage_map_tests returns test names, extract file paths
                    if isinstance(test_entry, str) and "::" in test_entry:
                        test_file = test_entry.split("::")[0]
                        tests.add(test_file)
                    elif isinstance(test_entry, str) and test_entry.endswith(".py"):
                        tests.add(test_entry)
        except Exception:
            pass

        # Strategy 3: Import scanning
        import_results = await _find_test_files_by_imports(module_name, source_file, repo_path)
        tests.update(import_results)

        # Record mappings
        source_to_tests[source_file] = sorted(tests)
        for test_path in tests:
            all_test_files.setdefault(test_path, []).append(source_file)

    # Read test file content, filtering by symbol relevance
    test_file_results: list[dict[str, Any]] = []
    total_lines = 0

    # Get all modified symbol names for quick lookup
    all_symbols: set[str] = set()
    for symbols in modified_symbols.values():
        all_symbols.update(symbols)

    for test_path in sorted(all_test_files.keys()):
        if total_lines >= max_total_lines:
            break

        full_path = Path(repo_path) / test_path
        if not full_path.exists():
            continue

        try:
            content = full_path.read_text()
        except Exception:
            continue

        lines = content.splitlines()
        line_count = len(lines)

        # Check which modified symbols this test file references
        symbols_referenced: list[str] = []
        if all_symbols:
            for symbol in all_symbols:
                if symbol in content:
                    symbols_referenced.append(symbol)

        # Truncate if needed
        truncated = False
        remaining = max_total_lines - total_lines
        cap = min(max_lines_per_file, remaining)
        if line_count > cap:
            lines = lines[:cap]
            truncated = True

        total_lines += len(lines)

        test_file_results.append({
            "path": test_path,
            "covers": all_test_files[test_path],
            "symbols_referenced": sorted(symbols_referenced),
            "line_count": line_count,
            "truncated": truncated,
            "content": "\n".join(lines),
        })

    # Detect duplicate coverage
    duplicate_coverage: dict[str, list[str]] = {}
    for source_file, tests in source_to_tests.items():
        if len(tests) > 1:
            duplicate_coverage[source_file] = tests

    return {
        "test_files": test_file_results,
        "test_file_count": len(test_file_results),
        "modified_symbols": {k: sorted(v) for k, v in modified_symbols.items()},
        "source_to_tests": source_to_tests,
        "duplicate_coverage": duplicate_coverage,
        "total_lines_included": total_lines,
    }


async def run_tests_for_files(
    changed_files: list[str],
    repo_path: str,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run pytest on tests related to changed source files.

    Discovers relevant tests via coverage-map (preferred) or naming conventions,
    then runs them and returns structured pass/fail results.

    Args:
        changed_files: List of changed Python source file paths (relative to repo).
        repo_path: Repository root path.
        timeout: Maximum seconds to allow pytest to run (default 120).

    Returns:
        Dict with pass/fail counts, status, output, test list, and duration.

    Example::

        >>> result = asyncio.run(run_tests_for_files(["src/foo.py"], "/repo"))
        >>> print(result["status"])  # "PASSED", "FAILED", "ERROR", or "TIMEOUT"
        >>> print(result["passed"], result["failed"])
    """
    import subprocess
    import time

    # Filter to non-test Python files
    source_files = [
        f for f in changed_files
        if f.endswith(".py")
        and not Path(f).name.startswith("test_")
        and not f.endswith("_test.py")
    ]

    if not source_files:
        return {
            "passed": 0, "failed": 0, "errors": 0, "total": 0,
            "status": "SKIPPED",
            "output": "No non-test Python source files to test",
            "tests_run": [],
            "duration_seconds": 0.0,
        }

    # Collect relevant test files from all sources
    all_tests: set[str] = set()

    for source_file in source_files:
        module_name = Path(source_file).stem

        # Strategy 1: Coverage map (most accurate)
        try:
            cov_result = await coverage_map_tests(source_file, repo_path)
            if "error" not in cov_result and cov_result.get("tests"):
                for test_entry in cov_result["tests"]:
                    if isinstance(test_entry, str) and "::" in test_entry:
                        test_file = test_entry.split("::")[0]
                        all_tests.add(test_file)
                    elif isinstance(test_entry, str) and test_entry.endswith(".py"):
                        all_tests.add(test_entry)
        except Exception:
            pass

        # Strategy 2: Naming convention fallback
        try:
            naming_results = await _find_test_files_by_naming(module_name, repo_path)
            all_tests.update(naming_results)
        except Exception:
            pass

    if not all_tests:
        return {
            "passed": 0, "failed": 0, "errors": 0, "total": 0,
            "status": "SKIPPED",
            "output": "No related test files found",
            "tests_run": [],
            "duration_seconds": 0.0,
        }

    # Verify test files exist
    test_files = sorted(
        t for t in all_tests if (Path(repo_path) / t).exists()
    )

    if not test_files:
        return {
            "passed": 0, "failed": 0, "errors": 0, "total": 0,
            "status": "SKIPPED",
            "output": "Related test files not found on disk",
            "tests_run": [],
            "duration_seconds": 0.0,
        }

    # Run pytest
    cmd = [
        sys.executable, "-m", "pytest",
        *test_files,
        "--tb=short", "--no-header", "-q",
    ]

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=timeout,
        )
        duration = time.monotonic() - start
        output = proc.stdout + proc.stderr

        # Parse pytest summary line: "X passed, Y failed, Z errors"
        passed = failed = errors = 0
        for line in output.splitlines():
            line_lower = line.lower()
            if "passed" in line_lower or "failed" in line_lower or "error" in line_lower:
                import re
                for match in re.finditer(r"(\d+) (passed|failed|error)", line_lower):
                    count = int(match.group(1))
                    kind = match.group(2)
                    if kind == "passed":
                        passed = count
                    elif kind == "failed":
                        failed = count
                    elif kind == "error":
                        errors = count

        total = passed + failed + errors
        if failed > 0 or errors > 0:
            status = "FAILED"
            # Truncate output to last 200 lines on failure
            lines = output.splitlines()
            if len(lines) > 200:
                output = "\n".join(["... (truncated)"] + lines[-200:])
        else:
            status = "PASSED" if proc.returncode == 0 else "ERROR"

        return {
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "total": total,
            "status": status,
            "output": output,
            "tests_run": test_files,
            "duration_seconds": round(duration, 2),
        }

    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return {
            "passed": 0, "failed": 0, "errors": 0, "total": 0,
            "status": "TIMEOUT",
            "output": f"pytest timed out after {timeout}s",
            "tests_run": test_files,
            "duration_seconds": round(duration, 2),
        }
    except FileNotFoundError:
        return {
            "passed": 0, "failed": 0, "errors": 0, "total": 0,
            "status": "ERROR",
            "output": "pytest not found — is it installed in the target environment?",
            "tests_run": test_files,
            "duration_seconds": 0.0,
        }


def format_test_results(results: dict[str, Any]) -> str:
    """Format test execution results for inclusion in review context.

    Args:
        results: Output from ``run_tests_for_files()``.

    Returns:
        Markdown-formatted string summarizing test results.

    Example::

        >>> print(format_test_results({"status": "PASSED", "passed": 5, ...}))
        ## Test Execution Results
        Status: PASSED (5 passed, 0 failed, 0 errors) in 1.2s
    """
    status = results.get("status", "UNKNOWN")
    passed = results.get("passed", 0)
    failed = results.get("failed", 0)
    errors = results.get("errors", 0)
    duration = results.get("duration_seconds", 0)
    tests_run = results.get("tests_run", [])

    lines = [
        "## Test Execution Results",
        f"Status: {status} ({passed} passed, {failed} failed, {errors} errors) in {duration}s",
    ]

    if tests_run:
        lines.append(f"Tests run for: {', '.join(tests_run)}")

    if status in ("FAILED", "ERROR", "TIMEOUT"):
        output = results.get("output", "")
        if output:
            lines.append("")
            lines.append("### Output:")
            lines.append(f"```\n{output}\n```")

    return "\n".join(lines)


async def related_test_files(file_path: str, repo_path: str) -> dict[str, Any]:
    """Find test files related to a specific source file.

    Observation tool wrapper — discovers test files by naming convention and
    import scanning, then returns their paths and which symbols they reference.

    Args:
        file_path: Source file path (relative to repo_path).
        repo_path: Repository root path.

    Returns:
        Dict with discovered test file paths and reference info.
    """
    module_name = Path(file_path).stem

    tests: set[str] = set()
    tests.update(await _find_test_files_by_naming(module_name, repo_path))
    tests.update(await _find_test_files_by_imports(module_name, file_path, repo_path))

    # Also check coverage map
    try:
        cov_result = await coverage_map_tests(file_path, repo_path)
        if "error" not in cov_result and cov_result.get("tests"):
            for test_entry in cov_result["tests"]:
                if isinstance(test_entry, str) and "::" in test_entry:
                    tests.add(test_entry.split("::")[0])
                elif isinstance(test_entry, str) and test_entry.endswith(".py"):
                    tests.add(test_entry)
    except Exception:
        pass

    test_info: list[dict[str, Any]] = []
    for test_path in sorted(tests):
        full = Path(repo_path) / test_path
        info: dict[str, Any] = {"path": test_path, "exists": full.exists()}
        if full.exists():
            try:
                info["line_count"] = len(full.read_text().splitlines())
            except Exception:
                pass
        test_info.append(info)

    return {
        "source_file": file_path,
        "test_files": test_info,
        "test_count": len(test_info),
    }


async def file_imports(file_path: str, repo_path: str | None = None) -> dict[str, Any]:
    """
    Extract import statements from a Python file.

    Args:
        file_path: Path to the Python file
        repo_path: Base path for relative file paths

    Returns:
        Dict with imports, from_imports, and the raw import section text
    """
    try:
        if repo_path and not Path(file_path).is_absolute():
            full_path = Path(repo_path) / file_path
        else:
            full_path = Path(file_path)

        source = full_path.read_text()
        tree = ast.parse(source)

        imports: list[str] = []
        from_imports: list[dict[str, Any]] = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [alias.name for alias in node.names]
                from_imports.append({"module": module, "names": names})

        # Extract raw import section (lines until first non-import/non-comment)
        lines = source.split("\n")
        import_section_lines = []
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            # Skip docstrings
            if '"""' in stripped or "'''" in stripped:
                in_docstring = not in_docstring
                import_section_lines.append(line)
                continue
            if in_docstring:
                import_section_lines.append(line)
                continue
            # Include imports, comments, blank lines, __future__
            if (stripped.startswith("import ") or
                stripped.startswith("from ") or
                stripped.startswith("#") or
                stripped == "" or
                stripped.startswith("__")):
                import_section_lines.append(line)
            else:
                break

        return {
            "file": str(file_path),
            "imports": imports,
            "from_imports": from_imports,
            "import_section": "\n".join(import_section_lines),
        }
    except Exception as e:
        return {"error": str(e), "file": file_path}


async def project_dependencies(repo_path: str) -> dict[str, Any]:
    """
    Get project dependencies from pyproject.toml or requirements.txt.

    Args:
        repo_path: Repository path

    Returns:
        Dict with dependencies from pyproject.toml and/or requirements.txt
    """
    try:
        result: dict[str, Any] = {"repo": repo_path}

        # Check pyproject.toml
        pyproject_path = Path(repo_path) / "pyproject.toml"
        if pyproject_path.exists():
            content = pyproject_path.read_text()
            result["pyproject_toml"] = content

            # Try to parse dependencies section
            try:
                import tomllib
                data = tomllib.loads(content)
                deps = data.get("project", {}).get("dependencies", [])
                optional_deps = data.get("project", {}).get("optional-dependencies", {})
                result["dependencies"] = deps
                result["optional_dependencies"] = optional_deps
            except Exception:
                # tomllib not available or parse error, raw content is still useful
                pass

        # Check requirements.txt
        requirements_path = Path(repo_path) / "requirements.txt"
        if requirements_path.exists():
            content = requirements_path.read_text()
            result["requirements_txt"] = content

        # Check requirements-dev.txt
        requirements_dev_path = Path(repo_path) / "requirements-dev.txt"
        if requirements_dev_path.exists():
            content = requirements_dev_path.read_text()
            result["requirements_dev_txt"] = content

        if "pyproject_toml" not in result and "requirements_txt" not in result:
            result["error"] = "No pyproject.toml or requirements.txt found"

        return result
    except Exception as e:
        return {"error": str(e), "repo": repo_path}


# Registry of all observation tools
OBSERVATION_TOOLS: dict[str, Any] = {
    "exception_hierarchy": exception_hierarchy,
    "raises_analysis": raises_analysis,
    "call_graph": call_graph,
    "find_usages": find_usages,
    "find_callers": find_callers,
    "git_blame": git_blame,
    "test_coverage": test_coverage,
    "coverage_map_tests": coverage_map_tests,
    "coverage_map_files": coverage_map_files,
    "function_body": function_body,
    "file_imports": file_imports,
    "project_dependencies": project_dependencies,
    "related_test_files": related_test_files,
}


async def run_observation(name: str, tool: str, params: dict[str, Any], repo_path: str) -> dict[str, Any]:
    """
    Run a single observation tool.

    Args:
        name: Name to assign to the result
        tool: Tool name from OBSERVATION_TOOLS
        params: Parameters for the tool
        repo_path: Repository path

    Returns:
        Dict with the observation result
    """
    import inspect

    if tool not in OBSERVATION_TOOLS:
        return {"name": name, "result": {"error": f"Unknown tool: {tool}"}}

    tool_func = OBSERVATION_TOOLS[tool]

    # Get valid parameters for this tool
    sig = inspect.signature(tool_func)
    valid_params = set(sig.parameters.keys())

    # Filter params to only valid ones
    filtered_params = {k: v for k, v in params.items() if k in valid_params}

    # Inject repo_path if the tool accepts it
    if "repo_path" in valid_params and "repo_path" not in filtered_params:
        filtered_params["repo_path"] = repo_path

    try:
        result = await tool_func(**filtered_params)
    except TypeError as e:
        result = {"error": f"Parameter error: {e}", "params_received": list(params.keys())}

    return {"name": name, "tool": tool, "result": result}


async def run_observations(observations: list[dict[str, Any]], repo_path: str) -> dict[str, Any]:
    """
    Run multiple observations in parallel and collect results.

    Args:
        observations: List of observation requests
        repo_path: Repository path

    Returns:
        Dict mapping observation names to results
    """
    results = {}
    tasks = []
    task_names = []

    for obs in observations:
        name = obs.get("name", "unnamed")
        tool = obs.get("tool")
        params = obs.get("params", {})

        if not tool:
            results[name] = {"error": "No tool specified"}
            continue

        tasks.append(run_observation(name, tool, params, repo_path))
        task_names.append(name)

    if tasks:
        task_results = await asyncio.gather(*tasks, return_exceptions=True)
        for name, result in zip(task_names, task_results):
            if isinstance(result, Exception):
                results[name] = {"error": str(result)}
            else:
                results[name] = result["result"]

    return results
