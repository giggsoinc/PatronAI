# =============================================================
# FRAGMENT: scan_first_run.py.frag
# PROJECT: PatronAI — Phase 1A
# VERSION: 1.0.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: First-run flag handler. setup_agent.sh creates
#          ~/.patronai/first_run.flag at install time. Phase 1A
#          scanners read IS_FIRST_RUN; if True, they widen their search
#          (e.g. tool/regex matching is less strict, vector-db hunt
#          covers all discovered repos rather than top-matches only).
#          The footer clears the flag after a successful payload print
#          so subsequent recurring scans run in the lighter, fast mode.
# AUDIT LOG:
#   v1.0.0  2026-04-26  Initial. Phase 1A.
# =============================================================

_FIRST_RUN_FLAG = AGENT_DIR / "first_run.flag"


def _is_first_run() -> bool:
    """Return True if the install-time flag file is still present.
    Best-effort — silent fail returns False so we degrade to fast scan."""
    try:
        return _FIRST_RUN_FLAG.exists()
    except Exception:  # intentional: must not crash
        return False


def _clear_first_run_flag() -> None:
    """Remove the flag once a deep scan has completed successfully.
    Caller (footer) wraps this in try/except so a failure here never
    kills the scan."""
    try:
        if _FIRST_RUN_FLAG.exists():
            _FIRST_RUN_FLAG.unlink()
    except Exception:  # intentional: must not crash
        pass


# Bind to a global so every downstream scanner can branch on it.
IS_FIRST_RUN: bool = _is_first_run()
