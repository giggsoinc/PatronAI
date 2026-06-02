# =============================================================
# FILE: src/chat/tools.py
# VERSION: 2.1.0
# UPDATED: 2026-05-02
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: PatronAI chat — analytics tools backed by hourly S3 rollups.
#          Volume-independent: each tool reads N small per-hour
#          dimension files (parallel S3 GETs) instead of scanning
#          raw findings/.../.jsonl. See src/jobs/hourly_rollup.py.
#          Tools take (scope, scope_id) — engine.py supplies these
#          based on the current view (exec → user, others → tenant).
# DEPENDS: query.rollup_reader
# AUDIT LOG:
#   v1.0.0  2026-04-28  Initial — in-memory events list filtering.
#   v2.0.0  2026-04-29  Rollup-backed; events arg dropped.
#   v2.1.0  2026-05-02  Moved from dashboard/ui/chat/ to src/chat/.
#                       Chat brain is no longer UI-coupled. sys.path
#                       workaround removed — query/ is a sibling package.
# =============================================================
# raven: loc-exempt — pending refactor into sub-modules (tracked separately)

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from query.rollup_reader import read_dimension_range, default_window

_SEV_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}
_BUCKET = os.environ.get("MARAUDER_SCAN_BUCKET", "")


# ── Window helpers ──────────────────────────────────────────────


def _window(days_back: int = 30) -> tuple[datetime, datetime]:
    return default_window(days_back=days_back)


def _window_hours(hours: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=max(1, int(hours)))
    return start, end


def _window_dates(d_from: str, d_to: str) -> tuple[datetime, datetime]:
    """Parse inclusive ISO date strings into a [start, end) window."""
    s = datetime.fromisoformat(d_from).replace(tzinfo=timezone.utc)
    e = (datetime.fromisoformat(d_to).replace(tzinfo=timezone.utc)
         + timedelta(days=1))
    return s, e


def _max_severity(by_sev: dict) -> str:
    if not by_sev:
        return "UNKNOWN"
    return max(by_sev.keys(), key=lambda s: _SEV_RANK.get(s.upper(), 0))


def _citation(scope: str, scope_id: str, dims: list,
              start: datetime, end: datetime,
              hits: int = 0, extra: Optional[dict] = None) -> dict:
    """Return a citation block describing where this answer's data came from.
    The LLM is instructed (in prompts.py) to surface this in every reply.
    """
    base = ("users/"   + scope_id) if scope == "user" else ("tenants/" + scope_id)
    bkt = _BUCKET or "<bucket>"
    src = (f"s3://{bkt}/{base}/rollup/"
           f"{start.strftime('%Y-%m-%dT%H')} → {end.strftime('%Y-%m-%dT%H')}/"
           f"{','.join(dims)}.json")
    cit = {
        "source":   "S3 hourly rollups",
        "scope":    scope,
        "scope_id": scope_id[:8] + "…",
        "window":   {"start": start.isoformat(), "end": end.isoformat()},
        "dimensions": dims,
        "rows_aggregated": int(hits),
        "s3_path_pattern": src,
    }
    if extra:
        cit.update(extra)
    return cit


def _is_empty_dim(payload: dict, dim: str) -> bool:
    """Severity is a flat dict; others are dict-of-dicts."""
    if not payload:
        return True
    if dim == "severity":
        return all(int(v or 0) == 0 for v in payload.values())
    return all(int((v or {}).get("hits", 0)) == 0 for v in payload.values())


def _no_data_envelope(scope: str, scope_id: str, dims: list,
                      start: datetime, end: datetime) -> dict:
    """Returned when rollups are empty for the requested scope+window.
    The LLM must NOT fabricate when it sees this — say so honestly."""
    return {
        "no_data": True,
        "_message": ("No rollup data available for this scope/window yet. "
                     "Either the hourly job hasn't run for this period, or "
                     "no findings exist for this user/tenant in this window."),
        "_citation": _citation(scope, scope_id, dims, start, end, hits=0),
    }


