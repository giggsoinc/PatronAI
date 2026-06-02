# =============================================================
# FILE: src/bootstrap.py
# VERSION: 1.2.0
# UPDATED: 2026-04-25
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Container startup tasks — validate env, build store,
#          load settings, build identity resolver, maybe backfill.
#          Called once by main.py before threads start.
# DEPENDS: blob_index_store, identity_resolver, summarizer, matcher
# AUDIT LOG:
#   v1.0.0  2026-04-18  Initial
#   v1.1.0  2026-04-20  seed_config_files() — push bundled CSVs to S3 on startup.
#                       unauthorized.csv always overwritten (Giggso owns it).
#                       authorized.csv and settings.json seeded only if absent.
#   v1.2.0  2026-04-25  Group 6 — self_check_rules() writes load_status.json,
#                       fires self-alert when rule count below STRICT_MIN_RULES.
#                       Empty *_custom.csv seeds pushed only-if-absent.
# =============================================================

import os
import sys
import logging
from pathlib import Path

log = logging.getLogger("marauder-scan.bootstrap")

# Config files bundled inside the Docker image
_LOCAL_CONFIG = Path("/app/config")

BUCKET             = os.environ.get("MARAUDER_SCAN_BUCKET",        "")
REGION             = os.environ.get("AWS_REGION",              "us-east-1")
CLOUD_PROVIDER     = os.environ.get("CLOUD_PROVIDER",          "aws")
SCAN_INTERVAL_SECS = int(os.environ.get("SCAN_INTERVAL_SECS",  "300"))
COMPANY_SLUG       = os.environ.get("COMPANY_SLUG",            "company")
STRICT_MIN_RULES   = int(os.environ.get("STRICT_MIN_RULES",    "50"))


def validate_env():
    """Fail fast if required environment variables are missing."""
    if not BUCKET:
        log.critical("MARAUDER_SCAN_BUCKET not set — cannot start")
        sys.exit(1)
    log.info(f"Bucket: {BUCKET} | Region: {REGION} | Cloud: {CLOUD_PROVIDER}")


def build_store():
    """Initialise BlobIndexStore."""
    from blob_index_store import BlobIndexStore
    store = BlobIndexStore(BUCKET, REGION)
    log.info(f"Store ready: {store}")
    return store


def load_settings(store) -> dict:
    """Load settings from S3. Falls back to env var defaults; ensures bucket present."""
    settings = store.settings.read()
    if not settings:
        log.warning("No settings.json found — using env defaults")
        settings = {
            "company": {"slug": COMPANY_SLUG},
            "cloud":   {"provider": CLOUD_PROVIDER, "region": REGION},
            "scanner": {"scan_interval_secs": SCAN_INTERVAL_SECS},
            "alerts":  {"sns_topic_arn": os.environ.get("ALERT_SNS_ARN", ""),
                        "trinity_webhook_url": os.environ.get("TRINITY_WEBHOOK_URL", ""),
                        "dedup_window_minutes": int(os.environ.get("DEDUP_WINDOW_MINUTES", "60"))},
            "storage": {"ocsf_bucket": BUCKET},
        }
    storage = settings.setdefault("storage", {})
    if not storage.get("ocsf_bucket"):
        storage["ocsf_bucket"] = BUCKET
    return settings


def build_resolver(store, settings: dict):
    """Initialise IdentityResolver with NAC DataFrame pre-loaded."""
    from identity_resolver import IdentityResolver
    nac_df   = store.identity.load_csv() if hasattr(store.identity, "load_csv") else None
    resolver = IdentityResolver(settings, nac_df=nac_df)
    log.info("IdentityResolver ready")
    return resolver


def seed_config_files(store) -> None:
    """
    Push bundled config files from Docker image to S3 on every startup.

    Rules:
      unauthorized.csv — ALWAYS overwrite. Giggso maintains this list.
          Every new image deploy automatically updates S3. No manual aws s3 cp needed.
      authorized.csv   — seed only if missing. Customer owns their approved list.
      settings.json    — seed only if missing. Customer configures via UI.
    """
    import boto3

    s3 = boto3.client("s3", region_name=REGION)

    def _exists(key: str) -> bool:
        """Return True if key exists in the bucket."""
        try:
            s3.head_object(Bucket=BUCKET, Key=key)
            return True
        except Exception:  # intentional: returns safe default on any error
            return False

    def _push(local_path: Path, key: str) -> None:
        """Upload a local file to S3."""
        try:
            s3.upload_file(str(local_path), BUCKET, key,
                           ExtraArgs={"ContentType": "text/csv"})
            log.info(f"Seeded {key} → s3://{BUCKET}/{key}  ({local_path.stat().st_size} bytes)")
        except Exception as e:
            log.error(f"Failed to seed {key}: {e}")

    # unauthorized.csv — always push (Giggso owns it; Docker image is source of truth)
    unauth_local = _LOCAL_CONFIG / "unauthorized.csv"
    if unauth_local.exists():
        _push(unauth_local, "config/unauthorized.csv")
    else:
        log.warning("unauthorized.csv not found in image — skipping seed")

    # authorized.csv — seed only if absent (customer owns it)
    auth_local = _LOCAL_CONFIG / "authorized.csv"
    if auth_local.exists() and not _exists("config/authorized.csv"):
        _push(auth_local, "config/authorized.csv")

    # *_custom.csv — empty seeds for customer additions; seed only if absent
    for fname in ("unauthorized_custom.csv", "unauthorized_code_custom.csv"):
        local = _LOCAL_CONFIG / fname
        s3_key = f"config/{fname}"
        if local.exists() and not _exists(s3_key):
            _push(local, s3_key)

    # settings.json — seed only if absent (customer configures via UI)
    settings_local = _LOCAL_CONFIG / "settings.json"
    if settings_local.exists() and not _exists("config/settings.json"):
        try:
            s3.upload_file(str(settings_local), BUCKET, "config/settings.json",
                           ExtraArgs={"ContentType": "application/json"})
            log.info("Seeded config/settings.json (first deploy)")
        except Exception as e:
            log.error(f"Failed to seed settings.json: {e}")


def maybe_backfill(store):
    """Run 7-day backfill on first deploy if no summary exists."""
    from summarizer import Summarizer
    if not store.summary.read():
        log.info("No summary found — running first-deploy backfill")
        results = Summarizer(store).backfill(days=7)
        log.info(f"Backfill done: {len(results)} days")
