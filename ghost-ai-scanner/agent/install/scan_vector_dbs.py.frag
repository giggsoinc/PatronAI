# =============================================================
# FRAGMENT: scan_vector_dbs.py.frag
# PROJECT: PatronAI — Phase 1A
# VERSION: 1.0.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Detect local vector / embedding stores. Two passes:
#            1. Common default home-dir caches (Chroma, LanceDB, etc)
#            2. Inside DISCOVERED_REPOS — vector store files checked-in
#               next to agent code
#          Emits `vector_db` findings with file kind + safe path + size.
#          Privacy: redactor pass + drop on unredacted-secret. Cap on
#          number of findings per scan to keep payload size sane.
# AUDIT LOG:
#   v1.0.0  2026-04-26  Initial. Phase 1A.
# =============================================================

# Map of "kind label" → set of filename markers and dir markers.
_VDB_FILE_MARKERS = {
    "chroma":  {"chroma.sqlite3", "chroma-collections.parquet"},
    "faiss":   set(),                                  # extension-only; see below
    "lancedb": set(),
    "qdrant":  {"meta.json"},                          # qdrant storage signature
    "milvus":  {"meta.kv"},
}
_VDB_EXTENSION_MARKERS = {
    ".faiss":  "faiss",
    ".index":  "faiss",
    ".lance":  "lancedb",
    ".duckdb": "duckdb_vector",
}
_VDB_DIR_MARKERS = {
    ".chroma":  "chroma",
    "chroma":   "chroma",
    "lancedb":  "lancedb",
    "qdrant":   "qdrant",
    "weaviate": "weaviate",
}

_VDB_HOME_HINTS = (".chroma", ".cache/lancedb", ".cache/chroma",
                   ".cache/huggingface/hub")
_VDB_MAX_FINDINGS = 200


def _classify_path(p: Path) -> str:
    """Return a kind label for a single path, or ''."""
    name = p.name.lower()
    for kind, names in _VDB_FILE_MARKERS.items():
        if name in names:
            return kind
    suffix = p.suffix.lower()
    if suffix in _VDB_EXTENSION_MARKERS:
        return _VDB_EXTENSION_MARKERS[suffix]
    if p.is_dir() and name in _VDB_DIR_MARKERS:
        return _VDB_DIR_MARKERS[name]
    return ""


def _emit_finding(p: Path, kind: str, source: str) -> dict:
    """Build a redacted vector_db finding dict for `p`."""
    try:
        size = p.stat().st_size if p.is_file() else 0
        mtime = int(p.stat().st_mtime)
    except Exception:  # intentional: must not crash
        size, mtime = 0, 0
    return _safe_finding({
        "type":          "vector_db",
        "kind":          kind,
        "source":        source,                      # "home_cache" | "repo"
        "path_safe":     _safe_path(p),
        "name":          p.name[:120],
        "bytes":         size,
        "mtime_epoch":   mtime,
    })


def _scan_home_caches() -> list:
    """First pass — known default cache dirs under $HOME."""
    h = Path.home()
    out: list = []
    for hint in _VDB_HOME_HINTS:
        root = h / hint
        if not root.exists():
            continue
        try:
            for p in root.rglob("*"):
                kind = _classify_path(p)
                if not kind:
                    continue
                f = _emit_finding(p, kind, "home_cache")
                if not _has_unredacted_secret(f):
                    out.append(f)
                if len(out) >= _VDB_MAX_FINDINGS:
                    return out
        except Exception:  # intentional: must not crash
            continue
    return out


def _scan_repos_for_vector_files() -> list:
    """Second pass — walk each discovered repo for vector files
    that have been checked in or generated alongside agent code."""
    out: list = []
    for repo in DISCOVERED_REPOS:
        repo_path = Path(str(repo.get("path_safe", ""))
                         .replace("~", str(Path.home()), 1))
        if not repo_path.exists() or not repo_path.is_dir():
            continue
        try:
            for p in repo_path.rglob("*"):
                if any(seg in {"node_modules", ".venv", "venv", "__pycache__"}
                       for seg in p.parts):
                    continue
                kind = _classify_path(p)
                if not kind:
                    continue
                f = _emit_finding(p, kind, "repo")
                f["repo_name"] = repo.get("name", "")
                if not _has_unredacted_secret(f):
                    out.append(f)
                if len(out) >= _VDB_MAX_FINDINGS:
                    return out
        except Exception:  # intentional: must not crash
            continue
    return out


def scan_vector_dbs() -> list:
    """Top-level scanner. Two passes; combined output capped for safety."""
    findings: list = []
    findings.extend(_scan_home_caches())
    if len(findings) < _VDB_MAX_FINDINGS:
        findings.extend(_scan_repos_for_vector_files())
    return findings[:_VDB_MAX_FINDINGS]
