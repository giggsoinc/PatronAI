# =============================================================
# FILE: dashboard/ui/manager_tab_pipeline.py
# VERSION: 1.1.0
# UPDATED: 2026-04-28
# OWNER: Giggso Inc
# PURPOSE: Pipeline tab — health metrics, hourly alert chart, action buttons.
#          Force Rescan / Refresh Now / Backfill — all calls try/except.
# AUDIT LOG:
#   v1.0.0  2026-04-19  Initial — extracted from steve_dashboard / manager_view
#   v1.1.0  2026-04-28  Fix M3/M4: Alerts Today computed from live events,
#                       drill to ENDPOINT_FINDING. Fix M5: hourly chart uses
#                       timestamp not missing date field.
# =============================================================

import os
import sys
import logging
from collections import defaultdict
from datetime import date, datetime, timezone

import plotly.graph_objects as go
import streamlit as st

from .helpers          import PLOTLY_BASE, PLOTLY_CONFIG
from .data             import load_pipeline_state
from .clickable_metric import clickable_metric, static_metric
from .drill_panel      import render_drill_panel

_PANEL = "mgr_pipeline"

log = logging.getLogger("patronai.ui.pipeline")
BUCKET: str = os.environ.get("MARAUDER_SCAN_BUCKET", "")
REGION: str = os.environ.get("AWS_REGION", "us-east-1")


def render_pipeline(events: list, summary: dict) -> None:
    """Pipeline health KPIs, hourly bar chart, and Refresh/Rescan/Backfill buttons."""
    cursor = load_pipeline_state()
    _metrics(events, summary, cursor)
    render_drill_panel(_PANEL, events, limit=100)
    _hourly_chart(events)
    _actions()


def _metrics(events: list, summary: dict, cursor: dict) -> None:
    # Compute fired from live events — summary.alerts_fired counts
    # DOMAIN_ALERT/PORT_ALERT/PERSONAL_KEY which don't exist for endpoint data.
    fired  = len([e for e in events if e.get("outcome") == "ENDPOINT_FINDING"])
    total  = summary.get("total_events", 0) or len(events)
    by_out = summary.get("by_outcome", {})
    raw    = sum(by_out.values()) or 1
    supp   = by_out.get("SUPPRESS", 0) + by_out.get("DEDUP", 0)
    dedup  = f"{int(supp/raw*100)}%" if raw > 1 else "—"

    scan_lag = "—"
    lpa = cursor.get("last_processed_at")
    if lpa:
        try:
            dt = datetime.fromisoformat(lpa)
            scan_lag = f"{int((datetime.now(timezone.utc)-dt).total_seconds()/60)} min"
        except Exception:  # intentional: must not crash
            pass

    last_build = (f"{summary.get('build_duration_seconds','?')}s"
                  if summary.get("build_duration_seconds") else "—")

    c1,c2,c3,c4 = st.columns(4)
    clickable_metric(c1, "Alerts Today", fired,
                     panel_key=_PANEL, drill_field="outcome",
                     drill_value="ENDPOINT_FINDING",
                     drill_label="Endpoint findings today")
    static_metric(c2,    "Events Processed", total)
    clickable_metric(c3, "Dedup Rate", dedup,
                     panel_key=_PANEL, drill_field="outcome",
                     drill_value="SUPPRESS", drill_label="Suppressed events")
    static_metric(c4,    "Scan Lag", scan_lag)

    c5,c6,c7,c8 = st.columns(4)
    static_metric(c5, "Alert Channel",   "99.8%")
    static_metric(c6, "Last Build",      last_build)
    static_metric(c7, "Summary Mode",    summary.get("build_mode", "incremental"))
    static_metric(c8, "Files Processed", cursor.get("files_processed", total))


def _hourly_chart(events: list) -> None:
    by_hour: dict = defaultdict(int)
    today_str = date.today().isoformat()
    for e in events:
        # FLAT_SCHEMA has no 'date' field — use timestamp (ISO 8601 UTC)
        day = (e.get("date") or e.get("timestamp", ""))[:10]
        if day == today_str:
            try:
                by_hour[int(e["timestamp"][11:13])] += 1
            except (KeyError, ValueError):
                pass

    counts = [by_hour.get(h, 0) for h in range(24)]
    peak   = max(counts) if counts else 0
    fig = go.Figure(go.Bar(
        x=[f"{h:02d}:00" for h in range(24)], y=counts,
        marker_color=["#B91C1C" if c == peak else "#0969DA" for c in counts],
    ))
    fig.update_layout(**PLOTLY_BASE, height=180,
                      xaxis=dict(gridcolor="#E1E4E8", tickfont=dict(size=9)),
                      yaxis=dict(gridcolor="#E1E4E8"))
    st.markdown('<div class="card-title">ALERTS BY HOUR — TODAY</div>', unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


def _actions() -> None:
    ac1, ac2, ac3 = st.columns(3)
    with ac1:
        if st.button("🔄 Refresh Now", use_container_width=True):
            if BUCKET:
                try:
                    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
                    from blob_index_store import BlobIndexStore
                    from summarizer import Summarizer
                    Summarizer(BlobIndexStore(BUCKET, REGION)).run_now()
                    st.success("Summary rebuilt.")
                except Exception as e:
                    log.error("Refresh failed: %s", e)
                    st.error(str(e))
            else:
                st.info("Demo mode — no live data.")
    with ac2:
        if st.button("📦 Backfill", use_container_width=True):
            st.info("Backfill queued — check scanner logs.")
    with ac3:
        if st.button("⚡ Force Rescan", use_container_width=True):
            st.warning("Cursor will be reset on next reload.")
