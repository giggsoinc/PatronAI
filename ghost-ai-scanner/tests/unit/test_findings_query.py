# =============================================================
# FILE: tests/unit/test_findings_query.py
# PROJECT: PatronAI — Phase 1A
# VERSION: 1.0.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Lock the read helpers + MCP hash registry contract:
#          - last_known_mcp_hash returns '' on miss / error; otherwise body
#          - record_mcp_hash issues a put_object with the right Key + Body
#          - read_by_email scans last N daily partitions and filters
#          - read_by_repo same pattern, different field
#          Mock-based; no real S3.
# AUDIT LOG:
#   v1.0.0  2026-04-26  Initial. Phase 1A.
# =============================================================

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))


# Polars binary readiness — affects only the read_by_email / read_by_repo
# tests (MCP-hash helpers do not touch polars). Some dev macs have polars-
# the-package installed but polars-the-binary missing; we detect that here
# and let those specific tests skip via the `requires_polars` marker.
def _polars_binary_ok() -> bool:
    try:
        import polars as _pl
        _pl.DataFrame([{"x": 1}])
        return True
    except Exception:  # intentional: must not crash
        return False


requires_polars = pytest.mark.skipif(
    not _polars_binary_ok(),
    reason="polars binary unusable on this host (dev-only skip)",
)


# ── MCP hash registry ──────────────────────────────────────────

def test_last_known_mcp_hash_returns_body_on_hit():
    from store.findings_query import last_known_mcp_hash

    s3 = MagicMock()
    s3.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=b"abc123def456\n"))
    }
    out = last_known_mcp_hash(s3, "bk", "alice-mbp", "claude_desktop")
    assert out == "abc123def456"
    s3.get_object.assert_called_once()
    key = s3.get_object.call_args.kwargs.get("Key") \
          or s3.get_object.call_args[1]["Key"]
    assert key.startswith("mcp_hashes/")
    assert "alice-mbp" in key
    assert "claude_desktop" in key


def test_last_known_mcp_hash_returns_empty_on_miss():
    from store.findings_query import last_known_mcp_hash

    s3 = MagicMock()
    s3.get_object.side_effect = Exception("NoSuchKey")
    assert last_known_mcp_hash(s3, "bk", "x", "y") == ""


def test_record_mcp_hash_writes_key_and_body():
    from store.findings_query import record_mcp_hash

    s3 = MagicMock()
    ok = record_mcp_hash(s3, "bk", "alice-mbp", "claude_desktop", "abc123")
    assert ok is True
    args = s3.put_object.call_args.kwargs
    assert args["Bucket"] == "bk"
    assert args["Body"]   == b"abc123"
    assert "alice-mbp" in args["Key"]
    assert "claude_desktop" in args["Key"]


def test_record_mcp_hash_returns_false_on_error():
    from store.findings_query import record_mcp_hash

    s3 = MagicMock()
    s3.put_object.side_effect = Exception("403")
    assert record_mcp_hash(s3, "bk", "x", "y", "abc") is False


def test_record_mcp_hash_sanitises_key():
    """Slashes / spaces in device or host names must not become path parts."""
    from store.findings_query import record_mcp_hash

    s3 = MagicMock()
    record_mcp_hash(s3, "bk", "alice/mbp lap", "cur sor/host", "abc")
    key = s3.put_object.call_args.kwargs["Key"]
    # No double-slashes from the inputs (the prefix has a single `/` before the
    # device segment which is expected; the device + host segments themselves
    # must not introduce any path-traversal slashes).
    assert "alice/mbp" not in key
    assert "cur sor/host" not in key
    assert "alice_mbp_lap" in key
    assert "cur_sor_host" in key


# ── read_by_email / read_by_repo ────────────────────────────────

def _make_store_with_rows(rows_per_day):
    """Return a fake findings_store whose `read(target_date=...)` returns a
    DataFrame from rows_per_day[target_date], else empty.

    Implemented as a real lightweight class because MagicMock's auto-attribute
    machinery interferes with directly assigning a function to .read."""
    import polars as pl

    class _FakeStore:
        def read(self, target_date, severity=None, limit=10_000):
            rows = rows_per_day.get(target_date, [])
            return pl.DataFrame(rows) if rows else pl.DataFrame()

    return _FakeStore()


@requires_polars
def test_read_by_email_filters_to_just_that_user():
    from store.findings_query import read_by_email
    from datetime import date

    today = date.today().isoformat()
    rows = {
        today: [
            {"email": "alice@x.com", "category": "mcp_server", "provider": "p1"},
            {"email": "bob@x.com",   "category": "mcp_server", "provider": "p2"},
            {"email": "alice@x.com", "category": "package",    "provider": "p3"},
        ],
    }
    store = _make_store_with_rows(rows)
    out = read_by_email(store, "alice@x.com", days=2)
    assert len(out) == 2
    assert all(r["email"] == "alice@x.com" for r in out)


@requires_polars
def test_read_by_email_returns_empty_for_missing_user():
    from store.findings_query import read_by_email
    from datetime import date

    rows = {date.today().isoformat(): [{"email": "alice@x.com"}]}
    store = _make_store_with_rows(rows)
    assert read_by_email(store, "ghost@x.com", days=2) == []


def test_read_by_email_empty_when_value_blank():
    from store.findings_query import read_by_email
    store = MagicMock()
    assert read_by_email(store, "", days=10) == []
    store.read.assert_not_called()


@requires_polars
def test_read_by_repo_filters_to_repo():
    from store.findings_query import read_by_repo
    from datetime import date

    today = date.today().isoformat()
    rows = {
        today: [
            {"repo_name": "rag",   "category": "tool_registration"},
            {"repo_name": "other", "category": "tool_registration"},
        ],
    }
    store = _make_store_with_rows(rows)
    out = read_by_repo(store, "rag", days=2)
    assert len(out) == 1
    assert out[0]["repo_name"] == "rag"


@requires_polars
def test_read_by_email_skips_partition_read_errors():
    """A partition that throws on read should be skipped without aborting."""
    from store.findings_query import read_by_email
    from datetime import date, timedelta
    import polars as pl

    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    class _FakeStore:
        def read(self, target_date, severity=None, limit=10_000):
            if target_date == yesterday:
                raise RuntimeError("simulated S3 outage")
            if target_date == today:
                return pl.DataFrame([{"email": "alice@x.com"}])
            return pl.DataFrame()

    out = read_by_email(_FakeStore(), "alice@x.com", days=3)
    assert len(out) == 1                                # today survived
