# =============================================================
# FILE: dashboard/ui/support_tab_fleet_actions.py
# VERSION: 1.0.0
# UPDATED: 2026-04-29
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Agent Fleet action helpers — revoke a package token and
#          write an immutable audit log entry to S3. Uses boto3
#          directly so the UI layer stays independent of src/store/.
#          Revoke purges:
#            config/HOOK_AGENTS/{token}/   (meta, status, scripts)
#            ocsf/agent/scans/{token}/     (uploaded scan data)
#          Then removes the entry from catalog.json and appends to
#          config/HOOK_AGENTS/audit.jsonl (append-only, JSONL).
# AUDIT LOG:
#   v1.0.0  2026-04-29  Initial — revoke + audit log for fleet tab.
# =============================================================

import json
import logging
from datetime import datetime, timezone

log = logging.getLogger("patronai.ui.fleet.actions")

_CATALOG_KEY = "config/HOOK_AGENTS/catalog.json"
_AUDIT_KEY   = "config/HOOK_AGENTS/audit.jsonl"


def revoke_agent(s3, bucket: str, token: str,
                 name: str, email: str, revoked_by: str) -> bool:
    """Remove an agent token: purge S3 objects + write audit log.

    Steps:
      1. Remove token from catalog.json
      2. Purge config/HOOK_AGENTS/{token}/ objects
      3. Purge ocsf/agent/scans/{token}/ objects
      4. Append to audit.jsonl (immutable)

    Returns True on success, False on unrecoverable error.
    """
    try:
        _remove_from_catalog(s3, bucket, token)
        _purge_prefix(s3, bucket, f"config/HOOK_AGENTS/{token}/")
        _purge_prefix(s3, bucket, f"ocsf/agent/scans/{token}/")
        _append_audit(s3, bucket, json.dumps({
            "action":      "revoke",
            "token":       token,
            "agent_name":  name,
            "agent_email": email,
            "revoked_by":  revoked_by,
            "revoked_at":  datetime.now(timezone.utc).isoformat(),
        }) + "\n")
        log.info("revoked token %s by %s", token[:8], revoked_by)
        return True
    except Exception as exc:
        log.error("revoke_agent failed [%s]: %s", token[:8], exc)
        return False


def load_audit_log(s3, bucket: str, limit: int = 50) -> list:
    """Return the last `limit` audit log entries as a list of dicts."""
    try:
        raw = s3.get_object(Bucket=bucket,
                            Key=_AUDIT_KEY)["Body"].read().decode()
        lines = [ln for ln in raw.strip().split("\n") if ln][-limit:]
        return [json.loads(ln) for ln in reversed(lines)]
    except Exception:  # intentional: must not crash
        return []


# ── Private helpers ───────────────────────────────────────────

def _remove_from_catalog(s3, bucket: str, token: str) -> None:
    """Read catalog.json, drop the token entry, write back."""
    try:
        raw     = s3.get_object(Bucket=bucket, Key=_CATALOG_KEY)["Body"].read()
        catalog = [e for e in json.loads(raw) if e.get("token") != token]
        s3.put_object(Bucket=bucket, Key=_CATALOG_KEY,
                      Body=json.dumps(catalog, indent=2).encode(),
                      ContentType="application/json")
    except s3.exceptions.NoSuchKey:
        pass  # catalog missing — nothing to remove
    except Exception as exc:
        log.warning("catalog update failed: %s", exc)


def _purge_prefix(s3, bucket: str, prefix: str) -> None:
    """Delete all S3 objects under prefix (silently ignores errors)."""
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objects:
                s3.delete_objects(Bucket=bucket,
                                  Delete={"Objects": objects, "Quiet": True})
    except Exception as exc:
        log.warning("purge_prefix %s: %s", prefix, exc)


def _append_audit(s3, bucket: str, line: str) -> None:
    """Append a JSONL line to the audit log (read-modify-write)."""
    try:
        try:
            existing = s3.get_object(
                Bucket=bucket, Key=_AUDIT_KEY)["Body"].read().decode()
        except Exception:  # intentional: must not crash
            existing = ""
        s3.put_object(Bucket=bucket, Key=_AUDIT_KEY,
                      Body=(existing + line).encode(),
                      ContentType="application/x-ndjson")
    except Exception as exc:
        log.error("audit log write failed: %s", exc)
