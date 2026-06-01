# =============================================================
# FILE: dashboard/ui/tabs/deploy_agents_table.py
# VERSION: 2.0.0
# UPDATED: 2026-04-27
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Deployment status table for Deploy Agents tab.
#          Per-row Whitelist + Delete controls.
#          v2: Delete is a two-step flow — download uninstall script
#          first, then confirm server-side deletion.
# DEPENDS: streamlit, agent_store
# AUDIT LOG:
#   v1.0.0  2026-04-20  Split from deploy_agents.py; added Delete button
#   v1.1.0  2026-04-20  Edit Whitelist expander per row
#   v2.0.0  2026-04-27  Two-step delete: download uninstall script → confirm.
# =============================================================
# raven: loc-exempt — pending refactor into sub-modules (tracked separately)

import os
import sys
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

_HOOK_PREFIX = "config/HOOK_AGENTS"
_UNINSTALL_TTL = 3600   # presigned URL valid for 1 hour

_STATUS_BADGE = {
    "pending":   '<span class="badge badge-medium">PENDING</span>',
    "installed": '<span class="badge badge-clean">INSTALLED</span>',
    "failed":    '<span class="badge badge-critical">FAILED</span>',
}


def _uninstall_url(store, token: str, os_type: str) -> str:
    """Generate a 1-hour presigned GET URL for the uninstall script.
    Returns '' if the object doesn't exist (pre-v1.6 packages)."""
    ext  = "ps1" if os_type == "windows" else "sh"
    key  = f"{_HOOK_PREFIX}/{token}/uninstall_agent.{ext}"
    try:
        store.s3.head_object(Bucket=store.bucket, Key=key)
        return store.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": store.bucket, "Key": key},
            ExpiresIn=_UNINSTALL_TTL,
        )
    except Exception:  # intentional: must not crash
        return ""


