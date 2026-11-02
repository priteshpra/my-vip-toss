"""Authentication dependencies for FastAPI endpoints and web routes."""

from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, status, Depends
from fastapi.responses import RedirectResponse
from app.auth.security import verify_signed_session_token
from app.config import settings


def get_current_admin_optional(request: Request) -> Optional[Dict[str, Any]]:
    """Inspect request cookie or Authorization header for signed admin session."""
    # 1. Check session cookie
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        # 2. Check Bearer token in Authorization header
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()

    if not token:
        return None

    session_data = verify_signed_session_token(token)
    return session_data


def get_current_admin(
    request: Request,
    admin: Optional[Dict[str, Any]] = Depends(get_current_admin_optional)
) -> Dict[str, Any]:
    """Enforce admin authentication. Returns admin session or raises 401/redirects."""
    if not admin:
        if "text/html" in request.headers.get("Accept", ""):
            # HTML web request: redirect to login page
            raise HTTPException(
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Location": "/auth/login?next=" + request.url.path}
            )
        # API request
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in as administrator."
        )
    return admin