# ── Tool 1 — get_summary_stats ──────────────────────────────────


def get_summary_stats(scope: str, scope_id: str,
                      days_back: int = 30) -> dict:
    """Posture snapshot for the current scope over the window."""
    start, end = _window(days_back)
    sev = read_dimension_range(scope, scope_id, "severity", start, end)
    prov = read_dimension_range(scope, scope_id, "provider", start, end)
    if scope == "tenant":
        users = read_dimension_range(scope, scope_id, "user", start, end)
        unique_users = len(users)
    else:
        unique_users = 1  # user-scope = themselves
    total = sum(int(v) for v in sev.values())
    if total == 0 and not prov:
        return _no_data_envelope(scope, scope_id, ["severity", "provider"],
                                 start, end)
    return {
        "window_days": days_back,
        "scope": scope,
        "total_findings": total,
        "severities": {k: int(v) for k, v in sev.items()},
        "unique_users": unique_users,
        "unique_providers": len(prov),
        "_citation": _citation(scope, scope_id,
                                ["by_severity", "by_provider"],
                                start, end, hits=total),
    }


# ── Tool 2 — get_top_risky_users ────────────────────────────────


def get_top_risky_users(scope: str, scope_id: str,
                        n: int = 5, days_back: int = 30) -> dict:
    """Top N users by total_risk in the window. Only meaningful at
    tenant scope; at user scope returns the single caller."""
    start, end = _window(days_back)
    users = read_dimension_range(scope, scope_id, "user", start, end)
    if _is_empty_dim(users, "user"):
        return _no_data_envelope(scope, scope_id, ["by_user"], start, end)
    items = sorted(users.items(),
                   key=lambda kv: float(kv[1].get("total_risk", 0)),
                   reverse=True)[: max(1, int(n))]
    rows = [{
        "user": email,
        "findings": int(u.get("hits", 0)),
        "total_risk": round(float(u.get("total_risk", 0.0)), 2),
        "max_severity": _max_severity(u.get("by_severity", {})),
        "providers": (u.get("providers") or [])[:8],
        "categories": u.get("categories", {}),
        "last_seen": u.get("last_seen", ""),
    } for email, u in items]
    return {"users": rows, "count": len(rows),
            "_citation": _citation(scope, scope_id, ["by_user"],
                                    start, end,
                                    hits=sum(r["findings"] for r in rows))}


# ── Tool 3 — get_user_risk_profile ──────────────────────────────


def get_user_risk_profile(scope: str, scope_id: str,
                          email: str, days_back: int = 90) -> dict:
    """Full profile for one user. At tenant scope, look up email in by_user.
    At user scope, the entire scope IS the user — email arg ignored."""
    start, end = _window(days_back)
    if scope == "tenant":
        users = read_dimension_range(scope, scope_id, "user", start, end)
        if _is_empty_dim(users, "user"):
            return _no_data_envelope(scope, scope_id, ["by_user"], start, end)
        u = users.get((email or "").lower(), {})
        if not u:
            return {"user": email, "found": False, "window_days": days_back,
                    "_citation": _citation(scope, scope_id, ["by_user"],
                                            start, end, hits=0)}
        return {"user": email, "found": True, "window_days": days_back,
                "total_findings": int(u.get("hits", 0)),
                "total_risk": round(float(u.get("total_risk", 0.0)), 2),
                "providers": u.get("providers", []),
                "device_count": int(u.get("device_count", 0)),
                "categories": u.get("categories", {}),
                "severities": u.get("by_severity", {}),
                "first_seen": u.get("first_seen", ""),
                "last_seen": u.get("last_seen", ""),
                "_citation": _citation(scope, scope_id, ["by_user"],
                                        start, end,
                                        hits=int(u.get("hits", 0)))}

    # User scope — caller's own data.
    sev = read_dimension_range(scope, scope_id, "severity", start, end)
    prov = read_dimension_range(scope, scope_id, "provider", start, end)
    cat  = read_dimension_range(scope, scope_id, "category", start, end)
    total = sum(int(v) for v in sev.values())
    if total == 0:
        return _no_data_envelope(scope, scope_id,
                                  ["by_severity", "by_provider", "by_category"],
                                  start, end)
    return {"user": email, "found": True, "window_days": days_back,
            "total_findings": total,
            "providers": sorted(prov.keys()),
            "categories": {k: int(v.get("hits", 0)) for k, v in cat.items()},
            "severities": sev,
            "_citation": _citation(scope, scope_id,
                                    ["by_severity", "by_provider", "by_category"],
                                    start, end, hits=total)}


