# =============================================================
# FILE: src/threads.py
# VERSION: 1.1.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Daemon thread targets for main.py.
#          scanner_loop      — ingest, summarize, alert every N seconds.
#          alerter_backlog   — CRITICAL-only safety net every 60s.
#          streamlit_proc    — Streamlit subprocess on port 8501.
#          url_refresh_loop  — daily re-mint of agent presigned URLs;
#                              fixes the 7-day silent cliff (Step 0).
# DEPENDS: ingestor, summarizer, alerter, agent_url_refresh, subprocess
# AUDIT LOG:
#   v1.0.0  2026-04-18  Initial.
#   v1.1.0  2026-04-25  Step 0 — url_refresh_loop wrapper.
# =============================================================

import os
import sys
import time
import logging
import subprocess
import threading

log = logging.getLogger("marauder-scan.threads")

STREAMLIT_PORT = int(os.environ.get("STREAMLIT_PORT", "8501"))
DEFAULT_INTERVAL = int(os.environ.get("SCAN_INTERVAL_SECS", "300"))
INTEGRATION_API_PORT = int(os.environ.get("INTEGRATION_API_PORT", "8002")) 


def scanner_loop(store, resolver, settings: dict, stop: threading.Event):
    """
    Main scanner thread.
    Reloads settings from S3 each cycle — picks up Streamlit edits.
    Sequence: ingest → summarize → alert.
    """
    from ingestor   import Ingestor
    from summarizer import Summarizer
    from alerter    import Alerter

    ingestor   = Ingestor(store, settings)
    summarizer = Summarizer(store)
    alerter    = Alerter(store, resolver, settings)
    log.info("Scanner loop started")

    while not stop.is_set():
        t0 = time.time()
        try:
            live      = store.settings.read() or settings
            interval  = int(live.get("scanner", {}).get("scan_interval_secs", DEFAULT_INTERVAL))
            ingestor._settings = live
            ingestor._bucket   = live.get("storage", {}).get("ocsf_bucket", "") or store.bucket
            alerter._sns_arn   = live.get("alerts", {}).get("sns_topic_arn", "")
            alerter._webhook   = live.get("alerts", {}).get("trinity_webhook_url", "")
            alerter._dedup_min = int(live.get("alerts", {}).get("dedup_window_minutes", 60))

            stats = ingestor.run()
            log.info(f"Cycle: {stats.get('files_processed',0)} files "
                     f"{stats.get('events_processed',0)} events "
                     f"{stats.get('alerts_fired',0)} alerts")

            if stats.get("events_processed", 0) > 0:
                summarizer.update_today()

            alerter.process_findings()

        except Exception as e:
            log.error(f"Scanner cycle error: {e}", exc_info=True)

        stop.wait(timeout=max(0, interval - (time.time() - t0)))

    log.info("Scanner loop stopped")


def alerter_backlog(store, resolver, settings: dict, stop: threading.Event):
    """
    Belt-and-suspenders CRITICAL alert thread.
    Runs every 60 seconds independent of the scanner cycle.
    """
    from alerter import Alerter
    alerter = Alerter(store, resolver, settings)
    log.info("Alerter backlog started")

    while not stop.is_set():
        try:
            alerter.process_findings(severities=["critical"])
        except Exception as e:
            log.error(f"Alerter backlog error: {e}", exc_info=True)
        stop.wait(timeout=60)

    log.info("Alerter backlog stopped")


def url_refresh_loop(store, stop: threading.Event):
    """Daily presigned-URL re-mint for every active hook agent (Step 0)."""
    from agent_url_refresh import url_refresh_loop as _loop
    _loop(store, stop)


def streamlit_proc(stop: threading.Event):
    """
    Streamlit subprocess. Prefers ghost_dashboard.py, falls back to app.py.
    Sets stop event if subprocess exits unexpectedly.
    """
    base    = os.path.dirname(os.path.dirname(__file__))
    primary = os.path.join(base, "dashboard", "ghost_dashboard.py")
    fallback= os.path.join(base, "dashboard", "app.py")
    script  = primary if os.path.exists(primary) else fallback

    cmd = [
        sys.executable, "-m", "streamlit", "run", script,
        "--server.port",              str(STREAMLIT_PORT),
        # nosec B104 — bind to all interfaces is required INSIDE the
        # container so the nginx reverse proxy in the same docker network
        # can reach Streamlit. The container only publishes :8501 via
        # docker-compose port mappings; in production those mappings
        # should be removed and only nginx (:80/:443) exposed externally.
        "--server.address",           "0.0.0.0",  # nosec B104
        "--server.headless",          "true",
        "--browser.gatherUsageStats", "false",
        "--server.fileWatcherType",   "none",
    ]
    log.info(f"Streamlit on :{STREAMLIT_PORT} — {os.path.basename(script)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    while not stop.is_set():
        line = proc.stdout.readline()
        if line:
            log.debug(f"[streamlit] {line.decode().rstrip()}")
        if proc.poll() is not None:
            log.error("Streamlit exited unexpectedly")
            stop.set()
            break
        time.sleep(0.1)

    proc.terminate()
    log.info("Streamlit stopped")


def integration_api_proc(stop: threading.Event):
    """FastAPI integration API subprocess on INTEGRATION_API_PORT."""
    base = os.path.dirname(os.path.dirname(__file__))
    app_path = os.path.join(base, "api.py")
    if not os.path.exists(app_path):
        log.warning("api.py not found — integration API disabled")
        return

    cmd = [
        sys.executable, "-m", "uvicorn", "api:app",
        "--host", "0.0.0.0",  # nosec B104 — same pattern as Streamlit
        "--port", str(INTEGRATION_API_PORT),
        "--app-dir", base,
        "--log-level", "info",
    ]
    log.info("Integration API on :%d", INTEGRATION_API_PORT)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    while not stop.is_set():
        line = proc.stdout.readline()
        if line:
            log.debug("[integration_api] %s", line.decode().rstrip())
        if proc.poll() is not None:
            log.error("Integration API exited unexpectedly")
            stop.set()
            break
        time.sleep(0.1)

    proc.terminate()
    log.info("Integration API stopped")
