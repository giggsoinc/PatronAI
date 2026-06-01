# =============================================================
# FILE: src/jobs/hourly_rollup.py
# VERSION: 1.1.0
# UPDATED: 2026-06-01
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Hourly aggregation of findings/YYYY/MM/DD/{sev}.jsonl into
#          small dimension files at TWO scopes:
#            users/{owner_hash16}/rollup/YYYY/MM/DD/HH/by_*.json
#            tenants/{company_hash16}/rollup/YYYY/MM/DD/HH/by_*.json
#          Lets chat tools answer at any time-window with O(hours)
#          small reads instead of O(events) raw scan.
# DEPENDS: store.findings_store (read), store.base_store (write),
#          normalizer.provider_names (human AI-tool names), boto3
# USAGE:
#   python -m src.jobs.hourly_rollup --catch-up
#   python -m src.jobs.hourly_rollup --backfill --start 2026-01-01T00 --end 2026-04-29T00
#   python -m src.jobs.hourly_rollup --hour 2026-04-29T15
# =============================================================
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

# Make sibling src modules importable when run as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from normalizer.provider_names import is_known  # noqa: E402

from ._rollup_agg import _ScopeAgg, _SEVERITY_RISK  # noqa: F401
from ._rollup_s3 import _s3, _put_gz, _select_rows_for_window, _append_unknown_providers

log = logging.getLogger("marauder-scan.jobs.hourly_rollup")

_BUCKET = os.environ.get("MARAUDER_SCAN_BUCKET", "")
_REGION = os.environ.get("AWS_REGION", "us-east-1")
_SEVERITIES = ["critical", "high", "medium", "unknown"]
_DIMENSIONS = ["provider", "user", "severity", "device", "category"]


# ── Path helpers ─────────────────────────────────────────────────


def _hash16(s: str) -> str:
    return hashlib.sha256((s or "").lower().encode()).hexdigest()[:16]


def _findings_key(d: datetime, severity: str) -> str:
    return f"findings/{d.year:04d}/{d.month:02d}/{d.day:02d}/{severity}.jsonl"


def _rollup_prefix_user(owner_hash: str, d: datetime) -> str:
    return (f"users/{owner_hash}/rollup/"
            f"{d.year:04d}/{d.month:02d}/{d.day:02d}/{d.hour:02d}/")


def _rollup_prefix_tenant(company_hash: str, d: datetime) -> str:
    return (f"tenants/{company_hash}/rollup/"
            f"{d.year:04d}/{d.month:02d}/{d.day:02d}/{d.hour:02d}/")


# ── Meta-dict builders ───────────────────────────────────────────


def _user_meta(o_hash, email, ws, we, rows, started, completed):
    return {"scope": "user", "owner_hash": o_hash, "owner_email": email,
            "window_start": ws.isoformat(), "window_end": we.isoformat(),
            "rows": rows,
            "run_started_at":   datetime.fromtimestamp(started,   tz=timezone.utc).isoformat(),
            "run_completed_at": datetime.fromtimestamp(completed, tz=timezone.utc).isoformat()}


def _tenant_meta(c_hash, name, ws, we, rows, users, started, completed):
    return {"scope": "tenant", "company_hash": c_hash, "company_name": name,
            "window_start": ws.isoformat(), "window_end": we.isoformat(),
            "rows": rows, "users": users,
            "run_started_at":   datetime.fromtimestamp(started,   tz=timezone.utc).isoformat(),
            "run_completed_at": datetime.fromtimestamp(completed, tz=timezone.utc).isoformat()}


# ── Top-level: compute + write one hour ─────────────────────────


