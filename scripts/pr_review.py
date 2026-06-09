#!/usr/bin/env python3
# =============================================================================
# scripts/pr_review.py — Automated PR review + GitHub comment poster
# Author: Giggso Inc / Ravi Venugopal
# Purpose: GPT-5.5 diff review; conflict detection; posts via gh CLI
# =============================================================================
# | Date       | Author | Change                         |
# |------------|--------|--------------------------------|
# | 2026-05-08 | RV     | Initial implementation         |
# =============================================================================

import json
import os
import sys
import subprocess
from dotenv import load_dotenv
import openai

load_dotenv()

MODEL = "gpt-5.5"
MAX_DIFF_CHARS = 15_000


def run_cmd(cmd: list[str]) -> tuple[int, str, str]:
    """Run shell command. Returns (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError as e:
        return 1, "", str(e)


def preprocess_text(text: str) -> str:
    """Strip blank lines and trailing whitespace before sending to LLM."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def count_tokens(text: str) -> int:
    """Estimate token count (4 chars ≈ 1 token) and log to stderr."""
    estimate = len(text) // 4
    sys.stderr.write(f"[pr_review] token estimate: ~{estimate}\n")
    return estimate


def get_pr_info() -> dict | None:
    """Fetch open PR metadata for current branch via gh CLI."""
    code, out, err = run_cmd(
        ["gh", "pr", "view", "--json", "number,title,mergeable,url,state"]
    )
    if code != 0:
        sys.stderr.write(f"[pr_review] no open PR: {err}\n")
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[pr_review] JSON parse error: {e}\n")
        return None


def get_pr_diff() -> str:
    """Fetch PR diff text via gh CLI."""
    code, out, _ = run_cmd(["gh", "pr", "diff"])
    return out if code == 0 else ""


def review_with_llm(diff: str, pr_title: str) -> str:
    """Send diff to GPT-5.5 and return a markdown review comment."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "[PR-REVIEW ERROR] OPENAI_API_KEY not set in .env"

    clean_diff = preprocess_text(diff[:MAX_DIFF_CHARS])
    count_tokens(clean_diff)

    prompt = (
        f'Senior code review for PR: "{pr_title}"\n\n'
        f"```diff\n{clean_diff}\n```\n\n"
        "Write a concise GitHub PR review in markdown. Include:\n"
        "1. Summary of changes (2–3 lines)\n"
        "2. Issues: bugs, missing type hints, CLAUDE.md violations, security concerns\n"
        "3. Positives if any\n"
        "4. Verdict: APPROVE / REQUEST_CHANGES / COMMENT\n"
        "Be direct. No filler."
    )

    try:
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return resp.choices[0].message.content
    except openai.APIError as e:
        sys.stderr.write(f"[pr_review] OpenAI error: {e}\n")
        return f"[PR-REVIEW ERROR] API call failed: {e}"


def post_comment(body: str) -> bool:
    """Post a review comment to the open PR via gh CLI."""
    code, _, err = run_cmd(["gh", "pr", "review", "--comment", "-b", body])
    if code != 0:
        sys.stderr.write(f"[pr_review] failed to post comment: {err}\n")
        return False
    return True


def main() -> int:
    """Entry point. Always exits 0 — PR review is advisory, never blocks."""
    pr = get_pr_info()
    if not pr:
        print("[PR-REVIEW] No open PR on this branch — skipping")
        return 0

    pr_num = pr.get("number")
    pr_title = pr.get("title", "")
    mergeable = pr.get("mergeable", "MERGEABLE")
    pr_url = pr.get("url", "")

    sys.stderr.write(f"[pr_review] reviewing PR #{pr_num}: {pr_title}\n")

    if mergeable == "CONFLICTING":
        conflict_msg = (
            "## ⚠️ CONFLICT DETECTED\n\n"
            "**Human review required** — this PR has merge conflicts "
            "that must be resolved manually before merge.\n\n"
            "_Posted by automated pre-push review gate._"
        )
        post_comment(conflict_msg)
        print(f"[PR-REVIEW CONFLICT] PR #{pr_num} has conflicts — human review required")
        return 0

    diff = get_pr_diff()
    if not diff:
        print("[PR-REVIEW] Empty diff — nothing to review")
        return 0

    review_body = review_with_llm(diff, pr_title)
    if post_comment(review_body):
        print(f"[PR-REVIEW DONE] Review posted on PR #{pr_num} — {pr_url}")
    else:
        print("[PR-REVIEW WARN] Review generated but failed to post to GitHub")

    return 0


if __name__ == "__main__":
    sys.exit(main())
