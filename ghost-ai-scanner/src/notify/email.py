# =============================================================
# FILE: src/notify/email.py
# VERSION: 1.0.0
# UPDATED: 2026-05-02
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Single home for ALL outbound email. Consolidates three
#          previously-duplicated SES paths:
#
#            scripts/email_utils.py:send_welcome_email
#            scripts/render_agent_package.py:_send_email
#            dashboard/ui/manager_tab_actions.py:send_alert_email
#
#          They each had their own boto3 client, their own sender
#          resolution (one used PATRONAI_FROM_EMAIL, the others
#          SES_SENDER_EMAIL), their own error logging, and only one
#          of three called ensure_recipient_verified. That meant the
#          on-demand action-item email silently failed in SES sandbox
#          to recipients the welcome flow had never seen.
#
#          This module collapses everything to ONE SES call site
#          (send) plus three thin convenience wrappers that compose
#          subject + body for each domain (welcome / OTP / alert).
#
# Public surface:
#    send(recipient, subject, body, *, auto_verify=True)
#    send_welcome(recipient, name, role, added_by, company)
#    send_agent_otp(recipient, name, otp, installer_url, company)
#    send_alert(recipients, events)
#    ensure_verified(recipient, region=None)
#
# DEPENDS: boto3, env vars SES_SENDER_EMAIL / SES_REGION / AWS_REGION /
#          PATRONAI_DASHBOARD_URL / COMPANY_NAME / PATRONAI_FROM_EMAIL.
#          IAM: ses:SendEmail, ses:VerifyEmailIdentity,
#               ses:GetIdentityVerificationAttributes.
# AUDIT LOG:
#   v1.0.0  2026-05-02  Initial. Replaces three duplicated SES paths.
# =============================================================
# raven: loc-exempt — pending refactor into sub-modules (tracked separately)

from __future__ import annotations

import logging
import os
from typing import Iterable, Optional, Sequence

log = logging.getLogger("patronai.notify.email")


# ── Internal helpers ─────────────────────────────────────────────


def _ses_region() -> str:
    """SES region: SES_REGION env var, falling back to AWS_REGION."""
    return (os.environ.get("SES_REGION")
            or os.environ.get("AWS_REGION", "us-east-1"))


def _ses_sender(company: str = "") -> str:
    """Resolve the SES Source address.

    Priority:
      1. SES_SENDER_EMAIL — the canonical setup.sh-managed value.
      2. PATRONAI_FROM_EMAIL — legacy alias kept for backwards compat
         with the old send_alert_email path that used it.
      3. patronai@<company>.com — last-resort fallback that probably
         won't be SES-verified; logs a WARN.
    """
    sender = os.environ.get("SES_SENDER_EMAIL")
    if sender:
        return sender
    legacy = os.environ.get("PATRONAI_FROM_EMAIL")
    if legacy:
        return legacy
    co = company or os.environ.get("COMPANY_NAME", "PatronAI")
    fallback = f"patronai@{co.lower()}.com"
    log.warning("SES_SENDER_EMAIL not set; using fallback %s — verify "
                "this address in SES or set SES_SENDER_EMAIL", fallback)
    return fallback


def _ses_err_code(exc: Exception) -> str:
    """Pull the boto3 ClientError code (MessageRejected,
    MailFromDomainNotVerifiedException, etc.) out of an exception,
    falling back to the exception class name."""
    code = getattr(getattr(exc, "response", {}), "get",
                   lambda *_: {})("Error", {}).get("Code", "")
    return code or type(exc).__name__


# ── Recipient verification ──────────────────────────────────────


def ensure_verified(recipient: str,
                     region: Optional[str] = None) -> dict:
    """Trigger SES identity verification for the recipient.

    SES sandbox mode requires both sender AND recipient to be
    verified. This call is the unblocker: AWS sends the recipient a
    one-click verification link. After they click, sends to them
    succeed (sandbox or production).

    Idempotent — checks current verification status first to avoid
    spamming already-verified addresses.

    Returns a status dict so callers can react:
        {"action": "already_verified" | "verified" | "pending" | "error",
         "status": <SES VerificationStatus or "" if unknown>,
         "recipient": <email>,
         "region": <region>,
         "error": <message if action == "error">}
    """
    import boto3

    region = region or _ses_region()
    addr = (recipient or "").strip()
    if not addr or "@" not in addr:
        return {"action": "error", "recipient": addr, "region": region,
                "status": "", "error": "invalid recipient email"}

    try:
        ses = boto3.client("ses", region_name=region)
        attrs = ses.get_identity_verification_attributes(Identities=[addr])
        status = (attrs.get("VerificationAttributes", {})
                       .get(addr, {})
                       .get("VerificationStatus", ""))
        if status == "Success":
            log.debug("ensure_verified: %s already verified", addr)
            return {"action": "already_verified", "status": status,
                    "recipient": addr, "region": region}
        ses.verify_email_identity(EmailAddress=addr)
        log.info("ensure_verified: triggered verification for %s "
                 "(was status=%s)", addr, status or "unknown")
        return {"action": "pending" if status == "Pending" else "verified",
                "status": status or "Pending",
                "recipient": addr, "region": region}
    except Exception as exc:
        err = _ses_err_code(exc)
        log.warning("ensure_verified failed for %s — region=%s "
                    "code=%s message=%s", addr, region, err, exc)
        return {"action": "error", "status": "", "recipient": addr,
                "region": region, "error": f"{err}: {exc}"}


# ── Generic send (the one SES call site) ────────────────────────