def compute_hourly_rollup(window_start: datetime,
                          window_end: Optional[datetime] = None) -> dict:
    """Aggregate [window_start, window_end) and write rollup files. Idempotent."""
    if not _BUCKET:
        log.warning("compute_hourly_rollup: MARAUDER_SCAN_BUCKET not set; skipping")
        return {"skipped": True}

    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=timezone.utc)
    if window_end is None:
        window_end = window_start + timedelta(hours=1)
    elif window_end.tzinfo is None:
        window_end = window_end.replace(tzinfo=timezone.utc)

    s3 = _s3()
    started = time.time()
    log.info("rollup: window %s → %s", window_start.isoformat(), window_end.isoformat())

    # Build aggregators per scope.
    user_aggs:   dict[str, _ScopeAgg] = defaultdict(_ScopeAgg)
    tenant_aggs: dict[str, _ScopeAgg] = defaultdict(_ScopeAgg)
    user_emails:  dict[str, str] = {}    # hash → original email (lowercased)
    tenant_names: dict[str, str] = {}    # hash → company name
    rows_total = 0
    unknown_providers: set[tuple[str, str]] = set()

    # The hour window may span two daily files if window crosses UTC midnight.
    # We process every day touched by [start, end) and re-filter in S3 Select.
    cur = window_start.replace(minute=0, second=0, microsecond=0)
    days_seen: set[str] = set()
    while cur < window_end:
        day_key = cur.strftime("%Y-%m-%d")
        if day_key not in days_seen:
            days_seen.add(day_key)
            for sev in _SEVERITIES:
                key = _findings_key(cur, sev)
                for row in _select_rows_for_window(s3, key, window_start, window_end):
                    rows_total += 1
                    owner = (row.get("owner") or row.get("email") or "unknown").lower()
                    company = row.get("company") or ""
                    o_hash = _hash16(owner)
                    c_hash = _hash16(company)
                    user_emails[o_hash]  = owner
                    tenant_names[c_hash] = company
                    user_aggs[o_hash].add(row)
                    tenant_aggs[c_hash].add(row)

                    prov_raw = row.get("provider") or ""
                    cat      = row.get("category") or ""
                    if prov_raw and not is_known(cat, prov_raw):
                        unknown_providers.add((cat, prov_raw))
        cur += timedelta(hours=1)

    completed = time.time()

    # Write per-user rollups.
    for o_hash, agg in user_aggs.items():
        prefix = _rollup_prefix_user(o_hash, window_start)
        ser = agg.serialise()
        ser.pop("by_user", None)  # redundant in per-user scope; the user IS the scope
        for dim, payload in ser.items():
            _put_gz(s3, prefix + f"by_{dim}.json", payload)
        _put_gz(s3, prefix + "_meta.json", _user_meta(
            o_hash, user_emails.get(o_hash, ""), window_start, window_end,
            agg.rows, started, completed))

    # Write per-tenant rollups.
    for c_hash, agg in tenant_aggs.items():
        prefix = _rollup_prefix_tenant(c_hash, window_start)
        for dim, payload in agg.serialise().items():
            _put_gz(s3, prefix + f"by_{dim}.json", payload)
        _put_gz(s3, prefix + "_meta.json", _tenant_meta(
            c_hash, tenant_names.get(c_hash, ""), window_start, window_end,
            agg.rows, len(agg.by_user), started, completed))

    # Append unknown providers to a single audit file (best-effort).
    if unknown_providers:
        _append_unknown_providers(s3, unknown_providers)

    log.info("rollup: window %s done — %d rows, %d users, %d tenants in %.1fs",
             window_start.isoformat(), rows_total, len(user_aggs),
             len(tenant_aggs), completed - started)
    return {"rows": rows_total, "users": len(user_aggs), "tenants": len(tenant_aggs),
            "unknown_providers": len(unknown_providers), "duration_s": completed - started}


# ── CLI ──────────────────────────────────────────────────────────


def _parse_iso_hour(s: str) -> datetime:
    # Accept 2026-04-29T15 or 2026-04-29T15:00 or full ISO.
    fmts = ["%Y-%m-%dT%H", "%Y-%m-%dT%H:%M",
            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H"]
    for f in fmts:
        try:
            return datetime.strptime(s, f).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise SystemExit(f"unparseable hour: {s}")


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    p = argparse.ArgumentParser(description="PatronAI hourly rollup job")
    p.add_argument("--hour", type=str, help="Single hour to process (UTC)")
    p.add_argument("--catch-up", action="store_true",
                   help="Fill all missing hours from latest-completed up to now-1h")
    p.add_argument("--backfill", action="store_true",
                   help="Process every hour in [--start, --end)")
    p.add_argument("--start", type=str, help="Backfill start (UTC)")
    p.add_argument("--end",   type=str, help="Backfill end (UTC, exclusive)")
    args = p.parse_args(argv)

    if args.hour:
        compute_hourly_rollup(_parse_iso_hour(args.hour))
    elif args.catch_up:
        catch_up_rollups()
    elif args.backfill:
        if not args.start or not args.end:
            p.error("--backfill requires --start and --end")
        n = backfill(_parse_iso_hour(args.start), _parse_iso_hour(args.end))
        log.info("backfill: %d hours processed", n)
    else:
        p.print_help()
        return 1
    return 0


# ── Re-exports for existing callers ─────────────────────────────
from ._rollup_scheduler import catch_up_rollups, backfill, scheduler_loop  # noqa: E402,F401


if __name__ == "__main__":
    sys.exit(main())
