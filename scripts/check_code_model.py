#!/usr/bin/env python3
# =============================================================================
# scripts/check_code_model.py — Structural model compliance checker
# Author: Giggso Inc / Ravi Venugopal
# Purpose: Verifies code follows PatronAI/Marauder Scan build patterns
# =============================================================================
# | Date       | Author | Change                         |
# |------------|--------|--------------------------------|
# | 2026-05-08 | RV     | Initial implementation         |
# =============================================================================

import ast
import re
import sys
import subprocess
from pathlib import Path

CX_ORACLE_RE = re.compile(r"\bimport cx_Oracle\b|from cx_Oracle\b")
RAW_FITZ_RE = re.compile(r"^import fitz\b", re.MULTILINE)
DOTENV_IN_FN_RE = re.compile(r"def\s+\w+[^:]*:\s*\n(?:[^\n]*\n)*?[^\n]*load_dotenv\(\)")
CAMEL_VAR_RE = re.compile(r"^\s{4,}[a-z]+[A-Z]\w*\s*=", re.MULTILINE)
LLM_CALL_RE = re.compile(r"\.chat\.completions\.create|openai\.ChatCompletion")
PREPROCESS_RE = re.compile(r"preprocess_text\s*\(")
TEMP_ZERO_RE = re.compile(r"temperature\s*=\s*0\b")
COUNT_TOKENS_RE = re.compile(r"count_tokens\s*\(")


def get_staged_files() -> list[str]:
    """Return staged Python files via git diff."""
    try:
        r = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, check=True,
        )
        return [f for f in r.stdout.strip().splitlines() if f.endswith(".py")]
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"[model] git diff failed: {e}\n")
        return []


def check_import_order(tree: ast.Module, path: str) -> list[str]:
    """Flag imports that appear after function/class definitions."""
    v: list[str] = []
    saw_def = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            saw_def = True
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and saw_def:
            v.append(
                f"[MODEL FAIL] {path}:{node.lineno} — import after definition — "
                "move all imports to the top of the file"
            )
    return v


def check_llm_conventions(content: str, path: str) -> list[str]:
    """Verify LLM calls follow preprocess → count_tokens → temperature=0 pattern."""
    v: list[str] = []
    if not LLM_CALL_RE.search(content):
        return v
    if not PREPROCESS_RE.search(content):
        v.append(
            f"[MODEL FAIL] {path} — LLM call without preprocess_text() — "
            "strip noise before sending to API"
        )
    if not COUNT_TOKENS_RE.search(content):
        v.append(
            f"[MODEL FAIL] {path} — LLM call without count_tokens() — "
            "log token count to stderr before every API call"
        )
    if not TEMP_ZERO_RE.search(content):
        v.append(
            f"[MODEL FAIL] {path} — LLM call missing temperature=0 — "
            "enforce deterministic output on all API calls"
        )
    return v


def check_file(path: str) -> list[str]:
    """Run all model compliance rules on one file."""
    v: list[str] = []
    try:
        content = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return [f"[MODEL FAIL] {path} — cannot read: {e}"]

    if CX_ORACLE_RE.search(content):
        v.append(
            f"[MODEL FAIL] {path} — cx_Oracle import — "
            "replace with oracledb (CLAUDE.md stack rule)"
        )
    if RAW_FITZ_RE.search(content):
        v.append(
            f"[MODEL FAIL] {path} — bare 'import fitz' — "
            "use 'import pymupdf as fitz' instead"
        )
    if CAMEL_VAR_RE.search(content):
        v.append(
            f"[MODEL FAIL] {path} — camelCase variable detected — "
            "use snake_case for all variables and functions"
        )

    v.extend(check_llm_conventions(content, path))

    try:
        tree = ast.parse(content)
        v.extend(check_import_order(tree, path))
    except SyntaxError:
        pass  # syntax errors are reported by check_code_quality.py

    return v


def main() -> int:
    """Entry point. Exit 0 = pass, 1 = violations found."""
    files = sys.argv[1:] or get_staged_files()
    if not files:
        print("[MODEL PASS] No Python files to check")
        return 0

    violations: list[str] = []
    for path in files:
        violations.extend(check_file(path))

    for msg in violations:
        print(msg)

    if not violations:
        print("[MODEL PASS] All structural model checks passed")
        return 0

    print(f"\n[MODEL BLOCK] {len(violations)} violation(s) — fix before push")
    return 1


if __name__ == "__main__":
    sys.exit(main())
