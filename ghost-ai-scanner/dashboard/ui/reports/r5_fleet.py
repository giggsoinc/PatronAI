# =============================================================
# FILE: dashboard/ui/reports/r5_fleet.py
# VERSION: 1.0.0
# UPDATED: 2026-04-28
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: R5 — Fleet Health & Coverage HTML builder.
#          Groups events by device, checks heartbeat staleness,
#          surfaces SILENT devices, and shows pipeline state.
# AUDIT LOG:
#   v1.0.0  2026-04-28  Initial.
# =============================================================

import sys
from datetime import datetime, timezone
from ._header import wrap_html

_HB_SRC = "agent_heartbeat"
_GIT_SRCS = {"agent_git_hook", "patronai_git_hook"}
_EP_SRC = "agent_endpoint_scan"


def _dev_key(e: dict) -> str:
    """Return the best available device identifier for an event."""
    return e.get("device_id") or e.get("src_hostname") or e.get("src_ip") or "unknown"


def _kpi(value: str, label: str, cls: str = "") -> str:
    """Return a single KPI card HTML fragment."""
    return (f'<div class="kc {cls}"><div class="kv">{value}</div>'
            f'<div class="kl">{label}</div></div>')


def _is_silent(hb_ts: str | None, now_utc: datetime) -> bool:
    """Return True if device has no heartbeat or heartbeat is older than 24 h."""
    if not hb_ts:
        return True
    try:
        hb_dt = datetime.fromisoformat(hb_ts[:19]).replace(tzinfo=timezone.utc)
        return (now_utc - hb_dt).total_seconds() / 3600 > 24
    except Exception:  # intentional: must not crash
        return True


def build_html(events: list, d_from: str, d_to: str,
               admin_email: str, company: str, logo_b64: str) -> str:
    """Build complete R5 Fleet Health HTML report.

    Args:
        events:      Raw event dicts from the PatronAI pipeline.
        d_from:      Start date string YYYY-MM-DD (inclusive).
        d_to:        End date string YYYY-MM-DD (inclusive).
        admin_email: Report requester email shown in header.
        company:     Tenant company name shown in header.
        logo_b64:    Base-64 encoded PNG logo (may be empty string).

    Returns:
        Complete HTML document as a string.
    """
    try:
        from ..data import load_pipeline_state
        pipeline: dict = load_pipeline_state()
    except Exception as exc:
        print(f"[r5] load_pipeline_state: {exc}", file=sys.stderr)
        pipeline = {}

    now_utc = datetime.now(timezone.utc)
    devices: dict[str, dict] = {}
    for e in events:
        k = _dev_key(e)
        if k not in devices:
            devices[k] = {"last_hb": None, "scans": 0, "ep": False, "git": False}
        src = e.get("source") or ""
        ts = e.get("timestamp") or ""
        if src == _HB_SRC and (not devices[k]["last_hb"] or ts > devices[k]["last_hb"]):
            devices[k]["last_hb"] = ts
        devices[k]["scans"] += 1
        if src == _EP_SRC:
            devices[k]["ep"] = True
        if src in _GIT_SRCS:
            devices[k]["git"] = True

    silent: list[str] = []
    beating = 0
    for dev, info in devices.items():
        if _is_silent(info["last_hb"], now_utc):
            silent.append(dev)
        else:
            beating += 1

    pip_files = str(pipeline.get("files_processed", "—"))
    kpi = ('<div class="kr">'
           + _kpi(str(len(devices)), "Total Devices")
           + _kpi(str(beating), "Heartbeating")
           + _kpi(str(len(silent)), "Silent", "crit" if silent else "")
           + _kpi(pip_files, "Pipeline Files")
           + "</div>")

    gap_box = ""
    if silent:
        listing = ", ".join(silent[:20]) + (f" (+{len(silent)-20} more)" if len(silent) > 20 else "")
        gap_box = (f'<div class="gb"><strong>SILENT DEVICES (&gt;24 h no heartbeat):</strong>'
                   f" {listing}</div>")

    dev_rows = ""
    for dev, info in sorted(devices.items()):
        st = "SILENT" if dev in silent else "✓"
        st_td = (f'<td style="color:#CF222E;font-weight:700">{st}</td>'
                 if dev in silent else f"<td>{st}</td>")
        dev_rows += (f"<tr><td>{dev}</td><td>{(info['last_hb'] or '—')[:19]}</td>"
                     f"<td>{info['scans']}</td><td>{'✓' if info['ep'] else '✗'}</td>"
                     f"<td>{'✓' if info['git'] else '✗'}</td>{st_td}</tr>")
    tbl = ('<div class="sec"><div class="st">Device Inventory</div>'
           "<table><tr><th>DEVICE</th><th>LAST HEARTBEAT</th><th>SCANS</th>"
           f"<th>ENDPOINT</th><th>GIT HOOK</th><th>STATUS</th></tr>{dev_rows}</table></div>")

    pip_sec = ('<div class="sec"><div class="st">Pipeline State</div>'
               "<table><tr><th>LAST KEY</th><th>LAST PROCESSED</th><th>TOTAL EVENTS</th></tr>"
               f"<tr><td>{pipeline.get('last_key','—')}</td>"
               f"<td>{pipeline.get('last_processed_at','—')}</td>"
               f"<td>{pipeline.get('total_events','—')}</td></tr></table></div>")

    return wrap_html("Fleet Health & Coverage Report",
                     kpi + gap_box + tbl + pip_sec,
                     company, d_from, d_to, admin_email, logo_b64)
