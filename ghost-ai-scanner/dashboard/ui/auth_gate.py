# =============================================================
# FILE: dashboard/ui/auth_gate.py
# VERSION: 2.0.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc
# PURPOSE: Main-dashboard auth gate. Phase 1B replaces env-var allowlists
#          with S3-backed users.json (UsersStore). Returns
#          (email, role, is_admin) on success.
#          Falls back to env-var allowlist if S3 store unavailable so the
#          dashboard never locks out on a transient outage.
# AUDIT LOG:
#   v1.0.0  2026-04-19  Initial — extracted + de-branded from ghost_dashboard.py
#   v2.0.0  2026-04-26  Phase 1B — S3 users.json via UsersStore;
#                       returns (email, role, is_admin) tuple.
# =============================================================

import os
import streamlit as st

_ALLOWED: list = [e.strip().lower() for e in
                  os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()]
_ADMINS:  list = [e.strip().lower() for e in
                  os.environ.get("ADMIN_EMAILS",  "").split(",") if e.strip()]
_COMPANY: str  = os.environ.get("COMPANY_NAME", "")
_BUCKET:  str  = os.environ.get("MARAUDER_SCAN_BUCKET", "")
_REGION:  str  = os.environ.get("AWS_REGION", "us-east-1")


def _users_store():
    """Lazy build a UsersStore. None on import / config error."""
    if not _BUCKET:
        return None
    try:
        import sys
        sys.path.insert(0, os.path.join(
            os.path.dirname(__file__), "..", "..", "src"))
        from store.users_store import UsersStore
        return UsersStore(_BUCKET, _REGION)
    except Exception:  # intentional: must not crash
        return None


def _resolve(email: str) -> tuple:
    """Return (role, is_admin) — '' role means unauthorised."""
    email = (email or "").strip().lower()
    store = _users_store()
    if store is not None:
        try:
            rec = store.get(email)
            if rec:
                return rec.get("role", "support"), bool(rec.get("is_admin"))
            if not store.read_all():
                return _env_fallback(email)
            return "", False
        except Exception:  # intentional: must not crash
            return _env_fallback(email)
    return _env_fallback(email)


def _env_fallback(email: str) -> tuple:
    """Legacy env-var resolver for when the S3 store is down."""
    if email in _ADMINS:
        return "manager", True
    if email in _ALLOWED:
        return "support", False
    return "", False


def gate() -> tuple:
    """Show login screen if not authenticated.
    Returns (email: str, role: str, is_admin: bool) on success."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
        st.session_state.email   = ""
        st.session_state.role    = ""
        st.session_state.is_admin = False

    if not st.session_state.authenticated:
        _render_login()
        st.stop()

    return (st.session_state.email,
            st.session_state.role,
            st.session_state.is_admin)


def _render_login() -> None:
    """Render the centred login card."""
    col = st.columns([1, 2, 1])[1]
    with col:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown(
            '<div class="card"><div class="card-title">'
            'PatronAI · User Interface</div>', unsafe_allow_html=True,
        )
        if _COMPANY:
            st.markdown(f"**{_COMPANY}** · Security Intelligence Platform")
        st.markdown("---")

        email = st.text_input(
            "Email address", placeholder="you@company.com",
            label_visibility="collapsed",
        )

        if st.button("→ Continue", type="primary", use_container_width=True):
            _check(email)

        admin_contact = _ADMINS[0] if _ADMINS else "your administrator"
        st.caption(f"Access is by invitation only. Contact "
                   f"{admin_contact} to request access.")
        st.markdown("</div>", unsafe_allow_html=True)


def _check(email: str) -> None:
    """Validate email + resolve role + set session state."""
    email = (email or "").strip().lower()
    if not email:
        st.warning("Please enter your email address.")
        return
    role, is_admin = _resolve(email)
    if not role:
        st.error("Access denied — your email is not on the access list.")
        return
    st.session_state.email         = email
    st.session_state.role          = role
    st.session_state.is_admin      = is_admin
    st.session_state.authenticated = True
    st.rerun()
