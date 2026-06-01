# =============================================================
# FRAGMENT: scan_mcp_configs.py.frag
# PROJECT: PatronAI — Phase 1A
# VERSION: 1.0.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Real MCP-server inventory. Reads the MCP config JSONs that
#          AI clients (Claude Desktop, Cursor, Continue, Cline) keep
#          on disk. Emits one `mcp_server` finding per server defined,
#          with redacted metadata (server name + command basename + arg
#          FLAGS only — no values) and a SHA-256 of the parent file for
#          change detection. Privacy: every finding passes through the
#          shared redactor; any finding still carrying a secret after
#          redaction is dropped entirely.
# AUDIT LOG:
#   v1.0.0  2026-04-26  Initial. Phase 1A.
# =============================================================

import hashlib

# Per-host config locations — broad enough to cover the common installs.
# Order matters only for deterministic output; not security-relevant.
def _mcp_config_paths() -> list:
    """Per-OS list of (host_label, config_path) candidates."""
    h = Path.home()
    if OS_NAME == "darwin":
        return [
            ("claude_desktop", h / "Library/Application Support/Claude/claude_desktop_config.json"),
            ("cursor",         h / ".cursor/mcp.json"),
            ("continue",       h / ".continue/config.json"),
            ("cline",          h / ".config/Cline/mcp_settings.json"),
        ]
    if OS_NAME == "windows":
        appdata = Path(os.environ.get("APPDATA", h / "AppData/Roaming"))
        return [
            ("claude_desktop", appdata / "Claude/claude_desktop_config.json"),
            ("cursor",         h / ".cursor/mcp.json"),
            ("continue",       h / ".continue/config.json"),
            ("cline",          h / ".config/Cline/mcp_settings.json"),
        ]
    # linux + other unix
    return [
        ("claude_desktop", h / ".config/Claude/claude_desktop_config.json"),
        ("cursor",         h / ".cursor/mcp.json"),
        ("continue",       h / ".continue/config.json"),
        ("cline",          h / ".config/Cline/mcp_settings.json"),
    ]


def _hash_file_bytes(p: Path) -> str:
    """Compute SHA-256 of a file's raw bytes, or '' on read error."""
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:  # intentional: must not crash
        return ""


def _arg_flags_only(args) -> list:
    """Take a list of MCP server args; keep only flag-shaped tokens
    (start with '-'). Drop values, paths, URLs, anything that could
    leak. Output capped at 12 flags per server."""
    if not isinstance(args, list):
        return []
    flags = [a for a in args if isinstance(a, str) and a.startswith("-")]
    return flags[:12]


def _env_keys_only(env) -> list:
    """Take a dict of env vars; return only the KEYS, never values."""
    if not isinstance(env, dict):
        return []
    return sorted(env.keys())[:24]


def _command_basename(cmd) -> str:
    """Reduce a full command path to its leaf executable name."""
    if not isinstance(cmd, str) or not cmd:
        return ""
    return Path(cmd).name


def _parse_one_config(host: str, path: Path) -> list:
    """Read one MCP config JSON; emit a finding per server. On any
    parse failure, return empty list (don't crash the scan)."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # intentional: must not crash
        return []
    servers = data.get("mcpServers") or {}
    if not isinstance(servers, dict) or not servers:
        return []
    config_sha = _hash_file_bytes(path)
    out: list = []
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        finding = {
            "type":             "mcp_server",
            "mcp_host":         host,
            "config_basename":  path.name,
            "config_sha256":    config_sha,
            "server_name":      str(name)[:120],
            "command_basename": _command_basename(spec.get("command")),
            "arg_flags":        _arg_flags_only(spec.get("args")),
            "env_keys_present": _env_keys_only(spec.get("env")),
            "transport":        str(spec.get("transport", "stdio"))[:24],
        }
        safe = _safe_finding(finding)
        if _has_unredacted_secret(safe):
            continue                                  # privacy gate — drop
        out.append(safe)
    return out


def scan_mcp_configs() -> list:
    """Walk every known MCP config path; emit `mcp_server` findings.
    Returns [] cleanly if no host is installed — common on locked-down
    fleets where neither Claude Desktop nor Cursor is in use."""
    findings: list = []
    for host, path in _mcp_config_paths():
        try:
            findings.extend(_parse_one_config(host, path))
        except Exception:  # intentional: must not crash
            continue                                  # never crash a scan
    return findings
