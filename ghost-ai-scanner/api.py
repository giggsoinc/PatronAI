# =============================================================
# FILE: api.py
# VERSION: 1.0.0
# UPDATED: 2026-06-12
# OWNER: Giggso Inc
# PURPOSE: Standalone REST API for querying Deploy Agents data.
#          Accepts an email address and returns deployment status
#          plus per-package whitelisted tools from S3.
# USAGE:
#   uvicorn api:app --host 0.0.0.0 --port 8002
# ENV:
#   MARAUDER_SCAN_BUCKET  — S3 bucket name (required)
#   AWS_REGION            — AWS region (default: us-east-1)
#   API_KEY               — bearer token for auth (optional but recommended)
# =============================================================

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from fastapi import FastAPI, HTTPException, Depends, Security, Query, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr

from store.agent_store import AgentStore
from blob_index_store import BlobIndexStore  # noqa: E402
from dashboard.ui.reports import r3_user  # noqa: E402
from dashboard.ui.reports._logo import fetch_logo_b64  # noqa: E402
from dashboard.ui.reports._pdf import html_to_pdf  # noqa: E402
from agent_package_store import AgentPackageStore

app = FastAPI(
    title="PatronAI Agent Status API",
    description="Query Deploy Agents deployment status and whitelisted tools by email.",
    version="1.0.0",
)

_bearer = HTTPBearer(auto_error=False)
_API_KEY = os.environ.get("API_KEY", "")


def _auth(creds: HTTPAuthorizationCredentials | None = Security(_bearer)) -> None:
    if not _API_KEY:
        return
    if creds is None or creds.credentials != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _get_store() -> AgentStore:
    bucket = os.environ.get("MARAUDER_SCAN_BUCKET", "")
    if not bucket:
        raise HTTPException(status_code=503, detail="MARAUDER_SCAN_BUCKET not configured")
    region = os.environ.get("AWS_REGION", "us-east-1")
    return AgentStore(bucket, region)


def _blob_store() -> BlobIndexStore:
    bucket = os.environ.get("MARAUDER_SCAN_BUCKET", "")
    region = os.environ.get("AWS_REGION", "us-east-1")
    if not bucket:
        raise HTTPException(status_code=503, detail="Tenant storage not configured")
    return BlobIndexStore(bucket, region)


# ── Request / Response models ─────────────────────────────────

class StatusRequest(BaseModel):
    email: str


class DeploymentRecord(BaseModel):
    name: str
    email: str
    platform: str
    status: str
    created: str
    whitelisted_tools: list[str]


class StatusResponse(BaseModel):
    email: str
    deployments: list[DeploymentRecord]


# ── Endpoint ──────────────────────────────────────────────────

@app.post("/agent/status", response_model=StatusResponse)
def get_agent_status(
    body: StatusRequest,
    _: None = Depends(_auth),
) -> StatusResponse:
    """
    Return deployment status and whitelisted tools for a given email.
    Matches all packages in the catalog where recipient_email equals the
    supplied email (case-insensitive). Returns an empty deployments list
    if no packages are found — 404 only when S3 is unreachable.
    """
    store = _get_store()
    lookup = body.email.strip().lower()

    try:
        catalog = store.refresh_statuses(store.list_catalog())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not read catalog: {exc}") from exc

    matched: list[DeploymentRecord] = []
    for entry in catalog:
        if entry.get("recipient_email", "").strip().lower() != lookup:
            continue

        token = entry.get("token", "")
        try:
            domains = store.get_authorized_domains(token)
        except Exception:
            domains = entry.get("authorized_domains", [])

        matched.append(DeploymentRecord(
            name=entry.get("recipient_name", ""),
            email=entry.get("recipient_email", ""),
            platform=entry.get("os_type", ""),
            status=entry.get("status", "pending"),
            created=entry.get("created_at", ""),
            whitelisted_tools=domains,
        ))

    return StatusResponse(email=body.email, deployments=matched)

 
@app.get("/agent/report")
def user_report(
    email: EmailStr = Query(..., description="Recipient email for the user risk report"),
    d_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD), defaults to 30 days ago"),
    d_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD), defaults to today"),
    format: str = Query("pdf", description="Output format: 'pdf' or 'html'"),
) -> Response:
    """
    Generate and return the User Risk Report (R3) for the given email.
    Supports returning either PDF (default) or HTML.
    """
    if format.lower() not in ("pdf", "html"):
        raise HTTPException(
            status_code=400,
            detail="Invalid format. Supported formats are 'pdf' or 'html'."
        )

    # 1. Parse/default date range
    if not d_from or not d_to:
        today = date.today()
        if not d_from:
            d_from = (today - timedelta(days=30)).isoformat()
        if not d_to:
            d_to = today.isoformat()

    store = _blob_store()

    # 2. Query S3 for findings dates to read only the ones that exist
    try:
        paginator = store.findings.s3.get_paginator("list_objects_v2")
        keys_to_read = []
        for page in paginator.paginate(Bucket=store.bucket, Prefix="findings/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".jsonl"):
                    continue
                parts = key.split("/")
                if len(parts) >= 5:
                    date_str = f"{parts[1]}-{parts[2]}-{parts[3]}"
                    if d_from <= date_str <= d_to:
                        keys_to_read.append(date_str)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list findings from S3: {exc}",
        )

    unique_dates = sorted(list(set(keys_to_read)))

    # 3. Fetch all events
    events = []
    for dt in unique_dates:
        try:
            df = store.findings.read(dt, limit=1000)
            if not df.is_empty():
                events.extend(df.to_dicts())
        except Exception:
            pass

    # 4. Fetch company name and logo
    company = os.environ.get("COMPANY_NAME", "PatronAI")
    logo_b64 = fetch_logo_b64(store.bucket, store.region)

    # 5. Build report HTML
    try:
        html_str = r3_user.build_html(
            events=events,
            d_from=d_from,
            d_to=d_to,
            admin_email=str(email),
            company=company,
            logo_b64=logo_b64,
            target_email=str(email),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to build report HTML: {exc}",
        )

    # 6. Return response based on format
    if format.lower() == "pdf":
        try:
            pdf_bytes = html_to_pdf(html_str)
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": f"attachment; filename=user_report_{email}.pdf"},
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to generate report PDF: {exc}",
            )

    return Response(content=html_str, media_type="text/html")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
