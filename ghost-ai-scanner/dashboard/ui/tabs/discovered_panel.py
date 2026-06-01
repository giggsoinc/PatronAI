# =============================================================
# FILE: dashboard/ui/tabs/discovered_panel.py
# VERSION: 1.0.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Slim L2 review queue — surfaces domains the matcher
#          flagged UNKNOWN (no allow/deny match) in the last 7 days,
#          ranked by event count. Admin can one-click promote to the
#          custom deny list or dismiss. No Gemma classifier yet —
#          pure aggregation; the LLM-assisted version is future work.
# DEPENDS: streamlit, pandas, boto3, matcher.rule_model, audit
# AUDIT LOG:
#   v1.0.0  2026-04-25  Initial. Group 2 — sustainable curation on-ramp.
# =============================================================

import io
import json
import logging
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import boto3
import pandas as pd
import streamlit as st

from .. import audit as _audit

log    = logging.getLogger("patronai.ui.discovered_panel")
BUCKET = os.environ.get("MARAUDER_SCAN_BUCKET", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")

DENY_CUSTOM_KEY = "config/unauthorized_custom.csv"
DISMISSED_KEY   = "config/discovered_dismissed.txt"


def render(is_admin: bool, email: str = "") -> None:
    """Discovered AI tools section — top UNKNOWN domains + promote/dismiss."""
    st.markdown("**Discovered AI tools — review queue**")
    st.caption(
        "Domains the matcher flagged UNKNOWN in the last 7 days. "
        "Ranked by event count. Promote to your custom deny list with one click, "
        "or dismiss to hide."
    )
    rows = _aggregate_unknowns()
    dismissed = _load_dismissed()
    rows = [r for r in rows if r["domain"] not in dismissed]
    if not rows:
        st.caption("No new unknown AI domains observed in the last 7 days.")
        return
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
    if not is_admin:
        return
    options = [r["domain"] for r in rows]
    picked = st.multiselect("Select domain(s) to act on", options, key="disc::picked")
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("Promote to deny", type="primary", key="disc::promote") and picked:
        _promote_to_deny(picked, email)
        st.rerun()
    if c2.button("Dismiss", key="disc::dismiss") and picked:
        _persist_dismissed(dismissed | set(picked), email, len(dismissed))
        st.rerun()


def _aggregate_unknowns() -> list:
    """Walk last-7-days findings; return [{domain, events, last_seen, sample_device}]."""
    s3 = boto3.client("s3", region_name=REGION)
    counters: Counter = Counter()
    last_seen: dict   = {}
    sample_dev: dict  = {}
    today = datetime.now(timezone.utc).date()
    paginator = s3.get_paginator("list_objects_v2")
    for offset in range(0, 7):
        d = today - timedelta(days=offset)
        prefix = f"ocsf/findings/{d.year}/{d.month:02d}/{d.day:02d}/"
        try:
            for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
                for obj in page.get("Contents", []):
                    _ingest_finding(s3, obj["Key"], counters, last_seen, sample_dev)
        except Exception as exc:
            log.debug("findings list %s failed: %s", prefix, exc)
    return [
        {"domain": dom, "events": cnt,
         "last_seen": last_seen.get(dom, ""), "sample_device": sample_dev.get(dom, "")}
        for dom, cnt in counters.most_common(50)
    ]


def _ingest_finding(s3, key: str, counters: Counter,
                    last_seen: dict, sample_dev: dict) -> None:
    """Read one finding object; if it's UNKNOWN with a domain, count it."""
    try:
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        payload = json.loads(body)
    except Exception:  # intentional: must not crash
        return
    if payload.get("outcome") != "UNKNOWN":
        return
    domain = (payload.get("dst_domain") or payload.get("domain") or "").lower().strip()
    if not domain:
        return
    counters[domain] += 1
    ts = payload.get("timestamp") or payload.get("time", "")
    if ts and (domain not in last_seen or ts > last_seen[domain]):
        last_seen[domain] = ts
    sample_dev.setdefault(domain, payload.get("device_id", "") or payload.get("src_ip", ""))


def _load_dismissed() -> set:
    """Read the persisted dismissed-domain set; empty on miss."""
    try:
        s3 = boto3.client("s3", region_name=REGION)
        body = s3.get_object(Bucket=BUCKET, Key=DISMISSED_KEY)["Body"].read().decode()
        return {ln.strip() for ln in body.splitlines() if ln.strip() and not ln.startswith("#")}
    except Exception:  # intentional: must not crash
        return set()


def _persist_dismissed(domains: set, email: str, before_count: int) -> None:
    """Overwrite the dismissed file in S3 + write an audit row."""
    body = "# Dismissed by admin via Provider Lists tab\n" + "\n".join(sorted(domains)) + "\n"
    try:
        boto3.client("s3", region_name=REGION).put_object(
            Bucket=BUCKET, Key=DISMISSED_KEY, Body=body.encode(), ContentType="text/plain",
        )
        _audit.write(email, "discovered.dismissed", str(before_count), str(len(domains)))
    except Exception as exc:
        log.error("dismissed save failed: %s", exc)


def _promote_to_deny(domains: list, email: str) -> None:
    """Append picked domains as deny rows in the custom CSV."""
    s3 = boto3.client("s3", region_name=REGION)
    try:
        existing = s3.get_object(Bucket=BUCKET, Key=DENY_CUSTOM_KEY)["Body"].read().decode()
    except Exception:  # intentional: must not crash
        existing = "name,category,domain,port,severity,notes\n"
    if not existing.endswith("\n"):
        existing += "\n"
    for d in domains:
        existing += f"Discovered {d},Discovered,{d},443,HIGH,Auto-promoted from review queue\n"
    try:
        s3.put_object(Bucket=BUCKET, Key=DENY_CUSTOM_KEY,
                      Body=existing.encode(), ContentType="text/csv")
        _audit.write(email, "discovered.promoted", "", ", ".join(domains))
        st.success(f"Promoted {len(domains)} domain(s) to your custom deny list.")
    except Exception as exc:
        log.error("promote failed: %s", exc)
        st.error(f"Promote failed: {exc}")