# ── Tool 4 — query_findings (rollup-flavoured) ──────────────────


def query_findings(scope: str, scope_id: str,
                   severity: str = "", user: str = "", category: str = "",
                   days_back: int = 30, limit: int = 20) -> dict:
    """Rollup-backed approximation: returns matching providers and counts
    rather than raw rows. Volume-independent — works at HUGE scale.

    Filters (all optional): severity, user, category. Time window via days_back.
    """
    start, end = _window(days_back)
    by_provider = read_dimension_range(scope, scope_id, "provider", start, end)

    items = []
    for prov, p in by_provider.items():
        sev_map = p.get("by_severity", {}) or {}
        cats    = p.get("categories", {})  or {}
        users   = p.get("users", [])       or []

        if severity and severity.upper() not in sev_map:
            continue
        if category and category not in cats:
            continue
        if user and user.lower() not in [u.lower() for u in users]:
            continue

        if severity:
            count = int(sev_map.get(severity.upper(), 0))
        elif category:
            count = int(cats.get(category, 0))
        else:
            count = int(p.get("hits", 0))
        if count <= 0:
            continue
        items.append({
            "provider": prov,
            "count": count,
            "users": users[:5],
            "user_count": int(p.get("user_count", len(users))),
            "categories": list(cats.keys()),
            "max_severity": _max_severity(sev_map),
            "last_seen": p.get("last_seen", ""),
        })
    items.sort(key=lambda x: x["count"], reverse=True)
    items = items[: max(1, int(limit))]
    if not items:
        return _no_data_envelope(scope, scope_id, ["by_provider"], start, end)
    return {
        "window_days": days_back,
        "filters": {"severity": severity, "user": user, "category": category},
        "matches": items,
        "match_count": len(items),
        "_note": "Counts aggregated from hourly rollups, not raw rows.",
        "_citation": _citation(scope, scope_id, ["by_provider"], start, end,
                                hits=sum(m["count"] for m in items)),
    }


# ── Tool 5 — get_fleet_status ───────────────────────────────────


def get_fleet_status(scope: str, scope_id: str, days_back: int = 7) -> dict:
    """Device activity in the window. 'Silent' = no rollup activity in the
    last 24 hours of the window."""
    start, end = _window(days_back)
    devices = read_dimension_range(scope, scope_id, "device", start, end)
    if _is_empty_dim(devices, "device"):
        return _no_data_envelope(scope, scope_id, ["by_device"], start, end)
    total = len(devices)
    top = sorted(
        [{"device": d, "hits": int(v.get("hits", 0)),
          "user_count": int(v.get("user_count", 0))}
         for d, v in devices.items()],
        key=lambda x: x["hits"], reverse=True)[:10]
    return {
        "window_days": days_back,
        "total_devices": total,
        "top_devices": top,
        "_citation": _citation(scope, scope_id, ["by_device"], start, end,
                                hits=sum(t["hits"] for t in top)),
    }


# ── Tool 6 — get_shadow_ai_census ───────────────────────────────


