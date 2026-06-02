# =============================================================
# FILE: src/store/base_store.py
# VERSION: 1.1.0
# UPDATED: 2026-05-01
# PURPOSE: Shared base class for all store modules.
#          Initialises boto3 S3 client and bucket reference once.
#          Every store inherits this — no repeated setup code.
# OWNER: Ravi Venugopal, Giggso Inc
# DEPENDS: boto3
# AUDIT LOG:
#   v1.0.0  2026-04-18  Initial.
#   v1.1.0  2026-05-01  Force SigV4 (signature_version='s3v4') on every
#                       store's S3 client. boto3 may default to deprecated
#                       SigV2 in some configs; SigV2 only works for
#                       us-east-1 buckets, fails under SSE-KMS, and is
#                       being phased out by AWS. Fixes presigned-URL
#                       SignatureDoesNotMatch errors on agent DMG/EXE
#                       downloads.
# =============================================================

import logging
import boto3
from botocore.config import Config

log = logging.getLogger("marauder-scan.store")

# All S3 clients share this config so presigned URLs are SigV4-signed.
# Path-style (vs virtual-hosted) is irrelevant to signing but s3v4 + the
# explicit region matters for cross-region buckets.
_S3_CLIENT_CONFIG = Config(
    signature_version="s3v4",
    s3={"addressing_style": "virtual"},
    retries={"max_attempts": 3, "mode": "standard"},
)


class BaseStore:
    """
    Parent class for all store modules.
    Holds the S3 client and bucket name.
    Inherit this — get S3 for free.
    """

    def __init__(self, bucket: str, region: str = "us-east-1"):
        # Single S3 client shared across all methods in the subclass.
        # SigV4 forced via _S3_CLIENT_CONFIG (see module docstring).
        self.bucket = bucket
        self.s3 = boto3.client("s3", region_name=region,
                                config=_S3_CLIENT_CONFIG)
        self.region = region

    def _get(self, key: str) -> bytes:
        """Fetch raw bytes from S3. Returns empty bytes if key not found."""
        try:
            resp = self.s3.get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return b""
        except Exception as e:
            log.error(f"S3 get failed [{key}]: {e}")
            return b""

    def _put(self, key: str, body: bytes, content_type: str = "application/json") -> bool:
        """Write bytes to S3. Returns True on success."""
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
            )
            return True
        except Exception as e:
            log.error(f"S3 put failed [{key}]: {e}")
            return False

    def _exists(self, key: str) -> bool:
        """Check if a key exists in S3 without downloading it."""
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # intentional: returns safe default on any error
            return False
