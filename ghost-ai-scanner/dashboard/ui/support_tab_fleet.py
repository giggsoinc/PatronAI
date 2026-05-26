# =============================================================
# FILE: dashboard/ui/support_tab_fleet.py
# VERSION: 2.1.0
# UPDATED: 2026-04-29
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Support AGENT FLEET tab — live table of deployed agents,
#          heartbeat status, device info, and online/offline badge.
#          Online = heartbeat within last 15 minutes.
#          v2: discovers ALL agents from ocsf/agent/scans/ prefix.
#          v2.1: Revoke button (🗑 popover) + audit log section.
# AUDIT LOG:
#   v1.0.0  2026-04-20  Initial
#   v2.0.0  2026-04-27  Discovery-based fleet — includes uncatalogued agents.
#   v2.1.0  2026-04-29  Revoke + audit log; email param for revoked_by.
# =============================================================

import os
from datetime import datetime, timezone, timedelta

import streamlit as st

from .support_tab_fleet_data    import build_fleet_entries, fmt_age
from .support_tab_fleet_actions import revoke_agent, load_audit_log

ONLINE_THRESHOLD = timedelta(minutes=15)
_BADGE_ONLINE  = '<span class="badge badge-clean">ONLINE</span>'
_BADGE_OFFLINE = '<span class="badge badge-medium">OFFLINE</span>'
_BADGE_PENDING = '<span class="badge badge-low">PENDING</span>'
_BADGE_UNINSTALLED = '<span class="badge badge-low">UNINSTALLED</span>'


def _resolve_status(entry: dict, now: datetime) -> tuple:
    """Return (badge_html, last_seen_str, bucket: online|offline|pending|uninstalled)."""
    ts_str  = entry.get("ts_str", "")
    ev_type = entry.get("ev_type", "")
    s3_mtime = entry.get("s3_mtime")  # datetime | None

    # Agent explicitly reported uninstall
    if ev_type == "UNINSTALLED":
        label = ts_str[:16] if ts_str else "—"
        return _BADGE_UNINSTALLED, label, "uninstalled"

    if ev_type == "HEARTBEAT" and ts_str:
        try:
            ts  = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age = now - ts
            if age <= ONLINE_THRESHOLD:
                return _BADGE_ONLINE, fmt_age(age), "online"
            return _BADGE_OFFLINE, fmt_age(age), "offline"
        except Exception:
            return _BADGE_OFFLINE, ts_str[:16], "offline"

    if s3_mtime:  # fall back to S3 last-modified of latest.json
        try:
            if s3_mtime.tzinfo is None:
                s3_mtime = s3_mtime.replace(tzinfo=timezone.utc)
            age = now - s3_mtime
            return _BADGE_PENDING, fmt_age(age), "pending"
        except Exception:
            pass

    return _BADGE_PENDING, "Never", "pending"


def render_fleet(email: str = "") -> None:
    """Render Agent Fleet tab. email = logged-in admin for audit log."""
    bucket = os.environ.get("MARAUDER_SCAN_BUCKET", "")
    region = os.environ.get("AWS_REGION", "us-east-1")

    if not bucket:
        st.info("Storage not configured.")
        return

    try:
        import boto3
        s3      = boto3.client("s3", region_name=region)
        entries = build_fleet_entries(s3, bucket)
    except Exception as e:
        st.error(f"Cannot connect to S3: {e}")
        return

    if not entries:
        st.info("No agents found. Go to Settings → Deploy Agents.")
        return

    now = datetime.now(timezone.utc)
    online_count = offline_count = pending_count = 0
    rows = []

    for entry in entries:
        badge, last_seen, bucket_key = _resolve_status(entry, now)
        if bucket_key == "online":
            online_count += 1
        elif bucket_key == "offline":
            offline_count += 1
        elif bucket_key == "uninstalled":
            pass  # don't count uninstalled as pending
        else:
            pending_count += 1

        rows.append({**entry, "badge": badge, "last_seen": last_seen})

    _ord = {"online": 0, "offline": 1, "pending": 2}
    rows.sort(key=lambda r: (_ord.get(
        "online" if "ONLINE" in r["badge"] else
        "offline" if "OFFLINE" in r["badge"] else "pending", 2),
        r["name"].lower()))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total agents",  len(entries))
    c2.metric("Online",        online_count)
    c3.metric("Offline",       offline_count)
    c4.metric("Never checked", pending_count)
    st.markdown("<br>", unsafe_allow_html=True)

    header = st.columns([2, 3, 2, 2, 1, 1])
    for col, label in zip(header,
                          ["Name", "Device", "OS", "Last Seen", "Status", ""]):
        col.markdown(f"**{label}**")
    st.divider()

    for row in rows:
        c = st.columns([2, 3, 2, 2, 1, 1])
        if row["email"]:
            c[0].markdown(
                f"<a href='?view=user_detail&email={row['email']}' "
                f"style='color:#0969DA;text-decoration:none;'>"
                f"{row['name']}</a>",
                unsafe_allow_html=True,
            )
        else:
            c[0].write(row["name"])
        c[1].write(row["device"])
        c[2].write(row["os"])
        c[3].write(row["last_seen"])
        c[4].markdown(row["badge"], unsafe_allow_html=True)
        with c[5].popover("🗑"):
            st.caption(f"Delete **{row['name'] or row['token'][:8]}**?")
            st.caption("This is permanent and audit-logged.")
            if st.button("Confirm delete",
                         key=f"_del_{row['token'][:8]}",
                         type="primary"):
                ok = revoke_agent(s3, bucket, row["token"],
                                  row["name"], row["email"], email)
                st.toast("✅ Deleted" if ok else "❌ Delete failed")
                st.rerun()

    with st.expander("📋  Revocation audit log", expanded=False):
        log_entries = load_audit_log(s3, bucket)
        if not log_entries:
            st.caption("No revocations recorded yet.")
        else:
            for entry in log_entries:
                st.markdown(
                    f"`{entry.get('revoked_at','')[:19]}` — "
                    f"**{entry.get('agent_name') or entry.get('agent_email','?')}** "
                    f"revoked by `{entry.get('revoked_by','?')}`"
                )
