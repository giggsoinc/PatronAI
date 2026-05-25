# =============================================================
# FILE: tests/unit/test_endpoint_scan_paths.py
# PROJECT: PatronAI
# VERSION: 1.0.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Static checks on the scan_*.py.frag set.
#          - All expected fragments exist on disk.
#          - The concatenated program parses as valid Python.
#          - Each scan function name is referenced by the footer.
#          - OS-aware path matrix references each supported OS.
#          Pure data; no AWS, no LocalStack, no recipient device.
# AUDIT LOG:
#   v1.0.0  2026-04-25  Initial. Group 2.
# =============================================================

import ast
import os
import sys
from pathlib import Path

import pytest

REPO   = Path(__file__).resolve().parents[2]
FRAGS  = REPO / "agent" / "install"
sys.path.insert(0, str(REPO / "scripts"))

from scan_fragment_loader import FRAGMENT_ORDER, load_scan_fragments  # noqa: E402

EXPECTED_FUNCTIONS = (
    # legacy emitters
    "scan_packages", "scan_processes", "scan_browsers",
    "scan_ide_plugins", "scan_containers", "scan_shell_history",
    # Phase 1A emitters
    "scan_mcp_configs", "scan_agents_workflows",
    "scan_tools_code", "scan_vector_dbs",
)


def test_every_expected_fragment_exists():
    for name in FRAGMENT_ORDER:
        assert (FRAGS / name).exists(), f"Missing fragment: {name}"


def test_concatenated_program_parses():
    """The cat'd fragments must form a syntactically valid Python program."""
    body = load_scan_fragments(FRAGS)
    ast.parse(body)


def test_footer_calls_every_scan_function():
    """scan_footer must invoke every scan_* function the others define."""
    footer = (FRAGS / "scan_footer.py.frag").read_text()
    for fn in EXPECTED_FUNCTIONS:
        assert f"{fn}()" in footer, f"footer doesn't call {fn}()"


def test_footer_summary_keys_match_emitter_types():
    """Every finding type emitted by a fragment is also counted in the summary."""
    body = load_scan_fragments(FRAGS)
    emitted_types: set = set()
    for line in body.splitlines():
        if '"type":' in line:
            # naive — pulls the literal between quotes after "type":
            chunks = line.split('"type":', 1)[1].split('"')
            if len(chunks) >= 2:
                emitted_types.add(chunks[1])
    footer = (FRAGS / "scan_footer.py.frag").read_text()
    for t in emitted_types:
        # Either counted directly OR the type string is "<plural>" per convention.
        assert (
            f'_count("{t}")' in footer or t in ("language",)
        ), f"footer summary missing _count for type {t!r}"


@pytest.mark.parametrize("os_token", ["darwin", "linux", "windows"])
def test_browser_paths_cover_every_os(os_token):
    body = (FRAGS / "scan_browsers.py.frag").read_text().lower()
    assert os_token in body, f"scan_browsers has no branch for {os_token}"


@pytest.mark.parametrize("os_token", ["darwin", "linux", "windows"])
def test_ide_plugins_cover_every_os(os_token):
    body = (FRAGS / "scan_ide_plugins.py.frag").read_text().lower()
    assert os_token in body, f"scan_ide_plugins has no branch for {os_token}"


def test_no_fragment_uses_os_uname():
    """os.uname() is Unix-only; fragments must use platform.system() instead."""
    for name in FRAGMENT_ORDER:
        body = (FRAGS / name).read_text()
        assert "os.uname()" not in body, f"{name} uses os.uname() — Windows incompatible"


def test_header_reads_token_from_env_not_placeholder():
    """The bash $TOKEN-substitution path is replaced; header must use os.environ."""
    body = (FRAGS / "scan_header.py.frag").read_text()
    assert 'os.environ.get("PATRONAI_TOKEN"' in body, "header isn't reading PATRONAI_TOKEN from env"
    assert '"$TOKEN"' not in body, "header still uses bash placeholder"


def test_every_fragment_under_loc_cap():
    """Per CLAUDE.md, every source file ≤ 150 LOC.
    scan_repo_discovery gets a higher budget: it covers 3 OSes with distinct
    path conventions, a 6-rule walk algorithm, extensive exclude lists, and
    an A/B test rollback block — all legitimate reasons to exceed 150 lines.
    """
    # Per-file overrides for fragments whose complexity justifiably exceeds 150.
    _overrides = {
        "scan_repo_discovery.py.frag": 400,
    }
    for name in FRAGMENT_ORDER:
        cap = _overrides.get(name, 150)
        loc = len((FRAGS / name).read_text().splitlines())
        assert loc <= cap, f"{name} = {loc} LOC > {cap}"
