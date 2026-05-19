# =============================================================
# FRAGMENT: scan_authorize_fetch.py.frag
# VERSION: 1.1.0
# UPDATED: 2026-05-19
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: At scan start, fetch this user's S3 authorized-provider
#          list (written by the dashboard's [Authorize] button) and
#          merge it into AUTH_LIST. Providers in the list are filtered
#          out by every scan_*() emitter via _is_authorized() — so
#          authorised tools never reach the dashboard, ending the
#          noise loop at source.
#          Storage layout:
#              s3://<bucket>/config/authorized/{email_safe}.json
#          Fetched via the presigned GET URL configured in
#          ~/.patronai/config.json under "authorized_list_url".
#          (Server's url_refresh_loop mints this alongside the
#          existing upload URL — wired in v2.1.0 of agent_store.)
# AUDIT LOG:
#   v1.0.0  2026-05-11  Initial.
#   v1.1.0  2026-05-19  Read URL from ~/.patronai/authorized_list_url file
#                       (written by heartbeat PYREFRESH); _cfg fallback kept.
# =============================================================

def _fetch_remote_authorized() -> list:
    """Best-effort: pull the per-user authorized list from S3.
    Returns a list of provider strings; empty on any failure so the
    scan still runs with whatever local AUTH_LIST already had."""
    url = ""
    try:
        url_file = AGENT_DIR / "authorized_list_url"
        if url_file.exists():
            url = url_file.read_text().strip()
    except Exception:
        pass
    if not url:
        url = _cfg.get("authorized_list_url", "").strip()
    if not url:
        return []
    try:
        import urllib.request
        # 5s timeout — scans must not stall on a slow / dead S3 endpoint.
        req = urllib.request.Request(url, headers={"User-Agent": "patronai-agent"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            doc = json.loads(resp.read().decode())
        providers = doc.get("providers", [])
        if isinstance(providers, list):
            return [str(p).strip().lower() for p in providers if p]
    except Exception:
        # Silent — agent must never block a scan on a remote-config failure.
        return []
    return []


# Merge remote list into AUTH_LIST. Local file remains the ground truth
# for offline operation; remote entries are additive.
_remote_auth = _fetch_remote_authorized()
if _remote_auth:
    AUTH_LIST = sorted(set(AUTH_LIST) | set(_remote_auth))
