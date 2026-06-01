# =============================================================
# FILE: src/jobs/_rollup_agg.py
# VERSION: 1.0.0
# UPDATED: 2026-06-01
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Extracted from hourly_rollup.py — aggregator helpers and
#          _ScopeAgg class. See hourly_rollup.py for context.
# =============================================================
from __future__ import annotations

from collections import defaultdict
from typing import Optional  # noqa: F401  (re-exported for callers)

from normalizer.provider_names import normalize_provider


def _empty_provider_entry() -> dict:
    return {"hits": 0, "_users": set(), "_devices": set(),
            "categories": defaultdict(int), "by_severity": defaultdict(int),
            "first_seen": "", "last_seen": ""}


def _empty_user_entry() -> dict:
    return {"hits": 0, "_providers": set(), "_devices": set(),
            "categories": defaultdict(int), "by_severity": defaultdict(int),
            "total_risk": 0.0, "first_seen": "", "last_seen": ""}


def _empty_simple_entry() -> dict:
    return {"hits": 0, "_users": set(), "_devices": set(),
            "by_severity": defaultdict(int)}


_SEVERITY_RISK = {"CRITICAL": 5.0, "HIGH": 3.0, "MEDIUM": 1.5,
                  "LOW": 0.5, "UNKNOWN": 0.5}


class _ScopeAgg:
    """Holds the 5 dimension dicts for one scope (one user OR one tenant)."""

    def __init__(self) -> None:
        self.by_provider: dict = defaultdict(_empty_provider_entry)
        self.by_user:     dict = defaultdict(_empty_user_entry)
        self.by_severity: dict = defaultdict(int)
        self.by_device:   dict = defaultdict(_empty_simple_entry)
        self.by_category: dict = defaultdict(_empty_simple_entry)
        self.rows: int = 0

    def add(self, row: dict) -> None:
        self.rows += 1
        prov_raw = row.get("provider") or ""
        category = row.get("category") or ""
        severity = (row.get("severity") or "UNKNOWN").upper()
        owner    = (row.get("owner") or row.get("email") or "unknown").lower()
        device   = row.get("src_hostname") or row.get("device_uuid") or ""
        ts       = row.get("timestamp") or ""

        # Severity → simple counter.
        self.by_severity[severity] += 1

        # Provider dimension — normalize to human name.
        if prov_raw:
            prov = normalize_provider(category, prov_raw)
            p = self.by_provider[prov]
            p["hits"] += 1
            p["_users"].add(owner)
            if device:
                p["_devices"].add(device)
            p["categories"][category] += 1
            p["by_severity"][severity] += 1
            if ts and (not p["first_seen"] or ts < p["first_seen"]):
                p["first_seen"] = ts
            if ts and ts > p["last_seen"]:
                p["last_seen"] = ts

        # User dimension.
        u = self.by_user[owner]
        u["hits"] += 1
        if prov_raw:
            u["_providers"].add(normalize_provider(category, prov_raw))
        if device:
            u["_devices"].add(device)
        u["categories"][category] += 1
        u["by_severity"][severity] += 1
        u["total_risk"] += _SEVERITY_RISK.get(severity, 0.5)
        if ts and (not u["first_seen"] or ts < u["first_seen"]):
            u["first_seen"] = ts
        if ts and ts > u["last_seen"]:
            u["last_seen"] = ts

        # Device dimension.
        if device:
            d = self.by_device[device]
            d["hits"] += 1
            d["_users"].add(owner)
            d["by_severity"][severity] += 1

        # Category dimension.
        if category:
            c = self.by_category[category]
            c["hits"] += 1
            c["_users"].add(owner)
            if device:
                c["_devices"].add(device)
            c["by_severity"][severity] += 1

    # ── Serialisation ────────────────────────────────────────────

    @staticmethod
    def _finalise_provider(p: dict) -> dict:
        return {"hits": p["hits"],
                "users": sorted(p["_users"]),
                "user_count": len(p["_users"]),
                "device_count": len(p["_devices"]),
                "categories": dict(p["categories"]),
                "by_severity": dict(p["by_severity"]),
                "first_seen": p["first_seen"],
                "last_seen": p["last_seen"]}

    @staticmethod
    def _finalise_user(u: dict) -> dict:
        return {"hits": u["hits"],
                "providers": sorted(u["_providers"]),
                "device_count": len(u["_devices"]),
                "categories": dict(u["categories"]),
                "by_severity": dict(u["by_severity"]),
                "total_risk": round(u["total_risk"], 2),
                "first_seen": u["first_seen"],
                "last_seen": u["last_seen"]}

    @staticmethod
    def _finalise_simple(s: dict) -> dict:
        return {"hits": s["hits"],
                "user_count": len(s["_users"]),
                "device_count": len(s.get("_devices", [])) if "_devices" in s else 0,
                "by_severity": dict(s["by_severity"])}

    def serialise(self) -> dict[str, dict]:
        return {
            "by_provider": {k: self._finalise_provider(v)
                            for k, v in self.by_provider.items()},
            "by_user":     {k: self._finalise_user(v)
                            for k, v in self.by_user.items()},
            "by_severity": dict(self.by_severity),
            "by_device":   {k: self._finalise_simple(v)
                            for k, v in self.by_device.items()},
            "by_category": {k: self._finalise_simple(v)
                            for k, v in self.by_category.items()},
        }
