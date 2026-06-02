# =============================================================
# FILE: dashboard/ui/exec_tab_risk.py
# VERSION: 1.1.0
# UPDATED: 2026-04-27
# OWNER: Giggso Inc
# PURPOSE: Risk Heatmap tab — category × severity matrix + top offenders.
# AUDIT LOG:
#   v1.0.0  2026-04-19  Initial — extracted from pam_dashboard / exec_view
#   v1.1.0  2026-04-27  Use category (finding type) instead of department
#                       for heatmap rows — department is empty for endpoint
#                       findings; category (mcp_server, browser, …) is set.
# =============================================================

from collections import defaultdict

import plotly.graph_objects as go
import streamlit as st

from .helpers      import PLOTLY_BASE, PLOTLY_CONFIG, SEV_COLOURS
from .drill_panel  import set_drill, render_drill_panel

_PANEL = "exec_risk"


def render_risk(events: list) -> None:
    """Category × severity heatmap and top-offender bar chart side-by-side.
    Rows = finding category (mcp_server, browser, package …); falls back to
    department when category is empty (network events). Click → drill."""
    col_l, col_r = st.columns([2, 1])
    with col_l:
        _heatmap(events)
    with col_r:
        _top_offenders(events)
    render_drill_panel(_PANEL, events, limit=100)


def _heatmap(events: list) -> None:
    """Rows = category (finding type); falls back to department for network
    events. This ensures the heatmap is populated even when identity
    resolution hasn't filled the department field yet."""
    def _row_label(e: dict) -> str:
        return e.get("category") or e.get("department") or "unknown"

    row_labels = sorted(set(_row_label(e) for e in events))
    row_labels  = [r for r in row_labels if r]   # drop empty
    if not row_labels:
        st.caption("No categorised events to display.")
        return

    sevs  = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    matrix = [
        [sum(1 for e in events if _row_label(e) == r and e.get("severity") == s)
         for s in sevs]
        for r in row_labels
    ]
    fig = go.Figure(go.Heatmap(
        z=matrix, x=sevs, y=row_labels,
        colorscale=[[0,"#F6F8FA"],[0.4,"#9EC4F1"],[0.7,"#9A6700"],[1,"#B91C1C"]],
        showscale=False,
        hovertemplate="<b>%{y}</b><br>%{x}: %{z} events<extra></extra>",
    ))
    fig.update_layout(**PLOTLY_BASE, height=max(280, len(row_labels) * 38))
    st.markdown('<div class="card-title">CATEGORY × SEVERITY MATRIX · click a cell</div>',
                unsafe_allow_html=True)
    event = st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG,
                            on_select="rerun", selection_mode="points",
                            key="exec_risk_heatmap")
    try:
        pts = (event.selection.points if event else []) or []
    except Exception:  # intentional: must not crash
        pts = []
    if pts:
        # x = severity column, y = category row — drill on whichever is clicked
        sev = pts[0].get("x", "")
        cat = pts[0].get("y", "")
        if sev:
            set_drill(_PANEL, f"Severity: {sev}", "severity", sev)
            st.rerun()
        elif cat:
            set_drill(_PANEL, f"Category: {cat}", "category", cat)
            st.rerun()


def _top_offenders(events: list) -> None:
    by_owner: dict = defaultdict(lambda: {"count": 0, "severity": "LOW", "dept": ""})
    for e in events:
        if e.get("outcome") != "SUPPRESS":
            o = e["owner"]
            by_owner[o]["count"] += 1
            by_owner[o]["dept"]   = e["department"]
            if e["severity"] == "CRITICAL":
                by_owner[o]["severity"] = "CRITICAL"
            elif e["severity"] == "HIGH" and by_owner[o]["severity"] != "CRITICAL":
                by_owner[o]["severity"] = "HIGH"

    top10   = sorted(by_owner.items(), key=lambda x: x[1]["count"], reverse=True)[:10]
    names   = [t[0] for t in top10]
    cnts    = [t[1]["count"] for t in top10]
    colours = [SEV_COLOURS.get(t[1]["severity"], "#57606A") for t in top10]

    fig = go.Figure(go.Bar(
        x=cnts[::-1], y=names[::-1], orientation="h",
        marker_color=colours[::-1],
        hovertemplate="%{y}: %{x} events<extra></extra>",
    ))
    fig.update_layout(**PLOTLY_BASE, height=350,
                      xaxis=dict(gridcolor="#E1E4E8"),
                      yaxis=dict(tickfont=dict(size=10)))
    st.markdown('<div class="card-title">TOP OFFENDERS · click a bar</div>',
                unsafe_allow_html=True)
    event = st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG,
                            on_select="rerun", selection_mode="points",
                            key="exec_risk_offenders")
    try:
        pts = (event.selection.points if event else []) or []
    except Exception:  # intentional: must not crash
        pts = []
    if pts:
        owner = pts[0].get("y", "")
        if owner:
            set_drill(_PANEL, f"Owner: {owner}", "owner", owner)
            st.rerun()
