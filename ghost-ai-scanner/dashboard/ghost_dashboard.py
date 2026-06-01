# =============================================================
# FILE: dashboard/ghost_dashboard.py
# VERSION: 3.3.0
# UPDATED: 2026-04-29
# OWNER: Giggso Inc
# PURPOSE: PatronAI UI — single-page Streamlit entry point.
#          Views: Home · Exec · Manager · Support · Reports · Settings
# AUDIT LOG:
#   v1.0.0  2026-04-07  Initial
#   v3.1.0  2026-04-28  Reports + Branding tab.
#   v3.2.0  2026-04-29  Home welcome page; logo from assets/branding/.
#   v3.3.0  2026-04-29  AI chat moved to persistent right-side column.
#                       Views render in col_main (75%); chat in col_chat (25%).
# =============================================================

import os
import sys
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ui.auth_gate import gate
from ui.sidebar   import render as render_sidebar
from ui.styles    import inject
from ui.data      import load_data

COMPANY_NAME: str = os.environ.get("COMPANY_NAME", "")
BUCKET:       str = os.environ.get("MARAUDER_SCAN_BUCKET", "")

st.set_page_config(
    page_title="PatronAI · User Interface",
    page_icon="assets/branding/patronai-icon.png",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={},
)

inject()


from ui.header import render_header as _render_header  # noqa: E402


def _prime_grafana_url() -> None:
    """Load grafana_url from S3 settings into session_state once per session."""
    if "grafana_url" in st.session_state or not BUCKET:
        return
    try:
        from blob_index_store import BlobIndexStore
        store = BlobIndexStore(BUCKET, os.environ.get("AWS_REGION", "us-east-1"))
        url = store.settings.read().get("alerts", {}).get("grafana_url", "")
        st.session_state["grafana_url"] = url or os.environ.get("GRAFANA_URL", "")
    except Exception:  # intentional: must not crash
        st.session_state["grafana_url"] = os.environ.get("GRAFANA_URL", "")


def main() -> None:
    _prime_grafana_url()
    email, role, is_admin = gate()
    from ui.user_detail_route import maybe_render_user_detail
    if maybe_render_user_detail(st.query_params, email, _render_header):
        return

    view = render_sidebar(email, role, is_admin)

    # Load data for all data-dependent views (home needs events for chat)
    events, summary = [], {}
    if view in ("exec", "manager", "providers", "support", "reports", "home"):
        events, summary = load_data(email=email, role=role)
    if view != "home":
        _render_header(summary)

    from ui.chat import render_chat_panel

    def _with_chat(view_key: str, view_fn, *a, **kw) -> None:
        """Render view_fn in left 75 %, chat panel in right 25 %."""
        cm, cc = st.columns([3, 1])
        with cm: view_fn(*a, **kw)
        with cc: render_chat_panel(events, email, view_key)

    if view == "home":
        from ui.home_view import render as home_render
        _with_chat("home", home_render, email, events, summary)
    elif view == "exec":
        from ui.exec_view import render as exec_render
        _with_chat("exec", exec_render, events, summary, email)
    elif view == "manager":
        from ui.manager_view import render as mgr_render
        _with_chat("manager", mgr_render, events, summary, email)
    elif view == "providers":
        from ui.tabs.provider_lists import render as pl_render
        pl_render(is_admin=False, email=email)
    elif view == "support":
        _with_chat("support", _render_support, events, summary, email)

    elif view == "reports":
        from ui.reports_view import render_reports
        render_reports(events, email)

    elif view == "settings" and is_admin:
        _render_settings(email)


def _build_store():
    """Return (BlobIndexStore | None, error_str | None)."""
    from blob_index_store import BlobIndexStore
    R = os.environ.get("AWS_REGION", "us-east-1")
    if not BUCKET:
        return None, "Tenant storage not configured."
    try:
        return BlobIndexStore(BUCKET, R), None
    except Exception as e:
        return None, str(e)


def _render_support(events: list, summary: dict, email: str) -> None:
    """Support view — rules health, code signals, coverage, agent fleet."""
    from ui.support_view import render as support_render
    store, err = _build_store()
    if err: st.error(err)
    support_render(events, summary, store, email)

def _render_settings(email: str) -> None:
    """Tabbed settings panel — admin only."""
    store, err = _build_store()
    if err:
        st.error(err); return
    _tab_names = ["Scanning","Alerting","Identity",
                  "Provider Lists","Users","Deploy Agents","Branding"]
    tabs = st.tabs(_tab_names)
    from ui.tabs.scanning import render as r_scan
    from ui.tabs.alerting import render as r_alert
    from ui.tabs.identity import render as r_ident
    from ui.tabs.provider_lists import render as r_prov
    from ui.tabs.users import render as r_users
    from ui.tabs.deploy_agents import render as r_agents
    from ui.tabs.branding import render as r_brand
    with tabs[0]: r_scan(store, email)
    with tabs[1]: r_alert(store, email)
    with tabs[2]: r_ident(store, email)
    with tabs[3]: r_prov(is_admin=True, email=email)
    with tabs[4]: r_users(email)
    with tabs[5]: r_agents(email)
    with tabs[6]: r_brand(email)


if __name__ == "__main__":
    main()
