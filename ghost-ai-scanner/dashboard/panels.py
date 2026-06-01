# =============================================================
# FILE: dashboard/panels.py
# VERSION: 1.2.0
# UPDATED: 2026-04-19
# OWNER: Giggso Inc
# PURPOSE: Reusable UI panels for the PatronAI settings app.py entry point.
#          Health metrics, action buttons, sidebar, Allow list editor.
#          De-technicalised: no AWS names, no internal ports or hostnames.
# AUDIT LOG:
#   v1.0.0  2026-04-18  Initial
#   v1.1.0  2026-04-19  Pam/Steve links removed; GRAFANA_URL public path
#   v1.2.0  2026-04-19  De-technicalise copy; remove internal docker URLs
# =============================================================

import os
import boto3
import streamlit as st
from datetime import datetime, timezone

BUCKET      = os.environ.get("MARAUDER_SCAN_BUCKET", "")
REGION      = os.environ.get("AWS_REGION", "us-east-1")
GRAFANA_URL = os.environ.get("GRAFANA_URL", "/grafana")


def health(store) -> None:
    """Scanner health panel — shown to all roles."""
    st.subheader("Scanner Health")

    try:
        cursor  = store.cursor.read()
        summary = store.summary.read()
        dedup   = store.dedup.stats()
    except Exception as e:
        st.error(f"Could not load health data: {e}")
        return

    last_scan = cursor.get("last_processed_at", "")
    if last_scan:
        try:
            dt       = datetime.fromisoformat(last_scan)
            now      = datetime.now(timezone.utc)
            mins_ago = int((now - dt).total_seconds() / 60)
            lag      = f"{mins_ago} min ago"
            status   = "🟢 Running" if mins_ago < 15 else "🔴 Lagging"
        except Exception:  # intentional: must not crash
            lag, status = last_scan[:16], "🟡 Unknown"
    else:
        lag, status = "Never", "🔴 Not started"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status",          status)
    c2.metric("Last Scan",       lag)
    c3.metric("Files Processed", cursor.get("files_processed", 0))
    c4.metric("Events Today",    summary.get("total_events", 0))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Alerts Today",      summary.get("alerts_fired",     0))
    c6.metric("Providers Detected",summary.get("unique_providers", 0))
    c7.metric("Dedup Suppressed",  dedup.get("total_suppressed_today", 0))
    built_at = summary.get("built_at", "")
    c8.metric("Summary Built", built_at[:16] if built_at else "Pending")


def actions(store, summarizer, is_admin: bool) -> None:
    """Action buttons — Refresh Now for all, rest admin only."""
    st.subheader("Actions")
    c1, c2, c3 = st.columns(3)

    with c1:
        if st.button("🔄 Refresh Now", use_container_width=True):
            try:
                with st.spinner("Rebuilding summary..."):
                    result = summarizer.run_now()
                st.success(
                    f"Done — {result.get('total_events', 0)} events, "
                    f"{result.get('build_duration_seconds', 0)}s"
                )
            except Exception as e:
                st.error(f"Refresh failed: {e}")

    if is_admin:
        with c2:
            if st.button("📦 Backfill (90 days)", use_container_width=True):
                try:
                    with st.spinner("Backfilling..."):
                        results = summarizer.backfill(days=90)
                    st.success(f"Backfill complete — {len(results)} days with data.")
                except Exception as e:
                    st.error(f"Backfill failed: {e}")

        with c3:
            if st.button("⚡ Force Rescan", use_container_width=True):
                try:
                    store.cursor.reset()
                    st.warning("Cursor reset. Scanner will re-process from lookback window.")
                except Exception as e:
                    st.error(f"Rescan failed: {e}")


def csv_editor() -> None:
    """Inline Allow list editor — admin only."""
    with st.expander("Edit Allow list", expanded=False):
        st.caption("Pattern format: *.openai.com  or  api.openai.com")
        try:
            s3  = boto3.client("s3", region_name=REGION)
            raw = s3.get_object(
                Bucket=BUCKET, Key="config/authorized.csv"
            )["Body"].read().decode()
        except Exception:  # intentional: must not crash
            raw = "name,domain_pattern,notes\n"

        edited = st.text_area("Allow list", value=raw, height=200)
        if st.button("Save Allow list"):
            try:
                s3 = boto3.client("s3", region_name=REGION)
                s3.put_object(Bucket=BUCKET, Key="config/authorized.csv",
                              Body=edited.encode(), ContentType="text/csv")
                st.success("Saved to tenant storage.")
            except Exception as e:
                st.error(f"Save failed: {e}")


def sidebar(email: str, is_admin: bool) -> None:
    """Sidebar with role indicator and dashboard link."""
    with st.sidebar:
        try:
            st.image("assets/branding/patronai-logo.png", width=200)
        except Exception:  # intentional: must not crash
            st.title("PatronAI")
        st.caption(f"Signed in as: **{email}**")
        st.caption(f"Role: {'Admin' if is_admin else 'Viewer'}")
        st.divider()
        grafana_dash = f"{GRAFANA_URL.rstrip('/')}/d/marauder-overview"
        st.markdown(f"[📊 Open dashboard]({grafana_dash})")
        st.divider()
        st.markdown("**Tenant storage**")
        st.code(BUCKET or "not set", language=None)
        st.caption(f"Region: {REGION}")
        st.divider()
        st.markdown("<small>PatronAI · v1.1.0</small>", unsafe_allow_html=True)
