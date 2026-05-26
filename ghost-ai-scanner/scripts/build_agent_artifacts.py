# =============================================================
# FILE: scripts/build_agent_artifacts.py
# VERSION: 1.0.0
# UPDATED: 2026-04-20
# OWNER: Giggso Inc
# PURPOSE: EC2-side artifact builders for agent installer packages.
#          _build_macos_dmg  — genisoimage HFS hybrid image (.dmg)
#          _build_windows_exe — makensis silent EXE wrapper (.exe)
#          Both run on Linux EC2. No macOS or Windows host needed.
# AUDIT LOG:
#   v1.0.0  2026-04-20  Initial
# =============================================================

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("marauder-scan.build_artifacts")

HOOK_AGENTS_PREFIX = "config/HOOK_AGENTS"


def _build_macos_dmg(
    sh_script: str,
    recipient_name: str,
    token: str,
    store,
) -> Optional[str]:
    """
    Build a macOS-mountable disk image on Linux.
    Tries genisoimage first; falls back to a plain tar.gz if unavailable.
    Stages a .command file — double-clicking it opens Terminal on macOS.
    Uploads to S3 and returns the S3 key, or None on failure.
    NOTE: For proper macOS compatibility on Sonoma+, consider switching
    to libdmg-hfsplus or a signed .pkg when Apple Developer ID is available.
    """
    firstname = recipient_name.split()[0]
    s3_key    = f"{HOOK_AGENTS_PREFIX}/{token}/PatronAI-Agent-{firstname}.dmg"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir  = Path(tmp)
            staging = tmpdir / "staging"
            staging.mkdir()

            # .command extension → Terminal opens the file on double-click on macOS
            cmd = staging / f"PatronAI-Agent-{firstname}.command"
            cmd.write_text(sh_script, encoding="utf-8")
            cmd.chmod(0o755)

            (staging / "README.txt").write_text(
                f"PatronAI Agent Installer\nRecipient: {recipient_name}\n\n"
                "IMPORTANT - Gatekeeper notice:\n"
                "If macOS shows 'can't be opened because it is from an\n"
                "unidentified developer', right-click the .command file\n"
                "and select 'Open', then click 'Open' in the dialog.\n\n"
                "Alternatively, run in Terminal:\n"
                "  bash /Volumes/PatronAI\\ Agent/PatronAI-Agent-" + firstname + ".command\n\n"
                "Requires: macOS 12+, Python 3\n",
                encoding="utf-8",
            )

            dmg_out = tmpdir / f"PatronAI-Agent-{firstname}.dmg"
            res = subprocess.run(
                [
                    "genisoimage", "-V", "PatronAI Agent",
                    "-D", "-R", "-apple", "-no-pad",
                    "-o", str(dmg_out), str(staging),
                ],
                capture_output=True, text=True, timeout=60,
            )
            if res.returncode != 0:
                log.error("genisoimage failed: %s", res.stderr[:400])
                return None

            store._put(s3_key, dmg_out.read_bytes(), "application/octet-stream")
            log.info("macOS DMG uploaded: %s", s3_key)
            return s3_key

    except Exception as e:
        log.error("_build_macos_dmg failed: %s", e)
        return None


def _build_windows_exe(
    ps1_script: str,
    recipient_name: str,
    token: str,
    store,
) -> Optional[str]:
    """
    Build a silent Windows EXE using NSIS on Linux.
    EXE self-extracts setup_agent.ps1 to %TEMP%\\PatronAI and runs it
    via PowerShell -ExecutionPolicy Bypass. Cleans up on exit.
    Uploads to S3 and returns the S3 key, or None on failure.
    """
    firstname = recipient_name.split()[0]
    exe_name  = f"PatronAI-Agent-{firstname}.exe"
    s3_key    = f"{HOOK_AGENTS_PREFIX}/{token}/{exe_name}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "setup_agent.ps1").write_text(ps1_script, encoding="utf-8")

            nsi_content = (
                f'Name "PatronAI Agent"\n'
                f'OutFile "{exe_name}"\n'
                f'InstallDir "$TEMP\\PatronAI"\n'
                f'RequestExecutionLevel user\n'
                f'SilentInstall normal\n\n'
                f'Section "Install"\n'
                f'  SetOutPath "$INSTDIR"\n'
                f'  File "setup_agent.ps1"\n'
                f'  ExecWait \'powershell.exe -ExecutionPolicy Bypass -NoExit'
                f' -File "$INSTDIR\\setup_agent.ps1"\'\n'
                f'  Delete "$INSTDIR\\setup_agent.ps1"\n'
                f'SectionEnd\n'
            )
            (tmpdir / "installer.nsi").write_text(nsi_content, encoding="utf-8")

            res = subprocess.run(
                ["makensis", str(tmpdir / "installer.nsi")],
                capture_output=True, text=True, timeout=120, cwd=str(tmpdir),
            )
            if res.returncode != 0:
                log.error("makensis failed: %s", res.stderr[:400])
                return None

            store._put(s3_key, (tmpdir / exe_name).read_bytes(), "application/octet-stream")
            log.info("Windows EXE uploaded: %s", s3_key)
            return s3_key

    except Exception as e:
        log.error("_build_windows_exe failed: %s", e)
        return None
