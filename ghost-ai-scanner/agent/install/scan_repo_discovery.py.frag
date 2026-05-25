# =============================================================
# FRAGMENT: scan_repo_discovery.py.frag
# PROJECT: PatronAI
# VERSION: 2.0.0
# UPDATED: 2026-05-22
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Multi-OS repo discovery. Walks $HOME + per-OS extra roots
#          (dev folders on all fixed drives, /Volumes, /mnt, etc).
#          Applies 6-rule algorithm: candidate roots -> DFS walk ->
#          absolute-path exclude -> name exclude -> hidden-unless-git
#          rule -> repo boundary. Capped at max_depth and max_seconds.
#
# A/B TEST: v1.0.0 (HOME-only) is at the bottom as a commented block.
#           To revert, uncomment v1 and comment out v2.
#
# AUDIT LOG:
#   v1.0.0  2026-04-26  Initial. Phase 1A. HOME-only walk.
#   v2.0.0  2026-05-22  Multi-OS roots, expanded excludes, hidden-
#                       unless-git rule, security hard-blocks.
# =============================================================

import time as _time
import string as _string

# ── Configuration ─────────────────────────────────────────────
_REPO_MAX_DEPTH   = 6
_REPO_MAX_SECONDS = 90.0   # TUNABLE: production default 90s

_EXTRA_ROOTS_WIN_DRIVE_RELATIVE = (
    "Code","Codebase","Repo","Repos","Repositories","Workspace","Workspaces",
    "Projects","Project","Dev","Development","Source","Sources","src",
    "Git","GitHub","work","Work",
)
_EXTRA_ROOTS_WIN_ABSOLUTE = (r"C:\dev", r"C:\src", r"D:\dev", r"D:\src", r"E:\dev", r"E:\src")

_EXTRA_ROOTS_MAC_HOME_REL = (
    "Code","Codebase","Repos","Repositories","Workspace","Projects","Dev",
    "Development","src","work","GitHub","Documents/Code","Documents/Repos",
    "Documents/GitHub",
)
_EXTRA_ROOTS_MAC_ABSOLUTE = ("/Users/Shared/Code", "/Users/Shared/Repos")
_EXTRA_ROOTS_MAC_SKIP_VOLUMES = frozenset({
    "Macintosh HD","Recovery","Preboot","VM","Update","xarts","iSCPreboot"
})

_EXTRA_ROOTS_LINUX_HOME_REL = (
    "Code","Codebase","Repos","Repositories","Workspace","Projects","Dev",
    "Development","src","work","GitHub",
)
_EXTRA_ROOTS_LINUX_ABSOLUTE = (
    "/opt/code","/opt/workspace","/opt/repos","/srv/code","/srv/workspace",
    "/data/code","/data/repos","/data/workspace","/workspace","/code",
)
_EXTRA_ROOTS_LINUX_SKIP_MOUNTS = frozenset({"lost+found","cdrom","dvd"})

# ── Exclude lists ─────────────────────────────────────────────
_EXCLUDE_ABS_WIN = (
    r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)",
    r"C:\ProgramData", r"C:\$Recycle.Bin", r"C:\System Volume Information",
    r"C:\Recovery", r"C:\PerfLogs", r"C:\Boot", r"C:\MSOCache", r"C:\Config.Msi",
    r"C:\Drivers", r"C:\Dell", r"C:\HP", r"C:\Lenovo", r"C:\Intel",
    r"C:\NVIDIA", r"C:\AMD", r"C:\OEM",
    r"C:\Users\Default", r"C:\Users\Default User", r"C:\Users\Public", r"C:\Users\All Users",
)

_EXCLUDE_ABS_MAC = (
    "/System","/Library","/Applications","/usr","/bin","/sbin","/etc","/var",
    "/private","/dev","/cores","/Network",
)

_EXCLUDE_ABS_LINUX = (
    "/proc","/sys","/dev","/run","/boot","/usr","/var","/etc","/lib","/lib32",
    "/lib64","/sbin","/bin","/lost+found","/tmp","/var/tmp","/snap","/flatpak","/.snapshots",
)

_EXCLUDE_NAMES = frozenset({
    "node_modules","bower_components","vendor",
    ".npm",".pnpm",".yarn",".pnpm-store",".nuget",".gradle",".m2",".ivy2",".sbt",
    ".cargo",".rustup",".pyenv",".rbenv",".nvm",".deno",".bun",".composer",
    ".venv","venv","env",".virtualenv","virtualenv","__pycache__",
    ".mypy_cache",".pytest_cache",".ruff_cache",".tox",
    ".next",".nuxt",".svelte-kit",".parcel-cache",".vite",".turbo",".cache-loader",
    ".docker",".podman",".kube",".minikube",".helm",".colima",
    ".terraform",".terragrunt-cache",
    ".idea",".vs",".vscode-server",".history",".ipynb_checkpoints",
    ".copilot",".codeium",".continue",".cline",".aider",".cursor-server",
    "coverage","htmlcov",".coverage",".nyc_output",
    ".Trash",".Trash-1000","$RECYCLE.BIN","System Volume Information",
})

