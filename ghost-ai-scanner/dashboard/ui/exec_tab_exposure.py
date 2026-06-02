# =============================================================
# FILE: dashboard/ui/exec_tab_exposure.py
# VERSION: 1.2.0
# UPDATED: 2026-04-28
# OWNER: Giggso Inc
# PURPOSE: Data Exposure tab — Sankey flow diagram + recent incidents table.
# AUDIT LOG:
#   v1.0.0  2026-04-19  Initial
#   v1.1.0  2026-04-27  Category nodes instead of empty department; customdata.
#   v1.2.0  2026-04-28  Owner hyperlinks in incidents table.
# =============================================================

from collections import defaultdict

import plotly.graph_objects as go
import streamlit as st

from .helpers      import PLOTLY_BASE, PLOTLY_CONFIG, sev_badge, geo_flag
from .drill_panel  import set_drill, render_drill_panel

_PANEL = "exec_exposure"


def render_exposure(events: list) -> None:
    """Sankey: department → provider, then recent high-severity incidents.
    Sankey node click → drill on department OR provider (auto-detected)."""
    _sankey(events)
    render_drill_panel(_PANEL, events, limit=100)
    _incidents_table(events)


def _sankey(events: list) -> None:
    """Sankey: category → AI provider. customdata on nodes guarantees
    node name in click event pts[]."""
    active  = [e for e in events if e.get("outcome") not in
               ("SUPPRESS", "HEARTBEAT", "CLEAN")]
    # Source: category (finding type). Fall back to department for network events.
    def _src_label(e: dict) -> str:
        return e.get("category") or e.get("department") or "unknown"

    cats_u  = [c for c in list(dict.fromkeys(_src_label(e) for e in active))
               if c][:8]
    provs_u = [p for p in list(dict.fromkeys(e.get("provider", "") for e in active))
               if p][:10]
    all_nodes = cats_u + provs_u
    node_idx  = {n: i for i, n in enumerate(all_nodes)}

    links: dict = defaultdict(int)
    for e in active:
        src_label = _src_label(e)
        prov      = e.get("provider", "")
        if src_label in node_idx and prov in node_idx:
            links[(src_label, prov)] += 1

    if not links:
        st.info("No flow data available.")
        return

    src = [node_idx[k[0]] for k in links]
    tgt = [node_idx[k[1]] for k in links]
    val = list(links.values())
    max_val = max(val)

    fig_sankey = go.Figure(go.Sankey(
        arrangement="snap",
        node=dict(
            pad=20,
            thickness=20,
            label=all_nodes,
            color=[
                "#0969DA" if i < len(cats_u) else "#9A6700"
                for i in range(len(all_nodes))
            ],
            line=dict(color="#D0D7DE", width=0.5),
            # customdata mirrors label — guaranteed present in click event pts
            customdata=all_nodes,
            hovertemplate="%{customdata}: %{value} events<extra></extra>",
        ),
        link=dict(
            source=src,
            target=tgt,
            value=val,
            color=[
                f"rgba(9,105,218,{min(0.15 + v / max_val * 0.5, 0.65)})"
                for v in val
            ],
            hovertemplate="%{source.label} → %{target.label}: "
                          "%{value} events<extra></extra>",
        ),
    ))
    # Spread PLOTLY_BASE but exclude 'font' and 'margin' — overridden below for Sankey
    _base = {k: v for k, v in PLOTLY_BASE.items() if k not in ("font", "margin")}
    fig_sankey.update_layout(
        **_base,
        height=420,
        font=dict(family="DM Sans, sans-serif", size=13, color="#1F2328"),
        margin=dict(l=120, r=120, t=20, b=20),
    )
    st.markdown(
        f'<div class="card-title">DATA FLOW — {len(cats_u)} CATEGORIES → '
        f'{len(provs_u)} AI PROVIDERS · {sum(val)} EVENTS</div>',
        unsafe_allow_html=True,
    )
    event = st.plotly_chart(fig_sankey, use_container_width=True,
                            config=PLOTLY_CONFIG,
                            on_select="rerun", selection_mode="points",
                            key="exec_exposure_sankey")
    try:
        pts = (event.selection.points if event else []) or []
    except Exception:  # intentional: must not crash
        pts = []
    if pts:
        # customdata is the reliable field; label is the fallback
        node_name = pts[0].get("customdata") or pts[0].get("label", "")
        if node_name in cats_u:
            set_drill(_PANEL, f"Category: {node_name}", "category", node_name)
            st.rerun()
        elif node_name in provs_u:
            set_drill(_PANEL, f"Provider: {node_name}", "provider", node_name)
            st.rerun()


def _link(e: str) -> str:
    """Owner cell — hyperlink when value looks like an email address."""
    return (f"<a href='?view=user_detail&email={e}' "
            f"style='color:#0969DA;text-decoration:none'>{e}</a>"
            if "@" in e else e)


def _incidents_table(events: list) -> None:
    from .time_fmt import fmt as fmt_time
    critical = [e for e in events
                if e.get("severity") in ("CRITICAL", "HIGH")][:15]
    _m = "font-family:JetBrains Mono;font-size:10px"
    rows = "".join(
        f"<tr><td>{fmt_time(e.get('timestamp'))}</td>"
        f"<td>{_link(e.get('email') or e.get('owner') or '—')}</td>"
        f"<td style='{_m}'>{e.get('category') or e.get('department') or '—'}</td>"
        f"<td style='{_m}'>{(e.get('provider') or '—')[:40]}</td>"
        f"<td>{sev_badge(e.get('severity','LOW'))}</td>"
        f"<td>{geo_flag(e.get('geo_country',''))} {e.get('geo_country','')}</td>"
        f"</tr>"
        for e in critical
    )
    hdr = "<th>TIMESTAMP</th><th>USER</th><th>CATEGORY</th><th>PROVIDER</th><th>SEVERITY</th><th>GEO</th>"
    st.markdown('<div class="card-title">RECENT INCIDENTS</div>', unsafe_allow_html=True)
    st.markdown(f"<table><thead><tr>{hdr}</tr></thead><tbody>{rows}</tbody></table>",
                unsafe_allow_html=True)
