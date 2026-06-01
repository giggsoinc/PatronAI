# =============================================================
# FRAGMENT: scan_processes.py.frag
# VERSION: 1.0.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Enumerate currently running AI-tool processes via ps.
#          Filtered through AUTH_LIST. Returns finding dicts.
# AUDIT LOG:
#   v1.0.0  2026-04-25  Initial. Extracted from setup_agent.sh.template.
# =============================================================

_AI_PROCS_RE = re.compile(
    r"\b(n8n|ollama|lm[._-]studio|lmstudio|gpt4all|jan|cursor|copilot|"
    r"codeium|tabnine|msty|chatbox|typing-mind|flowise|langflow)\b",
    re.IGNORECASE,
)


def _process_command_lines() -> list:
    """OS-aware enumeration of running process command lines."""
    if OS_NAME == "windows":
        try:
            out = subprocess.check_output(
                ["tasklist", "/FO", "CSV", "/NH"],
                stderr=subprocess.DEVNULL, text=True, timeout=10,
            )
        except Exception:  # intentional: must not crash
            return []
        return [ln.split(",", 1)[0].strip('"') for ln in out.splitlines() if ln]
    try:
        out = subprocess.check_output(["ps", "aux"], stderr=subprocess.DEVNULL, text=True, timeout=10)
    except Exception:  # intentional: must not crash
        return []
    cols: list = []
    for line in out.splitlines():
        parts = line.split()
        cols.append(" ".join(parts[10:]) if len(parts) > 10 else "")
    return cols


def scan_processes() -> list:
    """OS-aware process enumeration → regex match against AI process names."""
    findings: list = []
    seen: set = set()
    for cmd_col in _process_command_lines():
        m = _AI_PROCS_RE.search(cmd_col)
        if not m:
            continue
        proc = m.group(0).lower()
        if proc not in seen and not _is_authorized(proc):
            findings.append({"type": "process", "name": proc})
            seen.add(proc)
    return findings
