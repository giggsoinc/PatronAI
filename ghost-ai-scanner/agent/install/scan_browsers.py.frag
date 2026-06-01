# =============================================================
# FRAGMENT: scan_browsers.py.frag
# VERSION: 1.0.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Multi-browser × multi-OS history scan.
#          macOS: Safari, Chrome, Firefox, Edge, Brave, Arc, Opera, Vivaldi.
#          Linux: Chrome, Chromium, Firefox, Edge, Brave.
#          (Windows handled by scan_browsers.ps1.frag.)
#          7-day window. Matches the ENDPOINT_SCAN unauthorised-domain regex.
# AUDIT LOG:
#   v1.0.0  2026-04-25  Initial. Group 2.B — multi-browser path expansion.
# =============================================================

_UNAUTH_DOMAIN_RE = re.compile(
    r"\b(chat\.openai\.com|chatgpt\.com|claude\.ai|gemini\.google\.com|"
    r"copilot\.microsoft\.com|bard\.google\.com|poe\.com|perplexity\.ai|"
    r"you\.com|phind\.com|n8n\.io|app\.n8n\.io|midjourney\.com|leonardo\.ai|"
    r"runwayml\.com|stability\.ai|elevenlabs\.io|synthesia\.io|heygen\.com|"
    r"murf\.ai|suno\.(ai|com)|udio\.com|cursor\.(sh|com)|codeium\.com|"
    r"tabnine\.com|replit\.com|v0\.(dev|app)|bolt\.new|lovable\.dev|"
    r"flowiseai\.com|buildship\.com|stack-ai\.com|notebooklm\.google\.com|"
    r"manus\.(ai|im)|pika\.art|github\.com/copilot)\b",
    re.IGNORECASE,
)


def _scan_sqlite(db_path: Path, query: str) -> list:
    """Copy the SQLite DB (browser may hold a lock) then read URLs."""
    tmp_dir = Path(os.environ.get("TEMP", "/tmp"))
    tmp = tmp_dir / "patronai_hist.db"
    try:
        shutil.copy2(db_path, tmp)
        conn = sqlite3.connect(str(tmp))
        rows = conn.execute(query).fetchall()
        conn.close()
        return [r[0] for r in rows if r and r[0]]
    except Exception:  # intentional: must not crash
        return []
    finally:
        tmp.unlink(missing_ok=True)


def _browser_paths() -> dict:
    """OS-aware map of browser → (history_db, query). Covers macOS / Linux / Windows."""
    h = Path.home()
    chrome_q = "SELECT url FROM urls WHERE last_visit_time > (strftime('%s','now')-604800)*1000000"
    safari_q = "SELECT url FROM history_visits v JOIN history_items i ON v.history_item=i.id WHERE v.visit_time > (strftime('%s','now')-604800)"
    ff_q     = "SELECT url FROM moz_places WHERE last_visit_date > (strftime('%s','now')-604800)*1000000"

    def _ff_first(root: Path) -> Path:
        return next(iter(sorted(root.glob("*/places.sqlite"))), None) if root.exists() else None

    if OS_NAME == "darwin":
        return {
            "safari":  (h / "Library/Safari/History.db", safari_q),
            "chrome":  (h / "Library/Application Support/Google/Chrome/Default/History", chrome_q),
            "edge":    (h / "Library/Application Support/Microsoft Edge/Default/History", chrome_q),
            "brave":   (h / "Library/Application Support/BraveSoftware/Brave-Browser/Default/History", chrome_q),
            "arc":     (h / "Library/Application Support/Arc/User Data/Default/History", chrome_q),
            "opera":   (h / "Library/Application Support/com.operasoftware.Opera/History", chrome_q),
            "vivaldi": (h / "Library/Application Support/Vivaldi/Default/History", chrome_q),
            "firefox": (_ff_first(h / "Library/Application Support/Firefox/Profiles"), ff_q),
        }
    if OS_NAME == "windows":
        la = Path(os.environ.get("LOCALAPPDATA", h / "AppData/Local"))
        ad = Path(os.environ.get("APPDATA",      h / "AppData/Roaming"))
        return {
            "edge":    (la / "Microsoft/Edge/User Data/Default/History", chrome_q),
            "chrome":  (la / "Google/Chrome/User Data/Default/History",  chrome_q),
            "brave":   (la / "BraveSoftware/Brave-Browser/User Data/Default/History", chrome_q),
            "vivaldi": (la / "Vivaldi/User Data/Default/History", chrome_q),
            "opera":   (ad / "Opera Software/Opera Stable/History", chrome_q),
            "firefox": (_ff_first(ad / "Mozilla/Firefox/Profiles"), ff_q),
        }
    return {  # linux
        "chrome":   (h / ".config/google-chrome/Default/History", chrome_q),
        "chromium": (h / ".config/chromium/Default/History", chrome_q),
        "edge":     (h / ".config/microsoft-edge/Default/History", chrome_q),
        "brave":    (h / ".config/BraveSoftware/Brave-Browser/Default/History", chrome_q),
        "firefox":  (_ff_first(h / ".mozilla/firefox"), ff_q),
    }


def scan_browsers() -> list:
    """Walk each available browser DB, count unauthorised domain hits."""
    domain_hits: dict = {}
    for browser, (db, query) in _browser_paths().items():
        if not db or not Path(db).exists():
            continue
        for url in _scan_sqlite(Path(db), query):
            m = _UNAUTH_DOMAIN_RE.search(url)
            if not m:
                continue
            domain = m.group(0).lower()
            if _is_authorized(domain):
                continue
            key = (browser, domain)
            domain_hits[key] = domain_hits.get(key, 0) + 1
    return [
        {"type": "browser", "browser": b, "domain": d, "visits": c}
        for (b, d), c in domain_hits.items()
    ]
