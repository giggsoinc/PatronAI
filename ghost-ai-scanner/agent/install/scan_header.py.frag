# =============================================================
# FRAGMENT: scan_header.py.frag
# VERSION: 1.1.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Top of the rendered scan.sh Python block.
#          Imports + identity capture (email / device_uuid / MAC / IPs)
#          + authorised-list loading. Identity binds every scan +
#          heartbeat payload to a unique row: token, email, device_uuid,
#          mac_primary, ip_set, hostname.
# AUDIT LOG:
#   v1.0.0  2026-04-25  Initial. Group 2 — fragment refactor.
#   v1.1.0  2026-04-25  Step 0 — full identity capture from config.json.
# =============================================================
# ── Globals provided to all .frag files by this header ────────
# Path (pathlib), re, json, os, sys, time, platform, subprocess,
# socket, hashlib, shlex — all imported below; frags may use them
# without re-importing.

import json, os, platform, re, shutil, socket, sqlite3, subprocess, uuid
from pathlib import Path
from datetime import datetime, timezone, timedelta

TOKEN     = os.environ.get("PATRONAI_TOKEN", "")
COMPANY   = os.environ.get("PATRONAI_COMPANY", "")
OS_NAME   = platform.system().lower()                          # darwin / linux / windows
DEVICE_ID = platform.node() or os.environ.get("COMPUTERNAME", "unknown")
NOW       = datetime.now(timezone.utc).isoformat()
AGENT_DIR = Path(os.path.expanduser("~/.patronai")) if OS_NAME != "windows" \
            else Path(os.environ.get("USERPROFILE", "")) / ".patronai"


def _load_config() -> dict:
    """Read ~/.patronai/config.json; return {} if missing or unreadable."""
    try:
        return json.loads((AGENT_DIR / "config.json").read_text())
    except Exception:  # intentional: must not crash
        return {}


def _current_ips() -> list:
    """Return current local IPv4 addresses (excludes loopback). Best-effort."""
    try:
        host = socket.gethostname()
        return sorted({ip for ip in socket.gethostbyname_ex(host)[2] if not ip.startswith("127.")})
    except Exception:  # intentional: must not crash
        return []


_cfg          = _load_config()
EMAIL         = _cfg.get("email", "")
DEVICE_UUID   = _cfg.get("device_uuid", "")
MAC_PRIMARY   = _cfg.get("mac_primary", "") or ":".join(f"{(uuid.getnode() >> i) & 0xFF:02x}" for i in (40, 32, 24, 16, 8, 0))
IP_SET        = _current_ips()

_auth_raw  = (AGENT_DIR / "authorized_domains").read_text().strip() if (AGENT_DIR / "authorized_domains").exists() else ""
AUTH_LIST  = [d.strip().lower() for d in _auth_raw.split(",") if d.strip()]


def _is_authorized(name: str) -> bool:
    """Return True if `name` matches anything in the per-user authorised list."""
    n = (name or "").lower()
    return any(a and (a in n or n in a) for a in AUTH_LIST)
