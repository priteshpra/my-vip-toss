"""CSRF protection utilities, token generation, and request verification."""

import hmac
import hashlib
import time
import secrets
from typing import Optional
from fastapi import Request, HTTPException, status
from app.config import settings


def generate_csrf_token() -> str:
    """
    Generate a cryptographically signed CSRF token.
    Format: nonce.timestamp.signature
    """
    now = int(time.time())
    nonce = secrets.token_hex(16)
    message = f"{nonce}:{now}"
    signature = hmac.new(
        settings.secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return f"{nonce}.{now}.{signature}"


def validate_csrf_token(token: Optional[str], max_age_seconds: int = 7200) -> bool:
    """
    Validate HMAC signature and age of a CSRF token.
    Returns True if valid and not expired, False otherwise.
    """
    if not token or not isinstance(token, str):
        return False

    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False

        nonce, ts_str, signature = parts
        timestamp = int(ts_str)

        # Check token expiration
        now = int(time.time())
        if now - timestamp > max_age_seconds or timestamp > now + 300:
            return False

        # Re-compute expected signature
        message = f"{nonce}:{timestamp}"
        expected_sig = hmac.new(
            settings.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(signature, expected_sig)
    except Exception:
        return False


async def verify_csrf(request: Request) -> None:
    """
    FastAPI dependency / check that enforces CSRF token validation on mutating HTTP methods.
    Exempts programmatic API requests presenting Bearer tokens.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return

    # Programmatic API requests with Bearer tokens are immune to browser CSRF
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return

    # Check header first (e.g. HTMX or AJAX)
    token = request.headers.get("X-CSRF-Token")

    # If not in header, check form body if content-type is form data
    if not token:
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            try:
                form = await request.form()
                token = form.get("csrf_token")
            except Exception:
                token = None

    if not token or not validate_csrf_token(str(token)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed. Request rejected."
        )

