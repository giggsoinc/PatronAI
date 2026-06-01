# =============================================================
# FILE: src/jobs/_rollup_s3.py
# VERSION: 1.0.0
# UPDATED: 2026-06-01
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Extracted from hourly_rollup.py — S3 client helpers,
#          gzip put, SQL-safe ISO formatter, and windowed S3 Select.
#          See hourly_rollup.py for context.
# =============================================================
from __future__ import annotations

import gzip
import json
import logging
import os
from datetime import datetime, timezone
from typing import Iterable

import boto3

log = logging.getLogger("marauder-scan.jobs.hourly_rollup")

_BUCKET = os.environ.get("MARAUDER_SCAN_BUCKET", "")
_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _s3():
    return boto3.client("s3", region_name=_REGION)


def _put_gz(s3, key: str, payload: dict) -> None:
    body = gzip.compress(json.dumps(payload, default=str).encode("utf-8"))
    s3.put_object(Bucket=_BUCKET, Key=key, Body=body,
                  ContentType="application/json",
                  ContentEncoding="gzip")


def _sql_escape_iso(ts: datetime) -> str:
    """Render a datetime as an ISO string and strip anything that could
    break out of an S3 Select string literal. Inputs here are internally
    constructed datetime objects (never user-tainted), but this keeps the
    static analysers happy and is defense-in-depth if a future caller
    ever passes through external data."""
    iso = ts.isoformat()
    # Datetimes never contain quotes / newlines / NUL, but enforce it.
    return "".join(c for c in iso if c not in ("'", "\x00", "\n", "\r", "\\"))


def _select_rows_for_window(s3, key: str, window_start: datetime,
                            window_end: datetime) -> Iterable[dict]:
    """S3 Select rows in [window_start, window_end). Pushes timestamp filter
    to S3 so we never download out-of-window events. Falls back to GetObject
    on Select failure (e.g. mixed-schema parse errors) — degraded but works."""
    iso_start = _sql_escape_iso(window_start)
    iso_end   = _sql_escape_iso(window_end)
    # nosec B608 — string concatenation here builds a fixed query shape with
    # only sanitised internal datetimes; S3 Select doesn't support parameter
    # binding so escaping is the only mechanism.
    sql = (f"SELECT s.provider, s.category, s.severity, s.owner, s.email, "  # nosec B608
           f"s.src_hostname, s.device_uuid, s.timestamp, s.company "
           f"FROM s3object s "
           f"WHERE s.timestamp >= '{iso_start}' AND s.timestamp < '{iso_end}'")
    try:
        resp = s3.select_object_content(
            Bucket=_BUCKET, Key=key, ExpressionType="SQL", Expression=sql,
            InputSerialization={"JSON": {"Type": "LINES"}, "CompressionType": "NONE"},
            OutputSerialization={"JSON": {"RecordDelimiter": "\n"}},
        )
        for ev in resp["Payload"]:
            if "Records" not in ev:
                continue
            chunk = ev["Records"]["Payload"].decode()
            for line in chunk.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except s3.exceptions.NoSuchKey:
        return
    except Exception as exc:
        log.warning("S3 Select failed on %s: %s — falling back to GetObject", key, exc)
        try:
            obj = s3.get_object(Bucket=_BUCKET, Key=key)
            for line in obj["Body"].iter_lines():
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get("timestamp", "")
                if iso_start <= ts < iso_end:
                    yield row
        except s3.exceptions.NoSuchKey:
            return
        except Exception as exc2:
            log.error("Fallback GetObject also failed on %s: %s", key, exc2)


def _append_unknown_providers(s3, unknowns: set[tuple[str, str]]) -> None:
    key = "rollup-meta/unknown_providers.jsonl"
    try:
        try:
            existing = s3.get_object(Bucket=_BUCKET, Key=key)["Body"].read().decode()
        except s3.exceptions.NoSuchKey:
            existing = ""
        ts = datetime.now(timezone.utc).isoformat()
        new_lines = "\n".join(
            json.dumps({"ts": ts, "category": c, "raw_provider": p})
            for c, p in sorted(unknowns)
        )
        body = (existing.rstrip("\n") + "\n" + new_lines).lstrip("\n").encode()
        s3.put_object(Bucket=_BUCKET, Key=key, Body=body,
                      ContentType="application/x-ndjson")
    except Exception as exc:
        log.debug("unknown_providers append failed (non-fatal): %s", exc)
