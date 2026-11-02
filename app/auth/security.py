"""Cryptographic helpers, password verification, and session signing."""

import hmac
import hashlib
import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from app.config import settings


def hash_password(password: str) -> str:
    """Hash password securely using PBKDF2 with SHA-256 and salt."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000
    )
    return f"{salt}:{key.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against stored salt:hash string."""
    try:
        salt, expected_hex = hashed_password.split(":")
        computed_key = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000
        )
        return hmac.compare_digest(computed_key.hex(), expected_hex)
    except Exception:
        return False


def verify_admin_credentials(username: str, password: str) -> bool:
    """Verify admin login credentials against application configuration."""
    if not hmac.compare_digest(username.strip(), settings.admin_username.strip()):
        return False
    return hmac.compare_digest(password.strip(), settings.admin_password.strip())


def create_signed_session_token(payload: Dict[str, Any], expires_hours: int = 48) -> str:
    """Create a tamper-proof HMAC signed session token."""
    exp = (datetime.now(timezone.utc) + timedelta(hours=expires_hours)).timestamp()
    token_data = {
        "data": payload,
        "exp": exp,
        "nonce": secrets.token_hex(8)
    }
    json_bytes = json.dumps(token_data, separators=(",", ":")).encode("utf-8")
    b64_payload = base64.urlsafe_b64encode(json_bytes).decode("utf-8").rstrip("=")
    signature = hmac.new(
        settings.secret_key.encode("utf-8"),
        b64_payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return f"{b64_payload}.{signature}"


def verify_signed_session_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify and decode HMAC signed session token. Returns payload or None if invalid/expired."""
    try:
        parts = token.split(".")
        if len(parts) != 2:
            return None
        b64_payload, signature = parts
        expected_sig = hmac.new(
            settings.secret_key.encode("utf-8"),
            b64_payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None

        # Add base64 padding if needed
        padding = 4 - (len(b64_payload) % 4)
        if padding != 4:
            b64_payload += "=" * padding

        raw_json = base64.urlsafe_b64decode(b64_payload.encode("utf-8")).decode("utf-8")
        data = json.loads(raw_json)
        exp = data.get("exp", 0)
        if datetime.now(timezone.utc).timestamp() > exp:
            return None  # Expired
        return data.get("data")
    except Exception:
        return None

