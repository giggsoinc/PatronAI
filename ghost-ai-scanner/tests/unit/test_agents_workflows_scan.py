# =============================================================
# FILE: tests/unit/test_agents_workflows_scan.py
# PROJECT: PatronAI — Phase 1A
# VERSION: 1.0.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Lock the agent-workflows scanner's contract:
#          - finds n8n / Flowise / langflow workflow files on disk
#          - emits one finding per file with safe path
#          - returns [] when nothing is configured
#          - LOC cap respected
# AUDIT LOG:
#   v1.0.0  2026-04-26  Initial. Phase 1A.
# =============================================================

import json
import os
import re
import subprocess
from pathlib import Path

REPO  = Path(__file__).resolve().parents[2]
FRAGS = REPO / "agent" / "install"


def _run_workflows_scan(home: Path) -> list:
    """Exec redactor + workflows scanner under fake $HOME and return findings."""
    ns: dict = {
        "re": re, "Path": Path, "os": os, "json": json,
        "subprocess": subprocess,
        "OS_NAME": "darwin",
        "AGENT_DIR": home / ".patronai",
    }
    real_home = Path.home
    Path.home = staticmethod(lambda: home)                       # type: ignore
    try:
        for frag in ("scan_redactor.py.frag", "scan_agents_workflows.py.frag"):
            exec(compile((FRAGS / frag).read_text(), frag, "exec"), ns)
        return ns["scan_agents_workflows"]()
    finally:
        Path.home = real_home                                    # type: ignore


def test_no_roots_means_no_findings(tmp_path):
    """Empty fake home should produce zero workflow findings (cron may
    still fire on the real system; we assert >= 0 not == 0 for cron)."""
    out = _run_workflows_scan(tmp_path)
    workflow_findings = [f for f in out if f["type"] == "agent_workflow"]
    assert workflow_findings == []


def test_n8n_workflow_file_is_detected(tmp_path):
    n8n_dir = tmp_path / ".n8n" / "workflows"
    n8n_dir.mkdir(parents=True)
    (n8n_dir / "my-flow.json").write_text(json.dumps({"nodes": []}))
    out = _run_workflows_scan(tmp_path)
    workflow_findings = [f for f in out if f["type"] == "agent_workflow"]
    assert len(workflow_findings) == 1
    f = workflow_findings[0]
    assert f["framework"] == "n8n"
    assert f["filename"]  == "my-flow.json"
    assert "/Users/" not in f["file_safe"]               # path redacted


def test_flowise_workflow_file_is_detected(tmp_path):
    flow_dir = tmp_path / ".flowise"
    flow_dir.mkdir()
    (flow_dir / "agent.json").write_text("{}")
    out = _run_workflows_scan(tmp_path)
    workflow_findings = [f for f in out if f["type"] == "agent_workflow"]
    assert any(f["framework"] == "flowise" for f in workflow_findings)


def test_langflow_yaml_is_detected(tmp_path):
    lf_dir = tmp_path / ".langflow" / "flows"
    lf_dir.mkdir(parents=True)
    (lf_dir / "rag.yaml").write_text("name: rag\n")
    out = _run_workflows_scan(tmp_path)
    workflow_findings = [f for f in out if f["type"] == "agent_workflow"]
    assert any(f["framework"] == "langflow" for f in workflow_findings)


def test_non_workflow_files_are_skipped(tmp_path):
    """Random `.txt` files in workflow dirs must NOT become findings."""
    n8n_dir = tmp_path / ".n8n" / "workflows"
    n8n_dir.mkdir(parents=True)
    (n8n_dir / "notes.txt").write_text("hi")
    out = _run_workflows_scan(tmp_path)
    assert out == [] or all(f["filename"] != "notes.txt" for f in out)


def test_workflow_filename_capped_at_120_chars(tmp_path):
    n8n_dir = tmp_path / ".n8n" / "workflows"
    n8n_dir.mkdir(parents=True)
    long_name = ("x" * 130) + ".json"  # 135 chars — exceeds 120 cap but fits Windows MAX_PATH
    (n8n_dir / long_name).write_text("{}")
    out = _run_workflows_scan(tmp_path)
    f = next(x for x in out if x["type"] == "agent_workflow")
    assert len(f["filename"]) <= 120


def test_workflows_scanner_under_loc_cap():
    body = (FRAGS / "scan_agents_workflows.py.frag").read_text()
    assert len(body.splitlines()) <= 150
