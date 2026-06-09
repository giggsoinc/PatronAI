#!/usr/bin/env python3
# =============================================================================
# scripts/check_code_quality.py — CLAUDE.md code quality gate
# Author: Giggso Inc / Ravi Venugopal
# Purpose: AST-based checker for pre-push quality enforcement
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

MAX_LINES = 150
HEADER_RE = re.compile(r"#.*?(file:|purpose:|author:|={10,})", re.IGNORECASE)
AUDIT_RE = re.compile(r"#.*?audit\s*log", re.IGNORECASE)
CRED_RE = re.compile(
    r'(api_key|password|secret_key|token|passwd)\s*=\s*["\'][^"\']{4,}',
    re.IGNORECASE,
)
PRINT_RE = re.compile(r"^\s*print\s*\(", re.MULTILINE)
MCP_PATH_RE = re.compile(r"mcp", re.IGNORECASE)
EXTERNAL_CALLS = re.compile(
    r"\b(requests\.|boto3\.|openai\.|subprocess\.|open\(|oracledb\.)", re.MULTILINE
)


def get_staged_files() -> list[str]:
    """Return staged Python files via git diff."""
    try:
        r = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, check=True,
        )
        return [f for f in r.stdout.strip().splitlines() if f.endswith(".py")]
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"[quality] git diff failed: {e}\n")
        return []


def _line_no(content: str, offset: int) -> int:
    """Convert string offset to 1-based line number."""
    return content[:offset].count("\n") + 1


def check_file(path: str) -> list[str]:
    """Run all quality rules on one file. Returns list of violation strings."""
    v: list[str] = []
    try:
        content = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return [f"[QUALITY FAIL] {path} — cannot read: {e} — check file permissions"]

    lines = content.splitlines()

    if len(lines) > MAX_LINES:
        v.append(
            f"[QUALITY FAIL] {path}:{len(lines)} — exceeds 150-line limit "
            f"({len(lines)} lines) — split into smaller modules"
        )
    if not HEADER_RE.search(content[:600]):
        v.append(
            f"[QUALITY FAIL] {path}:1 — missing file header — "
            "add filename/purpose/author block at top"
        )
    if not AUDIT_RE.search(content):
        v.append(
            f"[QUALITY FAIL] {path}:1 — missing audit log table — "
            "add '# | Date | Author | Change |' table"
        )
    for m in CRED_RE.finditer(content):
        v.append(
            f"[QUALITY FAIL] {path}:{_line_no(content, m.start())} — "
            f"hardcoded credential '{m.group(1)}' — use os.getenv() + .env"
        )
    if MCP_PATH_RE.search(path):
        for m in PRINT_RE.finditer(content):
            v.append(
                f"[QUALITY FAIL] {path}:{_line_no(content, m.start())} — "
                "print() in MCP server — use sys.stderr.write() instead"
            )

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        v.append(f"[QUALITY FAIL] {path}:{e.lineno} — syntax error: {e.msg}")
        return v

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args.args + node.args.posonlyargs + node.args.kwonlyargs
        missing = [a.arg for a in args if a.annotation is None and a.arg != "self"]
        if missing or node.returns is None:
            v.append(
                f"[QUALITY FAIL] {path}:{node.lineno} — {node.name}() missing "
                f"type hints ({', '.join(missing) or 'return'}) — annotate all params + return"
            )
        if not ast.get_docstring(node):
            v.append(
                f"[QUALITY FAIL] {path}:{node.lineno} — {node.name}() "
                "missing docstring — add purpose comment"
            )

    return v


def main() -> int:
    """Entry point. Exit 0 = pass, 1 = violations found."""
    files = sys.argv[1:] or get_staged_files()
    if not files:
        print("[QUALITY PASS] No Python files to check")
        return 0

    violations: list[str] = []
    for path in files:
        violations.extend(check_file(path))

    for msg in violations:
        print(msg)

    if not violations:
        print("[QUALITY PASS] All quality checks passed")
        return 0

    print(f"\n[QUALITY BLOCK] {len(violations)} violation(s) — fix before push")
    return 1


if __name__ == "__main__":
    sys.exit(main())
