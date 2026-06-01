# =============================================================
# FILE: src/jobs/_rollup_scheduler.py
# VERSION: 1.0.0
# UPDATED: 2026-06-01
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Extracted from hourly_rollup.py — hour-floor helper,
#          catch-up, backfill, and scheduler-thread logic.
#          See hourly_rollup.py for context.
# =============================================================
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import boto3

log = logging.getLogger("marauder-scan.jobs.hourly_rollup")

_BUCKET = os.environ.get("MARAUDER_SCAN_BUCKET", "")
_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _hour_floor(t: datetime) -> datetime:
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.replace(minute=0, second=0, microsecond=0)


def _latest_completed_hour(s3) -> Optional[datetime]:
    """Find latest tenants/*/rollup/.../HH/_meta.json. Returns None if empty."""
    try:
        paginator = s3.get_paginator("list_objects_v2")
        latest: Optional[datetime] = None
        for page in paginator.paginate(Bucket=_BUCKET, Prefix="tenants/"):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                if not k.endswith("/_meta.json"):
                    continue
                # Path: tenants/{hash}/rollup/YYYY/MM/DD/HH/_meta.json
                parts = k.split("/")
                try:
                    yyyy, mm, dd, hh = parts[3], parts[4], parts[5], parts[6]
                    t = datetime(int(yyyy), int(mm), int(dd), int(hh),
                                 tzinfo=timezone.utc)
                    if latest is None or t > latest:
                        latest = t
                except (IndexError, ValueError):
                    continue
        return latest
    except Exception as exc:
        log.warning("latest_completed_hour scan failed: %s", exc)
        return None


def catch_up_rollups(max_hours: int = 720) -> int:
    """Process every missing hour from latest-completed up to now-1h.
    Bounded by max_hours (30 days default) so a long outage doesn't loop
    forever.  When no rollups exist at all (fresh deploy), backfill the
    last `ROLLUP_INITIAL_BACKFILL_DAYS` (env, default 7) — otherwise the
    chat would see empty rollups for any historical window and the LLM
    would (correctly) report no data, even when raw findings exist.
    Returns count of hours processed."""
    if not _BUCKET:
        return 0
    from . import hourly_rollup as _hr  # deferred to avoid circular import
    s3 = boto3.client("s3", region_name=_REGION)
    target_end = _hour_floor(datetime.now(timezone.utc))  # exclusive
    start = _latest_completed_hour(s3)
    if start is None:
        initial_days = int(os.environ.get("ROLLUP_INITIAL_BACKFILL_DAYS", "7"))
        start = target_end - timedelta(days=max(1, initial_days))
        log.info("catch_up_rollups: fresh deploy — backfilling last %d days "
                 "(%s → %s)", initial_days, start.isoformat(),
                 target_end.isoformat())
    else:
        start = start + timedelta(hours=1)

    processed = 0
    cur = start
    while cur < target_end and processed < max_hours:
        try:
            _hr.compute_hourly_rollup(cur)
        except Exception as exc:
            log.error("catch_up: hour %s failed: %s", cur.isoformat(), exc)
        cur += timedelta(hours=1)
        processed += 1
    log.info("catch_up_rollups: processed %d hours up to %s",
             processed, target_end.isoformat())
    return processed


def backfill(start: datetime, end: datetime) -> int:
    """Process every hour in [start, end). Returns count processed."""
    from . import hourly_rollup as _hr  # deferred to avoid circular import
    cur = _hour_floor(start)
    end = _hour_floor(end)
    n = 0
    while cur < end:
        try:
            _hr.compute_hourly_rollup(cur)
            n += 1
        except Exception as exc:
            log.error("backfill hour %s failed: %s", cur.isoformat(), exc)
        cur += timedelta(hours=1)
    return n


def scheduler_loop(stop_event, offset_minutes: int = 5) -> None:
    """Run forever. At HH:offset every hour, compute the hour that just ended.
    Fires catch_up_rollups once on startup so missing hours are filled.
    Safe to run as a daemon thread alongside scanner_loop."""
    if not _BUCKET:
        log.warning("scheduler_loop: bucket not set — exiting")
        return

    from . import hourly_rollup as _hr  # deferred to avoid circular import

    log.info("scheduler_loop: starting (offset=:%02d, catch-up first)", offset_minutes)
    try:
        catch_up_rollups()
    except Exception as exc:
        log.error("startup catch-up failed (non-fatal): %s", exc)

    while not stop_event.is_set():
        now = datetime.now(timezone.utc)
        # Next firing: top of the next hour + offset.
        nxt = (now.replace(minute=0, second=0, microsecond=0)
               + timedelta(hours=1, minutes=offset_minutes))
        sleep_s = max(30, (nxt - now).total_seconds())
        if stop_event.wait(timeout=sleep_s):
            return
        # Process the previous full hour [H-1:00, H:00).
        target = (datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
                  - timedelta(hours=1))
        try:
            _hr.compute_hourly_rollup(target)
        except Exception as exc:
            log.error("scheduled rollup for %s failed: %s", target.isoformat(), exc)
