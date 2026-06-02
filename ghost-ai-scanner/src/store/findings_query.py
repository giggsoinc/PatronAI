# =============================================================
# FILE: src/store/findings_query.py
# PROJECT: PatronAI
# VERSION: 1.0.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Phase 1A. Read helpers + MCP-hash registry. Kept separate
#          from findings_store.py to honour the 150-LOC cap when these
#          helpers landed. All functions take an s3 client + bucket
#          rather than depending on FindingsStore — easier to test,
#          easier for the dashboard to reuse without circular imports.
# DEPENDS: boto3, json, logging
# AUDIT LOG:
#   v1.0.0  2026-04-26  Initial. Phase 1A.
# =============================================================

import json
import logging
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger("marauder-scan.findings_query")

_MCP_HASH_PREFIX = "mcp_hashes/"


def _mcp_hash_key(device: str, mcp_host: str) -> str:
    """Compute the S3 key where one device+host MCP hash lives."""
    safe_dev  = device.replace("/", "_").replace(" ", "_")[:120]
    safe_host = mcp_host.replace("/", "_").replace(" ", "_")[:60]
    return f"{_MCP_HASH_PREFIX}{safe_dev}/{safe_host}.txt"


def last_known_mcp_hash(s3_client, bucket: str,
                        device: str, mcp_host: str) -> str:
    """Return the most recent SHA-256 we saw for (device, mcp_host),
    or '' if none recorded yet. Never raises — returns '' on error."""
    key = _mcp_hash_key(device, mcp_host)
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        return resp["Body"].read().decode("utf-8").strip()
    except Exception:  # intentional: returns safe default on any error
        return ""


def record_mcp_hash(s3_client, bucket: str,
                    device: str, mcp_host: str, sha256: str) -> bool:
    """Persist the latest MCP-config hash for (device, mcp_host).
    Idempotent — overwriting with the same value is a no-op."""
    key = _mcp_hash_key(device, mcp_host)
    try:
        s3_client.put_object(Bucket=bucket, Key=key,
                             Body=sha256.encode("utf-8"),
                             ContentType="text/plain")
        return True
    except Exception as e:
        log.warning(f"record_mcp_hash failed for {key}: {e}")
        return False


def read_by_email(findings_store, email: str, days: int = 30) -> list:
    """Return all findings for `email` across the last `days` days, as a
    flat list of dicts. Iterates date partitions descending."""
    return _scan_by_field(findings_store, "email", email, days)


def read_by_repo(findings_store, repo_name: str, days: int = 30) -> list:
    """Return all findings whose `repo_name` matches across the last
    `days` days. Used by the dashboard's per-repo asset map."""
    return _scan_by_field(findings_store, "repo_name", repo_name, days)


def _scan_by_field(findings_store, field: str, value: str, days: int) -> list:
    """Generic scan: walk last `days` daily partitions, filter findings
    where finding[field] == value. Stops early at first partition gap."""
    if not value:
        return []
    out: list = []
    today = date.today()
    for delta in range(days):
        target = (today - timedelta(days=delta)).isoformat()
        try:
            df = findings_store.read(target_date=target, severity=None,
                                     limit=10_000)
        except Exception as e:
            log.debug(f"read_by_field skip {target}: {e}")
            continue
        if df.is_empty():
            continue
        try:
            rows = df.to_dicts()
        except Exception:  # intentional: returns safe default on any error
            rows = []
        for r in rows:
            if r.get(field) == value:
                out.append(r)
    return out
