# =============================================================
# FILE: src/normalizer/agent.py
# PROJECT: PatronAI — Marauder Scan code layer
# VERSION: 1.4.0
# UPDATED: 2026-04-26
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Parse edge agent payloads into flat schema.
#          EVENT TYPES:
#          CODE_SIGNAL     — file system watcher detected AI framework
#          GIT_DIFF_SIGNAL — pre-commit hook staged diff
#          PROCESS_SIGNAL  — running process scan
#          HEARTBEAT       — periodic liveness ping (every 5 min)
#          ENDPOINT_SCAN   — full endpoint scan (packages/processes/browsers/
#                            ide_plugins/containers/shell_history)
# DEPENDS: normalizer.schema
# AUDIT LOG:
#   v1.0.0  2026-04-18  Initial — CODE/GIT/PROCESS signals
#   v1.1.0  2026-04-19  HEARTBEAT event type — device liveness ingestion
#   v1.2.0  2026-04-20  ENDPOINT_SCAN — package/process/browser findings
#   v1.3.0  2026-04-25  Step 0 — propagate identity (email/device_uuid/
#                       mac_primary/ip_set) from agent payloads onto events.
#   v1.4.0  2026-04-26  explode_endpoint_findings() — emit ONE flat event per
#                       finding so the inventory dashboard shows them as
#                       proper rows. Clean scans (zero findings) drop entirely
#                       (heartbeat covers liveness). Each event tagged with
#                       scan_id for grouping back to the source scan.
# =============================================================

import json
import logging
from typing import Optional
from .schema import empty_event

log = logging.getLogger("marauder-scan.normalizer.agent")


def parse(raw: dict, company: str = "") -> Optional[dict]:
    """
    Parse an edge agent payload into flat universal schema.
    Supports CODE_SIGNAL, GIT_DIFF_SIGNAL, PROCESS_SIGNAL, HEARTBEAT.
    Returns None if event type unrecognised.
    """
    event_type = raw.get("event_type", "")

    if event_type == "CODE_SIGNAL":
        return _parse_code_signal(raw, company)
    elif event_type == "GIT_DIFF_SIGNAL":
        return _parse_git_diff(raw, company)
    elif event_type == "PROCESS_SIGNAL":
        return _parse_process_signal(raw, company)
    elif event_type == "HEARTBEAT":
        return _parse_heartbeat(raw, company)
    elif event_type == "UNINSTALLED":
        return _parse_uninstalled(raw, company)
    elif event_type == "ENDPOINT_SCAN":
        return _parse_endpoint_scan(raw, company)
    else:
        log.debug(f"Unknown agent event type: {event_type}")
        return None


def _parse_code_signal(raw: dict, company: str) -> dict:
    """File watcher detected AI signal in source file."""
    event = empty_event("agent_fs_watcher", company)
    event["src_ip"]       = raw.get("device_id", "")
    event["src_hostname"] = raw.get("device_id", "")
    event["owner"]        = raw.get("owner", "")
    event["department"]   = raw.get("department", "")
    event["mac_address"]  = raw.get("mac_address", "")
    event["asset_type"]   = "laptop"
    event["code_snippet"] = raw.get("snippet", "")[:2000]
    event["file_path"]    = raw.get("file_path", "")
    event["timestamp"]    = raw.get("timestamp", event["timestamp"])
    event["outcome"]      = "PENDING_TRIAGE"
    event["severity"]     = "UNKNOWN"
    return event


def _parse_git_diff(raw: dict, company: str) -> dict:
    """Pre-commit hook detected AI signal in staged diff."""
    event = empty_event("agent_git_hook", company)
    event["src_ip"]       = raw.get("device_id", "")
    event["src_hostname"] = raw.get("device_id", "")
    event["owner"]        = raw.get("owner", raw.get("device_id", ""))
    event["asset_type"]   = "laptop"
    event["git_diff"]     = raw.get("diff_snippet", "")[:2000]
    event["code_snippet"] = raw.get("diff_snippet", "")[:2000]
    event["repo"]         = raw.get("repo", "")
    event["branch"]       = raw.get("branch", "")
    event["timestamp"]    = raw.get("timestamp", event["timestamp"])
    event["outcome"]      = "PENDING_TRIAGE"
    event["severity"]     = "UNKNOWN"
    return event