def send(recipient,
         subject: str,
         body: str,
         *,
         company: str = "",
         auto_verify: bool = True) -> bool:
    """Send a plain-text email via SES. Single SES call site for the
    whole codebase.

    Args:
        recipient:    A single address (str) or an iterable of addresses
                      (list/tuple of str).
        subject:      Subject line.
        body:         Plain-text body.
        company:      Optional company name; influences sender fallback.
        auto_verify:  If True (default), call ensure_verified() for each
                      recipient before sending. Skip with False for
                      paths that explicitly know recipient is already
                      verified or where verification cost is unwanted.

    Returns:
        True on SES send success, False on any SES / config error.
        Verification side-effect failure does NOT change the return.
    """
    import boto3

    region = _ses_region()
    sender = _ses_sender(company)

    if isinstance(recipient, str):
        recipients: list = [recipient]
    else:
        recipients = [r.strip() for r in recipient if r and str(r).strip()]
    if not recipients:
        log.error("send: no recipients")
        return False

    if auto_verify:
        for addr in recipients:
            ensure_verified(addr, region=region)

    try:
        ses = boto3.client("ses", region_name=region)
        ses.send_email(
            Source=sender,
            Destination={"ToAddresses": recipients},
            Message={
                "Subject": {"Data": subject},
                "Body":    {"Text": {"Data": body}},
            },
        )
        log.info("notify.email.send → %s (subject=%r, sender=%s, "
                 "region=%s)", recipients, subject[:60], sender, region)
        return True
    except Exception as exc:
        log.error("notify.email.send failed → %s — sender=%s region=%s "
                  "code=%s message=%s", recipients, sender, region,
                  _ses_err_code(exc), exc)
        return False


# ── Convenience wrappers — one per business domain ──────────────


def send_welcome(recipient: str, name: str, role: str,
                  added_by: str, company: str = "") -> bool:
    """Welcome / onboarding email when an admin adds a user.

    Body explains role + dashboard URL + warns about the separate
    AWS verification email the recipient may receive.
    """
    company  = company or os.environ.get("COMPANY_NAME", "PatronAI")
    dash_url = os.environ.get("PATRONAI_DASHBOARD_URL",
                                "https://your-patronai-dashboard")
    subject = f"Welcome to PatronAI — {company}"
    body = (
        f"Hi {name},\n\n"
        f"You have been added to the PatronAI security dashboard "
        f"for {company}.\n\n"
        f"  Role:      {role}\n"
        f"  Added by:  {added_by}\n\n"
        f"Log in here:\n"
        f"  {dash_url}\n\n"
        f"PatronAI monitors AI tool usage across your organisation "
        f"and surfaces security findings for your team.\n\n"
        f"You may also receive a separate email from "
        f"'no-reply-aws@amazon.com' asking you to verify your address "
        f"with AWS. Click the link in that email — it lets PatronAI "
        f"send you future notifications (agent installers, alerts, "
        f"reports) without manual intervention.\n\n"
        f"If you have questions, reply to this email or contact "
        f"your administrator ({added_by}).\n\n"
        f"— PatronAI · {company}\n"
    )
    return send(recipient, subject, body, company=company)


def send_agent_otp(recipient: str, name: str, otp: str,
                    installer_url: str, company: str = "") -> bool:
    """Agent-installer OTP + download link, sent when an admin
    generates a deploy package."""
    company = company or os.environ.get("COMPANY_NAME", "PatronAI")
    subject = "PatronAI Agent — Your Installation Package"
    body = (
        f"Hi {name},\n\n"
        f"Your PatronAI agent installer is ready.\n\n"
        f"Download link (expires in 48 hours):\n{installer_url}\n\n"
        f"Your one-time installation code:\n\n"
        f"    {otp}\n\n"
        f"To install:\n"
        f"  Mac/Linux: bash setup_agent.sh\n"
        f"  Windows:   powershell -ExecutionPolicy Bypass -File setup_agent.ps1\n\n"
        f"Enter the 6-digit code when prompted. It is single-use and "
        f"expires in 48 hours.\n\n"
        f"Your IT admin can also provide a one-click DMG (Mac) or EXE "
        f"(Windows).\n\n"
        f"Questions? Contact your IT administrator.\n\n"
        f"— PatronAI · {company}\n"
    )
    return send(recipient, subject, body, company=company)


def send_alert(recipients,
                events: Sequence[dict]) -> bool:
    """On-demand action-item alert. Bulleted summary of N selected
    findings sent to one or more recipients (typically ALERT_RECIPIENTS).

    Time formatting deliberately deferred to dashboard.ui.time_fmt so
    every email shows timestamps in the operator's local zone.
    """
    if not events:
        log.warning("send_alert: empty events list, nothing to send")
        return False

    # Build the body. Imports here so a missing dashboard package
    # (e.g. when notify.email is used from a CLI script) doesn't crash.
    try:
        from time_fmt import fmt as _fmt_time  # type: ignore
    except Exception:  # pragma: no cover — fall back to raw timestamp  # intentional: returns safe default on any error
        def _fmt_time(x):  # type: ignore
            return x or ""

    n = len(events)
    body_lines = [f"PatronAI Alert — {n} event(s) require attention\n"]
    for e in events[:10]:
        body_lines.append(
            f"  [{e.get('severity','?')}] {e.get('provider','?')} | "
            f"{e.get('owner','unknown')} | {_fmt_time(e.get('timestamp'))}"
        )
    if n > 10:
        body_lines.append(f"  … and {n - 10} more.")
    body = "\n".join(body_lines)

    if isinstance(recipients, str):
        # Accept comma-separated string for backwards compat.
        recipients = [r.strip() for r in recipients.split(",") if r.strip()]
    return send(list(recipients), f"PatronAI Alert — {n} event(s)", body)
