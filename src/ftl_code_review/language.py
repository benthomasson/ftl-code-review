"""Language profiles for multi-language code review."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LanguageProfile:
    name: str
    source_globs: list[str]
    source_extensions: list[str]
    fence_language: str
    import_line_prefixes: list[str]
    scope_style: str  # "indent" or "brace"
    test_globs: list[str]
    config_files: list[str]
    primary_extension: str
    has_ast_support: bool = False
    decorator_prefix: str | None = None
    dependency_files: list[str] = field(default_factory=list)
    test_file_patterns: list[str] = field(default_factory=list)

    def matches_extension(self, file_path: str) -> bool:
        return any(file_path.endswith(ext) for ext in self.source_extensions)

    def is_test_file(self, file_path: str) -> bool:
        from pathlib import PurePosixPath
        basename = PurePosixPath(file_path).name

        for pattern in self.test_file_patterns:
            if pattern.startswith("*") and basename.endswith(pattern[1:]):
                return True
            if pattern.endswith("*") and basename.startswith(pattern[:-1]):
                return True
            if basename == pattern:
                return True

        # Also check path components
        test_dirs = {"tests", "test", "testing", "__tests__", "__test__"}
        for part in PurePosixPath(file_path).parts:
            if part in test_dirs:
                return True

        return False

    def grep_include_args(self) -> list[str]:
        return [f"--include={g}" for g in self.source_globs]

    def test_file_search_patterns(self, module_name: str) -> list[str]:
        results = []
        for ext in self.source_extensions[:1]:
            results.append(f"test_{module_name}{ext}")
            results.append(f"{module_name}_test{ext}")
            results.append(f"{module_name}.test{ext}")
            results.append(f"{module_name}.spec{ext}")
        return results


PYTHON = LanguageProfile(
    name="python",
    source_globs=["*.py"],
    source_extensions=[".py"],
    fence_language="python",
    import_line_prefixes=["import ", "from "],
    scope_style="indent",
    test_globs=["test_*.py", "*_test.py"],
    config_files=["pyproject.toml", "setup.py", "setup.cfg"],
    primary_extension=".py",
    has_ast_support=True,
    decorator_prefix="@",
    dependency_files=["pyproject.toml", "requirements.txt", "requirements-dev.txt"],
    test_file_patterns=["test_*", "*_test.py", "conftest.py"],
)

JAVASCRIPT = LanguageProfile(
    name="javascript",
    source_globs=["*.js", "*.jsx", "*.mjs", "*.cjs"],
    source_extensions=[".js", ".jsx", ".mjs", ".cjs"],
    fence_language="javascript",
    import_line_prefixes=["import ", "require(", "const ", "import("],
    scope_style="brace",
    test_globs=["*.test.js", "*.spec.js", "*.test.jsx", "*.spec.jsx"],
    config_files=["package.json"],
    primary_extension=".js",
    dependency_files=["package.json"],
    test_file_patterns=["*.test.js", "*.spec.js", "*.test.jsx", "*.spec.jsx", "test_*"],
)

TYPESCRIPT = LanguageProfile(
    name="typescript",
    source_globs=["*.ts", "*.tsx", "*.mts", "*.cts"],
    source_extensions=[".ts", ".tsx", ".mts", ".cts"],
    fence_language="typescript",
    import_line_prefixes=["import ", "require(", "const ", "import("],
    scope_style="brace",
    test_globs=["*.test.ts", "*.spec.ts", "*.test.tsx", "*.spec.tsx"],
    config_files=["tsconfig.json"],
    primary_extension=".ts",
    decorator_prefix="@",
    dependency_files=["package.json"],
    test_file_patterns=["*.test.ts", "*.spec.ts", "*.test.tsx", "*.spec.tsx", "test_*"],
)

CPP = LanguageProfile(
    name="cpp",
    source_globs=["*.cpp", "*.cc", "*.cxx", "*.c", "*.h", "*.hpp", "*.hh", "*.hxx"],
    source_extensions=[".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".hxx"],
    fence_language="cpp",
    import_line_prefixes=["#include"],
    scope_style="brace",
    test_globs=["test_*.cpp", "*_test.cpp", "test_*.cc", "*_test.cc"],
    config_files=["CMakeLists.txt", "meson.build"],
    primary_extension=".cpp",
    dependency_files=["CMakeLists.txt", "conanfile.txt", "vcpkg.json"],
    test_file_patterns=["test_*", "*_test.cpp", "*_test.cc"],
)

RUST = LanguageProfile(
    name="rust",
    source_globs=["*.rs"],
    source_extensions=[".rs"],
    fence_language="rust",
    import_line_prefixes=["use ", "mod "],
    scope_style="brace",
    test_globs=["*_test.rs", "test_*.rs"],
    config_files=["Cargo.toml"],
    primary_extension=".rs",
    decorator_prefix="#[",
    dependency_files=["Cargo.toml"],
    test_file_patterns=["*_test.rs", "test_*"],
)

GO = LanguageProfile(
    name="go",
    source_globs=["*.go"],
    source_extensions=[".go"],
    fence_language="go",
    import_line_prefixes=["import"],
    scope_style="brace",
    test_globs=["*_test.go"],
    config_files=["go.mod"],
    primary_extension=".go",
    dependency_files=["go.mod", "go.sum"],
    test_file_patterns=["*_test.go"],
)

LANGUAGE_REGISTRY: dict[str, LanguageProfile] = {
    "python": PYTHON,
    "javascript": JAVASCRIPT,
    "typescript": TYPESCRIPT,
    "cpp": CPP,
    "rust": RUST,
    "go": GO,
}

_CONFIG_TO_LANGUAGE: dict[str, str] = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "CMakeLists.txt": "cpp",
    "meson.build": "cpp",
    "tsconfig.json": "typescript",
    "package.json": "javascript",
    "Cargo.toml": "rust",
    "go.mod": "go",
}


def detect_language(repo_path: str) -> LanguageProfile:
    """Auto-detect the repo's primary language. Falls back to Python."""
    for config_file, lang_name in _CONFIG_TO_LANGUAGE.items():
        if os.path.isfile(os.path.join(repo_path, config_file)):
            if lang_name in LANGUAGE_REGISTRY:
                return LANGUAGE_REGISTRY[lang_name]

    ext_counts: dict[str, int] = {}
    try:
        for root, _dirs, files in os.walk(repo_path):
            rel_root = os.path.relpath(root, repo_path)
            if any(p.startswith(".") for p in rel_root.split(os.sep)):
                continue
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext:
                    ext_counts[ext] = ext_counts.get(ext, 0) + 1
    except Exception:
        return PYTHON

    best_lang = None
    best_count = 0
    for lang in LANGUAGE_REGISTRY.values():
        count = sum(ext_counts.get(ext, 0) for ext in lang.source_extensions)
        if count > best_count:
            best_count = count
            best_lang = lang

    return best_lang or PYTHON


def filter_source_files(files: list[str], lang: LanguageProfile) -> list[str]:
    return [f for f in files if lang.matches_extension(f)]


def filter_non_test_source_files(files: list[str], lang: LanguageProfile) -> list[str]:
    return [f for f in files if lang.matches_extension(f) and not lang.is_test_file(f)]
