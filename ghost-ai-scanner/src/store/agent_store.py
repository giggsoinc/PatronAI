# =============================================================
# FILE: src/store/agent_store.py
# VERSION: 2.0.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: S3-backed catalog for agent delivery packages.
#          Generates tokens, bcrypt-hashes OTPs, uploads packages,
#          mints presigned URLs, tracks install status.
#          All control-plane objects under config/HOOK_AGENTS/.
#          Telemetry from agents lands under ocsf/agent/ so the
#          ingestor walks it on every cycle.
# DEPENDS: boto3, bcrypt
# AUDIT LOG:
#   v1.0.0  2026-04-19  Initial — agent delivery system.
#   v1.1.0  2026-04-19  S3 prefix: agents/ → config/HOOK_AGENTS/.
#   v1.2.0  2026-04-19  heartbeat_put_url (7-day TTL) for liveness pings.
#   v1.3.0  2026-04-20  delete_package — purge all objects under token prefix.
#   v1.4.0  2026-04-20  get_artifact_url for DMG/EXE artifacts.
#   v1.5.0  2026-04-20  scan_put_url — presigned PUT for endpoint scan results.
#   v1.6.0  2026-04-20  authorized_domains persisted; authorized_get_url.
#   v2.0.0  2026-04-25  Step 0 — heartbeat key moved into ocsf/agent/heartbeats/
#                       so ingestor sees it (was clobbering status.json outside
#                       the walked prefix). New write_url_bundle() + urls_refresh_url
#                       so the laptop refreshes presigned URLs daily — the 7-day
#                       cliff that was silently killing fleet agents is gone.
# =============================================================

import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt

from .base_store import BaseStore

log = logging.getLogger("marauder-scan.agent_store")

HOOK_AGENTS_PREFIX = "config/HOOK_AGENTS"
CATALOG_KEY        = f"{HOOK_AGENTS_PREFIX}/catalog.json"
PRESIGN_TTL        = 172800   # 48 hours — installer + meta delivery
HEARTBEAT_PRESIGN_TTL = 604800  # 7 days  — max AWS IAM presigned PUT TTL


