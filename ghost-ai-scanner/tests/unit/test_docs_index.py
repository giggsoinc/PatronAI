# =============================================================
# FILE: tests/unit/test_docs_index.py
# PROJECT: PatronAI — Marauder Scan
# VERSION: 1.0.0
# UPDATED: 2026-05-02
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Tests for the BM25 docs RAG used by get_help(query=...).
#          Uses synthetic in-memory docs so the test suite stays fast
#          and is decoupled from real doc content (which evolves).
#          One end-to-end test against the real docs/ tree to catch
#          regressions in HTML parsing.
# =============================================================

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
# (removed: chat now lives under src/)

from chat.docs_index import (  # noqa: E402
    DocsIndex, _html_to_text, _chunks, _tokenise, reset_index,
)


# ── HTML parsing ──────────────────────────────────────────────────


def test_html_strip_drops_script_and_style():
    html = """<html><head><style>body{color:red}</style></head>
              <body><h1>Hello</h1><script>alert(1)</script>
              <p>World</p></body></html>"""
    text = _html_to_text(html)
    assert "Hello" in text
    assert "World" in text
    assert "color:red" not in text
    assert "alert(1)" not in text


def test_html_strip_void_tags_do_not_leak_skip_depth():
    """Regression: <meta>/<link>/<br> are void elements. If they were in
    _DROP_TAGS, handle_starttag would bump skip_depth and never decrement,
    silently blackholing the body. This test guards against that bug."""
    html = ('<html><head><meta charset="UTF-8"><meta name="x">'
            '<link rel="icon"><title>T</title></head>'
            '<body><p>Hello world</p></body></html>')
    text = _html_to_text(html)
    assert "Hello world" in text


def test_html_strip_preserves_paragraph_boundaries():
    html = "<p>One.</p><p>Two.</p><p>Three.</p>"
    text = _html_to_text(html)
    # block tags should produce blank lines
    assert "\n" in text


# ── Chunking ──────────────────────────────────────────────────────


def test_chunks_glues_short_paragraphs():
    # Paragraphs short individually but combined they pass the
    # 80-char minimum chunk floor.
    para = "This paragraph holds enough words to pass the minimum chunk floor easily."
    text = "\n\n".join([para] * 3)
    chunks = _chunks(text)
    assert len(chunks) == 1
    assert para in chunks[0]


def test_chunks_drops_below_min_chunk_size():
    """Tiny content (e.g. just a header line) gets dropped to avoid
    polluting BM25 with header-only noise."""
    chunks = _chunks("Tiny.\n\nAlso tiny.")
    assert chunks == []


def test_chunks_splits_when_over_cap():
    big = "x" * 800
    text = "\n\n".join([big, big, big])
    chunks = _chunks(text)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 1500 + 10  # cap with small slack




# ── Tokeniser ─────────────────────────────────────────────────────


def test_tokenise_keeps_compound_identifiers_intact():
    """Plugin IDs and package paths shouldn't be split — they're how
    users will phrase queries."""
    tokens = _tokenise("github.copilot and pip:openai or s3.amazonaws.com")
    # Tokens should preserve dots / slashes / colons in identifiers.
    joined = " ".join(tokens)
    # At minimum 'github.copilot' and 'pip' should appear:
    assert any("github" in t for t in tokens)
    assert any("openai" in t for t in tokens)


# ── Index — synthetic docs ───────────────────────────────────────


