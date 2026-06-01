# =============================================================
# FRAGMENT: scan_shell_history.py.frag
# VERSION: 1.0.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Catch ephemeral AI usage that left no live trace —
#          docker pull/run of AI images, pip/npm/brew installs of
#          AI packages — by reading bash + zsh shell history.
#          Closes the deleted-container blind spot.
#          (PowerShell history handled by .ps1.frag.)
# AUDIT LOG:
#   v1.0.0  2026-04-25  Initial. Group 2.D — shell history scan.
# =============================================================

def _history_files() -> list:
    """OS-aware list of shell-history paths to scan."""
    h = Path.home()
    paths = [h / ".bash_history", h / ".zsh_history", h / ".history",
             h / ".local/share/fish/fish_history"]
    if OS_NAME == "windows":
        ad = Path(os.environ.get("APPDATA", h / "AppData/Roaming"))
        paths.append(ad / "Microsoft/Windows/PowerShell/PSReadLine/ConsoleHost_history.txt")
    return paths

_SHELL_AI_RE = re.compile(
    r"\b(docker\s+(pull|run|build|exec)\s+\S*(flowise|n8n|langflow|dify|"
    r"ollama|gpt4all|lmstudio|huggingface|openai|anthropic|cursor|vllm)|"
    r"pip3?\s+install\s+\S*(openai|anthropic|langchain|crewai|autogen|"
    r"flowise|langflow|ollama|llama-cpp-python|gpt4all|transformers)|"
    r"npm\s+(install|i)\s+\S*(@?n8n|flowise|langflow|@codeium|@github/copilot|"
    r"@tabnine|cursor)|"
    r"brew\s+install\s+\S*(ollama|lm-studio|gpt4all|jan-app|flowise))\b",
    re.IGNORECASE,
)


def scan_shell_history() -> list:
    """Read shell history files (last 1 MB each) and emit findings for AI commands."""
    findings: list = []
    for path in _history_files():
        if not path.exists():
            continue
        try:
            text = path.read_text(errors="replace")[-1_000_000:]   # last 1 MB
        except Exception:  # intentional: must not crash
            continue
        seen: set = set()
        for line in text.splitlines():
            m = _SHELL_AI_RE.search(line)
            if not m:
                continue
            cmd = m.group(0).strip().lower()
            if cmd in seen or _is_authorized(cmd):
                continue
            seen.add(cmd)
            findings.append({
                "type":         "shell_history",
                "shell":        path.name,
                "command_hint": cmd[:140],
            })
    return findings