def _parse_process_signal(raw: dict, company: str) -> dict:
    """Process scanner detected AI framework running on device."""
    event = empty_event("agent_process_scan", company)
    event["src_ip"]       = raw.get("device_id", "")
    event["src_hostname"] = raw.get("device_id", "")
    event["owner"]        = raw.get("owner", "")
    event["asset_type"]   = "laptop"
    event["process_name"] = raw.get("framework", "")
    event["dst_port"]     = raw.get("port", 0)
    event["timestamp"]    = raw.get("timestamp", event["timestamp"])
    event["outcome"]      = "PENDING_TRIAGE"
    event["severity"]     = "UNKNOWN"
    return event


def _bind_identity(event: dict, raw: dict) -> None:
    """Copy Step 0 identity fields from the raw agent payload onto the flat event."""
    ip_set       = raw.get("ip_set") or []
    event["src_ip"]       = (ip_set[0] if ip_set else "") or raw.get("device_id", "")
    event["src_hostname"] = raw.get("device_id", "")
    event["mac_address"]  = raw.get("mac_primary", "")
    event["owner"]        = raw.get("email", "") or raw.get("owner", "") or raw.get("device_id", "")
    event["device_uuid"]  = raw.get("device_uuid", "")
    event["email"]        = raw.get("email", "")
    event["ip_set"]       = ip_set
    event["asset_type"]   = "laptop"


def _parse_heartbeat(raw: dict, company: str) -> dict:
    """
    Periodic liveness ping from a deployed hook agent (every 5 min).
    Carries identity (email, device_uuid, mac_primary, ip_set) post-Step-0.
    """
    event = empty_event("agent_heartbeat", company)
    _bind_identity(event, raw)
    event["process_name"]  = raw.get("agent_version", "")   # reuse field for version
    event["timestamp"]     = raw.get("timestamp", event["timestamp"])
    event["outcome"]       = "HEARTBEAT"
    event["severity"]      = "CLEAN"
    import json
    event["notes"] = json.dumps({
        "os_name":        raw.get("os_name", ""),
        "os_version":     raw.get("os_version", ""),
        "agent_version":  raw.get("agent_version", ""),
        "token":          raw.get("token", ""),
    })
    log.debug(f"HEARTBEAT from {event['src_hostname']} email={event['email']} ips={event['ip_set']}")
    return event


def _parse_uninstalled(raw: dict, company: str) -> dict:
    """Agent uninstall notification — marks agent as uninstalled."""
    event = empty_event("agent_heartbeat", company)
    _bind_identity(event, raw)
    event["timestamp"]    = raw.get("uninstalled_at", raw.get("timestamp", event["timestamp"]))
    event["outcome"]      = "UNINSTALLED"
    event["severity"]     = "LOW"
    event["notes"] = json.dumps({
        "event_type":     "UNINSTALLED",
        "token":          raw.get("token", ""),
        "uninstalled_at": raw.get("uninstalled_at", ""),
    })
    log.info(f"UNINSTALLED from {event['src_hostname']} email={event['email']} token={raw.get('token','')}")
    return event


def explode_endpoint_findings(raw: dict, company: str) -> list:
    """Public wrapper — see normalizer/agent_explode.py for logic."""
    from .agent_explode import explode_endpoint_findings as _ex
    return _ex(raw, company, _bind_identity)


def _parse_endpoint_scan(raw: dict, company: str) -> dict:
    """
    Full endpoint scan — multi-surface findings.
    Each finding in raw['findings'] becomes one event downstream.
    Returns one representative event; pipeline expands multi-finding scans.
    """
    import json
    findings = raw.get("findings", [])
    summary  = raw.get("summary", {})

    types = {f.get("type", "") for f in findings}
    if {"browser", "process", "container_log_signal"} & types:
        severity = "HIGH"
    elif {"package", "ide_plugin", "container_image"} & types:
        severity = "MEDIUM"
    elif "shell_history" in types:
        severity = "LOW"
    else:
        severity = "LOW"

    event = empty_event("agent_endpoint_scan", company)
    _bind_identity(event, raw)
    event["timestamp"]    = raw.get("timestamp", event["timestamp"])
    event["outcome"]      = "ALERT" if findings else "CLEAN"
    event["severity"]     = severity if findings else "CLEAN"
    event["notes"]        = json.dumps({
        "findings_count":      len(findings),
        "finding_types":       list(types),
        "summary":             summary,
        "token":               raw.get("token", ""),
    })
    log.debug(f"ENDPOINT_SCAN from {event['src_hostname']} email={event['email']} findings={len(findings)}")
    return event
