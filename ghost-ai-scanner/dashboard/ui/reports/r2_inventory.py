# =============================================================
# FILE: dashboard/ui/reports/r2_inventory.py
# VERSION: 1.0.0 / UPDATED: 2026-04-28 / OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: R2 — AI Asset Inventory HTML builder.
#          Deduped (owner, device, category, provider) rows, summary KPIs.
# AUDIT LOG: v1.0.0 2026-04-28 Initial.
# =============================================================

from . import _header

try:
    from ..manager_tab_ai_inventory_data import (  # type: ignore
        CATEGORY_LABELS, PHASE_1A_CATEGORIES,
    )
except Exception:  # intentional: must not crash
    CATEGORY_LABELS: dict = {}
    PHASE_1A_CATEGORIES: tuple = ()

_MAX_ROWS = 200


def _dedup(evts: list) -> list:
    """Dedup to latest event per (owner, device_id, category, provider) key."""
    seen: dict = {}
    for e in evts:
        key = (
            e.get("owner") or e.get("email") or "",
            e.get("device_id") or "",
            e.get("category") or "",
            e.get("provider") or "",
        )
        ts = e.get("timestamp") or ""
        if key not in seen or ts > (seen[key].get("timestamp") or ""):
            seen[key] = e
    return list(seen.values())


def _kpi_card(value: str, label: str) -> str:
    """Render a single KPI card."""
    return (f'<div class="kc"><div class="kv">{value}</div>'
            f'<div class="kl">{label}</div></div>')


def build_html(events: list, d_from: str, d_to: str,
               admin_email: str, company: str, logo_b64: str) -> str:
    """Build AI Asset Inventory HTML report string."""
    evts = _header.filter_by_date(events, d_from, d_to)
    evts = [e for e in evts if e.get("outcome") == "ENDPOINT_FINDING"]
    deduped = _dedup(evts)

    total = len(deduped)
    unique_owners = {e.get("owner") or e.get("email") for e in deduped} - {"", None}
    unique_devices = {e.get("device_id") for e in deduped} - {"", None}
    unique_providers = {e.get("provider") for e in deduped} - {"", None}

    kpi_row = (
        '<div class="kr">'
        + _kpi_card(str(total), "Unique Assets")
        + _kpi_card(str(len(unique_owners)), "Unique Owners")
        + _kpi_card(str(len(unique_devices)), "Unique Devices")
        + _kpi_card(str(len(unique_providers)), "Unique Providers")
        + "</div>"
    )

    display = deduped[:_MAX_ROWS]
    caption = (f"Showing {len(display)} of {total} assets"
               if total > _MAX_ROWS else f"{total} asset{'s' if total != 1 else ''}")

    rows = "".join(
        "<tr>"
        f"<td>{e.get('owner') or e.get('email') or '—'}</td>"
        f"<td>{e.get('device_id') or '—'}</td>"
        f"<td>{CATEGORY_LABELS.get(e.get('category', ''), e.get('category') or '—')}</td>"
        f"<td>{e.get('provider') or '—'}</td>"
        f"<td>{(e.get('timestamp') or '')[:10] or '—'}</td>"
        f"<td>{_header.sev_tag(e.get('severity') or 'LOW')}</td>"
        "</tr>"
        for e in display
    ) or "<tr><td colspan='6'>No assets found in this period.</td></tr>"

    tbl = (
        f'<p style="font-size:9px;color:#57606A;margin:4px 0">{caption}</p>'
        "<table>"
        "<tr><th>OWNER</th><th>DEVICE</th><th>CATEGORY</th>"
        "<th>PROVIDER</th><th>LAST SEEN</th><th>SEV</th></tr>"
        + rows + "</table>"
    )

    body = (
        '<div class="sec"><div class="st">Asset Summary</div>'
        + kpi_row + "</div>"
        + '<div class="sec"><div class="st">Asset Inventory</div>'
        + tbl + "</div>"
    )
    return _header.wrap_html(
        "AI Asset Inventory", body, company, d_from, d_to, admin_email, logo_b64
    )
