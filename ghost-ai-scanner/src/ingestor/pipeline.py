# =============================================================
# FILE: src/ingestor/pipeline.py
# VERSION: 2.0.0
# UPDATED: 2026-04-26
# OWNER: Ravi Venugopal, Giggso Inc
# PURPOSE: Per-event processing pipeline. Takes raw event dict,
#          normalises to flat schema, matches against provider lists,
#          writes finding to store. Identity resolution and alerting
#          handled downstream by alerter.py.
# AUDIT LOG:
#   v1.0.0  2026-04-18  Initial.
#   v2.0.0  2026-04-26  ENDPOINT_SCAN no longer dropped at the dst-domain
#                       check. New _process_endpoint_scan() explodes each
#                       finding into one flat event. Clean scans drop entirely
#                       (heartbeat covers liveness). Pre-classified events
#                       skip the matcher (agent already filtered them).
#   v2.1.0  2026-04-26  Phase 1A. After writing each `mcp_server` finding,
#                       compare its config_sha256 to the last known hash
#                       for (device, mcp_host). On flip, emit a derived
#                       `mcp_config_changed` event with severity HIGH so
#                       the alerter pages someone when MCP config changes.
# DEPENDS: normalizer, matcher, blob_index_store
# =============================================================

import json
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("marauder-scan.ingestor.pipeline")


class Pipeline:
    """
    Processes one raw event through normalize → match → store.
    Stateless — safe to call concurrently.
    """

    def __init__(self, store, authorized: list, unauthorized: list, company: str = ""):
        # store: BlobIndexStore instance
        # authorized / unauthorized: loaded once per cycle by loader.py
        self._store        = store
        self._authorized   = authorized
        self._unauthorized = unauthorized
        self._company      = company

    def process(self, raw: dict) -> Optional[str]:
        """
        Process one raw event through the pipeline.
        Returns outcome string or None if event was skipped.

        Steps:
        1. Extract hint and raw payload
        2. Normalize to flat universal schema
        3. Match against provider lists — get verdict
        4. Merge verdict into event
        5. Write to findings store (skip SUPPRESS)
        6. Return outcome for cycle stats
        """
        from normalizer import normalize
        from matcher    import match

        # Pre-route — ENDPOINT_SCAN is multi-finding. Explode it into one
        # flat event per finding and write directly. Pre-classified by the
        # agent; no matcher pass needed. Clean scans drop entirely.
        if isinstance(raw, dict) and raw.get("event_type") == "ENDPOINT_SCAN":
            return self._process_endpoint_scan(raw)

        # Step 1: extract source hint
        hint = raw.pop("_hint", "")
        raw_payload = raw.get("_raw", raw)  # vpc_flow sends {"_raw": line}

        # Step 2: normalize
        event = normalize(raw_payload, source_hint=hint, company=self._company)
        if event is None:
            log.debug("Event skipped by normalizer")
            return None

        # HEARTBEAT — liveness ping from hook agent. No destination to match.
        # Write directly to findings store and return — skip steps 3-4.
        if event.get("outcome") == "HEARTBEAT":
            self._store.findings.write(event)
            log.debug(f"HEARTBEAT recorded: {event.get('src_hostname')}")
            return "HEARTBEAT"

        # UNINSTALLED — agent notifying it was removed. Write finding and
        # update the agent's status.json so the fleet view shows it.
        if event.get("outcome") == "UNINSTALLED":
            self._store.findings.write(event)
            self._update_agent_status(event)
            log.info(f"UNINSTALLED recorded: {event.get('src_hostname')} email={event.get('email')}")
            return "UNINSTALLED"

        # Skip events with no destination info — nothing to match
        if not event.get("dst_domain") and not event.get("dst_port"):
            log.debug("Event has no dst_domain or dst_port — skipped")
            return None

        # Step 3: match
        verdict = match(event, self._authorized, self._unauthorized)

        # Step 4: merge verdict into flat event
        event.update(verdict)

        # Step 5: write to store — skip authorized traffic
        if event["outcome"] != "SUPPRESS":
            self._store.findings.write(event)
            log.debug(
                f"{event['outcome']}: {event.get('dst_domain') or event.get('dst_port')} "
                f"← {event.get('src_ip')} [{event['severity']}]"
            )

        # Step 6: return outcome for stats
        return event["outcome"]

    def _process_endpoint_scan(self, raw: dict) -> Optional[str]:
        """Explode an ENDPOINT_SCAN payload into per-finding events and persist."""
        from normalizer.agent          import explode_endpoint_findings
        from .pipeline_mcp_change      import maybe_emit_mcp_change
        events = explode_endpoint_findings(raw, self._company)
        if not events:
            log.debug("Clean scan — dropped (heartbeat covers liveness)")
            return None
        for ev in events:
            self._store.findings.write(ev)
            maybe_emit_mcp_change(self._store, ev)
            log.debug(
                f"ENDPOINT_FINDING [{ev['severity']}]: {ev['provider']} "
                f"← {ev.get('email') or ev.get('src_hostname')} "
                f"(scan={ev.get('scan_id','')[:30]})"
            )
        return "ENDPOINT_FINDING"

    def _update_agent_status(self, event: dict) -> None:
        """Update agent status.json in S3 to 'uninstalled' when UNINSTALLED event received."""
        try:
            notes = json.loads(event.get("notes", "{}"))
            token = notes.get("token", "")
            if not token:
                return
            now = datetime.now(timezone.utc).isoformat()
            status = {
                "token":          token,
                "status":         "uninstalled",
                "event_type":     "UNINSTALLED",
                "device_id":      event.get("src_hostname", ""),
                "email":          event.get("email", ""),
                "timestamp":      event.get("timestamp", now),
                "uninstalled_at": event.get("timestamp", now),
                "updated_at":     now,
            }
            self._store.agent.write_agent_status(token, status)
            log.info("Agent status updated to 'uninstalled' for token=%s", token[:8])
        except Exception as e:
            log.error("_update_agent_status failed: %s", e)
