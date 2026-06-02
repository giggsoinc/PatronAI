# =============================================================
# FILE: src/store/_agent_otp.py
# VERSION: 1.0.0
# UPDATED: 2026-06-01
# OWNER: Giggso Inc (Ravi Venugopal)
# PURPOSE: Extracted from agent_store.py — OTP generation and
#          bcrypt validation helpers. Stateless standalone functions.
# DEPENDS: secrets, bcrypt
# =============================================================
from __future__ import annotations

import secrets

import bcrypt


def generate_otp() -> str:
    """Return a cryptographically secure 6-digit OTP string."""
    return str(secrets.randbelow(900000) + 100000)


def hash_otp(otp: str) -> str:
    """Return bcrypt hash of otp (rounds=12). Store the hash, not the OTP."""
    return bcrypt.hashpw(otp.encode(), bcrypt.gensalt(rounds=12)).decode()


def check_otp(otp: str, hashed: str) -> bool:
    """Validate OTP against stored bcrypt hash."""
    try:
        return bcrypt.checkpw(otp.encode(), hashed.encode())
    except Exception:  # intentional: returns safe default on any error
        return False
