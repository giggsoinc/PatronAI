# =============================================================
# FILE: src/store/report_store.py
# VERSION: 1.0.0
# UPDATED: 2026-04-18
# PURPOSE: Write PDF reports to S3 and return pre-signed URLs.
#          Grafana download button calls this via reporter.py.
#          Pre-signed URL valid for 1 hour — no public bucket needed.
# OWNER: Ravi Venugopal, Giggso Inc
# DEPENDS: store.base_store
# =============================================================

import logging
from datetime import date
from typing import Optional
from .base_store import BaseStore

log = logging.getLogger("marauder-scan.report_store")

PRESIGNED_EXPIRY_SECONDS = 3600  # 1 hour


class ReportStore(BaseStore):
    """Write PDF reports to S3. Return pre-signed download URLs."""

    def _key(self, report_date: str) -> str:
        return f"reports/{report_date}-report.pdf"

    def write(self, pdf_bytes: bytes, report_date: Optional[str] = None) -> str:
        """
        Write PDF to S3.
        Returns pre-signed URL valid for 1 hour.
        Returns empty string on failure.
        """
        target = report_date or date.today().isoformat()
        key = self._key(target)

        # Write PDF bytes to S3
        ok = self._put(key, pdf_bytes, "application/pdf")
        if not ok:
            log.error(f"Failed to write report: {key}")
            return ""

        # Generate pre-signed URL — no public access needed
        try:
            url = self.s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=PRESIGNED_EXPIRY_SECONDS,
            )
            log.info(f"Report written: {key} — URL valid {PRESIGNED_EXPIRY_SECONDS}s")
            return url
        except Exception as e:
            log.error(f"Failed to generate pre-signed URL: {e}")
            return ""

    def list_reports(self) -> list:
        """
        List all available reports in S3.
        Returns list of dicts with date and download URL.
        """
        try:
            paginator = self.s3.get_paginator("list_objects_v2")
            reports = []
            for page in paginator.paginate(Bucket=self.bucket, Prefix="reports/"):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if not key.endswith(".pdf"):
                        continue
                    filename = key.split("/")[-1]
                    report_date = filename.replace("-report.pdf", "")
                    try:
                        url = self.s3.generate_presigned_url(
                            "get_object",
                            Params={"Bucket": self.bucket, "Key": key},
                            ExpiresIn=PRESIGNED_EXPIRY_SECONDS,
                        )
                    except Exception:  # intentional: returns safe default on any error
                        url = ""
                    reports.append({
                        "date":          report_date,
                        "key":           key,
                        "size_kb":       round(obj["Size"] / 1024, 1),
                        "last_modified": obj["LastModified"].isoformat(),
                        "download_url":  url,
                    })
            return sorted(reports, key=lambda r: r["date"], reverse=True)
        except Exception as e:
            log.error(f"Failed to list reports: {e}")
            return []
