# =============================================================
# FILE: dashboard/ui/time_fmt.py
# PROJECT: PatronAI — Phase 1B
# VERSION: 1.0.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Single time-format helper used by every dashboard table /
#          chart axis / tooltip. Converts ISO-8601 timestamps to
#          `DD-MMM-YY HH24:MM:SS TZ` (e.g. `26-APR-26 14:30:45 IST`)
#          rendered in the viewer's local timezone.
#          Browser TZ is auto-detected via st.context.timezone (Streamlit
#          1.32+); falls back to a session_state override for older
#          versions, then UTC. The original UTC ISO string is exposed
#          via `tooltip()` so audit teams can copy-paste raw UTC.
# DEPENDS: streamlit, datetime, zoneinfo (stdlib 3.9+)
# AUDIT LOG:
#   v1.0.0  2026-04-26  Initial. Phase 1B.
# =============================================================

from datetime import datetime, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # intentional: must not crash                                           # pragma: no cover
    ZoneInfo = None                                          # type: ignore

try:
    import streamlit as st
except Exception:  # intentional: must not crash                                           # pragma: no cover
    st = None                                                # type: ignore


def _viewer_tz_name() -> str:
    """Best-effort browser-timezone detection. Returns IANA name or 'UTC'."""
    if st is None:
        return "UTC"
    # Streamlit 1.32+ exposes browser timezone via st.context.timezone.
    try:
        tz = getattr(st.context, "timezone", None)
        if tz:
            return str(tz)
    except Exception:  # intentional: must not crash
        pass
    # Fall back to a session-state override (set elsewhere in the app).
    try:
        tz = st.session_state.get("user_timezone")
        if tz:
            return str(tz)
    except Exception:  # intentional: must not crash
        pass
    return "UTC"


def _tz_label(tz_name: str) -> str:
    """Short 3-letter (or up-to-5) tz code from an IANA name.
    Falls back to the IANA region segment when no abbreviation is known."""
    abbr = {
        "UTC":              "UTC",
        "Asia/Kolkata":     "IST",
        "Asia/Calcutta":    "IST",
        "America/New_York": "EST",
        "America/Chicago":  "CST",
        "America/Denver":   "MST",
        "America/Los_Angeles": "PST",
        "Europe/London":    "GMT",
        "Europe/Paris":     "CET",
        "Europe/Berlin":    "CET",
        "Australia/Sydney": "AEDT",
        "Asia/Tokyo":       "JST",
        "Asia/Singapore":   "SGT",
    }
    if tz_name in abbr:
        return abbr[tz_name]
    # Fallback — last segment of the IANA name (e.g. "Asia/Yangon" -> "Yangon")
    return tz_name.rsplit("/", 1)[-1] if "/" in tz_name else tz_name


def _parse_iso(s) -> Optional[datetime]:
    """Best-effort parse of any ISO-8601 string. Returns naive-aware UTC dt."""
    if s is None or s == "":
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    try:
        text = str(s).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # intentional: must not crash
        return None


_MONTH = ("JAN","FEB","MAR","APR","MAY","JUN",
          "JUL","AUG","SEP","OCT","NOV","DEC")


def fmt(iso, tz_name: Optional[str] = None) -> str:
    """`DD-MMM-YY HH24:MM:SS TZ` in viewer's local TZ.
    Returns '' for empty / unparseable input so it's safe to drop into
    f-strings without guarding."""
    dt = _parse_iso(iso)
    if dt is None:
        return ""
    name = tz_name or _viewer_tz_name()
    if ZoneInfo is not None and name != "UTC":
        try:
            dt = dt.astimezone(ZoneInfo(name))
        except Exception:  # intentional: must not crash
            name = "UTC"
            dt = dt.astimezone(timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return (f"{dt.day:02d}-{_MONTH[dt.month - 1]}-{dt.year % 100:02d} "
            f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d} "
            f"{_tz_label(name)}")


def relative(iso) -> str:
    """Short relative string — '2m ago', '3h ago', '5d ago'. '' on miss."""
    dt = _parse_iso(iso)
    if dt is None:
        return ""
    delta = (datetime.now(timezone.utc) - dt).total_seconds()
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def tooltip(iso) -> str:
    """Original UTC ISO timestamp — for HTML title= attributes."""
    dt = _parse_iso(iso)
    return dt.isoformat() if dt else ""
