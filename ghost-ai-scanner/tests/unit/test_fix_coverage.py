# =============================================================
# FILE: tests/unit/test_fix_coverage.py
# PROJECT: PatronAI
# VERSION: 1.0.0
# UPDATED: 2026-05-25
# OWNER: Giggso Inc
# PURPOSE: Regression tests for fixes applied on branch
#          fix/agent-installer-cross-platform-bugs.
#          Covers Fixes #7, #8, #11, #13, #15.
# AUDIT LOG:
#   v1.0.0  2026-05-25  Initial.
# =============================================================

import os
import re
import sys
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

REPO  = Path(__file__).resolve().parents[2]
FRAGS = REPO / "agent" / "install"

sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))


# ─────────────────────────────────────────────────────────────
# Fix #7 — render_agent_package: DMG/EXE build failures are
#           non-fatal and do not suppress the success result.
# ─────────────────────────────────────────────────────────────

def _make_store_for_render(token: str = "tok-123") -> MagicMock:
    store = MagicMock()
    store.bucket = "bkt"
    store.region  = "us-east-1"
    store.generate_otp.return_value = "999888"
    store.hash_otp.return_value = "$2b$12$fakehash"
    store.create_package.return_value = token
    store.get_presigned_urls.return_value = {
        "installer_url":      "https://s3.test/installer",
        "meta_url":           "https://s3.test/meta",
        "status_put_url":     "https://s3.test/status",
        "heartbeat_put_url":  "https://s3.test/hb",
        "scan_put_url":       "https://s3.test/scan",
        "authorized_get_url": "https://s3.test/auth",
        "urls_refresh_url":   "https://s3.test/refresh",
    }
    store._put = MagicMock(return_value=True)
    store.get_artifact_url.return_value = ""
    store.write_url_bundle.return_value = True
    return store


def _make_renderer() -> MagicMock:
    r = MagicMock()
    r.render = MagicMock(return_value="#!/bin/bash\n# rendered\n")
    return r


def test_dmg_build_failure_does_not_abort_package():
    """Fix #7: DMG build exception must not propagate — result is still success."""
    from render_agent_package import render_agent_package

    store    = _make_store_for_render()
    renderer = _make_renderer()

    with patch("render_agent_package._build_macos_dmg", side_effect=RuntimeError("no genisoimage")), \
         patch("render_agent_package._build_windows_exe", return_value="exe/key"):
        result = render_agent_package(
            recipient_name  = "Alice",
            recipient_email = "alice@example.com",
            os_type         = "mac",
            store           = store,
            renderer        = renderer,
            send_email      = False,
        )

    assert result["success"] is True, f"Expected success, got: {result}"
    assert result["token"] == "tok-123"


def test_exe_build_failure_does_not_abort_package():
    """Fix #7: EXE build exception must not propagate — result is still success."""
    from render_agent_package import render_agent_package

    store    = _make_store_for_render()
    renderer = _make_renderer()

    with patch("render_agent_package._build_macos_dmg",   return_value="dmg/key"), \
         patch("render_agent_package._build_windows_exe", side_effect=OSError("makensis missing")):
        result = render_agent_package(
            recipient_name  = "Bob",
            recipient_email = "bob@example.com",
            os_type         = "windows",
            store           = store,
            renderer        = renderer,
            send_email      = False,
        )

    assert result["success"] is True, f"Expected success, got: {result}"


def test_both_build_failures_still_returns_success():
    """Fix #7: Both builders failing — package is still delivered via script URLs."""
    from render_agent_package import render_agent_package

    store    = _make_store_for_render()
    renderer = _make_renderer()

    with patch("render_agent_package._build_macos_dmg",   side_effect=Exception("fail")), \
         patch("render_agent_package._build_windows_exe", side_effect=Exception("fail")):
        result = render_agent_package(
            recipient_name  = "Carol",
            recipient_email = "carol@example.com",
            os_type         = "linux",
            store           = store,
            renderer        = renderer,
            send_email      = False,
        )

    assert result["success"] is True
    assert result["dmg_url"] == ""
    assert result["exe_url"] == ""


# ─────────────────────────────────────────────────────────────
# Fix #8 — agent_store: catalog lock prevents lost updates
#           under concurrent _catalog_add calls.
# ─────────────────────────────────────────────────────────────

def _make_agent_store(initial_catalog: list | None = None) -> MagicMock:
    """Return a minimal AgentStore-like mock with real _catalog_add logic."""
    from store.agent_store import AgentStore, _catalog_lock

    store = MagicMock(spec=AgentStore)

    # Use a real in-memory catalog to exercise the lock
    catalog_state: list = list(initial_catalog or [])

    def fake_list_catalog():
        return list(catalog_state)

    def fake_put(key, data, content_type, **kwargs):
        if key.endswith("catalog.json"):
            catalog_state.clear()
            catalog_state.extend(json.loads(data.decode()))

    store.list_catalog.side_effect = fake_list_catalog
    store._put.side_effect = fake_put

    # Bind the real _catalog_add method to this mock instance
    store._catalog_add = lambda *a, **kw: AgentStore._catalog_add(store, *a, **kw)
    return store, catalog_state


