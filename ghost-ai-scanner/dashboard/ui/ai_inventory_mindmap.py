# =============================================================
# FILE: dashboard/ui/ai_inventory_mindmap.py
# PROJECT: PatronAI — Mega-PR
# VERSION: 1.2.0
# UPDATED: 2026-04-29
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Mind-map network/graph for Manager → AI INVENTORY tab
#          and User Detail ASSETS tab.
#          Hierarchy: Owner → Category → Provider (ROOT hidden).
#          Custom radial tree layout — owners radiate from centre.
#          Node click → drill filter on the panel below.
# DEPENDS: plotly, ai_inventory_mindmap_data, theme
# AUDIT LOG:
#   v1.0.0  2026-04-28  Initial. Replaces ai_inventory_treemap.py.
#   v1.1.0  2026-04-28  panel_key/chart_key/title params; render_user_mindmap.
#   v1.2.0  2026-04-29  Fix: remove explicit st.rerun() (on_select already
#                       reruns; double-rerun caused infinite loop).
#                       Hide ROOT node — owners radiate from centre directly.
#                       Track rendered_nodes separately so customdata index
#                       matches point_index correctly.
#                       Chart key version-bumped on drill-clear so Plotly
#                       resets its selection state.
# =============================================================

import plotly.graph_objects as go
import streamlit as st

from .manager_tab_ai_inventory_data import phase_1a_only, dedup_latest
from .ai_inventory_mindmap_data     import build_graph, _radial_pos, ROOT
from .drill_panel                   import set_drill
from .theme                         import SEV

_PANEL   = "mindmap"
_SEV_COL = {k: v[0] for k, v in SEV.items()}   # fg colour per severity
_SIZE    = {0: 28, 1: 20, 2: 14, 3: 9}
_BASE_COL = {0: "#0969DA", 1: "#1F2328"}


def render_mindmap(events: list, *,
                   panel_key: str = _PANEL,
                   chart_key: str = "ai_mindmap",
                   title: str = "") -> None:
    """Render an interactive mind-map above the AI inventory table.
    Clicking an owner, category, or provider node sets a drill filter."""
    base = dedup_latest(phase_1a_only(events))
    if not base:
        st.caption("No Phase 1A findings yet — mind map appears after next scan.")
        return

    ocp, edges, meta, node_labels = build_graph(base)
    pos = _radial_pos(ocp)

    # ── Edge trace — skip ROOT edges (owners radiate from centre) ─
    ex, ey = [], []
    for a, b in edges:
        if a == ROOT or b == ROOT:   # hide ROOT hub; owners become anchors
            continue
        if a in pos and b in pos:
            ex += [pos[a][0], pos[b][0], None]
            ey += [pos[a][1], pos[b][1], None]

    edge_trace = go.Scatter(
        x=ex, y=ey, mode="lines",
        line=dict(width=1, color="#D0D7DE"),
        hoverinfo="none",
    )

    # ── Node trace — skip ROOT; track rendered order for accurate index ─
    nx_list, ny_list, ncolors, nsizes, ntext, rendered_nodes = \
        [], [], [], [], [], []
    for n in node_labels:
        if n not in pos or n == ROOT:   # ROOT node hidden
            continue
        m   = meta.get(n, {"level": 0, "severity": "CLEAN"})
        lv  = m["level"]
        sev = m.get("severity", "CLEAN")
        nx_list.append(pos[n][0])
        ny_list.append(pos[n][1])
        ncolors.append(_BASE_COL.get(lv, _SEV_COL.get(sev, "#57606A")))
        nsizes.append(_SIZE.get(lv, 9))
        ntext.append(n.split("\n")[0])   # first line = display label
        rendered_nodes.append(n)         # matches Plotly point_index exactly

    node_trace = go.Scatter(
        x=nx_list, y=ny_list,
        mode="markers+text",
        marker=dict(size=nsizes, color=ncolors,
                    line=dict(width=1.5, color="#FFFFFF")),
        text=ntext, textposition="top center",
        textfont=dict(size=9, color="#1F2328"),
        customdata=rendered_nodes,       # index-aligned with rendered points
        hovertemplate="<b>%{text}</b><extra></extra>",
    )

    fig = go.Figure(data=[edge_trace, node_trace])
    fig.update_layout(
        showlegend=False, hovermode="closest",
        clickmode="event+select",   # single click selects a point → on_select fires
        dragmode=False,             # disable zoom/pan so clicks are the primary action
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=10, b=0), height=480,
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        font=dict(family="JetBrains Mono", size=10, color="#1F2328"),
    )

    _title = title or "AI ASSET MIND MAP · click a node to drill"
    st.markdown(f'<div class="card-title">{_title}</div>',
                unsafe_allow_html=True)
    # Chart key includes a version counter — bumped by the drill-clear button
    # so Plotly resets its selection state and doesn't re-trigger the drill.
    _ver = st.session_state.get(f"_chart_ver_{panel_key}", 0)
    sel = st.plotly_chart(fig, use_container_width=True,
                          on_select="rerun", selection_mode="points",
                          key=f"{chart_key}_v{_ver}")
    try:
        pts = (sel.selection.points if sel else []) or []
    except Exception:  # intentional: must not crash
        pts = []
    if not pts:
        return

    idx  = pts[0].get("point_index", -1)
    if not (0 <= idx < len(rendered_nodes)):
        return
    name = rendered_nodes[idx]       # accurate: customdata is index-aligned
    m    = meta.get(name, {})
    lv   = m.get("level", 0)

    if lv == 1:
        set_drill(panel_key, f"Owner: {m.get('raw_owner', name)}",
                  "owner", m.get("raw_owner", name))
    elif lv == 2:
        set_drill(panel_key, f"Category: {m.get('raw_cat', name)}",
                  "category", m.get("raw_cat", name))
    elif lv == 3:
        raw = name.split("\n")[0]
        set_drill(panel_key, f"Provider: {raw}", "provider", raw)
    # NOTE: do NOT call st.rerun() here.
    # on_select="rerun" already triggered a rerun when the node was clicked.
    # A second st.rerun() would create an infinite loop: chart preserves its
    # selection state across reruns, so set_drill + rerun would fire forever.


def render_user_mindmap(events: list, email: str) -> None:
    """User-detail mind map — ASSETS tab in user_detail.py.
    Receives the FULL event list; filters + deduplicates internally."""
    from .manager_tab_ai_inventory_data import phase_1a_only, dedup_latest
    from .drill_panel import render_drill_panel as _rdp
    ue = dedup_latest(phase_1a_only(
        [e for e in events
         if (e.get("email") or e.get("owner") or "") == email]))
    if not ue:
        st.info("No Phase 1A AI assets recorded for this user yet.")
        return
    render_mindmap(ue, panel_key="user_mm",
                   chart_key="user_detail_mindmap",
                   title=f"AI ASSET MAP — {email} · click a node to drill")
    _rdp("user_mm", ue, limit=50)