class AgentStore(BaseStore):
    """Manages OTP-locked agent installer packages on S3."""

    # ── OTP helpers ───────────────────────────────────────────

    def generate_otp(self) -> str:
        """Return a cryptographically secure 6-digit OTP string."""
        return str(secrets.randbelow(900000) + 100000)

    def hash_otp(self, otp: str) -> str:
        """Return bcrypt hash of otp (rounds=12). Store the hash, not the OTP."""
        return bcrypt.hashpw(otp.encode(), bcrypt.gensalt(rounds=12)).decode()

    def check_otp(self, otp: str, hashed: str) -> bool:
        """Validate OTP against stored bcrypt hash."""
        try:
            return bcrypt.checkpw(otp.encode(), hashed.encode())
        except Exception:
            return False

    # ── Package lifecycle ─────────────────────────────────────

    def create_package(
        self,
        recipient_name: str,
        recipient_email: str,
        os_type: str,
        rendered_script: str,
        otp_hash: str,
        authorized_domains: list | None = None,
    ) -> Optional[str]:
        """
        Upload meta.json + status.json + installer + authorized.csv to S3.
        authorized_domains: per-user list of allowed tool domains/packages.
        Returns token string or None on failure.
        """
        token      = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
        ext        = "ps1" if os_type == "windows" else "sh"
        script_key = f"{HOOK_AGENTS_PREFIX}/{token}/setup_agent.{ext}"
        domains    = authorized_domains or []

        meta = {
            "token":              token,
            "recipient_name":     recipient_name,
            "recipient_email":    recipient_email,
            "os_type":            os_type,
            "otp_hash":           otp_hash,
            "created_at":         created_at,
            "expires_at":         expires_at,
            "script_key":         script_key,
            "authorized_domains": domains,
        }
        status = {"token": token, "status": "pending", "updated_at": created_at}
        # authorized.csv: one domain per line, no header — agent fetches on every scan
        auth_csv = "\n".join(domains)

        try:
            self._put(f"{HOOK_AGENTS_PREFIX}/{token}/meta.json",
                      json.dumps(meta).encode(), "application/json")
            self._put(f"{HOOK_AGENTS_PREFIX}/{token}/status.json",
                      json.dumps(status).encode(), "application/json")
            self._put(f"{HOOK_AGENTS_PREFIX}/{token}/authorized.csv",
                      auth_csv.encode(), "text/csv")
            self._put(script_key,
                      rendered_script.encode(), "text/plain")
            self._catalog_add(token, recipient_name, recipient_email, os_type, created_at)
            return token
        except Exception as e:
            log.error("create_package failed: %s", e)
            return None

    def get_presigned_urls(self, token: str, os_type: str) -> dict:
        """
        Return presigned URLs for client use. Each URL is a time-bound,
        key-locked S3 capability. Heartbeat lands inside ocsf/ so the
        ingestor walks it. urls_refresh_url points at a daily-rotated
        bundle so the agent can pull fresh URLs before the 7-day cliff.
        """
        ext = "ps1" if os_type == "windows" else "sh"
        try:
            return {
                "installer_url":       self._sign_get(f"{HOOK_AGENTS_PREFIX}/{token}/setup_agent.{ext}", PRESIGN_TTL),
                "meta_url":            self._sign_get(f"{HOOK_AGENTS_PREFIX}/{token}/meta.json",           PRESIGN_TTL),
                "status_put_url":      self._sign_put(f"{HOOK_AGENTS_PREFIX}/{token}/status.json",         PRESIGN_TTL),
                "heartbeat_put_url":   self._sign_put(f"ocsf/agent/heartbeats/{token}/latest.json",        HEARTBEAT_PRESIGN_TTL),
                "scan_put_url":        self._sign_put(f"ocsf/agent/scans/{token}/latest.json",             HEARTBEAT_PRESIGN_TTL),
                "authorized_get_url":  self._sign_get(f"{HOOK_AGENTS_PREFIX}/{token}/authorized.csv",      HEARTBEAT_PRESIGN_TTL),
                "urls_refresh_url":    self._sign_get(f"{HOOK_AGENTS_PREFIX}/{token}/urls.json",           HEARTBEAT_PRESIGN_TTL),
            }
        except Exception as e:
            log.error("get_presigned_urls failed [%s]: %s", token, e)
            return {}

    def _sign_get(self, key: str, ttl: int) -> str:
        """Mint a presigned GET URL. Caller catches errors."""
        return self.s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl,
        )

    def _sign_put(self, key: str, ttl: int) -> str:
        """Mint a presigned PUT URL with JSON content-type binding."""
        return self.s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": "application/json"},
            ExpiresIn=ttl,
        )

    def write_url_bundle(self, token: str, os_type: str) -> bool:
        """Re-mint heartbeat / scan / authorized URLs and write the bundle to S3.

        Called by the daily url_refresh_loop. The bundle excludes urls_refresh_url
        itself (the agent already has that one and we don't want a chicken-and-egg).
        """
        urls = self.get_presigned_urls(token, os_type)
        if not urls:
            return False
        bundle = {
            "minted_at":          datetime.now(timezone.utc).isoformat(),
            "expires_at":         (datetime.now(timezone.utc) + timedelta(seconds=HEARTBEAT_PRESIGN_TTL)).isoformat(),
            "heartbeat_put_url":  urls["heartbeat_put_url"],
            "scan_put_url":       urls["scan_put_url"],
            "authorized_get_url": urls["authorized_get_url"],
        }
        try:
            self._put(f"{HOOK_AGENTS_PREFIX}/{token}/urls.json",
                      json.dumps(bundle).encode(), "application/json")
            return True
        except Exception as e:
            log.error("write_url_bundle [%s] failed: %s", token, e)
            return False

    def get_artifact_url(self, key: str) -> str:
        """Return a presigned GET URL for an arbitrary S3 key (48 h TTL)."""
        try:
            return self.s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=PRESIGN_TTL,
            )
        except Exception as e:
            log.error("get_artifact_url failed [%s]: %s", key, e)
            return ""

    def get_authorized_domains(self, token: str) -> list:
        """Read current authorized_domains for a token from meta.json."""
        try:
            raw = self._get(f"{HOOK_AGENTS_PREFIX}/{token}/meta.json")
            if not raw:
                return []
            return json.loads(raw).get("authorized_domains", [])
        except Exception as e:
            log.error("get_authorized_domains failed [%s]: %s", token, e)
            return []

    def update_authorized_domains(self, token: str, domains: list) -> bool:
        """
        Update authorized domains for an existing agent package.
        Writes new authorized.csv to S3 (agent picks it up within 30 min).
        Also updates meta.json so the UI reflects the current state.
        """
        domains = [d.strip().lower() for d in domains if d.strip()]
        try:
            # Update authorized.csv — agent fetches this on every scan
            auth_csv = "\n".join(domains)
            self._put(f"{HOOK_AGENTS_PREFIX}/{token}/authorized.csv",
                      auth_csv.encode(), "text/csv")
            # Patch meta.json
            raw = self._get(f"{HOOK_AGENTS_PREFIX}/{token}/meta.json")
            if raw:
                meta = json.loads(raw)
                meta["authorized_domains"] = domains
                meta["authorized_updated_at"] = datetime.now(timezone.utc).isoformat()
                self._put(f"{HOOK_AGENTS_PREFIX}/{token}/meta.json",
                          json.dumps(meta).encode(), "application/json")
            log.info("updated authorized_domains [%s]: %s", token[:8], domains)
            return True
        except Exception as e:
            log.error("update_authorized_domains failed [%s]: %s", token, e)
            return False

    def list_catalog(self) -> list:
        """Return all catalog entries as a list of dicts."""
        try:
            raw = self._get(CATALOG_KEY)
            if not raw:
                return []
            return json.loads(raw)
        except Exception as e:
            log.error("list_catalog failed: %s", e)
            return []

    def refresh_statuses(self, catalog: list) -> list:
        """Hydrate each catalog entry with current status from S3."""
        for entry in catalog:
            try:
                raw = self._get(f"{HOOK_AGENTS_PREFIX}/{entry['token']}/status.json")
                if raw:
                    entry["status"] = json.loads(raw).get("status", "pending")
            except Exception:
                pass
        return catalog

    # ── Catalog management ────────────────────────────────────

    def _catalog_add(
        self,
        token: str,
        recipient_name: str,
        recipient_email: str,
        os_type: str,
        created_at: str,
    ) -> None:
        """Append a new entry to catalog.json on S3."""
        catalog = self.list_catalog()
        catalog.append({
            "token":           token,
            "recipient_name":  recipient_name,
            "recipient_email": recipient_email,
            "os_type":         os_type,
            "created_at":      created_at,
            "status":          "pending",
        })
        try:
            self._put(CATALOG_KEY, json.dumps(catalog, indent=2).encode(), "application/json")
        except Exception as e:
            log.error("_catalog_add write failed: %s", e)

    def write_agent_status(self, token: str, status: dict) -> bool:
        """Write agent status.json to S3. Used by the ingestor on lifecycle events."""
        try:
            self._put(
                f"{HOOK_AGENTS_PREFIX}/{token}/status.json",
                json.dumps(status).encode(),
                "application/json",
            )
            return True
        except Exception as e:
            log.error("write_agent_status failed [%s]: %s", token[:8], e)
            return False

    def delete_package(self, token: str, os_type: str = "") -> bool:
        """
        Remove package from catalog and purge ALL S3 objects under the token prefix.
        Covers sh, ps1, dmg, exe, meta.json, status.json — no hard-coded list needed.
        os_type retained for API compatibility but no longer drives key selection.
        """
        prefix = f"{HOOK_AGENTS_PREFIX}/{token}/"
        try:
            paginator = self.s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if objects:
                    self.s3.delete_objects(
                        Bucket=self.bucket,
                        Delete={"Objects": objects, "Quiet": True},
                    )
            catalog = [e for e in self.list_catalog() if e["token"] != token]
            self._put(CATALOG_KEY, json.dumps(catalog, indent=2).encode(),
                      "application/json")
            log.info("delete_package: purged prefix %s (%d objects)", prefix, len(objects) if 'objects' in dir() else 0)
            return True
        except Exception as e:
            log.error("delete_package failed [%s]: %s", token, e)
            return False
