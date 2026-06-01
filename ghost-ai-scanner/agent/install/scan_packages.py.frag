# =============================================================
# FRAGMENT: scan_packages.py.frag
# VERSION: 1.0.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Enumerate AI packages installed via pip / npm / brew.
#          Filtered through AUTH_LIST so per-user whitelisted
#          packages are suppressed. Returns finding dicts.
# AUDIT LOG:
#   v1.0.0  2026-04-25  Initial. Extracted from setup_agent.sh.template.
# =============================================================

_AI_PKGS_RE = re.compile(
    r"\b(openai|anthropic|langchain|langgraph|llama[-_.]index|haystack|autogen|"
    r"crewai|pydantic[-_.]ai|smolagents|transformers|huggingface[-_.]hub|"
    r"diffusers|cohere|mistralai|together|groq|replicate|instructor|marvin|"
    r"dspy|outlines|aisuite|agno|phidata|n8n|@n8n|cursor|copilot|codeium|"
    r"tabnine|github-copilot|ollama|lm-studio|gpt4all|jan-app|flowise|"
    r"langflow|buildship|stack-ai)\b",
    re.IGNORECASE,
)


def _package_managers() -> list:
    """Return (manager_label, command) pairs for the current OS."""
    if OS_NAME == "windows":
        return [
            ("pip",     ["pip",     "list", "--format=columns"]),
            ("npm",     ["npm",     "list", "-g", "--depth=0"]),
            ("choco",   ["choco",   "list", "-lo"]),
            ("winget",  ["winget",  "list"]),
        ]
    return [
        ("pip",  ["pip3", "list", "--format=columns"]),
        ("npm",  ["npm", "list", "-g", "--depth=0"]),
        ("brew", ["brew", "list"]),
    ]


def scan_packages() -> list:
    """Run package-manager listings, regex match against AI package patterns."""
    findings: list = []
    for mgr, cmd in _package_managers():
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=20)
        except Exception:  # intentional: must not crash
            continue
        for line in out.splitlines():
            parts = line.split()
            name = parts[0].strip(" ├─└│") if parts else ""
            if name and _AI_PKGS_RE.search(name) and not _is_authorized(name):
                findings.append({"type": "package", "manager": mgr, "name": name})
    return findings
