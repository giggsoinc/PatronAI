# =============================================================
# FILE: scripts/render_agent_package.py
# VERSION: 1.6.0
# UPDATED: 2026-04-27
# OWNER: Giggso Inc
# PURPOSE: Main entry point for generating an agent delivery package.
#          Orchestrates: OTP generation → template render → S3 upload
#          → presigned URLs → SES email → DMG + EXE build on EC2.
#          Callable from Streamlit or CLI.
# AUDIT LOG:
#   v1.0.0  2026-04-19  Initial — agent delivery system
#   v1.1.0  2026-04-19  S3 prefix agents/ → config/HOOK_AGENTS/; DMG builder
#   v1.2.0  2026-04-19  HEARTBEAT_PUT_URL template placeholder (7-day liveness)
#   v1.3.0  2026-04-20  EC2-side builders — always render sh + ps1, build DMG
#                       and EXE on EC2 via genisoimage / makensis.
#   v1.4.0  2026-04-20  authorized_domains per-user whitelist; SCAN_PUT_URL for
#                       endpoint package/process/browser-history scan results.
#   v1.5.0  2026-04-25  Group 2 — concatenate scan_*.py.frag files into the
#                       INLINE_SCAN_PYTHON placeholder. Templates become thin
#                       orchestrators; scan logic lives in fragments.
#   v1.5.1  2026-04-25  Fix: store.agent.write_url_bundle → store.write_url_bundle.
#   v1.6.0  2026-04-27  Render uninstall_agent.sh/.ps1 and store alongside installer.
# =============================================================

import json
import logging
import os
from pathlib import Path
from typing import Optional

from build_agent_artifacts import _build_macos_dmg, _build_windows_exe
from scan_fragment_loader  import load_scan_fragments

log = logging.getLogger("marauder-scan.render_agent")

HOOK_AGENTS_PREFIX       = "config/HOOK_AGENTS"
TEMPLATE_DIR             = Path(__file__).parent.parent / "agent" / "install"
SH_TEMPLATE              = TEMPLATE_DIR / "setup_agent.sh.template"
PS1_TEMPLATE             = TEMPLATE_DIR / "setup_agent.ps1.template"
UNINSTALL_SH_TEMPLATE    = TEMPLATE_DIR / "uninstall_agent.sh.template"
UNINSTALL_PS1_TEMPLATE   = TEMPLATE_DIR / "uninstall_agent.ps1.template"