def get_shadow_ai_census(scope: str, scope_id: str,
                         days_back: int = 90, limit: int = 20) -> dict:
    """The headline tool — top AI providers ranked by hits.
    Provider names are pre-normalised to human form ('OpenAI ChatGPT',
    'GitHub Copilot') by the rollup job."""
    start, end = _window(days_back)
    by_provider = read_dimension_range(scope, scope_id, "provider", start, end)
    if _is_empty_dim(by_provider, "provider"):
        return _no_data_envelope(scope, scope_id, ["by_provider"], start, end)
    items = []
    for prov, p in by_provider.items():
        items.append({
            "provider": prov,
            "hits": int(p.get("hits", 0)),
            "user_count": int(p.get("user_count", 0)),
            "device_count": int(p.get("device_count", 0)),
            "categories": list((p.get("categories") or {}).keys()),
            "max_severity": _max_severity(p.get("by_severity", {})),
            "first_seen": p.get("first_seen", ""),
            "last_seen": p.get("last_seen", ""),
        })
    items.sort(key=lambda x: x["hits"], reverse=True)
    items = items[: max(1, int(limit))]
    return {"providers": items, "count": len(items),
            "_citation": _citation(scope, scope_id, ["by_provider"],
                                    start, end,
                                    hits=sum(p["hits"] for p in items))}


# ── Tool 7 — get_recent_activity ────────────────────────────────


def get_recent_activity(scope: str, scope_id: str,
                        hours: int = 24) -> dict:
    """Activity in the last N hours via the severity dimension."""
    start, end = _window_hours(hours)
    sev = read_dimension_range(scope, scope_id, "severity", start, end)
    prov = read_dimension_range(scope, scope_id, "provider", start, end)
    total = sum(int(v) for v in sev.values())
    if total == 0 and not prov:
        return _no_data_envelope(scope, scope_id,
                                  ["by_severity", "by_provider"], start, end)
    return {
        "window_hours": hours,
        "total_findings": total,
        "by_severity": {k: int(v) for k, v in sev.items()},
        "top_providers": sorted(
            [{"provider": p, "hits": int(v.get("hits", 0))}
             for p, v in prov.items()],
            key=lambda x: x["hits"], reverse=True)[:10],
        "_citation": _citation(scope, scope_id,
                                ["by_severity", "by_provider"],
                                start, end, hits=total),
    }


# ── Tool 8 — compare_periods ────────────────────────────────────


def compare_periods(scope: str, scope_id: str,
                    d1f: str, d1t: str, d2f: str, d2t: str) -> dict:
    """Compare two date ranges. Returns finding deltas + new providers/users."""
    s1, e1 = _window_dates(d1f, d1t)
    s2, e2 = _window_dates(d2f, d2t)

    p1 = read_dimension_range(scope, scope_id, "provider", s1, e1)
    p2 = read_dimension_range(scope, scope_id, "provider", s2, e2)
    sev1 = read_dimension_range(scope, scope_id, "severity", s1, e1)
    sev2 = read_dimension_range(scope, scope_id, "severity", s2, e2)

    n1 = sum(int(v) for v in sev1.values())
    n2 = sum(int(v) for v in sev2.values())

    if scope == "tenant":
        u1 = read_dimension_range(scope, scope_id, "user", s1, e1)
        u2 = read_dimension_range(scope, scope_id, "user", s2, e2)
        new_users = sorted(set(u2.keys()) - set(u1.keys()))
    else:
        new_users = []

    return {
        "period_1": {"range": f"{d1f}→{d1t}", "findings": n1,
                      "providers": len(p1)},
        "period_2": {"range": f"{d2f}→{d2t}", "findings": n2,
                      "providers": len(p2)},
        "delta_findings": n2 - n1,
        "new_providers": sorted(set(p2.keys()) - set(p1.keys())),
        "new_users": new_users,
        "_citation": {
            "source":   "S3 hourly rollups (two windows)",
            "scope":    scope,
            "scope_id": scope_id[:8] + "…",
            "period_1": _citation(scope, scope_id, ["by_provider", "by_severity"], s1, e1, hits=n1),
            "period_2": _citation(scope, scope_id, ["by_provider", "by_severity"], s2, e2, hits=n2),
        },
    }