_EXCLUDE_HIDDEN_ALWAYS = frozenset({
    ".ssh",".gnupg",".password-store",".aws",".azure",".gcloud",
    ".kube",".docker",".pulumi",".terraform.d",".netrc",".git-credentials",
})
# Note: ~/.config/gcloud is excluded via _is_excluded_abs() (absolute path check),
# not here — this set only matches on entry.name (final path component).

# ── Helper: detect OS ─────────────────────────────────────────
_WALKER_OS = platform.system()  # "Windows", "Darwin", "Linux"


# ── Build candidate roots ─────────────────────────────────────
def _candidate_roots() -> list:
    home = Path.home()
    roots = [home]

    if _WALKER_OS == "Windows":
        # Per-drive probe for fixed drives
        for drv in _string.ascii_uppercase:
            root = Path(f"{drv}:\\")
            if not root.exists():
                continue
            try:
                import ctypes
                if ctypes.windll.kernel32.GetDriveTypeW(str(root)) != 3:
                    continue
            except Exception:
                pass
            for name in _EXTRA_ROOTS_WIN_DRIVE_RELATIVE:
                p = root / name
                if p.exists() and p.is_dir():
                    roots.append(p)
        for p in _EXTRA_ROOTS_WIN_ABSOLUTE:
            pp = Path(p)
            if pp.exists() and pp.is_dir():
                roots.append(pp)

    elif _WALKER_OS == "Darwin":
        for name in _EXTRA_ROOTS_MAC_HOME_REL:
            p = home / name
            if p.exists() and p.is_dir():
                roots.append(p)
        for p in _EXTRA_ROOTS_MAC_ABSOLUTE:
            pp = Path(p)
            if pp.exists() and pp.is_dir():
                roots.append(pp)
        vols = Path("/Volumes")
        if vols.exists():
            for v in vols.iterdir():
                if v.name in _EXTRA_ROOTS_MAC_SKIP_VOLUMES or v.name.startswith("Macintosh"):
                    continue
                if v.is_dir() and not v.is_symlink():
                    roots.append(v)

    else:  # Linux
        for name in _EXTRA_ROOTS_LINUX_HOME_REL:
            p = home / name
            if p.exists() and p.is_dir():
                roots.append(p)
        for p in _EXTRA_ROOTS_LINUX_ABSOLUTE:
            pp = Path(p)
            if pp.exists() and pp.is_dir():
                roots.append(pp)
        for base in (Path("/mnt"), Path("/media")):
            if base.exists():
                for m in base.iterdir():
                    if m.name in _EXTRA_ROOTS_LINUX_SKIP_MOUNTS:
                        continue
                    if m.is_dir() and not m.is_symlink():
                        roots.append(m)
        # WSL detection — probe all single-letter Windows drives mounted under /mnt
        if Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists():
            mnt = Path("/mnt")
            if mnt.exists():
                for entry in mnt.iterdir():
                    if len(entry.name) == 1 and entry.name.isalpha() and entry.is_dir():
                        for name in ("Code","Repos","Workspace","Projects","Dev","src"):
                            p = entry / name
                            if p.exists() and p.is_dir():
                                roots.append(p)

    # De-dupe, preserve order
    seen = set()
    ordered = []
    for r in roots:
        key = str(r).lower() if _WALKER_OS == "Windows" else str(r)
        if key not in seen:
            seen.add(key)
            ordered.append(r)
    return ordered


# ── Absolute-path exclude check ───────────────────────────────
def _is_excluded_abs(path: Path) -> bool:
    p = str(path)
    if _WALKER_OS == "Windows":
        pl = p.lower()
        appdata = os.path.join(os.environ.get("USERPROFILE", ""), "AppData").lower()
        if pl == appdata or pl.startswith(appdata + os.sep):
            return True
        for e in _EXCLUDE_ABS_WIN:
            el = e.lower()
            if pl == el or pl.startswith(el + "\\"):
                return True
    elif _WALKER_OS == "Darwin":
        home_excludes = (
            str(Path.home() / "Library"), str(Path.home() / "Applications"),
            str(Path.home() / "Music"), str(Path.home() / "Pictures"),
            str(Path.home() / "Movies"), str(Path.home() / "Public"),
            str(Path.home() / ".config" / "gcloud"),
        )
        for e in _EXCLUDE_ABS_MAC + home_excludes:
            if p == e or p.startswith(e + "/"):
                return True
    else:
        home_excludes = (
            str(Path.home() / ".local/share/Trash"),
            str(Path.home() / "snap"),
            str(Path.home() / ".config" / "gcloud"),
        )
        for e in _EXCLUDE_ABS_LINUX + home_excludes:
            if p == e or p.startswith(e + "/"):
                return True
    return False


# ── Git helpers (unchanged from v1) ───────────────────────────
def _git_remote_host(repo_root: Path) -> str:
    """Best-effort: extract github.com / gitlab.com from .git/config."""
    cfg = repo_root / ".git" / "config"
    if not cfg.exists():
        return ""
    try:
        text = cfg.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    m = re.search(r"url\s*=\s*[^@\s]*@?([A-Za-z0-9._\-]+(?:\.[A-Za-z]{2,})+)", text)
    return m.group(1).lower() if m else ""


