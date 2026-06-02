# =============================================================
# FRAGMENT: scan_containers.py.frag
# VERSION: 1.0.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Layered container introspection — no docker exec.
#          L1: image-name match against AI image regex.
#          L2: docker logs (last 500 lines) grep for AI signal patterns.
#          Covers running AND stopped containers. No journal access.
# AUDIT LOG:
#   v1.0.0  2026-04-25  Initial. Group 2.D — container scan.
# =============================================================

_AI_IMAGE_RE = re.compile(
    r"\b(flowiseai/flowise|n8nio/n8n|langflow|dify|ollama|gpt4all|"
    r"lmstudio|huggingface|openai|anthropic|cursor|vllm|"
    r"localai|fastchat|text-generation-inference)\b",
    re.IGNORECASE,
)
_AI_LOG_RE = re.compile(
    r"\b(api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|"
    r"sk-proj-|sk-ant-|hf_[A-Za-z0-9]{8,}|ChatCompletion|loading model|"
    r"prompt_tokens|completion_tokens|model loaded|gguf|ollama)\b",
    re.IGNORECASE,
)


def scan_containers() -> list:
    """L1 image-name + L2 docker-logs scan across running and stopped containers."""
    findings: list = []
    try:
        listing = subprocess.check_output(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"],
            stderr=subprocess.DEVNULL, text=True, timeout=10,
        )
    except Exception:  # intentional: must not crash
        return findings

    for line in listing.splitlines():
        cells = line.split("\t")
        if len(cells) < 3:
            continue
        name, image, status = cells[0], cells[1], cells[2]

        # L1 — image name match (cheap; works on stopped containers)
        if _AI_IMAGE_RE.search(image) and not _is_authorized(image):
            findings.append({
                "type": "container_image", "name": name, "image": image, "status": status,
            })

        # L2 — log scan (one signal hit suffices)
        try:
            logs = subprocess.check_output(
                ["docker", "logs", "--tail", "500", name],
                stderr=subprocess.STDOUT, text=True, timeout=8, errors="replace",
            )
        except Exception:  # intentional: must not crash
            continue
        m = _AI_LOG_RE.search(logs)
        if m and not _is_authorized(image):
            findings.append({
                "type": "container_log_signal", "name": name, "image": image,
                "signal": m.group(0).lower(),
            })
    return findings
