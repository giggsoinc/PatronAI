# =============================================================
# FILE: dashboard/ui/drill_panel.py
# PROJECT: PatronAI — Mega-PR (drill-down everywhere)
# VERSION: 1.1.0
# UPDATED: 2026-04-29
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Single drill-down convention used by every view.
#          Two surfaces:
#            (a) set_drill / clear_drill / get_drill — pure session-state
#                helpers, importable from anywhere (no Streamlit calls
#                inside the predicate, so unit-testable).
#            (b) render_drill_panel — renders the filter chip + inline
#                results table directly below the element that set it.
#          Drills are stored under f"drill_{panel_key}" so multiple
#          panels can be open simultaneously without clobbering each
#          other.
# DEPENDS: streamlit (for render_drill_panel only)
# AUDIT LOG:
#   v1.0.0  2026-04-27  Initial. Mega-PR.
#   v1.1.0  2026-04-29  Clear button bumps _chart_ver_{panel_key} so
#                       Plotly mind-map resets its selection state on
#                       the next render (prevents drill re-activating).
# =============================================================

from typing import Callable, Optional


_DRILL_PREFIX = "drill_"


def _key(panel_key: str) -> str:
    """Session-state key for a panel."""
    return f"{_DRILL_PREFIX}{panel_key}"


def set_drill(panel_key: str, label: str, field: str, value) -> None:
    """Open a drill-down for `panel_key`. The drill-down filters rows
    where `row.get(field) == value`. `label` is the human-readable
    chip text shown in the panel.
    Stored shape: {"label": str, "field": str, "value": Any}."""
    import streamlit as st
    st.session_state[_key(panel_key)] = {
        "label": label, "field": field, "value": value,
    }


def clear_drill(panel_key: str) -> None:
    """Close the drill-down for `panel_key` (no-op if not open)."""
    import streamlit as st
    st.session_state.pop(_key(panel_key), None)


def get_drill(panel_key: str) -> Optional[dict]:
    """Return the active drill dict, or None when no drill is open.
    Safe to call outside Streamlit — returns None when no st.session_state."""
    try:
        import streamlit as st
        return st.session_state.get(_key(panel_key))
    except Exception:  # intentional: must not crash
        return None


def has_drill(panel_key: str) -> bool:
    """True iff a drill is currently open for this panel."""
    return get_drill(panel_key) is not None


def apply_drill(events: list, drill: dict) -> list:
    """Filter events by the drill predicate. Pure function — no Streamlit.
    `drill` shape matches `set_drill`."""
    if not drill:
        return events
    field = drill.get("field", "")
    value = drill.get("value")
    if not field:
        return events
    out = []
    for e in events:
        try:
            if e.get(field) == value:
                out.append(e)
        except Exception:  # intentional: must not crash
            continue
    return out


def render_drill_panel(panel_key: str, events: list,
                       row_renderer: Optional[Callable] = None,
                       limit: int = 50) -> None:
    """If a drill is open, render a chip + inline table for `events`
    filtered by the active drill. Caller passes the FULL event list;
    we apply the drill filter here.
    `row_renderer(events_subset)` (if given) does the rendering — it's
    handed the already-filtered list. When omitted, we fall back to a
    minimal HTML table of (timestamp, owner, provider, severity)."""
    import streamlit as st
    drill = get_drill(panel_key)
    if not drill:
        return
    subset = apply_drill(events, drill)[:limit]

    head_l, head_r = st.columns([8, 1])
    with head_l:
        st.markdown(
            f"<div class='drill-panel'>"
            f"<span class='drill-chip'>{drill.get('label', 'drill')}</span>"
            f"<span style='font-family:JetBrains Mono;font-size:11px;"
            f"color:#57606A;margin-left:8px'>{len(subset)} row(s)</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with head_r:
        if st.button("✕ Clear", key=f"drill_clear_{panel_key}"):
            clear_drill(panel_key)
            # Bump chart version — forces any Plotly chart using this panel_key
            # to reinitialise with no selection, so the drill can't re-activate
            # from stale Plotly selection state on the next render.
            _ver_key = f"_chart_ver_{panel_key}"
            st.session_state[_ver_key] = st.session_state.get(_ver_key, 0) + 1
            st.rerun()

    if not subset:
        st.caption("No matching events.")
        return

    if row_renderer is not None:
        row_renderer(subset)
        return

    # Default minimal renderer
    rows_html = "".join(
        f"<tr>"
        f"<td style='font-family:JetBrains Mono;font-size:10px;color:#57606A'>"
        f"{e.get('timestamp', '')[:19]}</td>"
        f"<td>{e.get('owner') or e.get('email', '—')}</td>"
        f"<td style='font-family:JetBrains Mono;font-size:11px'>"
        f"{(e.get('provider') or '—')[:60]}</td>"
        f"<td>{e.get('severity', 'UNKNOWN')}</td>"
        f"</tr>"
        for e in subset
    )
    st.markdown(
        f"<table><thead><tr><th>TIME</th><th>OWNER</th>"
        f"<th>PROVIDER</th><th>SEV</th></tr></thead>"
        f"<tbody>{rows_html}</tbody></table>",
        unsafe_allow_html=True,
    )
