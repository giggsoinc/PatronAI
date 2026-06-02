# =============================================================
# FILE: src/chat/docs_index.py
# VERSION: 1.1.0
# UPDATED: 2026-05-02
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: BM25-backed retrieval over PatronAI's HTML + Markdown
#          docs. Loaded once at first call (lazy), kept in memory for
#          process lifetime. Powers `get_help(query=...)` so the chat
#          can answer "how do I install the Linux agent / uninstall on
#          Mac / what is the rollup architecture" from real docs
#          rather than hallucinating.
#
#          Pure stdlib + rank_bm25 (one tiny dep, no native libs).
#          No embeddings, no vector DB, no external services.
# DEPENDS: rank_bm25 (>=0.2.2), html.parser (stdlib)
# AUDIT LOG:
#   v1.0.0  2026-05-02  Initial — built in dashboard/ui/chat/.
#   v1.1.0  2026-05-02  Moved to src/chat/. _DOC_ROOTS depth adjusted
#                       (parents[4] → parents[2]) since this file is
#                       now ghost-ai-scanner/src/chat/docs_index.py
#                       instead of ghost-ai-scanner/dashboard/ui/chat/...
# =============================================================
# raven: loc-exempt — pending refactor into sub-modules (tracked separately)

from __future__ import annotations

import logging
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

log = logging.getLogger("patronai.chat.docs_index")

# Roots where docs live. First match wins per relative path. Archive
# included so historic guides (e.g. Mac agent uninstall) stay reachable.
# This file is at <repo>/ghost-ai-scanner/src/chat/docs_index.py:
#   parents[0] = src/chat/
#   parents[1] = src/
#   parents[2] = ghost-ai-scanner/
#   parents[3] = <repo>/
_GAS_ROOT = Path(__file__).resolve().parents[2]            # ghost-ai-scanner/
_REPO_ROOT = _GAS_ROOT.parent                                # repo root
_DOC_ROOTS = [
    _GAS_ROOT  / "docs",
    _REPO_ROOT / "docs",
]
_ALLOWED_EXT = {".md", ".html", ".htm"}
_MAX_CHUNK_CHARS = 1500   # ~300-400 tokens per chunk
_MAX_CHUNKS_PER_DOC = 50  # safety cap on pathological docs
_MIN_CHUNK_CHARS = 80     # drop chunks shorter than this (header noise, etc.)
_TOPK_DEFAULT = 3
_BM25_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+\-./]*")


# ── HTML → text ──────────────────────────────────────────────────


