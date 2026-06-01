# =============================================================
# FILE: src/query/_rollup_merge.py
# VERSION: 1.0.0
# UPDATED: 2026-06-01
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Extracted from rollup_reader.py — per-dimension merge and
#          finalise helpers for aggregating hourly rollup slices.
#          No external imports — pure built-in types.
# =============================================================
from __future__ import annotations


def _merge_provider(merged: dict, src: dict) -> None:
    """Merge two by_provider dicts. `users` is a sorted list of email strings;
    set-union for distinct counts."""
    for prov, entry in src.items():
        if prov not in merged:
            merged[prov] = {
                "hits": int(entry.get("hits", 0)),
                "_users": set(entry.get("users", []) or []),
                "device_count": int(entry.get("device_count", 0)),
                "categories": dict(entry.get("categories", {}) or {}),
                "by_severity": dict(entry.get("by_severity", {}) or {}),
                "first_seen": entry.get("first_seen", "") or "",
                "last_seen":  entry.get("last_seen",  "") or "",
            }
        else:
            m = merged[prov]
            m["hits"] += int(entry.get("hits", 0))
            m["_users"].update(entry.get("users", []) or [])
            m["device_count"] += int(entry.get("device_count", 0))
            for k, v in (entry.get("categories", {}) or {}).items():
                m["categories"][k] = m["categories"].get(k, 0) + int(v)
            for k, v in (entry.get("by_severity", {}) or {}).items():
                m["by_severity"][k] = m["by_severity"].get(k, 0) + int(v)
            fs = entry.get("first_seen", "")
            ls = entry.get("last_seen",  "")
            if fs and (not m["first_seen"] or fs < m["first_seen"]):
                m["first_seen"] = fs
            if ls and ls > m["last_seen"]:
                m["last_seen"] = ls


def _merge_user(merged: dict, src: dict) -> None:
    """Merge two by_user dicts. Providers are set-unioned for distinct counts."""
    for u, entry in src.items():
        if u not in merged:
            merged[u] = {
                "hits": int(entry.get("hits", 0)),
                "_providers": set(entry.get("providers", []) or []),
                "device_count": int(entry.get("device_count", 0)),
                "categories": dict(entry.get("categories", {}) or {}),
                "by_severity": dict(entry.get("by_severity", {}) or {}),
                "total_risk": float(entry.get("total_risk", 0.0)),
                "first_seen": entry.get("first_seen", "") or "",
                "last_seen":  entry.get("last_seen",  "") or "",
            }
        else:
            m = merged[u]
            m["hits"] += int(entry.get("hits", 0))
            m["_providers"].update(entry.get("providers", []) or [])
            m["device_count"] += int(entry.get("device_count", 0))
            for k, v in (entry.get("categories", {}) or {}).items():
                m["categories"][k] = m["categories"].get(k, 0) + int(v)
            for k, v in (entry.get("by_severity", {}) or {}).items():
                m["by_severity"][k] = m["by_severity"].get(k, 0) + int(v)
            m["total_risk"] += float(entry.get("total_risk", 0.0))
            fs = entry.get("first_seen", "")
            ls = entry.get("last_seen",  "")
            if fs and (not m["first_seen"] or fs < m["first_seen"]):
                m["first_seen"] = fs
            if ls and ls > m["last_seen"]:
                m["last_seen"] = ls


def _merge_severity(merged: dict, src: dict) -> None:
    """Merge two by_severity flat dicts (simple int counters)."""
    for k, v in src.items():
        merged[k] = merged.get(k, 0) + int(v)


def _merge_simple(merged: dict, src: dict) -> None:
    """Merge device / category dimension dicts."""
    for k, entry in src.items():
        if k not in merged:
            merged[k] = {
                "hits": int(entry.get("hits", 0)),
                "user_count": int(entry.get("user_count", 0)),
                "device_count": int(entry.get("device_count", 0)),
                "by_severity": dict(entry.get("by_severity", {}) or {}),
            }
        else:
            m = merged[k]
            m["hits"] += int(entry.get("hits", 0))
            m["user_count"] += int(entry.get("user_count", 0))
            m["device_count"] += int(entry.get("device_count", 0))
            for kk, vv in (entry.get("by_severity", {}) or {}).items():
                m["by_severity"][kk] = m["by_severity"].get(kk, 0) + int(vv)


def _finalise(dim: str, merged: dict) -> dict:
    """Convert internal sets back to counts/sorted lists for JSON output."""
    if dim == "provider":
        out = {}
        for prov, m in merged.items():
            users = sorted(m.pop("_users"))
            m["users"] = users
            m["user_count"] = len(users)
            out[prov] = m
        return out
    if dim == "user":
        out = {}
        for u, m in merged.items():
            provs = sorted(m.pop("_providers"))
            m["providers"] = provs
            m["provider_count"] = len(provs)
            out[u] = m
        return out
    return merged
