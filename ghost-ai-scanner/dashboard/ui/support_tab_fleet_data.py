# =============================================================
# FILE: dashboard/ui/support_tab_fleet_data.py
# PROJECT: PatronAI — Mega-PR
# VERSION: 2.0.0
# UPDATED: 2026-04-27
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Pure-data helpers for the Agent Fleet tab. Discovers ALL
#          deployed tokens from ocsf/agent/scans/ prefix — not just
#          catalog.json — so agents installed out-of-band or before the
#          catalog feature existed still appear in the fleet view.
# DEPENDS: boto3 (provided by caller via the s3 client param)
# AUDIT LOG:
#   v1.0.0  2026-04-27  Initial. Mega-PR.
#   v2.0.0  2026-04-27  Discovery from ocsf/agent/scans/ prefix so
#                       uncatalogued agents (e.g. Akila) are visible.
# =============================================================

import json
import logging
from datetime import datetime, timezone, timedelta

log = logging.getLogger("patronai.ui.support_fleet.data")

_SCANS_PREFIX = "ocsf/agent/scans/"


def load_catalog(s3, bucket: str) -> dict:
    """Load catalog.json → dict keyed by token. {} on miss."""
    try:
        obj = s3.get_object(Bucket=bucket,
                            Key="config/HOOK_AGENTS/catalog.json")
        entries = json.loads(obj["Body"].read())
        return {e["token"]: e for e in entries if e.get("token")}
    except Exception as e:
        log.warning("catalog load failed: %s", e)
        return {}


def discover_all_tokens(s3, bucket: str) -> dict:
    """List ocsf/agent/scans/ and return {token: s3_last_modified}.
    This finds every agent that has ever uploaded — regardless of catalog."""
    tokens: dict = {}
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket,
                                       Prefix=_SCANS_PREFIX,
                                       Delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                prefix = cp.get("Prefix", "")
                token  = prefix.rstrip("/").split("/")[-1]
                if token:
                    tokens[token] = None
        # Grab last-modified from latest.json for each token
        for token in list(tokens):
            try:
                head = s3.head_object(Bucket=bucket,
                                      Key=f"{_SCANS_PREFIX}{token}/latest.json")
                tokens[token] = head.get("LastModified")
            except Exception:
                pass
    except Exception as e:
        log.warning("Token discovery failed: %s", e)
    return tokens


def load_scan_info(s3, bucket: str, token: str) -> dict:
    """Read latest.json for a token. Returns {} on miss."""
    try:
        obj = s3.get_object(Bucket=bucket,
                            Key=f"{_SCANS_PREFIX}{token}/latest.json")
        return json.loads(obj["Body"].read())
    except Exception:  # intentional: must not crash
        return {}


def load_status(s3, bucket: str, token: str) -> dict:
    """Load heartbeat status.json for a token. Returns {} on miss."""
    try:
        obj = s3.get_object(
            Bucket=bucket,
            Key=f"config/HOOK_AGENTS/{token}/status.json",
        )
        return json.loads(obj["Body"].read())
    except Exception:  # intentional: must not crash
        return {}


def build_fleet_entries(s3, bucket: str) -> list:
    """Merge catalog + discovered tokens into a list of fleet row dicts.
    Catalog provides name/email/os_type. For uncatalogued tokens,
    identity falls back to latest.json email/src_hostname fields."""
    catalog = load_catalog(s3, bucket)
    tokens  = discover_all_tokens(s3, bucket)
    # Ensure catalog tokens are included even if scan prefix is missing
    for t in catalog:
        tokens.setdefault(t, None)

    entries = []
    for token, s3_mtime in tokens.items():
        cat     = catalog.get(token, {})
        status  = load_status(s3, bucket, token)
        scan    = {} if (cat and status) else load_scan_info(s3, bucket, token)

        # Identity: prefer catalog → latest.json
        name  = (cat.get("recipient_name") or
                 scan.get("email") or
                 scan.get("owner") or "—")
        email = (cat.get("recipient_email") or
                 scan.get("email") or
                 scan.get("owner") or "")

        # Device: prefer heartbeat status → scan → S3 mtime label
        device = (status.get("device_id") or
                  scan.get("src_hostname") or
                  scan.get("device_id") or "—")

        os_name = status.get("os_name", cat.get("os_type", "—"))
        os_ver  = status.get("os_version", "")

        # Last-seen: heartbeat timestamp → S3 last-modified of latest.json
        ts_str   = status.get("timestamp", "")
        ev_type  = status.get("event_type", "")
        s3_ts    = s3_mtime  # datetime or None

        entries.append({
            "token":    token,
            "name":     name,
            "email":    email,
            "device":   device,
            "os":       f"{os_name} {os_ver}".strip(),
            "ts_str":   ts_str,
            "ev_type":  ev_type,
            "s3_mtime": s3_ts,
            "os_type":  cat.get("os_type", ""),
            "catalogued": bool(cat),
        })
    return entries


def fmt_age(delta: timedelta) -> str:
    """Human-readable age string."""
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    return f"{secs // 3600}h ago"
