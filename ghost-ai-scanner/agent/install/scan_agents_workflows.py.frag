# =============================================================
# FRAGMENT: scan_agents_workflows.py.frag
# PROJECT: PatronAI — Phase 1A
# VERSION: 1.0.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Inventory autonomous-agent workflows + scheduled triggers.
#          Catches things `scan_processes` misses because they're
#          configured-but-not-currently-running. Three sources:
#            1. n8n / Flowise / langflow workflow files on disk
#            2. crontab entries containing AI-tool keywords
#            3. macOS launchd plists / Linux systemd user services with
#               AI-tool keywords in the program path
#          All output runs through the shared redactor; secrets-after-
#          redaction findings are dropped.
# AUDIT LOG:
#   v1.0.0  2026-04-26  Initial. Phase 1A.
# =============================================================

# Workflow file roots — one per supported framework.
def _workflow_roots() -> list:
    """Per-OS candidate roots for workflow JSON / YAML files."""
    h = Path.home()
    return [
        ("n8n",       h / ".n8n" / "workflows"),
        ("flowise",   h / ".flowise"),
        ("flowise",   h / "flowise" / "data"),
        ("langflow",  h / ".langflow" / "flows"),
        ("dify",      h / ".dify"),
    ]


def _scan_workflow_files() -> list:
    """Walk known workflow roots; emit a finding per JSON/YAML file."""
    findings: list = []
    for framework, root in _workflow_roots():
        if not root.exists():
            continue
        try:
            for child in root.rglob("*"):
                if not child.is_file():
                    continue
                if child.suffix.lower() not in (".json", ".yaml", ".yml"):
                    continue
                try:
                    size = child.stat().st_size
                    mtime = int(child.stat().st_mtime)
                except Exception:  # intentional: must not crash
                    size, mtime = 0, 0
                finding = {
                    "type":         "agent_workflow",
                    "framework":    framework,
                    "file_safe":    _safe_path(child),
                    "filename":     child.name[:120],
                    "bytes":        size,
                    "mtime_epoch":  mtime,
                }
                safe = _safe_finding(finding)
                if not _has_unredacted_secret(safe):
                    findings.append(safe)
        except Exception:  # intentional: must not crash
            continue
    return findings


_AI_SCHEDULE_RE = re.compile(
    r"\b(openai|anthropic|claude|chatgpt|gpt-4|gpt-3|"
    r"langchain|langgraph|llama[-_.]index|crewai|autogen|"
    r"n8n|flowise|langflow|dify|ollama|huggingface)\b",
    re.IGNORECASE,
)


def _scan_crontab() -> list:
    """Parse `crontab -l` output; flag entries matching AI-tool regex."""
    if OS_NAME == "windows":
        return []                                     # uses Task Scheduler instead
    try:
        out = subprocess.check_output(
            ["crontab", "-l"], stderr=subprocess.DEVNULL, text=True, timeout=5,
        )
    except Exception:  # intentional: must not crash
        return []
    findings: list = []
    for line in out.splitlines():
        if not line or line.startswith("#"):
            continue
        if not _AI_SCHEDULE_RE.search(line):
            continue
        # Cron entry format: m h dom mon dow CMD. Take first 5 tokens as schedule.
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        finding = {
            "type":          "agent_scheduled",
            "trigger":       "crontab",
            "schedule_expr": " ".join(parts[:5]),
            "command_safe":  _redact_text(parts[5])[:240],
        }
        safe = _safe_finding(finding)
        if not _has_unredacted_secret(safe):
            findings.append(safe)
    return findings


def _scan_launchd() -> list:
    """macOS only: enumerate ~/Library/LaunchAgents/*.plist whose
    contents reference AI-tool keywords. Reads as text — no plistlib
    needed for keyword match. Returns plist filename + redacted snippet."""
    if OS_NAME != "darwin":
        return []
    root = Path.home() / "Library" / "LaunchAgents"
    if not root.exists():
        return []
    findings: list = []
    try:
        for plist in root.glob("*.plist"):
            try:
                text = plist.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # intentional: must not crash
                continue
            if not _AI_SCHEDULE_RE.search(text):
                continue
            finding = {
                "type":         "agent_scheduled",
                "trigger":      "launchd",
                "plist_name":   plist.name,
                "plist_safe":   _safe_path(plist),
            }
            safe = _safe_finding(finding)
            if not _has_unredacted_secret(safe):
                findings.append(safe)
    except Exception:  # intentional: must not crash
        return findings
    return findings


def scan_agents_workflows() -> list:
    """Top-level scanner — combines workflow files + cron + launchd."""
    out: list = []
    out.extend(_scan_workflow_files())
    out.extend(_scan_crontab())
    out.extend(_scan_launchd())
    return out
