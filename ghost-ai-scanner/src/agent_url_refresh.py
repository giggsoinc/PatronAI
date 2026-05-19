# =============================================================
# FILE: src/agent_url_refresh.py
# VERSION: 1.1.0
# UPDATED: 2026-05-19
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Daily re-mint of presigned URL bundles for every active
#          hook-agent token. Without this, write URLs baked into the
#          installer expire at 7 days and the laptop goes silent —
#          the silent-cliff bug Step 0 was opened to fix.
#          Walks AgentStore catalog, mints fresh URLs per token,
#          writes urls.json under config/HOOK_AGENTS/{token}/.
#          Designed to run as a daemon thread alongside scanner_loop.
# DEPENDS: store.agent_store, blob_index_store
# AUDIT LOG:
#   v1.0.0  2026-04-25  Initial. Step 0 — fix the URL-expiry blocker.
#   v1.1.0  2026-05-19  Pass recipient_email to write_url_bundle so
#                       authorized_list_url is minted per-user.
# =============================================================

import logging
import os
import time
from datetime import datetime, timezone

log = logging.getLogger("marauder-scan.url_refresh")

REFRESH_INTERVAL_SECS = int(os.environ.get("URL_REFRESH_INTERVAL_SECS", "86400"))   # 24 h
REFRESH_STARTUP_DELAY = 60   # let main loops settle before first refresh


def refresh_all_tokens(store) -> dict:
    """Walk the catalog and re-mint a urls.json for every active token.

    Returns a stats dict for logging. Never raises — a single token
    failing must not stop the rest of the fleet from refreshing.
    """
    catalog = store.agent.list_catalog() if hasattr(store, "agent") else []
    if not catalog:
        log.info("url_refresh: catalog empty — nothing to mint")
        return {"checked": 0, "minted": 0, "failed": 0}
    minted = 0
    failed = 0
    for entry in catalog:
        token   = entry.get("token", "")
        os_type = entry.get("os_type", "mac")
        email   = entry.get("recipient_email", "")
        if not token:
            continue
        try:
            ok = store.agent.write_url_bundle(token, os_type, email)
            if ok:
                minted += 1
            else:
                failed += 1
        except Exception as exc:                         # never let one token kill the loop
            failed += 1
            log.error("url_refresh: token %s mint failed: %s", token[:8], exc)
    stats = {
        "checked":    len(catalog),
        "minted":     minted,
        "failed":     failed,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    log.info("url_refresh: %s", stats)
    return stats


def url_refresh_loop(store, stop) -> None:
    """Daemon thread target — daily re-mint until stop is set."""
    log.info("url_refresh_loop: started (interval=%ds)", REFRESH_INTERVAL_SECS)
    if stop.wait(timeout=REFRESH_STARTUP_DELAY):
        return
    while not stop.is_set():
        t0 = time.time()
        try:
            refresh_all_tokens(store)
        except Exception as exc:                         # belt-and-suspenders
            log.error("url_refresh_loop tick failed: %s", exc, exc_info=True)
        elapsed = time.time() - t0
        stop.wait(timeout=max(0, REFRESH_INTERVAL_SECS - elapsed))
    log.info("url_refresh_loop: stopped")
