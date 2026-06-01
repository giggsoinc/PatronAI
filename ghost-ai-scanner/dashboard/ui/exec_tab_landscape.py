# =============================================================
# FILE: dashboard/ui/exec_tab_landscape.py
# VERSION: 1.1.0
# UPDATED: 2026-04-27
# OWNER: Giggso Inc
# PURPOSE: AI Landscape tab — world map, provider bubble, 30-day trend.
# AUDIT LOG:
#   v1.0.0  2026-04-19  Initial — extracted from pam_dashboard / exec_view
#   v1.1.0  2026-04-27  Fix 30-day trend: use timestamp not missing date field.
#                       Add customdata to bubble for reliable click events.
#                       Empty-state message for world map when no geo data.
# =============================================================

from collections import defaultdict

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .helpers      import PLOTLY_BASE, PLOTLY_CONFIG, SEV_COLOURS, COUNTRY_ISO
from .drill_panel  import set_drill, render_drill_panel

_PANEL = "exec_landscape"


def render_landscape(events: list, summary: dict) -> None:
    """World map + provider bubble side-by-side, then 30-day trend.
    Provider bubble click → drill on provider."""
    col_l, col_r = st.columns([3, 2])
    with col_l:
        _world_map(events)
    with col_r:
        _provider_bubble(events)
    _trend_30d(events)
    render_drill_panel(_PANEL, events, limit=100)


def _world_map(events: list) -> None:
    by_geo = defaultdict(int)
    for e in events:
        if e.get("geo_country") and e.get("outcome") != "SUPPRESS":
            by_geo[e["geo_country"]] += 1

    geo_df = pd.DataFrame([
        {"iso": COUNTRY_ISO.get(c, ""), "count": n}
        for c, n in by_geo.items() if c in COUNTRY_ISO
    ])
    if geo_df.empty:
        st.caption("No geographic data in this dataset — "
                   "endpoint scan events do not carry geo fields.")
        return

    fig = go.Figure(go.Choropleth(
        locations=geo_df["iso"], z=geo_df["count"],
        colorscale=[[0,"#F6F8FA"],[0.5,"#0969DA"],[1,"#B91C1C"]],
        showscale=False, marker_line_color="#D0D7DE", marker_line_width=0.5,
    ))
    fig.update_layout(**{**PLOTLY_BASE,
        "geo": dict(showframe=False, showcoastlines=True, coastlinecolor="#D0D7DE",
                    bgcolor="rgba(0,0,0,0)", showland=True, landcolor="#F6F8FA",
                    showocean=True, oceancolor="#FFFFFF", showlakes=False),
        "height": 260, "margin": dict(l=0,r=0,t=10,b=0),
    })
    st.markdown('<div class="card-title">DESTINATION COUNTRIES</div>', unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)


def _provider_bubble(events: list) -> None:
    by_prov: dict = defaultdict(lambda: {"count": 0, "bytes": 0, "severity": "LOW"})
    for e in events:
        if e.get("outcome") != "SUPPRESS":
            p = e["provider"]
            by_prov[p]["count"]  += 1
            by_prov[p]["bytes"]  += e.get("bytes_out", 0)
            if e["severity"] == "CRITICAL":
                by_prov[p]["severity"] = "CRITICAL"
            elif e["severity"] == "HIGH" and by_prov[p]["severity"] != "CRITICAL":
                by_prov[p]["severity"] = "HIGH"

    df = pd.DataFrame([
        {"provider": p, "count": v["count"],
         "bytes_mb": round(v["bytes"]/1_000_000, 1),
         "colour": SEV_COLOURS.get(v["severity"], "#57606A")}
        for p, v in sorted(by_prov.items(), key=lambda x: x[1]["count"], reverse=True)[:12]
    ])
    if df.empty:
        return

    fig = go.Figure(go.Scatter(
        x=df["bytes_mb"], y=df["count"], mode="markers+text",
        marker=dict(size=df["count"].apply(lambda x: min(max(x*1.5,12),48)),
                    color=df["colour"], opacity=0.85,
                    line=dict(width=1, color="#D0D7DE")),
        text=df["provider"], textposition="top center",
        textfont=dict(size=9, color="#57606A"),
        # customdata carries the provider name into the selection event dict
        # — more reliable than text which Plotly.js may not surface in pts[].
        customdata=df["provider"].tolist(),
        hovertemplate="<b>%{customdata}</b><br>Events: %{y}<br>MB out: %{x}<extra></extra>",
    ))
    fig.update_layout(**PLOTLY_BASE, height=260,
                      xaxis=dict(title="MB Out", gridcolor="#E1E4E8", zeroline=False),
                      yaxis=dict(title="Events",  gridcolor="#E1E4E8", zeroline=False))
    st.markdown('<div class="card-title">PROVIDER ACTIVITY · click a bubble to drill</div>',
                unsafe_allow_html=True)
    event = st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG,
                            on_select="rerun", selection_mode="points",
                            key="exec_landscape_bubble")
    try:
        pts = (event.selection.points if event else []) or []
    except Exception:  # intentional: must not crash
        pts = []
    if pts:
        # customdata is the reliable field; fall back to text then label
        prov = (pts[0].get("customdata") or pts[0].get("text")
                or pts[0].get("label", ""))
        if prov:
            set_drill(_PANEL, f"Provider: {prov}", "provider", prov)
            st.rerun()


def _trend_30d(events: list) -> None:
    by_day: dict = defaultdict(int)
    for e in events:
        if e.get("outcome") not in ("SUPPRESS", "HEARTBEAT", "CLEAN"):
            # FLAT_SCHEMA has no 'date' field — use timestamp (ISO 8601 UTC)
            day_key = (e.get("date") or e.get("timestamp", ""))[:10]
            if day_key:
                by_day[day_key] += 1

    dates  = sorted(by_day.keys())[-30:]
    counts = [by_day[d] for d in dates]

    if not dates:
        st.markdown('<div class="card-title">30-DAY EVENT TREND</div>',
                    unsafe_allow_html=True)
        st.caption("No dated events found — scan data may not yet have timestamps.")
        return

    fig = go.Figure([go.Scatter(
        x=dates, y=counts, fill="tozeroy",
        fillcolor="rgba(9,105,218,0.10)",
        line=dict(color="#0969DA", width=2), mode="lines",
        hovertemplate="%{x}: %{y} events<extra></extra>",
    )])
    fig.update_layout(**PLOTLY_BASE, height=160,
                      xaxis=dict(gridcolor="#E1E4E8", zeroline=False, tickfont=dict(size=9)),
                      yaxis=dict(gridcolor="#E1E4E8", zeroline=False))
    st.markdown('<div class="card-title">30-DAY EVENT TREND</div>', unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
