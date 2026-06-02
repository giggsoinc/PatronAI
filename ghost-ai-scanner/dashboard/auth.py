# =============================================================
# FILE: dashboard/auth.py
# VERSION: 2.0.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc
# PURPOSE: PatronAI auth gate. Phase 1B replaces env-var allowlists with
#          an S3-backed users.json (UsersStore). Returns (email, role,
#          is_admin) on success. Falls back to env-var allowlist if the
#          users store is unavailable so the dashboard never locks itself
#          out on a transient S3 outage.
# AUDIT LOG:
#   v1.0.0  2026-04-18  Initial.
#   v1.1.0  2026-04-19  Informative contact hint on access denial.
#   v1.2.0  2026-04-19  De-technicalised copy.
#   v2.0.0  2026-04-26  Phase 1B — S3 users.json via UsersStore.
#                       Returns (email, role, is_admin) tuple.
#                       Backwards-compatible env-var fallback retained.
# =============================================================

import os
import streamlit as st

# Env-var fallbacks — only used if the S3 users-store is unreachable.
_FALLBACK_ALLOWED = [e.strip().lower() for e in
                     os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()]
_FALLBACK_ADMINS  = [e.strip().lower() for e in
                     os.environ.get("ADMIN_EMAILS",  "").split(",") if e.strip()]

BUCKET  = os.environ.get("MARAUDER_SCAN_BUCKET", "")
REGION  = os.environ.get("AWS_REGION", "us-east-1")


def _users_store():
    """Lazy-construct a UsersStore. None on import / config error so the
    caller can fall back to env-var allowlist gracefully."""
    if not BUCKET:
        return None
    try:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
        from store.users_store import UsersStore
        return UsersStore(BUCKET, REGION)
    except Exception:  # intentional: must not crash
        return None


def _resolve_role(email: str) -> tuple:
    """Return (role, is_admin) for an email, or ('', False) if not authorised.
    Tries S3 users.json first; falls back to env-var allowlist on store error."""
    email = (email or "").strip().lower()
    store = _users_store()
    if store is not None:
        try:
            rec = store.get(email)
            if rec:
                return rec.get("role", "support"), bool(rec.get("is_admin"))
            if not store.read_all():                # empty store + env vars → fallback
                return _env_fallback(email)
            return "", False                       # store has users but not this one
        except Exception:  # intentional: must not crash
            return _env_fallback(email)
    return _env_fallback(email)


def _env_fallback(email: str) -> tuple:
    """Legacy env-var resolver. Used only when S3 users-store is down."""
    if email in _FALLBACK_ADMINS:
        return "manager", True
    if email in _FALLBACK_ALLOWED:
        return "support", False
    return "", False


def gate() -> tuple:
    """Email allowlist gate. Returns (email, role, is_admin).
    Calls st.stop() if not authenticated."""
    if "email" not in st.session_state:
        st.session_state.email         = ""
        st.session_state.authenticated = False
        st.session_state.role          = ""
        st.session_state.is_admin      = False

    if not st.session_state.authenticated:
        _render_login()
        st.stop()

    return (st.session_state.email,
            st.session_state.role,
            st.session_state.is_admin)


def _render_login() -> None:
    """Login screen shown before authentication."""
    company = os.environ.get("COMPANY_NAME", "")

    try:
        st.image("assets/branding/patronai-logo.png", width=240)
    except Exception:  # intentional: must not crash
        st.title("PatronAI")
    st.caption(f"Settings — {company}" if company else "Settings")
    st.divider()

    if not BUCKET:
        st.error("Tenant storage not configured. Contact your administrator.")
        st.stop()

    st.info(f"Tenant storage connected ({REGION})")

    email = st.text_input(
        "Enter your email to continue",
        placeholder="you@company.com",
        key="email_input",
    )

    if st.button("Continue", type="primary"):
        email = (email or "").strip().lower()
        if not email:
            st.warning("Please enter your email address.")
            return
        role, is_admin = _resolve_role(email)
        if not role:
            admin_hint = (_FALLBACK_ADMINS[0] if _FALLBACK_ADMINS
                          else "your administrator")
            st.error(f"Access denied. Contact {admin_hint} to request access.")
            return
        st.session_state.email         = email
        st.session_state.role          = role
        st.session_state.is_admin      = is_admin
        st.session_state.authenticated = True
        st.rerun()