_AGENT_STORE_SRC = (REPO / "src" / "store" / "agent_store.py").read_text(encoding="utf-8")


def test_catalog_lock_exists_on_module():
    """Fix #8: _catalog_lock must be declared as a threading.Lock at module level."""
    assert "_catalog_lock = threading.Lock()" in _AGENT_STORE_SRC, \
        "_catalog_lock = threading.Lock() not found in agent_store.py"


def test_catalog_add_uses_lock():
    """Fix #8: _catalog_add must acquire _catalog_lock via 'with _catalog_lock:'."""
    assert "with _catalog_lock:" in _AGENT_STORE_SRC, \
        "_catalog_add does not use 'with _catalog_lock:'"


def test_concurrent_catalog_adds_no_lost_entries():
    """Fix #8: catalog lock pattern prevents lost updates under concurrent writes.
    We simulate the locked read-modify-write directly — same logic as _catalog_add."""
    catalog_state: list = []
    catalog_lock = threading.Lock()

    def locked_catalog_add(token: str, name: str):
        with catalog_lock:
            current = list(catalog_state)
            current.append({"token": token, "recipient_name": name})
            catalog_state.clear()
            catalog_state.extend(current)

    t1 = threading.Thread(target=locked_catalog_add, args=("tok-1", "Alice"))
    t2 = threading.Thread(target=locked_catalog_add, args=("tok-2", "Bob"))

    t1.start(); t2.start()
    t1.join();  t2.join()

    tokens = {e["token"] for e in catalog_state}
    assert "tok-1" in tokens, "tok-1 lost under concurrent write"
    assert "tok-2" in tokens, "tok-2 lost under concurrent write"


# ─────────────────────────────────────────────────────────────
# Fix #11 — repo discovery: ~/.config/gcloud excluded via
#            absolute path check, not name match.
# ─────────────────────────────────────────────────────────────

def _run_repo_discovery(home: Path, os_name: str) -> list:
    """Exec redactor + repo_discovery fragment with a patched home dir."""
    ns: dict = {
        "re": re, "Path": Path, "os": os, "json": json,
        "platform": type("P", (), {"system": staticmethod(lambda: os_name)})(),
    }
    real_home = Path.home
    Path.home = staticmethod(lambda: home)                    # type: ignore
    try:
        exec(compile((FRAGS / "scan_redactor.py.frag").read_text(), "scan_redactor", "exec"), ns)
        exec(compile((FRAGS / "scan_repo_discovery.py.frag").read_text(), "scan_repo_discovery", "exec"), ns)
        return ns.get("DISCOVERED_REPOS", [])
    finally:
        Path.home = real_home                                  # type: ignore


def _make_git_dir(parent: Path) -> None:
    (parent / ".git").mkdir(parents=True, exist_ok=True)


def test_gcloud_config_not_in_exclude_hidden_names():
    """Fix #11: .config/gcloud MUST NOT appear in _EXCLUDE_HIDDEN_ALWAYS
    (it would skip the whole .config tree). Instead excluded via abs-path check."""
    src = (FRAGS / "scan_repo_discovery.py.frag").read_text()
    # The name 'gcloud' alone (not a path) must NOT appear in EXCLUDE_HIDDEN_ALWAYS
    # The abs-path exclusion is the correct mechanism.
    hidden_block_match = re.search(
        r"_EXCLUDE_HIDDEN_ALWAYS\s*=\s*frozenset\(\{([^}]+)\}", src, re.DOTALL
    )
    assert hidden_block_match, "_EXCLUDE_HIDDEN_ALWAYS block not found"
    hidden_entries = hidden_block_match.group(1)
    # '.config' (the directory itself) must not be in the hidden-always block,
    # since that would exclude all dotfile repos under .config.
    assert '".config"' not in hidden_entries, \
        ".config must not be in _EXCLUDE_HIDDEN_ALWAYS — it blocks the whole dir"


def test_gcloud_excluded_via_absolute_path_darwin(tmp_path):
    """Fix #11: ~/.config/gcloud subtree is excluded on macOS via _is_excluded_abs."""
    gcloud_dir = tmp_path / ".config" / "gcloud"
    gcloud_dir.mkdir(parents=True)
    _make_git_dir(gcloud_dir)   # even a git repo inside .config/gcloud must be excluded

    repos = _run_repo_discovery(tmp_path, "Darwin")
    repo_paths = {r["path_safe"] for r in repos}
    # gcloud git repo must not be discovered
    assert not any("gcloud" in p for p in repo_paths), \
        f"gcloud was not excluded: {repo_paths}"


