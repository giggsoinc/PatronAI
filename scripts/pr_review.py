#!/usr/bin/env python3
# =============================================================
# FILE: scripts/pr_review.py
# VERSION: 1.0.0
# UPDATED: 2026-05-18
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Async advisory PR review — spawned by pre-push hook.
#          Gets diff vs main, calls gpt-4.1-mini for advisory,
#          posts to GitHub PR via gh CLI (if open PR exists).
#          Falls back to .raven/pr_review.log if no PR / no key.
#          Never blocks push — exits 0 always.
# AUDIT LOG:
#   v1.0.0  2026-05-18  Initial implementation
# =============================================================

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(
    stream=sys.stderr, level=logging.INFO,
    format="%(levelname)s  pr_review: %(message)s",
)

_REPO_ROOT      = Path(__file__).parent.parent
_LOG_PATH       = _REPO_ROOT / ".raven" / "pr_review.log"
_MAX_DIFF_BYTES = 12_000
_SKIP_BRANCHES  = {"main", "master", "HEAD"}


def _run(cmd: list[str], timeout: int = 30) -> str:
    """Run subprocess, return stdout. Returns '' on any error."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(_REPO_ROOT), timeout=timeout,
        )
        return r.stdout.strip()
    except Exception as exc:
        log.debug("cmd %s error: %s", cmd, exc)
        return ""


def _current_branch() -> str:
    """Return the current git branch name."""
    return _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])


def _diff_vs_main() -> str:
    """Return Python-file diff of this branch vs main, truncated."""
    diff = _run(["git", "diff", "main...HEAD", "--unified=3", "--", "*.py"])
    if len(diff) > _MAX_DIFF_BYTES:
        diff = diff[:_MAX_DIFF_BYTES] + "\n\n[...diff truncated — see full branch diff]"
    return diff


def _get_pr_number(branch: str) -> str | None:
    """Return open PR number for branch via gh CLI, or None."""
    out = _run(["gh", "pr", "list", "--head", branch,
                "--state", "open", "--json", "number"])
    if not out:
        return None
    try:
        items = json.loads(out)
        return str(items[0]["number"]) if items else None
    except Exception:
        return None


def _llm_review(diff: str) -> str:
    """Call gpt-4.1-mini for advisory review. Returns markdown string."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return "_LLM review skipped — OPENAI\\_API\\_KEY not set._"
    try:
        import openai  # type: ignore
        client = openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior security-focused Python reviewer. "
                        "Review the git diff and produce a concise advisory in markdown: "
                        "## Summary (2 bullets max), "
                        "## Risks (security/data issues only — skip if none), "
                        "## Suggestions (top 3 max, one line each). "
                        "Keep total response under 300 words. This is advisory only."
                    ),
                },
                {"role": "user", "content": diff},
            ],
            max_tokens=400,
        )
        return resp.choices[0].message.content or "_No review content returned._"
    except Exception as exc:
        return f"_LLM review failed: {exc}_"


def _post_pr_comment(pr_num: str, body: str) -> bool:
    """Post a comment to the PR via gh CLI. Returns True on success."""
    try:
        result = subprocess.run(
            ["gh", "pr", "comment", pr_num, "--body", body],
            capture_output=True, text=True,
            cwd=str(_REPO_ROOT), timeout=30,
        )
        return result.returncode == 0
    except Exception as exc:
        log.debug("gh comment failed: %s", exc)
        return False


def _write_log(branch: str, review: str) -> None:
    """Append review to .raven/pr_review.log as fallback."""
    try:
        from datetime import datetime, timezone
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"\n---\nbranch: {branch}\ntimestamp: {ts}\n{review}\n")
    except Exception as exc:
        log.debug("log write failed: %s", exc)


def main() -> None:
    """Entry point — async advisory PR review, never blocks push."""
    branch = _current_branch()
    if not branch or branch in _SKIP_BRANCHES:
        return

    diff = _diff_vs_main()
    if not diff:
        log.info("no Python diff vs main — skipping review")
        return

    review   = _llm_review(diff)
    pr_num   = _get_pr_number(branch)
    comment  = f"### PatronAI PR Review (advisory)\n\n{review}"

    if pr_num:
        posted = _post_pr_comment(pr_num, comment)
        if posted:
            log.info("advisory review posted to PR #%s", pr_num)
            return
        log.info("gh post failed — falling back to log")

    _write_log(branch, review)
    log.info("advisory review written to .raven/pr_review.log")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.error("pr_review crashed (non-blocking): %s", exc)
    sys.exit(0)
