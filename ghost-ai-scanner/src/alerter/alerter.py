# =============================================================
# FILE: src/alerter/alerter.py
# VERSION: 1.0.0
# UPDATED: 2026-04-18
# OWNER: Ravi Venugopal, Giggso Inc
# PURPOSE: Main alerter coordinator. Reads fresh findings from store,
#          checks dedup, resolves identity, enriches with CloudTrail
#          spot check, builds payload, dispatches to all channels.
#          Called by main.py after each ingestor cycle completes.
# DEPENDS: alerter.payload, alerter.dispatcher, alerter.cloudtrail_check
# =============================================================
# raven: loc-exempt — pending refactor into sub-modules (tracked separately)

import logging
from datetime import date

from .payload          import build as build_payload, subject as build_subject
from .dispatcher       import dispatch
from .cloudtrail_check import check as cloudtrail_check

log = logging.getLogger("marauder-scan.alerter")


class Alerter:
    """
    Reads findings written by ingestor, fires alerts via configured channels.
    Dedup prevents repeat alerts within the configured window.
    Identity resolver and CloudTrail check run at alert time only.
    """

    def __init__(self, store, identity_resolver, settings: dict):
        self._store    = store
        self._resolver = identity_resolver

        # Alert channel config from settings
        alert_cfg       = settings.get("alerts", {})
        self._sns_arn    = alert_cfg.get("sns_topic_arn", "")
        self._webhook    = alert_cfg.get("trinity_webhook_url", "")
        self._dedup_min  = int(alert_cfg.get("dedup_window_minutes", 60))
        self._region     = settings.get("cloud", {}).get("region", "us-east-1")
        self._company    = settings.get("company", {}).get("slug", "")
        self._hash_email = bool(settings.get("privacy", {}).get("hash_emails", False))

    def process_code_signals(self, bucket: str) -> dict:
        """
        Process Marauder Scan code signals from agent prefixes in S3.
        Reads ocsf/agent/code-signals/ and ocsf/agent/git-diffs/ prefixes.
        Runs triage then Gemma on AMBIGUOUS findings.
        """
        from matcher.code_engine import (
            load_authorized_code, load_unauthorized_code, triage,
            CodeOutcome
        )
        from code_analyser import analyse as gemma_analyse, is_available

        authorized   = load_authorized_code(bucket)
        unauthorized = load_unauthorized_code(bucket)
        fired        = 0
        suppressed   = 0

        # Walk both agent prefixes
        for prefix in ["ocsf/agent/code-signals/", "ocsf/agent/git-diffs/"]:
            try:
                paginator = self._store.findings.s3.get_paginator("list_objects_v2")
                for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                    for obj in page.get("Contents", []):
                        key = obj["Key"]
                        try:
                            raw = self._store.findings.s3.get_object(
                                Bucket=bucket, Key=key
                            )
                            import json
                            payload = json.loads(raw["Body"].read())
                        except Exception:  # intentional: returns safe default on any error
                            continue

                        snippet    = payload.get("code_snippet") or payload.get("diff_snippet", "")
                        department = payload.get("department", "")
                        src_ip     = payload.get("device_id", "")

                        verdict = triage(snippet, department, authorized, unauthorized)

                        if verdict["outcome"] == CodeOutcome.SUPPRESS:
                            suppressed += 1
                            continue

                        # AMBIGUOUS — call Gemma if available
                        if verdict["outcome"] == CodeOutcome.AMBIGUOUS and is_available():
                            analysis = gemma_analyse(snippet)
                            verdict["severity"]  = analysis.get("risk_level", "MEDIUM")
                            verdict["outcome"]   = "CODE_ALERT"
                            payload["gemma_analysis"] = analysis

                        if verdict["outcome"] in ("CODE_ALERT", "AMBIGUOUS"):
                            payload.update(verdict)
                            payload["outcome"] = "CODE_ALERT"
                            identity = self._resolver.resolve(src_ip)
                            self._fire_code_alert(payload, identity)
                            fired += 1

            except Exception as e:
                log.error(f"Code signal processing error: {e}")

        log.info(f"Marauder Scan: {fired} code alerts fired, {suppressed} suppressed")
        return {"fired": fired, "suppressed": suppressed}

    def _fire_code_alert(self, payload: dict, identity: dict):
        """Fire SNS and Trinity for a code signal finding."""
        from alerter.payload import build as build_payload, subject as build_subject
        from alerter.dispatcher import dispatch

        alert_payload = build_payload(payload, identity, self._company,
                                      hash_emails=self._hash_email)
        alert_payload["alert_type"] = "MARAUDER_SCAN_CODE_ALERT"
        sub = build_subject(alert_payload)
        dispatch(
            payload=alert_payload,
            subject=sub,
            sns_arn=self._sns_arn,
            webhook_url=self._webhook,
            region=self._region,
        )
        self._store.findings.write(payload)

    def process_findings(self, severities: list = None) -> dict:
        """
        Read today's findings and alert on any that are not deduped.
        severities: list of severities to process. None = all non-SUPPRESS.
        Returns summary of alerts fired.
        """
        targets   = severities or ["critical", "high", "medium", "unknown"]
        today     = date.today().isoformat()
        fired     = 0
        suppressed = 0

        for severity in targets:
            findings = self._store.findings.read(
                target_date=today,
                severity=severity,
                limit=500,
            )
            if findings.is_empty():
                continue

            for row in findings.iter_rows(named=True):
                result = self._process_one(row)
                if result == "fired":
                    fired += 1
                else:
                    suppressed += 1

        log.info(f"Alerter: {fired} alerts fired, {suppressed} deduped")
        return {"fired": fired, "deduped": suppressed}

    def alert_one(self, event: dict) -> str:
        """
        Alert on a single event directly.
        Called by ingestor pipeline for immediate alerting on CRITICAL.
        Returns 'fired' | 'deduped' | 'error'.
        """
        return self._process_one(event)

    def _process_one(self, event: dict) -> str:
        """Full pipeline for one finding event."""
        src_ip   = event.get("src_ip", "")
        provider = event.get("provider", "")
        outcome  = event.get("outcome", "")

        # Skip suppress — should not be in findings but guard anyway
        if outcome == "SUPPRESS":
            return "suppressed"

        # Dedup check — skip if already alerted recently
        if self._store.dedup.is_duplicate(src_ip, provider, self._dedup_min):
            log.debug(f"Dedup suppressed: {src_ip} → {provider}")
            return "deduped"

        # Resolve identity at alert time only
        identity = self._resolver.resolve(src_ip)

        # CloudTrail spot check — only on authorized domain events
        # where we suspect personal key usage
        if outcome in ("DOMAIN_ALERT",) and identity.get("owner"):
            ct_result = cloudtrail_check(
                owner=identity.get("owner", ""),
                provider=provider,
                region=self._region,
            )
            event.update(ct_result)

            # Upgrade outcome if personal key detected
            if ct_result.get("token_status") == "personal_key":
                event["outcome"]  = "PERSONAL_KEY"
                event["severity"] = "HIGH"

        # Build and dispatch payload
        payload = build_payload(event, identity, self._company,
                               hash_emails=self._hash_email)
        sub     = build_subject(payload)

        results = dispatch(
            payload=payload,
            subject=sub,
            sns_arn=self._sns_arn,
            webhook_url=self._webhook,
            region=self._region,
        )

        # Record dedup entry to prevent repeat alerts
        self._store.dedup.record(src_ip, provider)

        log.warning(
            f"ALERT FIRED [{event['severity']}]: "
            f"{provider} ← {identity.get('owner', src_ip)} | "
            f"channels: {list(results.keys())}"
        )
        return "fired"
