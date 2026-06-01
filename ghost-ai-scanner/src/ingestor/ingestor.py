# =============================================================
# FILE: src/ingestor/ingestor.py
# VERSION: 2.0.0
# UPDATED: 2026-04-26
# OWNER: Ravi Venugopal, Giggso Inc
# PURPOSE: Main ingestor coordinator. Runs one scan cycle.
#          Wakes, reads timestamp cursor, walks S3 for files modified
#          after the cursor, processes each event through the pipeline,
#          advances cursor on max LastModified seen.
#          Timeline and persistence maintained via blob_index_store.
# AUDIT LOG:
#   v1.0.0  2026-04-18  Initial — key-based cursor.
#   v2.0.0  2026-04-26  Switched to LastModified timestamp cursor so
#                       overwritten files (heartbeats/scans → latest.json)
#                       get re-read on every cycle.
# DEPENDS: ingestor.s3_walker, ingestor.pipeline, matcher.loader
# =============================================================

import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

from .s3_walker import S3Walker
from .pipeline  import Pipeline

log = logging.getLogger("marauder-scan.ingestor")


class Ingestor:
    """
    Runs one scan cycle per call to run().
    Called by main.py on a timer every SCAN_INTERVAL_SECS.
    """

    def __init__(self, store, settings: dict):
        # store: BlobIndexStore — all persistence goes through here
        self._store    = store
        self._settings = settings
        self._empty_unauthorized_cycles = 0   # metric: consecutive cycles with no list

        # S3 config
        s3_cfg         = settings.get("storage", {})
        self._bucket   = settings.get("storage", {}).get("ocsf_bucket", "") or store.bucket
        self._prefix   = s3_cfg.get("ocsf_prefix", "ocsf/")
        self._max_files = settings.get("scanner", {}).get("max_files_per_cycle", 100)
        self._company  = settings.get("company", {}).get("slug", "")
        self._region   = settings.get("cloud", {}).get("region", "us-east-1")

        self._walker   = S3Walker(self._bucket, self._region)

    def run(self) -> dict:
        """
        Execute one full scan cycle.
        Returns cycle stats dict for logging and summarizer.
        """
        cycle_start = time.time()
        log.info("Scan cycle starting...")

        # Load provider lists fresh every cycle — picks up CSV edits
        from matcher.loader import load_authorized, load_unauthorized
        authorized   = load_authorized(self._bucket)
        unauthorized = load_unauthorized(self._bucket)

        if not unauthorized:
            self._empty_unauthorized_cycles += 1
            log.error("Unauthorized list empty — matching disabled (consecutive=%d). Check S3 CSV.", self._empty_unauthorized_cycles)
        else:
            self._empty_unauthorized_cycles = 0

        # Build pipeline with fresh lists (pipeline handles empty unauthorized gracefully:
        # ENDPOINT_SCAN events are pre-classified and bypass the matcher entirely,
        # so endpoint inventory continues even when the unauthorized list is missing)
        pipeline = Pipeline(
            store=self._store,
            authorized=authorized,
            unauthorized=unauthorized,
            company=self._company,
        )

        # Read cursor — timestamp where we left off last cycle.
        cursor       = self._store.cursor.read()
        cursor_ts    = _parse_iso(cursor.get("cursor_ts"))
        last_key_log = cursor.get("last_key") or "first run"
        log.info(f"Cursor: ts={cursor_ts} last_key={last_key_log}")

        # Walk S3 for files modified after cursor_ts (returns oldest-first).
        new_files = self._walker.list_new_files(
            prefix=self._prefix,
            after_ts=cursor_ts,
            max_files=self._max_files,
        )

        if not new_files:
            log.info("No new OCSF files. Cycle complete.")
            return self._stats(cycle_start, 0, 0, {})

        # Process each file; advance cursor on max LastModified seen.
        outcome_counts = defaultdict(int)
        total_events   = 0
        last_processed = cursor.get("last_key") or ""
        max_lm         = cursor_ts

        for key, lm in new_files:
            file_events = 0
            for raw_event in self._walker.read_events(key):
                outcome = pipeline.process(raw_event)
                if outcome:
                    outcome_counts[outcome] += 1
                    file_events += 1
                    total_events += 1
            log.debug(f"Processed {file_events} events from {key}")
            last_processed = key
            if max_lm is None or lm > max_lm:
                max_lm = lm

        self._store.cursor.write(
            cursor_ts=max_lm,
            last_key=last_processed,
            files_processed=len(new_files),
            total_events=total_events,
        )

        stats = self._stats(cycle_start, len(new_files), total_events, outcome_counts)
        log.info(
            f"Cycle complete — {len(new_files)} files, {total_events} events, "
            f"{outcome_counts.get('DOMAIN_ALERT', 0) + outcome_counts.get('PORT_ALERT', 0) + outcome_counts.get('ENDPOINT_FINDING', 0)} alerts"
        )
        return stats

    def _stats(self, start: float, files: int, events: int, outcomes: dict) -> dict:
        """Build cycle stats dict for summarizer and Grafana pipeline panel."""
        return {
            "duration_seconds": round(time.time() - start, 2),
            "files_processed":  files,
            "events_processed": events,
            "outcomes":         dict(outcomes),
            "alerts_fired":     outcomes.get("DOMAIN_ALERT", 0)
                                + outcomes.get("PORT_ALERT", 0)
                                + outcomes.get("ENDPOINT_FINDING", 0),
            "suppressed":       outcomes.get("SUPPRESS", 0),
            "unknown":          outcomes.get("UNKNOWN", 0),
        }

def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    """Best-effort ISO-8601 parse; returns None on miss or bad input."""
    if not s: return None
    try: return datetime.fromisoformat(s)
    except Exception: return None  # intentional: returns safe default on any error
