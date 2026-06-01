# =============================================================
# FILE: tests/unit/test_secret_patterns.py
# VERSION: 1.0.0
# UPDATED: 2026-05-01
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: CI safety net — fail if any git-tracked text file contains
#          patterns that look like real AWS credentials. Catches anyone
#          who bypasses the local pre-commit hook with --no-verify.
#          Pairs with: scripts/git-hooks/pre-commit (local block) and
#          GitHub Push Protection / Secret Scanning (remote block).
# =============================================================

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]   # …/marauder-scan-complete

# AWS Access Key ID pattern. AKIAIOSFODNN7EXAMPLE is the official AWS
# documentation example and is allow-listed.
_AWS_KEY_RE = re.compile(rb"AKIA[A-Z0-9]{16}")
_ALLOW_KEY  = b"AKIAIOSFODNN7EXAMPLE"  # noqa: SECRET — test data, not a real key

# AWS temporary session token prefix.
_AWS_TMP_RE = re.compile(rb"ASIA[A-Z0-9]{16}")

# Presigned-URL fragment — catches whole leaked URLs even if the key
# ID itself was rotated/redacted.
_AWS_SIG_RE = re.compile(rb"AWSAccessKeyId=AKIA")

# Secret-shaped value on a line that names AWS_SECRET_ACCESS_KEY.
_AWS_SECRET_RE = re.compile(
    rb"(AWS_SECRET_ACCESS_KEY|aws_secret_access_key)\s*[:=]\s*[A-Za-z0-9/+=]{40}"
)

# Files we never scan: this test file (it contains the patterns by design),
# the pre-commit hook (likewise), the docs that document the patterns.
_SELF_REFERENCING = {
    "ghost-ai-scanner/tests/unit/test_secret_patterns.py",
    "ghost-ai-scanner/scripts/git-hooks/pre-commit",
    "ghost-ai-scanner/tests/unit/test_secret_redactor.py",
}


def _git_tracked_text_files() -> list[Path]:
    """Return every git-tracked file under repo root, excluding obvious
    binaries by extension. We use git so .gitignore'd files are skipped."""
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    binary_exts = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico",
                   ".svg", ".woff", ".woff2", ".ttf", ".eot", ".zip",
                   ".tar", ".gz", ".bz2", ".7z", ".dmg", ".exe",
                   ".pyc", ".pyo", ".so", ".dylib", ".o", ".bin",
                   ".gguf", ".onnx", ".pt", ".bin"}
    files: list[Path] = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        if line in _SELF_REFERENCING:
            continue
        p = REPO_ROOT / line
        if p.suffix.lower() in binary_exts:
            continue
        if p.exists() and p.is_file():
            files.append(p)
    return files


@pytest.fixture(scope="module")
def tracked_files() -> list[Path]:
    return _git_tracked_text_files()


def test_no_aws_access_key_in_tracked_files(tracked_files):
    """No real AWS access key (AKIA…) may appear in any tracked text file."""
    leaks = []
    for p in tracked_files:
        try:
            content = p.read_bytes()
        except OSError:
            continue
        for match in _AWS_KEY_RE.finditer(content):
            if match.group(0) == _ALLOW_KEY:
                continue
            # Get the line number for a useful failure message.
            line_no = content.count(b"\n", 0, match.start()) + 1
            leaks.append(f"{p.relative_to(REPO_ROOT)}:{line_no} → "
                         f"{match.group(0).decode()}")
    assert not leaks, (
        "AWS access key(s) found in git-tracked files:\n  - "
        + "\n  - ".join(leaks)
        + "\n\nRotate the key NOW, then remove from git history with "
          "git filter-repo. See ghost-ai-scanner/docs/chat-rollups.md "
          "for the runbook."
    )


def test_no_aws_session_token_in_tracked_files(tracked_files):
    """No AWS temporary session token (ASIA…) in any tracked text file."""
    leaks = []
    for p in tracked_files:
        try:
            content = p.read_bytes()
        except OSError:
            continue
        for match in _AWS_TMP_RE.finditer(content):
            line_no = content.count(b"\n", 0, match.start()) + 1
            leaks.append(f"{p.relative_to(REPO_ROOT)}:{line_no} → "
                         f"{match.group(0).decode()}")
    assert not leaks, (
        "AWS session token(s) found in git-tracked files:\n  - "
        + "\n  - ".join(leaks)
    )


def test_no_presigned_url_signature_in_tracked_files(tracked_files):
    """No `AWSAccessKeyId=AKIA...` fragment in any tracked text file."""
    leaks = []
    for p in tracked_files:
        try:
            content = p.read_bytes()
        except OSError:
            continue
        for match in _AWS_SIG_RE.finditer(content):
            line_no = content.count(b"\n", 0, match.start()) + 1
            leaks.append(f"{p.relative_to(REPO_ROOT)}:{line_no}")
    assert not leaks, (
        "Presigned-URL signature fragment(s) in git-tracked files:\n  - "
        + "\n  - ".join(leaks)
    )


def test_no_aws_secret_value_in_tracked_files(tracked_files):
    """No 40-char secret-shaped value paired with AWS_SECRET_ACCESS_KEY."""
    leaks = []
    for p in tracked_files:
        try:
            content = p.read_bytes()
        except OSError:
            continue
        for match in _AWS_SECRET_RE.finditer(content):
            line_no = content.count(b"\n", 0, match.start()) + 1
            leaks.append(f"{p.relative_to(REPO_ROOT)}:{line_no}")
    assert not leaks, (
        "AWS secret access key in git-tracked files:\n  - "
        + "\n  - ".join(leaks)
    )


def test_generated_installers_not_committed(tracked_files):
    """The rendered installer files (without .template suffix) must never
    be committed. The generator at scripts/render_agent_package.py writes
    them to S3, not to disk in the repo."""
    forbidden = {
        "ghost-ai-scanner/agent/install/setup_agent.sh",
        "ghost-ai-scanner/agent/install/setup_agent.ps1",
    }
    tracked_relative = {
        str(p.relative_to(REPO_ROOT)) for p in tracked_files
    }
    overlap = forbidden & tracked_relative
    assert not overlap, (
        "Generated installer file(s) committed to git:\n  - "
        + "\n  - ".join(sorted(overlap))
        + "\n\nThese are per-recipient outputs. Templates "
          "(setup_agent.{sh,ps1}.template) are the only files that "
          "belong in this directory."
    )
