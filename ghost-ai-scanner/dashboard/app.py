# =============================================================
# FILE: dashboard/app.py
# VERSION: 1.2.0
# UPDATED: 2026-04-19
# OWNER: Giggso Inc
# PURPOSE: PatronAI Streamlit settings UI entry point.
#          Wires auth, health panel, actions, settings form,
#          CSV editor and sidebar. Handles ?action=refresh
#          query param from Grafana link panels.
#          Admin section tabbed: Settings | Deploy Agents.
# USAGE: streamlit run dashboard/app.py
# DEPENDS: dashboard.auth, dashboard.panels, dashboard.settings_form
# AUDIT LOG:
#   v1.1.0  2026-04-19  Initial
#   v1.2.0  2026-04-19  Admin section tabbed; Deploy Agents tab added
# =============================================================

import os
import sys

import streamlit as st

# Add src to path so store and summarizer can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from blob_index_store import BlobIndexStore
from summarizer       import Summarizer
from auth             import gate
from panels           import health, actions, csv_editor, sidebar
from settings_form    import render as render_settings
from ui.tabs.deploy_agents import render as deploy_agents_render

BUCKET       = os.environ.get("MARAUDER_SCAN_BUCKET", "")
REGION       = os.environ.get("AWS_REGION", "us-east-1")
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Company")

# ── Page config ───────────────────────────────────────────────
st.set_page_config(
    page_title="PatronAI — Settings",
    page_icon="assets/branding/patronai-icon.png",
    layout="wide",
    initial_sidebar_state="expanded",
)


def handle_query_params(store, summarizer):
    """Handle ?action=refresh from Grafana refresh button link."""
    if st.query_params.get("action") == "refresh":
        with st.spinner("Rebuilding summary..."):
            result = summarizer.run_now()
        st.success(
            f"Summary refreshed — {result.get('total_events', 0)} events "
            f"in {result.get('build_duration_seconds', 0)}s"
        )
        st.query_params.clear()


def main():
    # Auth gate — returns (email, is_admin) or stops
    email, is_admin = gate()

    if not BUCKET:
        st.error("MARAUDER_SCAN_BUCKET environment variable not set.")
        st.stop()

    # Initialise store and summarizer
    store      = BlobIndexStore(BUCKET, REGION)
    summarizer = Summarizer(store)

    # Sidebar
    sidebar(email, is_admin)

    # Handle Grafana refresh param
    handle_query_params(store, summarizer)

    # Page header — PatronAI wordmark, fallback to text
    try:
        st.image("assets/branding/patronai-logo.png", width=240)
    except Exception:  # intentional: must not crash
        st.title("PatronAI")
    st.caption(f"Settings — {COMPANY_NAME} · {BUCKET}")
    st.divider()

    # Health panel — all roles
    health(store)
    st.divider()

    # Actions — Refresh Now all roles, Backfill + Rescan admin only
    actions(store, summarizer, is_admin)
    st.divider()

    # Settings form, CSV editor, Deploy Agents — admin only
    if is_admin:
        t_settings, t_agents = st.tabs(["⚙ Settings", "🚀 Deploy Agents"])
        with t_settings:
            render_settings(store, email)
            st.divider()
            csv_editor()
        with t_agents:
            deploy_agents_render(email)
    else:
        st.info(
            "You have viewer access. "
            "Contact your administrator to change scanner settings."
        )


if __name__ == "__main__":
    main()
