# =============================================================
# FILE: main.py
# VERSION: 1.2.0
# UPDATED: 2026-04-29
# OWNER: Giggso Inc
# PURPOSE: Container entrypoint. Wires bootstrap and threads.
#          Watchdog restarts container if any thread dies.
# USAGE: python main.py
# AUDIT LOG:
#   v1.0.0  2026-04-18  Initial.
#   v1.1.0  2026-04-29  Switch LLM to LFM2.5-1.2B-Thinking via --hf-repo.
#                       Remove curl download logic; llama-server handles it.
#                       LLAMA_CACHE=/models routes HF cache to named volume.
#   v1.2.0  2026-04-29  Hourly S3 rollup scheduler (per-user + per-tenant
#                       trees) + chat-history S3 lifecycle policy at startup.
#   v1.3.0  2026-05-03  Fix llama-server 500 "Context size has been
#                       exceeded": added --parallel 1 + ctx 16384.
#   v1.4.0  2026-05-11  Wire findings_compact daemon. Collapses raw
#                       findings/ (which append every 30 min) into a
#                       deduped findings_current/ view keyed on
#                       finding_signature. Also auto-resolves
#                       signatures unseen for STALE_CYCLES (default 24
#                       cycles). Fixes 1-laptop-shows-1020-endpoints
#                       and 50-alerts-from-1-snapshot dashboard noise.
# =============================================================

import glob
import os
import subprocess
import sys
import time
import logging
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("marauder-scan.main")

sys.path.insert(0, "src")

from bootstrap   import validate_env, build_store, load_settings, build_resolver, maybe_backfill, seed_config_files
from rule_health  import self_check_rules
from threads      import scanner_loop, alerter_backlog, url_refresh_loop, streamlit_proc, integration_api_proc
from jobs.hourly_rollup    import scheduler_loop as rollup_scheduler_loop
from jobs.docs_refresh     import docs_refresh_loop
from jobs.findings_compact import scheduler_loop as compact_scheduler_loop

_HF_REPO  = os.environ.get("LLM_MODEL_REPO", "LiquidAI/LFM2.5-1.2B-Thinking-GGUF")
_LLM_PORT = int(os.environ.get("LLM_SERVER_PORT", "8080"))
_ROLLUP_OFFSET_MIN = int(os.environ.get("ROLLUP_HOURLY_OFFSET_MINUTES", "5"))
_CHAT_RETENTION_DAYS = int(os.environ.get("CHAT_HISTORY_RETENTION_DAYS", "30"))


def _llama_server_thread() -> None:
    """Background daemon: run llama-server; downloads model from HuggingFace on first boot.

    Performance flags:
      --threads <all-cores>  : llama.cpp defaults to ~half cores; the
                                rest were idle. Empirically ~1.5× speedup
                                at 100% CPU during a turn.
      --predict <max>        : hard server-side cap on output tokens.
                                Belt-and-braces with the client-side
                                LLM_MAX_TOKENS so a runaway response
                                can't blow past N tokens even if a
                                client forgets to set max_tokens.
                                Default 384 (≤100-word answers + Sources
                                footer fit comfortably in 384 tokens).
      --parallel <slots>     : number of concurrent generation slots.
                                CRITICAL — llama-server divides ctx-size
                                EVENLY across slots. Default is 4, so
                                with --ctx-size 8192 each slot only gets
                                2048 tokens. Our chat prompts (system
                                prompt + 8 tool schemas + chat history +
                                tool results) easily reach 3000-4000
                                tokens, exceeding the per-slot budget
                                and triggering 500 'Context size has
                                been exceeded'. Single-tenant chat needs
                                exactly 1 slot — so each slot gets the
                                full --ctx-size.
                                Override with LLM_PARALLEL_SLOTS env if
                                you ever multi-tenant the chat.
      --ctx-size <N>         : per-slot context window (after --parallel
                                division). Bumped 8192 → 16384 to give
                                comfortable headroom for chat history +
                                Thinking-model reasoning + tool results
                                + answer + Sources footer. RAM cost
                                ~250 MB per 8192 token doubling on Q4_K_M
                                — fine on a t3.large. Override with
                                LLM_CTX_SIZE env.
    """
    threads  = max(2, os.cpu_count() or 4)
    predict  = int(os.environ.get("LLM_MAX_TOKENS", "384"))
    parallel = int(os.environ.get("LLM_PARALLEL_SLOTS", "1"))
    ctx_size = int(os.environ.get("LLM_CTX_SIZE", "16384"))
    log.info("llama-server: starting on :%d — repo: %s — "
             "threads=%d predict=%d parallel=%d ctx=%d",
             _LLM_PORT, _HF_REPO, threads, predict, parallel, ctx_size)
    # LLAMA_CACHE=/models routes the HuggingFace download to the named Docker volume.
    subprocess.Popen(
        ["llama-server",
         "--hf-repo",  _HF_REPO,
         "--port",     str(_LLM_PORT), "--host", "127.0.0.1",
         "--ctx-size", str(ctx_size),
         "--parallel", str(parallel),
         "--threads",  str(threads),
         "--predict",  str(predict)],
        stdout=subprocess.DEVNULL, stderr=None,
        env={**os.environ, "LLAMA_CACHE": "/models"},
    )
    log.info("llama-server: process launched on :%d", _LLM_PORT)


