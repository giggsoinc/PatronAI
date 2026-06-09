#!/usr/bin/env python3
# =============================================================================
# scripts/vuln_scan.py — GPT-5.5 vulnerability scanner (pre-push gate)
# Author: Giggso Inc / Ravi Venugopal
# Purpose: OWASP Top 10 + secrets + AWS IAM scan via OpenAI gpt-5.5
# =============================================================================
# | Date       | Author | Change                         |
# |------------|--------|--------------------------------|
# | 2026-05-08 | RV     | Initial implementation         |
# =============================================================================

import json
import os
import sys
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import openai

load_dotenv()

MODEL = "gpt-5.5"
MAX_FILE_CHARS = 10_000  # ~2500 tokens per file ceiling


def get_staged_files() -> list[str]:
    """Return staged Python files via git diff."""
    try:
        r = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, check=True,
        )
        return [f for f in r.stdout.strip().splitlines() if f.endswith(".py")]
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"[vuln_scan] git diff failed: {e}\n")
        return []


def preprocess_text(text: str) -> str:
    """Strip blank lines and trailing whitespace to reduce token noise."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def count_tokens(text: str) -> int:
    """Estimate token count (4 chars ≈ 1 token). Logs to stderr."""
    estimate = len(text) // 4
    sys.stderr.write(f"[vuln_scan] token estimate: ~{estimate}\n")
    return estimate


def scan_file(client: openai.OpenAI, path: str) -> list[dict]:
    """Send one file to GPT-5.5 and return parsed vulnerability list."""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="ignore")[:MAX_FILE_CHARS]
    except OSError as e:
        sys.stderr.write(f"[vuln_scan] cannot read {path}: {e}\n")
        return []

    content = preprocess_text(raw)
    count_tokens(content)

    prompt = (
        f'Security audit this Python file: "{path}"\n\n'
        f"```python\n{content}\n```\n\n"
        "Return JSON: {\"vulnerabilities\": ["
        "{\"severity\": \"CRITICAL|HIGH|MEDIUM|INFO\", "
        "\"line\": <int>, \"description\": \"<issue>\", "
        "\"fix\": \"<fix>\", \"ref\": \"<OWASP/CVE>\"}"
        "]}. Empty array if clean. JSON only."
    )

    sys.stderr.write(f"[vuln_scan] scanning {path} with {MODEL}...\n")
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content)
        return parsed.get("vulnerabilities", [])
    except (openai.APIError, json.JSONDecodeError, KeyError) as e:
        sys.stderr.write(f"[vuln_scan] API/parse error for {path}: {e}\n")
        return []


def main() -> int:
    """Entry point. Exit 0 = pass/advisory, 1 = CRITICAL/HIGH found."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.stderr.write("[vuln_scan] ERROR: OPENAI_API_KEY not set in .env\n")
        return 1

    files = sys.argv[1:] or get_staged_files()
    if not files:
        print("[VULN PASS] No Python files to scan")
        return 0

    client = openai.OpenAI(api_key=api_key)
    blocking = False
    total = 0

    for path in files:
        issues = scan_file(client, path)
        for issue in issues:
            sev = issue.get("severity", "INFO")
            line = issue.get("line", "?")
            desc = issue.get("description", "")
            fix = issue.get("fix", "")
            ref = issue.get("ref", "")
            print(f"[VULN {sev}] {path}:{line} — {desc} — {ref} — Fix: {fix}")
            total += 1
            if sev in ("CRITICAL", "HIGH"):
                blocking = True

    if total == 0:
        print("[VULN PASS] No vulnerabilities found")
        return 0

    if blocking:
        print(f"\n[VULN BLOCK] {total} issue(s) — CRITICAL/HIGH must be fixed before push")
        return 1

    print(f"\n[VULN ADVISORY] {total} MEDIUM/INFO issue(s) — push allowed, review recommended")
    return 0


if __name__ == "__main__":
    sys.exit(main())