def render_agent_package(
    recipient_name: str,
    recipient_email: str,
    os_type: str,
    store,
    renderer,
    send_email: bool = True,
    authorized_domains: list | None = None,
) -> dict:
    """
    Generate a complete agent delivery package.

    Always builds BOTH macOS DMG and Windows EXE on EC2.
    authorized_domains: per-user allowlist baked into the script.
      Scan findings matching these domains/packages are suppressed.
    Returns dict with: token, otp, installer_url, meta_url,
    status_put_url, heartbeat_put_url, scan_put_url,
    dmg_url, exe_url, success, error.
    """
    if os_type not in ("mac", "linux", "windows"):
        return {"success": False, "error": f"Unsupported os_type: {os_type}"}
    if not SH_TEMPLATE.exists():
        return {"success": False, "error": f"Template not found: {SH_TEMPLATE}"}
    if not PS1_TEMPLATE.exists():
        return {"success": False, "error": f"Template not found: {PS1_TEMPLATE}"}

    try:
        otp      = store.generate_otp()
        otp_hash = store.hash_otp(otp)
    except Exception as e:
        log.error("OTP generation failed: %s", e)
        return {"success": False, "error": f"OTP generation failed: {e}"}

    bucket  = store.bucket
    region  = store.region
    company = os.environ.get("COMPANY_NAME", "PatronAI")
    # Authorised domains as comma-separated string baked into the script
    auth_domains_str = ",".join(authorized_domains) if authorized_domains else ""

    try:
        from datetime import datetime, timezone, timedelta
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()

        # Concatenate scan fragments once — same Python is inlined in
        # both bash and PowerShell installer templates.
        inline_scan_python = load_scan_fragments(TEMPLATE_DIR)

        # Step 0 — recipient-side self-test scripts; baked into the installer
        # so a single artifact carries everything the recipient needs.
        diag_sh_path  = TEMPLATE_DIR / "diagnose.sh"
        diag_ps1_path = TEMPLATE_DIR / "diagnose.ps1"
        inline_diagnose_sh  = diag_sh_path.read_text(encoding="utf-8")  if diag_sh_path.exists()  else ""
        inline_diagnose_ps1 = diag_ps1_path.read_text(encoding="utf-8") if diag_ps1_path.exists() else ""

        # ── Pass 1: placeholder render to get token ───────────
        placeholder_ctx = {
            "RECIPIENT_NAME":      recipient_name,
            "RECIPIENT_EMAIL":     recipient_email,
            "BUCKET":              bucket,
            "REGION":              region,
            "COMPANY":             company,
            "TOKEN":               "PENDING",
            "EXPIRES_AT":          expires_at,
            "META_URL":            "PENDING",
            "STATUS_PUT_URL":      "PENDING",
            "HEARTBEAT_PUT_URL":   "PENDING",
            "SCAN_PUT_URL":        "PENDING",
            "AUTHORIZED_GET_URL":  "PENDING",
            "URLS_REFRESH_URL":    "PENDING",
            "AUTHORIZED_DOMAINS":  auth_domains_str,
            "INLINE_SCAN_PYTHON":  inline_scan_python,
            "INLINE_DIAGNOSE_SH":  inline_diagnose_sh,
            "INLINE_DIAGNOSE_PS1": inline_diagnose_ps1,
        }
        pre_sh = renderer.render(str(SH_TEMPLATE), placeholder_ctx)

        # create_package uploads sh, meta.json, status.json, authorized.csv → token
        token = store.create_package(
            recipient_name     = recipient_name,
            recipient_email    = recipient_email,
            os_type            = os_type,
            rendered_script    = pre_sh,
            otp_hash           = otp_hash,
            authorized_domains = authorized_domains or [],
        )
        if not token:
            return {"success": False, "error": "Failed to upload package to S3"}

        urls = store.get_presigned_urls(token, os_type)
        if not urls:
            return {"success": False, "error": "Failed to generate presigned URLs"}

        # Seed the first urls.json bundle so the laptop has refreshable URLs from minute 0.
        store.write_url_bundle(token, os_type)

        # ── Pass 2: re-render both templates with real URLs ───
        final_ctx = {
            "RECIPIENT_NAME":     recipient_name,
            "RECIPIENT_EMAIL":    recipient_email,
            "BUCKET":             bucket,
            "REGION":             region,
            "COMPANY":            company,
            "TOKEN":              token,
            "EXPIRES_AT":         expires_at,
            "META_URL":           urls["meta_url"],
            "STATUS_PUT_URL":     urls["status_put_url"],
            "HEARTBEAT_PUT_URL":  urls.get("heartbeat_put_url", ""),
            "SCAN_PUT_URL":       urls.get("scan_put_url", ""),
            "AUTHORIZED_GET_URL": urls.get("authorized_get_url", ""),
            "URLS_REFRESH_URL":   urls.get("urls_refresh_url", ""),
            "AUTHORIZED_DOMAINS": auth_domains_str,  # fallback if URL unreachable
            "INLINE_SCAN_PYTHON": inline_scan_python,
            "INLINE_DIAGNOSE_SH":  inline_diagnose_sh,
            "INLINE_DIAGNOSE_PS1": inline_diagnose_ps1,
        }
        sh_script  = renderer.render(str(SH_TEMPLATE),  final_ctx)
        ps1_script = renderer.render(str(PS1_TEMPLATE), final_ctx)

        # Overwrite sh; upload ps1 alongside it
        store._put(f"{HOOK_AGENTS_PREFIX}/{token}/setup_agent.sh",
                   sh_script.encode(),  "text/plain")
        store._put(f"{HOOK_AGENTS_PREFIX}/{token}/setup_agent.ps1",
                   ps1_script.encode(), "text/plain")

        # Render and store personalised uninstall scripts (token baked in)
        uninstall_ctx = {
            "RECIPIENT_NAME":  recipient_name,
            "RECIPIENT_EMAIL": recipient_email,
            "COMPANY":         company,
            "TOKEN":           token,
            "EXPIRES_AT":      expires_at,
        }
        if UNINSTALL_SH_TEMPLATE.exists():
            uninstall_sh = renderer.render(str(UNINSTALL_SH_TEMPLATE), uninstall_ctx)
            store._put(f"{HOOK_AGENTS_PREFIX}/{token}/uninstall_agent.sh",
                       uninstall_sh.encode(), "text/plain")
        if UNINSTALL_PS1_TEMPLATE.exists():
            uninstall_ps1 = renderer.render(str(UNINSTALL_PS1_TEMPLATE), uninstall_ctx)
            store._put(f"{HOOK_AGENTS_PREFIX}/{token}/uninstall_agent.ps1",
                       uninstall_ps1.encode(), "text/plain")

    except Exception as e:
        log.error("Package generation failed: %s", e)
        return {"success": False, "error": str(e)}

    # ── EC2-side artifact builds ──────────────────────────────
    dmg_key, exe_key = "", ""
    try:
        dmg_key = _build_macos_dmg(sh_script,  recipient_name, token, store)
    except Exception as e:
        log.error("DMG build failed for token %s: %s", token[:8], e)
    try:
        exe_key = _build_windows_exe(ps1_script, recipient_name, token, store)
    except Exception as e:
        log.error("EXE build failed for token %s: %s", token[:8], e)

    dmg_url = store.get_artifact_url(dmg_key) if dmg_key else ""
    exe_url = store.get_artifact_url(exe_key) if exe_key else ""

    result = {
        "success":            True,
        "token":              token,
        "otp":                otp,
        "installer_url":      urls["installer_url"],
        "meta_url":           urls["meta_url"],
        "status_put_url":     urls["status_put_url"],
        "heartbeat_put_url":  urls.get("heartbeat_put_url", ""),
        "scan_put_url":       urls.get("scan_put_url", ""),
        "authorized_domains": authorized_domains or [],
        "dmg_url":            dmg_url,
        "exe_url":            exe_url,
    }

    if send_email:
        result["email_sent"] = _send_email(
            recipient_name, recipient_email, otp,
            urls["installer_url"], company
        )

    return result


def _send_email(
    recipient_name: str,
    recipient_email: str,
    otp: str,
    installer_url: str,
    company: str,
) -> bool:
    """Thin shim: delegates to notify.email.send_agent_otp.

    Kept as a private function in this module so existing callers
    don't move; all SES logic (sender resolution, region, recipient
    verification, error logging) lives in src/notify/email.py.
    """
    from notify.email import send_agent_otp
    return send_agent_otp(recipient=recipient_email,
                          name=recipient_name,
                          otp=otp,
                          installer_url=installer_url,
                          company=company)
