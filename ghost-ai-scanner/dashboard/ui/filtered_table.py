# =============================================================
# FILE: dashboard/ui/filtered_table.py
# PROJECT: PatronAI — Phase 1B
# VERSION: 1.0.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: One helper used by every dashboard grid — adds a global
#          search bar + active-filter chips + result-count caption
#          on top of any pandas DataFrame, then renders via
#          st.dataframe with row selection if requested.
#          Per-column filtering rides on Streamlit's native header
#          UI (1.30+) which we expose by passing through column_config.
# DEPENDS: streamlit, pandas
# AUDIT LOG:
#   v1.0.0  2026-04-26  Initial. Phase 1B.
# =============================================================

import streamlit as st


def filtered_table(df, key: str, column_config=None,
                   selection_mode=None, height=None):
    """
    Render a DataFrame with global search + result count.

    Args:
      df:             pandas DataFrame
      key:            unique session key (search-input + table widgets)
      column_config:  forwarded to st.dataframe (column-header filters
                      ride along when enabled by Streamlit native UI)
      selection_mode: if set, st.dataframe runs in selection mode
                      ("single-row" | "multi-row")
      height:         optional pixel height

    Returns:
      (filtered_df, selected_rows)
        selected_rows is [] when selection_mode is None or no rows picked.
    """
    if df is None or df.empty:
        st.info("No rows.")
        return df, []

    # Global search bar
    q = st.text_input(
        "Search any column",
        placeholder="type to filter — matches any column substring …",
        key=f"{key}_search",
        label_visibility="collapsed",
    )

    filtered = _apply_global_search(df, q) if q else df

    # Active-filter chip + count line
    n_total = len(df)
    n_shown = len(filtered)
    chip = ""
    if q:
        chip = (f"<span style='font-family:JetBrains Mono;font-size:10px;"
                f"padding:2px 8px;border-radius:10px;background:#DDF4FF;"
                f"color:#0969DA;border:1px solid #0969DA;"
                f"margin-right:6px'>search: \"{q}\"</span>")
    st.markdown(
        f"<div style='font-family:JetBrains Mono;font-size:11px;"
        f"color:#57606A;margin:4px 0 8px 0'>{chip}"
        f"Showing {n_shown} of {n_total}</div>",
        unsafe_allow_html=True,
    )

    # Render the table — with or without selection mode.
    kwargs = {
        "use_container_width": True,
        "hide_index":          True,
    }
    if column_config is not None:
        kwargs["column_config"] = column_config
    if height is not None:
        kwargs["height"] = height

    if selection_mode:
        kwargs["on_select"]      = "rerun"
        kwargs["selection_mode"] = selection_mode
        result = st.dataframe(filtered, key=f"{key}_table", **kwargs)
        try:
            sel_rows = result.selection.rows
        except Exception:  # intentional: must not crash
            sel_rows = []
        return filtered, sel_rows

    st.dataframe(filtered, key=f"{key}_table", **kwargs)
    return filtered, []


def _apply_global_search(df, q: str):
    """Filter DataFrame to rows where ANY string column contains q (case-insensitive).
    Returns the filtered DataFrame (copy)."""
    q = q.strip().lower()
    if not q:
        return df
    str_cols = [c for c in df.columns
                if df[c].dtype == object or str(df[c].dtype).startswith("string")]
    if not str_cols:
        return df
    mask = None
    for c in str_cols:
        col_mask = df[c].astype(str).str.lower().str.contains(q, na=False)
        mask = col_mask if mask is None else (mask | col_mask)
    return df[mask] if mask is not None else df


def clear_filters_button(key: str) -> None:
    """Optional clear-all button — pass the same key the table uses.
    Resets the global search input."""
    if st.button("Clear filters", key=f"{key}_clear"):
        st.session_state.pop(f"{key}_search", None)
        st.rerun()


def search_box(key: str, placeholder: str = "type to filter …") -> str:
    """Render a stand-alone search input. Returns the trimmed query.
    Use this above HTML-rendered tables that can't go through st.dataframe."""
    q = st.text_input(
        "Search", placeholder=placeholder,
        key=f"{key}_search", label_visibility="collapsed",
    )
    return (q or "").strip()


def apply_search_dicts(rows: list, query: str) -> list:
    """Filter a list-of-dicts: keep rows where ANY value contains `query`
    (case-insensitive). Empty / blank query returns the input untouched."""
    q = (query or "").strip().lower()
    if not q:
        return rows
    out = []
    for r in rows:
        try:
            blob = " ".join(str(v) for v in r.values()).lower()
        except Exception:  # intentional: must not crash
            continue
        if q in blob:
            out.append(r)
    return out
