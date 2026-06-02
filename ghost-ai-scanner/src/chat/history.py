# =============================================================
# FILE: src/chat/history.py
# VERSION: 1.1.0
# UPDATED: 2026-04-29
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Chat history persistence — S3 JSONL read/write.
#          Path: s3://{bucket}/chat/{sha256(email)[:16]}/{view}/YYYY-MM-DD.jsonl
#          Email is never stored in S3; SHA-256 prefix only (privacy).
#          All errors are swallowed — history failure must never
#          crash the dashboard chat widget.
# DEPENDS: boto3
# AUDIT LOG:
#   v1.0.0  2026-04-28  Initial.
#   v1.1.0  2026-04-29  Add clear_history (Clear button → S3 delete) +
#                       ensure_lifecycle_policy (auto-expire chat/ keys
#                       after CHAT_HISTORY_RETENTION_DAYS, default 30).
# =============================================================

import hashlib
import json
import logging
import os
from datetime import date

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger("patronai.chat.history")

_BUCKET  = os.environ.get("MARAUDER_SCAN_BUCKET", "")
_REGION  = os.environ.get("AWS_REGION", "us-east-1")
_WINDOW  = 20  # max messages loaded on widget open


# ── Path helpers ──────────────────────────────────────────────

def _hash(email: str) -> str:
    """First 16 hex chars of SHA-256(email) — privacy-safe folder name."""
    return hashlib.sha256(email.lower().encode()).hexdigest()[:16]


def _key_today(email: str, view: str) -> str:
    return f"chat/{_hash(email)}/{view}/{date.today().isoformat()}.jsonl"


def _prefix(email: str, view: str) -> str:
    return f"chat/{_hash(email)}/{view}/"


# ── Public API ────────────────────────────────────────────────

def load_history(email: str, view: str) -> list:
    """Return up to _WINDOW most-recent messages for user+view.
    Scans up to 3 daily files newest-first. Returns [] on any error."""
    bkt = _BUCKET
    if not bkt:
        return []
    try:
        s3   = boto3.client("s3", region_name=_REGION)
        resp = s3.list_objects_v2(Bucket=bkt, Prefix=_prefix(email, view))
        keys = sorted([o["Key"] for o in resp.get("Contents", [])],
                      reverse=True)
        msgs: list = []
        for key in keys[:3]:
            try:
                raw   = s3.get_object(Bucket=bkt, Key=key)["Body"].read()
                lines = [ln for ln in raw.decode().splitlines() if ln.strip()]
                for ln in reversed(lines):
                    msgs.insert(0, json.loads(ln))
                    if len(msgs) >= _WINDOW:
                        break
            except Exception:  # intentional: returns safe default on any error
                continue
            if len(msgs) >= _WINDOW:
                break
        return msgs[-_WINDOW:]
    except Exception as exc:
        log.warning("history load failed: %s", exc)
        return []


def append_history(email: str, view: str, messages: list) -> None:
    """Append messages to today's JSONL file. Silent on error.
    Each message is a dict with keys: role, content, ts."""
    bkt = _BUCKET
    if not bkt or not messages:
        return
    try:
        s3  = boto3.client("s3", region_name=_REGION)
        key = _key_today(email, view)
        try:
            existing = s3.get_object(Bucket=bkt, Key=key)["Body"].read().decode()
        except ClientError:
            existing = ""
        new_lines = "\n".join(json.dumps(m) for m in messages)
        body = (existing.rstrip("\n") + "\n" + new_lines).lstrip("\n")
        s3.put_object(Bucket=bkt, Key=key, Body=body.encode(),
                      ContentType="application/x-ndjson")
    except Exception as exc:
        log.warning("history append failed: %s", exc)


def clear_history(email: str, view: str) -> tuple[bool, int]:
    """Delete every chat-history key under chat/{hash16}/{view}/.
    Returns (success, deleted_count). Other users' data untouched.
    Silent-but-reported on failure — caller (widget) surfaces a toast."""
    bkt = _BUCKET
    if not bkt:
        return (False, 0)
    try:
        s3 = boto3.client("s3", region_name=_REGION)
        prefix = _prefix(email, view)
        deleted = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bkt, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if not keys:
                continue
            # delete_objects caps at 1000 per call.
            for i in range(0, len(keys), 1000):
                batch = keys[i:i + 1000]
                s3.delete_objects(Bucket=bkt, Delete={"Objects": batch,
                                                     "Quiet": True})
                deleted += len(batch)
        log.info("clear_history: deleted %d keys under %s", deleted, prefix)
        return (True, deleted)
    except Exception as exc:
        log.warning("clear_history failed for %s/%s: %s", email, view, exc)
        return (False, 0)


_RULE_ID = "patronai-chat-history-expiry"


def ensure_lifecycle_policy(retention_days: int = 30) -> bool:
    """Ensure the bucket has a lifecycle rule expiring keys under chat/
    after `retention_days`. Merges with any existing rules — never replaces
    them. Idempotent: safe to call on every startup.

    Returns True on success (or unchanged), False on failure."""
    bkt = _BUCKET
    if not bkt:
        return False
    try:
        s3 = boto3.client("s3", region_name=_REGION)
        try:
            cur = s3.get_bucket_lifecycle_configuration(Bucket=bkt)
            existing_rules = cur.get("Rules", [])
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchLifecycleConfiguration",
                        "NoSuchBucketPolicy"):
                existing_rules = []
            else:
                raise

        desired = {
            "ID": _RULE_ID,
            "Filter": {"Prefix": "chat/"},
            "Status": "Enabled",
            "Expiration": {"Days": int(retention_days)},
            # Also clean up incomplete multipart uploads in the same prefix.
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
        }

        # Replace our rule by ID if present; preserve all other rules verbatim.
        merged = [r for r in existing_rules if r.get("ID") != _RULE_ID]
        # If an identical rule already exists, no-op.
        for r in existing_rules:
            if r.get("ID") == _RULE_ID and \
               r.get("Filter", {}).get("Prefix") == "chat/" and \
               int(r.get("Expiration", {}).get("Days", -1)) == int(retention_days):
                log.debug("ensure_lifecycle_policy: rule already up-to-date")
                return True
        merged.append(desired)

        s3.put_bucket_lifecycle_configuration(
            Bucket=bkt,
            LifecycleConfiguration={"Rules": merged},
        )
        log.info("ensure_lifecycle_policy: chat/ → expire after %d days "
                 "(total rules: %d)", retention_days, len(merged))
        return True
    except Exception as exc:
        log.warning("ensure_lifecycle_policy failed: %s", exc)
        return False