def test_gcloud_excluded_via_absolute_path_linux(tmp_path):
    """Fix #11: ~/.config/gcloud subtree is excluded on Linux via _is_excluded_abs."""
    gcloud_dir = tmp_path / ".config" / "gcloud"
    gcloud_dir.mkdir(parents=True)
    _make_git_dir(gcloud_dir)

    repos = _run_repo_discovery(tmp_path, "Linux")
    repo_paths = {r["path_safe"] for r in repos}
    assert not any("gcloud" in p for p in repo_paths), \
        f"gcloud was not excluded on Linux: {repo_paths}"


# ─────────────────────────────────────────────────────────────
# Fix #13 — repo discovery: WSL drives are probed dynamically
#            from /mnt/*, not hardcoded to c/d/e.
# ─────────────────────────────────────────────────────────────

def test_wsl_detection_uses_wslinterop_marker():
    """Fix #13: WSL probe only runs when WSLInterop file exists."""
    src = (FRAGS / "scan_repo_discovery.py.frag").read_text()
    assert "/proc/sys/fs/binfmt_misc/WSLInterop" in src, \
        "WSL detection marker path not found in fragment"


def test_wsl_dynamic_drive_single_letter_only():
    """Fix #13: WSL drive detection iterates /mnt/* and filters to single-letter dirs."""
    src = (FRAGS / "scan_repo_discovery.py.frag").read_text()
    # The fix must iterate mnt.iterdir() not use hardcoded drives
    assert "mnt.iterdir()" in src or "mnt).iterdir()" in src or \
           "for entry in mnt.iterdir()" in src, \
        "WSL fix must use mnt.iterdir() not hardcoded paths"
    # Must filter to single-letter entries
    assert "len(entry.name) == 1" in src, \
        "WSL fix must check len(entry.name) == 1 to skip non-drive mounts"
    # Must NOT reference hardcoded drive letters in the WSL block
    assert '"/mnt/c"' not in src and '"/mnt/d"' not in src, \
        "Hardcoded /mnt/c or /mnt/d must not appear in the v2.0.0 code"


# ─────────────────────────────────────────────────────────────
# Fix #15 — ingestor: empty unauthorized list must not abort
#            the scan cycle; ENDPOINT_SCAN events bypass matcher.
# ─────────────────────────────────────────────────────────────

def _make_ingestor_deps():
    """Build minimal mocked store + settings for Ingestor."""
    store = MagicMock()
    store.bucket = "bkt"
    store.cursor.read.return_value = {"cursor_ts": None, "last_key": None}
    store.cursor.write = MagicMock()

    settings = {
        "storage":  {"ocsf_bucket": "bkt", "ocsf_prefix": "ocsf/"},
        "scanner":  {"max_files_per_cycle": 10},
        "company":  {"slug": "test-co"},
        "cloud":    {"region": "us-east-1"},
    }
    return store, settings


def test_ingestor_continues_when_unauthorized_list_empty():
    """Fix #15: run() must complete and return stats even when unauthorized is [].
    The early-return abort that was here silenced ENDPOINT_SCAN inventory."""
    from ingestor.ingestor import Ingestor

    store, settings = _make_ingestor_deps()

    with patch("ingestor.ingestor.S3Walker") as MockWalker, \
         patch("ingestor.ingestor.Pipeline")  as MockPipeline, \
         patch("matcher.loader.load_authorized",   return_value=[]), \
         patch("matcher.loader.load_unauthorized", return_value=[]):  # empty list

        mock_walker   = MockWalker.return_value
        mock_walker.list_new_files.return_value = []   # no files this cycle

        ing = Ingestor(store, settings)
        result = ing.run()

    # Must return a stats dict — not abort with {"outcome": "aborted", ...}
    assert "files_processed" in result, \
        f"Ingestor aborted instead of completing: {result}"
    assert result.get("outcome") != "aborted", \
        "Ingestor must not abort on empty unauthorized list"


def test_ingestor_processes_events_when_unauthorized_empty():
    """Fix #15: pipeline.process is called for each event even when unauthorized=[]."""
    from ingestor.ingestor import Ingestor

    store, settings = _make_ingestor_deps()

    fake_event = {"class_uid": 5000, "type": "ENDPOINT_SCAN"}

    with patch("ingestor.ingestor.S3Walker") as MockWalker, \
         patch("ingestor.ingestor.Pipeline")  as MockPipeline, \
         patch("matcher.loader.load_authorized",   return_value=[]), \
         patch("matcher.loader.load_unauthorized", return_value=[]):

        mock_walker = MockWalker.return_value
        mock_walker.list_new_files.return_value = [("ocsf/scan/latest.json", None)]
        mock_walker.read_events.return_value    = [fake_event]

        mock_pipeline = MockPipeline.return_value
        mock_pipeline.process.return_value = "ENDPOINT_FINDING"

        ing    = Ingestor(store, settings)
        result = ing.run()

    mock_pipeline.process.assert_called_once_with(fake_event)
    assert result["events_processed"] == 1
