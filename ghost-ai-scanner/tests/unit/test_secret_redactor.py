# =============================================================
# FILE: tests/unit/test_secret_redactor.py
# PROJECT: PatronAI — Phase 1A
# VERSION: 1.0.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Lock the redactor's hits/misses contract. The fragment is
#          loaded as text and exec'd in a controlled namespace because
#          .frag files aren't valid Python module names.
# AUDIT LOG:
#   v1.0.0  2026-04-26  Initial. Phase 1A.
# =============================================================

import re
import sys
from pathlib import Path

REPO  = Path(__file__).resolve().parents[2]
FRAGS = REPO / "agent" / "install"


def _load(*names) -> dict:
    """Exec the named fragment(s) in a shared namespace. Returns the ns."""
    ns: dict = {"re": re, "Path": Path, "os": __import__("os"),
                "json": __import__("json"), "subprocess": None,
                "OS_NAME": "darwin", "AGENT_DIR": Path("/tmp/.patronai-test")}
    for n in names:
        body = (FRAGS / n).read_text(encoding="utf-8")
        exec(compile(body, n, "exec"), ns)
    return ns


def test_openai_key_is_redacted():
    ns = _load("scan_redactor.py.frag")
    out = ns["_redact_text"]("token=sk-abcdefghij1234567890ABCDEFGHIJ")
    assert "sk-" not in out
    assert "REDACTED" in out


def test_anthropic_key_is_redacted():
    ns = _load("scan_redactor.py.frag")
    out = ns["_redact_text"]("Authorization: Bearer sk-ant-abc12345defg67890hij1234567890")
    assert "sk-ant-" not in out
    assert "REDACTED" in out


def test_aws_access_key_is_redacted():
    ns = _load("scan_redactor.py.frag")
    out = ns["_redact_text"]("aws AKIAIOSFODNN7EXAMPLE used")  # noqa: SECRET — test data, not a real key
    assert "AKIA" not in out


def test_jwt_is_redacted():
    ns = _load("scan_redactor.py.frag")
    sample = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." \
             "eyJzdWIiOiJ1c2VyIiwiaWF0IjoxNzAwMDAwMDAwfQ." \
             "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKL"
    out = ns["_redact_text"](sample)
    assert "eyJ" not in out


def test_home_path_is_normalised():
    ns = _load("scan_redactor.py.frag")
    out = ns["_safe_path"](str(Path.home() / "projects" / "thing"))
    assert "/Users/" not in out and "/home/" not in out
    assert out.startswith("~")


def test_safe_finding_redacts_nested_dict():
    ns = _load("scan_redactor.py.frag")
    finding = {"type": "x", "args": ["sk-1234567890abcdefghijABCDEFGHIJ"]}
    out = ns["_safe_finding"](finding)
    assert "sk-1234567890abcdefghij" not in str(out)


def test_has_unredacted_secret_flags_remaining_secret():
    ns = _load("scan_redactor.py.frag")
    # Synthetic — bypass the redactor by handing _has_unredacted_secret
    # a finding that still contains a secret
    finding = {"raw": "sk-abcdefghij1234567890ABCDEFGHIJ"}
    assert ns["_has_unredacted_secret"](finding) is True


def test_safe_path_handles_empty():
    ns = _load("scan_redactor.py.frag")
    assert ns["_safe_path"](None) == ""
    assert ns["_safe_path"]("") == ""


def test_redact_text_handles_empty():
    ns = _load("scan_redactor.py.frag")
    assert ns["_redact_text"](None) == ""
    assert ns["_redact_text"]("") == ""


def test_redactor_under_loc_cap():
    body = (FRAGS / "scan_redactor.py.frag").read_text()
    assert len(body.splitlines()) <= 150