def _git_head_sha(repo_root: Path) -> str:
    """Return short HEAD sha (first 7 chars) or '' if unreadable."""
    head = repo_root / ".git" / "HEAD"
    if not head.exists():
        return ""
    try:
        ref = head.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception:
        return ""
    if ref.startswith("ref: "):
        ref_path = repo_root / ".git" / ref[5:]
        if ref_path.exists():
            try:
                return ref_path.read_text(encoding="utf-8").strip()[:7]
            except Exception:
                return ""
    return ref[:7] if re.fullmatch(r"[0-9a-f]{40}", ref) else ""


# ── DFS Walker (v2 — multi-root, 6-rule algorithm) ────────────
def _walk_for_repos(root: Path, deadline: float) -> list:
    """DFS walk under root. Returns repo root paths."""
    found: list = []
    stack: list = [(root, 0)]
    while stack and (_REPO_MAX_SECONDS == 0 or _time.time() < deadline):
        current, depth = stack.pop()
        if depth > _REPO_MAX_DEPTH:
            continue
        if _is_excluded_abs(current):
            continue
        try:
            children = list(current.iterdir())
        except (PermissionError, OSError):
            continue

        # Rule 6: Repo boundary
        if any(c.name == ".git" and c.is_dir() for c in children):
            found.append(current)
            continue

        for child in children:
            try:
                if not child.is_dir() or child.is_symlink():
                    continue
            except OSError:
                continue
            name = child.name
            # Rule 4: Name exclude
            if name in _EXCLUDE_NAMES:
                continue
            # Rule 5: Hidden directory rule
            if name.startswith("."):
                if name in _EXCLUDE_HIDDEN_ALWAYS:
                    continue
                # Hidden-unless-git: peek for .git inside
                if (child / ".git").is_dir():
                    found.append(child)
                continue  # don't recurse into hidden dirs without .git
            stack.append((child, depth + 1))
    return found


def discover_repos() -> list:
    """Return discovered repo dicts across all candidate roots."""
    deadline = _time.time() + _REPO_MAX_SECONDS if _REPO_MAX_SECONDS > 0 else float('inf')
    repos: list = []
    for root in _candidate_roots():
        if _REPO_MAX_SECONDS > 0 and _time.time() >= deadline:
            break
        if _is_excluded_abs(root):
            continue
        repos.extend(_walk_for_repos(root, deadline))
    # De-dupe
    seen = set()
    out = []
    for r in repos:
        try:
            key = str(r.resolve())
        except Exception:
            key = str(r)
        if key not in seen:
            seen.add(key)
            out.append({
                "path_safe":   _safe_path(r),
                "name":        r.name,
                "head_sha":    _git_head_sha(r),
                "remote_host": _git_remote_host(r),
            })
    return out


# Compute once at scan time so downstream scanners reuse the result.
DISCOVERED_REPOS: list = []
try:
    DISCOVERED_REPOS = discover_repos()
except Exception:
    DISCOVERED_REPOS = []


# =============================================================
# v1.0.0 (A/B TEST — original HOME-only walker)
# Uncomment below and comment out everything above to revert.
# =============================================================
# import time as _time
#
# _REPO_EXCLUDE_NAMES = {
#     "node_modules", ".venv", "venv", "vendor", "__pycache__",
#     ".tox", ".gradle", ".m2", ".cargo", ".cache", ".docker",
#     "Library", "Applications", ".Trash", "private",
#     ".npm", ".pnpm", ".yarn", ".rustup", ".pyenv", ".rbenv",
#     "Pictures", "Music", "Movies", "Public",
# }
# _REPO_MAX_DEPTH    = 6
# _REPO_MAX_SECONDS  = 60.0
#
# def _walk_for_repos(root: Path, deadline: float) -> list:
#     found: list = []
#     stack: list = [(root, 0)]
#     while stack and _time.time() < deadline:
#         current, depth = stack.pop()
#         if depth > _REPO_MAX_DEPTH:
#             continue
#         try:
#             children = list(current.iterdir())
#         except Exception:
#             continue
#         if any(c.name == ".git" and c.is_dir() for c in children):
#             found.append(current)
#             continue
#         for child in children:
#             if not child.is_dir() or child.is_symlink():
#                 continue
#             if child.name in _REPO_EXCLUDE_NAMES or child.name.startswith("."):
#                 if child.name not in (".github", ".gitlab"):
#                     continue
#             stack.append((child, depth + 1))
#     return found
#
# def discover_repos(root=None) -> list:
#     home = Path(root) if root else Path.home()
#     deadline = _time.time() + _REPO_MAX_SECONDS
#     repos = _walk_for_repos(home, deadline)
#     out: list = []
#     for r in repos:
#         out.append({
#             "path_safe":   _safe_path(r),
#             "name":        r.name,
#             "head_sha":    _git_head_sha(r),
#             "remote_host": _git_remote_host(r),
#         })
#     return out
#
# DISCOVERED_REPOS: list = []
# try:
#     DISCOVERED_REPOS = discover_repos()
# except Exception:
#     DISCOVERED_REPOS = []