def main():
    log.info("PatronAI — Starting — Giggso Inc v1.2.0")

    validate_env()
    store    = build_store()
    seed_config_files(store)          # push bundled CSVs → S3 on every startup
    self_check_rules()                # validate merged rule counts; emit self-alert if low
    settings = load_settings(store)
    resolver = build_resolver(store, settings)
    maybe_backfill(store)

    # Apply the chat-history lifecycle rule once per boot (idempotent).
    # chat.history lives in src/chat/ — sys.path already has src/.
    try:
        from chat.history import ensure_lifecycle_policy
        ensure_lifecycle_policy(_CHAT_RETENTION_DAYS)
    except Exception as exc:
        log.warning("ensure_lifecycle_policy failed (non-fatal): %s", exc)

    stop = threading.Event()

    # llama-server runs independently — downloads model on first boot then serves on :8080
    threading.Thread(target=_llama_server_thread, name="llama_server", daemon=True).start()
    log.info("Started: llama_server (background)")

    threads = [
        threading.Thread(target=scanner_loop,        args=(store, resolver, settings, stop), name="scanner",        daemon=True),
        threading.Thread(target=alerter_backlog,     args=(store, resolver, settings, stop), name="alerter",        daemon=True),
        threading.Thread(target=url_refresh_loop,    args=(store, stop),                     name="url_refresh",    daemon=True),
        threading.Thread(target=rollup_scheduler_loop, args=(stop, _ROLLUP_OFFSET_MIN),      name="rollup_scheduler", daemon=True),
        threading.Thread(target=compact_scheduler_loop, args=(store, stop),                   name="findings_compact", daemon=True),
        threading.Thread(target=docs_refresh_loop,   args=(stop,),                           name="docs_refresh",   daemon=True),
        threading.Thread(target=streamlit_proc,      args=(stop,),                           name="streamlit",      daemon=True),
    ]
    if os.environ.get("INTEGRATION_API_ENABLED", "1") == "1":
        threads.append(
            threading.Thread(target=integration_api_proc, args=(stop,), name="integration_api", daemon=True),
        ) 

    for t in threads:
        t.start()
        log.info(f"Started: {t.name}")

    try:
        while not stop.is_set():
            for t in threads:
                if not t.is_alive():
                    log.critical(f"Thread died: {t.name} — shutting down")
                    stop.set()
            time.sleep(5)
    except KeyboardInterrupt:
        log.info("Interrupt — shutting down")
        stop.set()

    for t in threads:
        t.join(timeout=10)
    log.info("Stopped.")
    sys.exit(0)


if __name__ == "__main__":
    main()