class _HTMLStripper(HTMLParser):
    """Minimal HTML→text converter. Drops <script>, <style>; preserves
    paragraph boundaries; collapses whitespace."""

    # Tags whose CONTENT we drop. Must NOT include void elements
    # (<meta>, <link>, <br>) because handle_starttag fires for them but
    # handle_endtag does not — that would leak skip_depth and silently
    # blackhole the whole body. Void elements have no content anyway, so
    # they don't need to be in this list.
    _DROP_TAGS = {"script", "style", "noscript"}
    _BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer",
                   "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
                   "br", "pre", "blockquote", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._DROP_TAGS:
            self.skip_depth += 1
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._DROP_TAGS and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        out = "".join(self.parts)
        out = re.sub(r"[ \t]+", " ", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out.strip()


def _html_to_text(html: str) -> str:
    parser = _HTMLStripper()
    try:
        parser.feed(html)
        return parser.text()
    except Exception as exc:
        log.warning("HTML parse failed: %s", exc)
        return ""


# ── Chunking ─────────────────────────────────────────────────────


def _chunks(text: str) -> list[str]:
    """Split on blank lines, glue small adjacent paragraphs up to the
    char cap, drop micro-chunks. Preserves rough section structure so
    BM25 hits return self-contained passages."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        candidate = (buf + "\n\n" + p) if buf else p
        if len(candidate) <= _MAX_CHUNK_CHARS:
            buf = candidate
        else:
            if buf:
                chunks.append(buf)
            buf = p[:_MAX_CHUNK_CHARS]
        if len(chunks) >= _MAX_CHUNKS_PER_DOC:
            break
    if buf:
        chunks.append(buf)
    return [c for c in chunks if len(c) >= _MIN_CHUNK_CHARS]


# ── Tokenisation ─────────────────────────────────────────────────


def _tokenise(text: str) -> list[str]:
    """Lowercase, alphanumeric + a few punctuation chars (so 'github.copilot'
    stays one token). Stop-word list deliberately small — product docs are
    short, every word can carry signal."""
    return [t.lower() for t in _BM25_TOKEN_RE.findall(text)]


# ── Index ────────────────────────────────────────────────────────


class DocsIndex:
    """In-memory BM25 over HTML+MD docs. One instance per process.
    Idempotent rebuild via load(); lazy on first query.

    refresh() is the cheap "has anything changed?" entry point — used
    by the chat refresh_docs tool and the docs_refresh_loop daemon.
    """

    def __init__(self) -> None:
        self.chunks: list[dict] = []   # [{path, title, text, tokens}]
        self.bm25 = None               # rank_bm25.BM25Okapi
        self._loaded = False
        # Latest mtime among files indexed at last load(). 0 = never loaded.
        self._last_indexed_mtime: float = 0.0
        self._last_indexed_at: float = 0.0
        self._last_indexed_files: int = 0

    # ── Loading ─────────────────────────────────────────────────

    def _walk(self) -> list[Path]:
        out: list[Path] = []
        seen: set[str] = set()
        for root in _DOC_ROOTS:
            if not root.exists():
                continue
            for p in root.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in _ALLOWED_EXT:
                    continue
                # Dedup across root variants (worktree + main checkout).
                rel = str(p.relative_to(root))
                if rel in seen:
                    continue
                seen.add(rel)
                out.append(p)
        return out

    def _read_one(self, path: Path) -> Optional[str]:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            log.warning("docs_index: failed to read %s: %s", path, exc)
            return None
        if path.suffix.lower() in (".html", ".htm"):
            return _html_to_text(raw)
        return raw

    def _title_for(self, path: Path, text: str) -> str:
        """First markdown heading or HTML <title>-equivalent first line."""
        for line in text.splitlines():
            line = line.strip(" #").strip()
            if line:
                return line[:120]
        return path.name

    def load(self) -> int:
        """(Re)build the index. Returns chunk count."""
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            log.error("docs_index: rank_bm25 not installed — RAG disabled")
            self._loaded = False
            return 0

        import time as _time
        self.chunks = []
        max_mtime = 0.0
        files_seen: set[str] = set()
        for path in self._walk():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            text = self._read_one(path)
            if not text:
                continue
            title = self._title_for(path, text)
            file_chunks = _chunks(text)
            if file_chunks:
                files_seen.add(str(path.name))
                if mtime > max_mtime:
                    max_mtime = mtime
            for chunk in file_chunks:
                self.chunks.append({
                    "path": str(path.name),
                    "title": title,
                    "text": chunk,
                    "tokens": _tokenise(chunk),
                })
        if not self.chunks:
            log.warning("docs_index: no docs indexed")
            self.bm25 = None
            self._loaded = True
            self._last_indexed_mtime = max_mtime
            self._last_indexed_at = _time.time()
            self._last_indexed_files = 0
            return 0
        self.bm25 = BM25Okapi([c["tokens"] for c in self.chunks])
        self._loaded = True
        self._last_indexed_mtime = max_mtime
        self._last_indexed_at = _time.time()
        self._last_indexed_files = len(files_seen)
        log.info("docs_index: %d chunks across %d files",
                 len(self.chunks), self._last_indexed_files)
        return len(self.chunks)

    # ── Refresh ─────────────────────────────────────────────────

    def _current_max_mtime(self) -> float:
        """Cheap scan — stat() every doc, return max mtime. No reads."""
        m = 0.0
        for p in self._walk():
            try:
                t = p.stat().st_mtime
                if t > m:
                    m = t
            except OSError:
                continue
        return m

    def refresh(self, force: bool = False) -> dict:
        """Idempotent: rebuild only if any doc's mtime is newer than the
        last indexed mtime. Returns a status dict with counts so callers
        (chat tool, daemon) can report what happened.

        Args:
            force: rebuild even if nothing has changed.
        """
        if not self._loaded and not force:
            self.load()
            return {
                "action": "initial_load",
                "chunks": len(self.chunks),
                "files":  self._last_indexed_files,
                "indexed_at": self._last_indexed_at,
            }
        cur_mtime = self._current_max_mtime()
        if not force and cur_mtime <= self._last_indexed_mtime:
            return {
                "action": "no_change",
                "chunks": len(self.chunks),
                "files":  self._last_indexed_files,
                "indexed_at": self._last_indexed_at,
                "last_doc_mtime": self._last_indexed_mtime,
            }
        chunks_before = len(self.chunks)
        files_before  = self._last_indexed_files
        self.load()
        return {
            "action": "force_reload" if force else "reindexed",
            "chunks_before": chunks_before,
            "chunks_after":  len(self.chunks),
            "files_before":  files_before,
            "files_after":   self._last_indexed_files,
            "indexed_at":    self._last_indexed_at,
            "last_doc_mtime": self._last_indexed_mtime,
        }

    def status(self) -> dict:
        """Read-only snapshot of index state, for diagnostics."""
        return {
            "loaded":       self._loaded,
            "chunks":       len(self.chunks),
            "files":        self._last_indexed_files,
            "indexed_at":   self._last_indexed_at,
            "last_doc_mtime": self._last_indexed_mtime,
        }

    # ── Query ───────────────────────────────────────────────────

    def query(self, q: str, top_k: int = _TOPK_DEFAULT) -> list[dict]:
        """Return top_k chunks for the query, sorted by BM25 score."""
        if not self._loaded:
            self.load()
        if not self.bm25 or not q.strip():
            return []
        tokens = _tokenise(q)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda kv: kv[1], reverse=True)
        out: list[dict] = []
        for idx, score in ranked[:max(1, int(top_k))]:
            if score <= 0:
                continue
            c = self.chunks[idx]
            out.append({
                "score": round(float(score), 3),
                "source": c["path"],
                "title": c["title"],
                "text": c["text"],
            })
        return out


# ── Module-level singleton ──────────────────────────────────────


_INDEX: Optional[DocsIndex] = None


def get_index() -> DocsIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = DocsIndex()
        _INDEX.load()
    return _INDEX


def reset_index() -> None:
    """Test/CLI hook — drop the singleton so next call rebuilds."""
    global _INDEX
    _INDEX = None