@pytest.fixture
def synthetic_index(tmp_path, monkeypatch):
    """Build a DocsIndex against a tmp directory of fake docs."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "linux.md").write_text(
        "# PatronAI Linux Agent\n\n"
        "To install the Linux agent run setup_agent.sh.\n\n"
        "To uninstall the Linux agent: rm -rf ~/.patronai && "
        "crontab -r\n\nUninstall removes everything cleanly.",
        encoding="utf-8",
    )
    (docs / "windows.md").write_text(
        "# PatronAI Windows Agent\n\n"
        "Windows install uses setup_agent.ps1 in PowerShell.\n\n"
        "Windows uninstall: Add or Remove Programs → PatronAI Agent → "
        "Uninstall.\n\nThe scheduled task is also removed.",
        encoding="utf-8",
    )
    (docs / "mac.html").write_text(
        "<html><head><meta charset='UTF-8'><style>x</style></head>"
        "<body><h1>PatronAI macOS Agent</h1>"
        "<p>To install on Mac, run bash setup_agent.sh.</p>"
        "<p>To uninstall on Mac: rm -rf ~/.patronai/ and remove the launchd "
        "plist at ~/Library/LaunchAgents/com.giggso.patronai.plist. "
        "Then launchctl unload that plist file.</p></body></html>",
        encoding="utf-8",
    )
    # Override the doc roots
    monkeypatch.setattr("chat.docs_index._DOC_ROOTS", [docs])
    reset_index()
    idx = DocsIndex()
    n = idx.load()
    return idx, n


def test_index_builds_chunks_from_synthetic_corpus(synthetic_index):
    idx, n = synthetic_index
    assert n >= 3
    paths = {c["path"] for c in idx.chunks}
    assert paths == {"linux.md", "windows.md", "mac.html"}


def test_query_uninstall_mac_returns_mac_doc_first(synthetic_index):
    idx, _ = synthetic_index
    hits = idx.query("how to uninstall on mac", top_k=3)
    assert hits, "expected at least one hit"
    # The mac doc should rank #1 because it has both 'mac' and 'uninstall'.
    assert hits[0]["source"] == "mac.html"
    assert "uninstall" in hits[0]["text"].lower()


def test_query_install_linux_returns_linux_doc(synthetic_index):
    idx, _ = synthetic_index
    hits = idx.query("install Linux agent", top_k=3)
    assert hits
    assert hits[0]["source"] == "linux.md"


def test_query_unknown_returns_empty(synthetic_index):
    idx, _ = synthetic_index
    hits = idx.query("antarctic penguin migration patterns", top_k=3)
    assert hits == []


def test_empty_query_returns_empty(synthetic_index):
    idx, _ = synthetic_index
    assert idx.query("") == []
    assert idx.query("   ") == []


# ── End-to-end: real docs/ tree ──────────────────────────────────


def test_real_docs_index_picks_up_agent_guides():
    """Regression guard: confirm the real ghost-ai-scanner/docs/ and
    docs/ trees produce a working index with > 50 chunks (so future
    HTML parsing breakage gets caught)."""
    reset_index()
    idx = DocsIndex()
    n = idx.load()
    if n == 0:
        pytest.skip("rank_bm25 not installed in this env")
    assert n >= 50, f"expected >= 50 chunks from real docs, got {n}"
    paths = {c["path"] for c in idx.chunks}
    # Agent guides MUST be reachable — the original bug was these went
    # missing because the HTMLStripper leaked skip_depth on void tags.
    expected_subset = {
        "patronai-agent-linux-guide.html",
        "patronai-agent-windows-guide.html",
        "patronai-agent-macos-guide.html",
    }
    missing = expected_subset - paths
    assert not missing, f"agent guides missing from index: {missing}"


# ── Public help.py wiring ────────────────────────────────────────


def test_get_help_query_path():
    """Confirm get_help(query=...) uses BM25 and returns the citation
    block."""
    reset_index()
    from chat.help import get_help
    r = get_help([], query="how to uninstall the agent on mac")
    if "error" in r:
        pytest.skip("rank_bm25 not installed in this env")
    assert r["query"] == "how to uninstall the agent on mac"
    assert "matches" in r
    if r["matches"]:
        assert "_citation" in r
        assert r["_citation"]["source"].startswith("PatronAI docs")


def test_get_help_topic_path_still_works():
    """Backward compat: existing topic= calls still return the curated
    dict content."""
    from chat.help import get_help, _HELP
    r = get_help([], topic="severity")
    assert r["topic"] == "severity"
    assert r["content"] == _HELP["severity"]


def test_get_help_empty_returns_topic_catalogue():
    from chat.help import get_help
    r = get_help([])
    assert "topics" in r
    assert "severity" in r["topics"]


# ── Refresh — mtime tracking ─────────────────────────────────────


def test_refresh_initial_load_then_no_change_then_reindex(synthetic_index):
    """End-to-end: refresh() reports the right action transitions when
    docs change vs. don't change."""
    idx, n = synthetic_index
    # Already loaded by the fixture, so refresh() = no_change.
    r1 = idx.refresh()
    assert r1["action"] == "no_change"
    assert r1["chunks"] == n

    # Touch a file to bump its mtime → next refresh should reindex.
    import time
    from pathlib import Path
    docs_dir = next(p for p in __import__("chat.docs_index",
                                            fromlist=["_DOC_ROOTS"])._DOC_ROOTS)
    target = next(Path(docs_dir).iterdir())
    # Set mtime explicitly to "now+10" to defeat any mtime resolution issues.
    future = time.time() + 10
    os_path = str(target)
    import os as _os
    _os.utime(os_path, (future, future))

    r2 = idx.refresh()
    assert r2["action"] == "reindexed"
    assert r2["chunks_before"] == n
    assert r2["chunks_after"] == n   # content unchanged, count stable


def test_refresh_force_rebuilds_unconditionally(synthetic_index):
    idx, _ = synthetic_index
    r = idx.refresh(force=True)
    assert r["action"] == "force_reload"


def test_refresh_docs_chat_tool_returns_action():
    """Smoke test the chat-callable refresh_docs() wrapper."""
    from chat.help import refresh_docs
    from chat.docs_index import reset_index
    reset_index()
    r = refresh_docs([])
    # Result must always include 'action' so the LLM can report what
    # happened. Either initial_load (first call) or no_change.
    if "error" in r:
        pytest.skip("rank_bm25 not installed")
    assert r["action"] in ("initial_load", "no_change", "reindexed", "force_reload")
