# =============================================================
# FILE: dashboard/ui/reports/r6_compliance.py
# VERSION: 1.0.0
# UPDATED: 2026-04-28
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: R6 — Compliance Audit Trail HTML builder.
#          Immutable findings export with SHA-256 integrity hash
#          over the full serialised event list.
# AUDIT LOG:
#   v1.0.0  2026-04-28  Initial.
# =============================================================

import hashlib
import json
import sys
from datetime import datetime

from ._header import filter_by_date, sev_tag, wrap_html

_MAX_ROWS = 500


def _period_days(d_from: str, d_to: str) -> str:
    """Calculate inclusive day count between two YYYY-MM-DD strings."""
    try:
        delta = datetime.fromisoformat(d_to) - datetime.fromisoformat(d_from)
        return str(delta.days + 1)
    except Exception:  # intentional: must not crash
        return "—"


def _kpi_card(value: str, label: str) -> str:
    """Return a single KPI card HTML fragment."""
    return (
        f'<div class="kc">'
        f'<div class="kv">{value}</div>'
        f'<div class="kl">{label}</div>'
        f"</div>"
    )


def _events_table(events: list) -> str:
    """Build the compliance audit trail HTML table (max _MAX_ROWS rows)."""
    rows = ""
    for e in events[:_MAX_ROWS]:
        ts = (e.get("timestamp") or "")[:19]
        user = e.get("email") or e.get("owner") or "—"
        dev = (e.get("device_id") or e.get("src_hostname") or e.get("src_ip") or "—")[:30]
        prov = (e.get("provider") or "—")[:40]
        outcome = e.get("outcome") or "—"
        sev = (e.get("severity") or "LOW").upper()
        src = e.get("source") or "—"
        rows += (
            f"<tr><td>{ts}</td><td>{user}</td><td>{dev}</td>"
            f"<td>{prov}</td><td>{outcome}</td>"
            f"<td>{sev_tag(sev)}</td><td>{src}</td></tr>"
        )
    truncated = ""
    if len(events) > _MAX_ROWS:
        truncated = (
            f'<div class="vb">Showing first {_MAX_ROWS} of '
            f"{len(events)} records. Integrity hash covers the full dataset.</div>"
        )
    header = (
        "<tr><th>TIMESTAMP</th><th>USER</th><th>DEVICE</th>"
        "<th>PROVIDER</th><th>OUTCOME</th><th>SEV</th><th>SOURCE</th></tr>"
    )
    return f"{truncated}<table>{header}{rows}</table>"


def build_html(
    events: list,
    d_from: str,
    d_to: str,
    admin_email: str,
    company: str,
    logo_b64: str,
) -> str:
    """Build complete R6 Compliance Audit Trail HTML report.

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
        filtered = filter_by_date(events, d_from, d_to)
    except Exception as exc:
        print(f"[r6] filter_by_date error: {exc}", file=sys.stderr)
        filtered = list(events)

    events_sorted = sorted(filtered, key=lambda e: e.get("timestamp") or "")

    try:
        digest = hashlib.sha256(
            json.dumps(events_sorted, sort_keys=True, default=str).encode()
        ).hexdigest()
    except Exception as exc:
        print(f"[r6] sha256 error: {exc}", file=sys.stderr)
        digest = "error-computing-hash"

    total = len(events_sorted)
    u_users = len({e.get("email") or e.get("owner") for e in events_sorted} - {None, ""})
    u_prov = len({e.get("provider") for e in events_sorted} - {None, ""})
    days = _period_days(d_from, d_to)

    kpi = (
        '<div class="kr">'
        + _kpi_card(str(total), "Total Events")
        + _kpi_card(str(u_users), "Unique Users")
        + _kpi_card(str(u_prov), "Unique Providers")
        + _kpi_card(days, "Period Days")
        + "</div>"
    )

    digest_display = f"{digest[:32]}…{digest[-8:]}"
    integrity_box = (
        f'<div class="ok"><strong>Integrity Verified — SHA-256:</strong> '
        f"<code>{digest_display}</code><br>"
        f"<span style='font-size:9px'>Full hash in report footer</span></div>"
    )

    table_sec = (
        '<div class="sec"><div class="st">Audit Trail</div>'
        + _events_table(events_sorted)
        + "</div>"
    )

    footer_note = (
        f'<div style="font-size:9px;color:#57606A;margin-top:12px;'
        f'font-family:monospace;word-break:break-all;">'
        f"Full SHA-256: {digest}</div>"
    )

    body = kpi + integrity_box + table_sec + footer_note
    return wrap_html(
        "Compliance Audit Trail",
        body,
        company,
        d_from,
        d_to,
        admin_email,
        logo_b64,
    )
