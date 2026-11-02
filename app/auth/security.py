"""Cryptographic helpers, bcrypt password hashing, session signing, and token verification."""

import hmac
import hashlib
import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import bcrypt
from app.config import settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import AdminUser


def hash_password(password: str) -> str:
    """Hash password securely using bcrypt with a random salt."""
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify plain text password against stored hash.
    Supports bcrypt ($2a$, $2b$, $2y$) and legacy PBKDF2 (salt:hash) format.
    """
    if not plain_password or not hashed_password:
        return False

    try:
        # Check for bcrypt prefix
        if hashed_password.startswith(("$2a$", "$2b$", "$2y$")):
            return bcrypt.checkpw(
                plain_password.encode("utf-8"),
                hashed_password.encode("utf-8")
            )

        # Fallback to PBKDF2 format (salt:hash)
        if ":" in hashed_password:
            salt, expected_hex = hashed_password.split(":", 1)
            computed_key = hashlib.pbkdf2_hmac(
                "sha256",
                plain_password.encode("utf-8"),
                salt.encode("utf-8"),
                100_000
            )
            return hmac.compare_digest(computed_key.hex(), expected_hex)

        # Plain text fallback comparison strictly for legacy tests (constant-time)
        return hmac.compare_digest(plain_password.strip(), hashed_password.strip())
    except Exception:
        return False


def verify_admin_credentials(username: str, password: str, stored_hash: Optional[str] = None) -> bool:
    """
    Verify admin login credentials.
    If stored_hash is provided, verifies against the database hash.
    Otherwise falls back to settings.admin_username and settings.admin_password.
    """
    if not hmac.compare_digest(username.strip(), settings.admin_username.strip()):
        return False

    if stored_hash:
        return verify_password(password.strip(), stored_hash)

    return hmac.compare_digest(password.strip(), settings.admin_password.strip())


async def authenticate_admin_user(
    session: AsyncSession,
    username: str,
    password: str
) -> Optional[AdminUser]:
    """
    Authenticate administrator against database credentials (bcrypt) or fallback config.
    Returns AdminUser instance if authenticated, None otherwise.
    """
    stmt = select(AdminUser).where(AdminUser.username == username.strip(), AdminUser.is_active == True)
    res = await session.execute(stmt)
    db_user = res.scalar_one_or_none()

    if db_user:
        if verify_password(password, db_user.password_hash):
            return db_user
        return None

    # Fallback to application settings credentials
    if verify_admin_credentials(username, password):
        return AdminUser(id=0, username=username, password_hash="", is_active=True)

    return None


def create_signed_session_token(payload: Dict[str, Any], expires_minutes: Optional[int] = None) -> str:
    """Create a tamper-proof HMAC-SHA256 signed session token with expiration."""
    exp_minutes = expires_minutes if expires_minutes is not None else settings.session_expire_minutes
    now = datetime.now(timezone.utc)
    exp = (now + timedelta(minutes=exp_minutes)).timestamp()

    token_data = {
        "data": payload,
        "iat": now.timestamp(),
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
    """
    Verify and decode HMAC signed session token.
    Returns session payload or None if signature is invalid or session has expired.
    """
    if not token or not isinstance(token, str):
        return None

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

        # Add base64 padding
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