def render_status_table() -> None:
    """Deployment status table with per-row Delete buttons."""
    st.markdown("**Deployment Status**")

    bucket = os.environ.get("MARAUDER_SCAN_BUCKET", "")
    region = os.environ.get("AWS_REGION", "us-east-1")
    if not bucket:
        return

    try:
        from store.agent_store import AgentStore
        store   = AgentStore(bucket, region)
        catalog = store.refresh_statuses(store.list_catalog())
    except Exception as e:
        st.caption(f"Could not load status: {e}")
        return

    if not catalog:
        st.caption("No packages generated yet.")
        return

    # ── Header row ────────────────────────────────────────────
    h = st.columns([2, 3, 1, 1, 2, 1, 1])
    for col, label in zip(h, ["Name", "Email", "Platform",
                               "Status", "Created", "", ""]):
        col.markdown(f"**{label}**")
    st.divider()

    # ── Data rows ─────────────────────────────────────────────
    for entry in reversed(catalog):
        token   = entry.get("token", "")
        os_type = entry.get("os_type", "mac")
        status  = entry.get("status", "pending")
        badge   = _STATUS_BADGE.get(status,
                    '<span class="badge badge-low">UNKNOWN</span>')

        c = st.columns([2, 3, 1, 1, 2, 1, 1])
        c[0].write(entry.get("recipient_name", ""))
        c[1].write(entry.get("recipient_email", ""))
        c[2].write(os_type)
        c[3].markdown(badge, unsafe_allow_html=True)
        from ..time_fmt import fmt as _fmt_time
        c[4].caption(_fmt_time(entry.get("created_at")))

        if c[5].button("Whitelist", key=f"wl_{token}", type="secondary",
                       help="Edit this user's authorised tools"):
            st.session_state[f"edit_wl_{token}"] = \
                not st.session_state.get(f"edit_wl_{token}", False)

        if c[6].button("Delete", key=f"del_{token}", type="secondary"):
            # Toggle the confirm panel — don't delete immediately
            st.session_state[f"confirm_del_{token}"] = \
                not st.session_state.get(f"confirm_del_{token}", False)
            st.rerun()

        # ── Uninstall + confirm delete panel ─────────────────
        if st.session_state.get(f"confirm_del_{token}", False):
            with st.container():
                st.markdown(
                    "<div style='background:#FFF8F0;border:1px solid #D97706;"
                    "border-radius:6px;padding:14px 18px;margin:4px 0 12px 0'>",
                    unsafe_allow_html=True,
                )
                name = entry.get("recipient_name", token[:8])
                st.markdown(
                    f"⚠ **Before deleting** — download the uninstall script "
                    f"and run it on **{name}'s** machine first."
                )
                url = _uninstall_url(store, token, os_type)
                if url:
                    ext  = "ps1" if os_type == "windows" else "sh"
                    st.markdown(
                        f"<a href='{url}' download='uninstall_agent.{ext}' "
                        f"style='display:inline-block;padding:6px 14px;"
                        f"background:#0969DA;color:#fff;border-radius:5px;"
                        f"font-family:JetBrains Mono;font-size:11px;"
                        f"text-decoration:none;margin-bottom:10px'>"
                        f"⬇ Download uninstall_agent.{ext}</a>",
                        unsafe_allow_html=True,
                    )
                    if os_type == "windows":
                        st.code(
                            "powershell -ExecutionPolicy Bypass "
                            "-File uninstall_agent.ps1",
                            language="powershell",
                        )
                    else:
                        st.code("bash uninstall_agent.sh", language="bash")
                else:
                    st.caption(
                        "No uninstall script available — this package was "
                        "generated before v1.6.0. Use the generic uninstall "
                        "script from the PatronAI docs."
                    )
                st.markdown("</div>", unsafe_allow_html=True)

                d1, d2 = st.columns([1, 5])
                if d1.button("Confirm Delete", key=f"confirm_del_btn_{token}",
                             type="primary"):
                    st.session_state.pop(f"confirm_del_{token}", None)
                    _delete(store, token, os_type)
                    st.rerun()
                if d2.button("Cancel", key=f"cancel_del_{token}"):
                    st.session_state.pop(f"confirm_del_{token}", None)
                    st.rerun()

        # ── Inline whitelist editor ───────────────────────────
        if st.session_state.get(f"edit_wl_{token}", False):
            with st.container():
                st.markdown(
                    "<div style='background:#FFFFFF;border:1px solid #D0D7DE;"
                    "border-radius:6px;padding:12px 16px;margin:4px 0 12px 0'>",
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"Authorised tools for "
                    f"**{entry.get('recipient_name', token[:8])}**"
                )
                current = store.get_authorized_domains(token)
                new_raw = st.text_area(
                    "One domain or package name per line",
                    value="\n".join(current),
                    height=100,
                    key=f"wl_input_{token}",
                    label_visibility="collapsed",
                    placeholder="canva.com\nfigma.com\nnotion.so",
                )
                sc1, sc2 = st.columns([1, 5])
                if sc1.button("Save", key=f"wl_save_{token}", type="primary"):
                    new_domains = [d.strip() for d in new_raw.splitlines()
                                   if d.strip()]
                    if store.update_authorized_domains(token, new_domains):
                        st.success(
                            f"Whitelist updated — agent picks up changes "
                            f"within 30 min. ({len(new_domains)} entries)"
                        )
                        st.session_state[f"edit_wl_{token}"] = False
                        st.rerun()
                    else:
                        st.error("Save failed — check logs.")
                if sc2.button("Cancel", key=f"wl_cancel_{token}"):
                    st.session_state[f"edit_wl_{token}"] = False
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)


def _delete(store, token: str, os_type: str) -> None:
    """Delete package from catalog and S3."""
    try:
        ok = store.delete_package(token, os_type)
        if ok:
            st.success(f"Deleted {token[:8]}…")
        else:
            st.error("Delete failed — check logs.")
    except Exception as e:
        st.error(f"Delete error: {e}")
