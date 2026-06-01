# =============================================================
# FILE: dashboard/ui/tabs/provider_lists_io.py
# VERSION: 1.1.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: S3 IO + CSV-to-DataFrame helpers for the Provider Lists tab.
#          Extracted from provider_lists.py to honour the 150-LOC cap.
#          Session-state cache keyed by S3 path — first render fetches
#          from S3; subsequent renders read from session_state. Save
#          updates the cache; clear_cache() drops it.
# DEPENDS: boto3, pandas, streamlit, matcher.rule_model, ui.audit
# AUDIT LOG:
#   v1.0.0  2026-04-25  Initial. Group 6.
#   v1.1.0  2026-04-25  Group 6.5 — session-state cache + clear_cache().
# =============================================================

import io
import json
import logging
import os

import boto3
import pandas as pd
import streamlit as st

from matcher.rule_model import parse_csv_text
from .. import audit as _audit

log    = logging.getLogger("patronai.ui.provider_lists_io")
BUCKET = os.environ.get("MARAUDER_SCAN_BUCKET", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")


def render_status_banner(status_key: str) -> None:
    """Show a red banner when the latest rule load is degraded."""
    try:
        s3 = boto3.client("s3", region_name=REGION)
        body = s3.get_object(Bucket=BUCKET, Key=status_key)["Body"].read()
        status = json.loads(body)
    except Exception:  # intentional: must not crash
        return
    if status.get("below_threshold"):
        st.error(
            f"⚠ Ruleset degraded: only {status.get('deny_count', 0)} deny rules "
            f"loaded (threshold {status.get('strict_min_rules', 50)}). "
            "Matcher running with reduced coverage — review baseline + custom CSVs."
        )


def render_readonly_csv(key: str) -> None:
    """Render a CSV at `key` as a read-only dataframe; comment lines stripped."""
    try:
        s3 = boto3.client("s3", region_name=REGION)
        raw = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
        body = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("#"))
        df = pd.read_csv(io.StringIO(body))
        st.dataframe(df, use_container_width=True, hide_index=True)
    except Exception as exc:
        log.debug("read-only render of %s failed: %s", key, exc)
        st.info("Not yet provisioned in tenant storage.")


def read_csv_df(key: str, cols: list) -> pd.DataFrame:
    """Cached read. First call fetches from S3; later calls read st.session_state."""
    cache_key = f"cache::{key}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    df = _fetch_csv_df(key, cols)
    st.session_state[cache_key] = df
    return df


def _fetch_csv_df(key: str, cols: list) -> pd.DataFrame:
    """Fetch a CSV from S3 into a DataFrame; empty-with-cols on miss."""
    try:
        s3 = boto3.client("s3", region_name=REGION)
        raw = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
        body = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("#"))
        return pd.read_csv(io.StringIO(body)) if body.strip() else pd.DataFrame(columns=cols)
    except Exception:  # intentional: must not crash
        return pd.DataFrame(columns=cols)


def clear_cache(key: str, cols: list) -> None:
    """Reset the session-state cache for `key` to an empty-with-cols DataFrame."""
    st.session_state[f"cache::{key}"] = pd.DataFrame(columns=cols)


def read_validated(key: str, validator) -> list:
    """Read a CSV at `key` and return validated rows only (errors discarded)."""
    try:
        s3 = boto3.client("s3", region_name=REGION)
        raw = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
    except Exception:  # intentional: must not crash
        return []
    rows, _ = parse_csv_text(raw, validator)
    return rows


def put_csv(key: str, body: str, email: str, audit_field: str,
            before: int, after: int, override: bool) -> None:
    """Write a CSV body to S3, refresh the cache, append an audit row, toast."""
    try:
        boto3.client("s3", region_name=REGION).put_object(
            Bucket=BUCKET, Key=key, Body=body.encode(), ContentType="text/csv",
        )
        # Refresh cache to the saved value so subsequent renders match S3
        try:
            stripped = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
            st.session_state[f"cache::{key}"] = (
                pd.read_csv(io.StringIO(stripped)) if stripped.strip() else pd.DataFrame()
            )
        except Exception as exc:
            log.debug("cache refresh after put_csv failed: %s", exc)
        suffix = " (override)" if override else ""
        _audit.write(email, audit_field, f"{before} entries", f"{after} entries{suffix}")
        st.success(f"Saved · {after} rows.")
    except Exception as exc:
        log.error("save %s failed: %s", key, exc)
        st.error(f"Save failed: {exc}")
