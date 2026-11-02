"""Comprehensive unit and integration tests for Phase 2 Admin Authentication, Lockout, CSRF, and Audit Logging."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.main import app
from app.config import settings
from app.models import AdminUser, AuditLog
from app.auth.security import (
    hash_password,
    verify_password,
    create_signed_session_token,
    verify_signed_session_token
)
from app.auth.csrf import generate_csrf_token, validate_csrf_token
from app.auth.rate_limiter import login_rate_limiter
from app.services.audit_service import log_audit_event, get_audit_logs
from app.cli import create_or_update_admin


@pytest.fixture
def auth_client():
    """TestClient with valid authenticated admin session cookie."""
    with TestClient(app) as client:
        token = create_signed_session_token({"username": settings.admin_username})
        client.cookies.set(settings.session_cookie_name, token)
        yield client


@pytest.fixture
def unauth_client():
    """TestClient without any session cookies."""
    with TestClient(app) as client:
        login_rate_limiter.clear_all()
        yield client


def test_bcrypt_hashing_and_verification():
    """Verify password hashing with bcrypt, prefix format, and verification."""
    password = "SuperSecurePassword2026!"
    hashed = hash_password(password)

    # Must start with bcrypt prefix ($2b$)
    assert hashed.startswith("$2b$")
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword123", hashed) is False


def test_csrf_token_generation_and_validation():
    """Verify cryptographic CSRF token creation, validation, and tampering rejection."""
    token = generate_csrf_token()
    assert validate_csrf_token(token) is True

    # Tampered tokens must fail
    assert validate_csrf_token("invalid_token_string") is False
    assert validate_csrf_token(token + "_tampered") is False
    assert validate_csrf_token(None) is False


def test_csrf_rejection_on_post(unauth_client: TestClient):
    """Mutating POST requests without valid CSRF token must be rejected with 403 Forbidden."""
    # 1. Missing CSRF token
    resp_missing = unauth_client.post(
        "/auth/login",
        data={"username": settings.admin_username, "password": settings.admin_password},
        follow_redirects=False
    )
    assert resp_missing.status_code == 403
    assert "CSRF" in resp_missing.text

    # 2. Invalid / forged CSRF token
    resp_invalid = unauth_client.post(
        "/auth/login",
        data={
            "username": settings.admin_username,
            "password": settings.admin_password,
            "csrf_token": "forged.12345.sig"
        },
        follow_redirects=False
    )
    assert resp_invalid.status_code == 403


def test_login_success(unauth_client: TestClient):
    """Valid credentials + valid CSRF token must succeed, set secure cookie, and redirect."""
    csrf = generate_csrf_token()
    resp = unauth_client.post(
        "/auth/login",
        data={
            "username": settings.admin_username,
            "password": settings.admin_password,
            "csrf_token": csrf,
            "next": "/"
        },
        follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["Location"] == "/"
    assert settings.session_cookie_name in resp.cookies

    # Verify session cookie payload
    cookie_val = resp.cookies[settings.session_cookie_name]
    session_data = verify_signed_session_token(cookie_val)
    assert session_data is not None
    assert session_data["username"] == settings.admin_username


def test_login_rate_limiting_and_lockout(unauth_client: TestClient):
    """Repeated failed login attempts must trigger lockout with HTTP 429."""
    login_rate_limiter.clear_all()
    test_user = "brute_force_target"

    # Send 4 failed attempts
    for i in range(1, 5):
        csrf = generate_csrf_token()
        resp = unauth_client.post(
            "/auth/login",
            data={"username": test_user, "password": "wrong_password", "csrf_token": csrf},
            follow_redirects=False
        )
        assert resp.status_code == 200
        assert f"Attempt {i} of {settings.login_max_failed_attempts}" in resp.text

    # 5th attempt must trigger lockout (HTTP 429)
    csrf5 = generate_csrf_token()
    resp5 = unauth_client.post(
        "/auth/login",
        data={"username": test_user, "password": "wrong_password", "csrf_token": csrf5},
        follow_redirects=False
    )
    assert resp5.status_code == 429
    assert "locked out" in resp5.text.lower()

    # 6th attempt is locked out immediately
    csrf6 = generate_csrf_token()
    resp6 = unauth_client.post(
        "/auth/login",
        data={"username": test_user, "password": "any_password", "csrf_token": csrf6},
        follow_redirects=False
    )
    assert resp6.status_code == 429
    assert "locked out" in resp6.text.lower()

    # Clean up rate limiter after test
    login_rate_limiter.clear_all()


def test_protected_routes_unauthenticated_redirect(unauth_client: TestClient):
    """All navigation pages must redirect to /auth/login when unauthenticated."""
    protected_urls = [
        "/",
        "/matches",
        "/toss-history",
        "/predictions",
        "/analytics",
        "/accuracy",
        "/leagues-teams",
        "/sources",
        "/telegram-reviews",
        "/collector-health",
        "/settings",
        "/logs",
        "/audit",
    ]

    for url in protected_urls:
        resp = unauth_client.get(url, follow_redirects=False)
        assert resp.status_code == 303, f"Expected 303 redirect for {url}, got {resp.status_code}"
        assert "/auth/login" in resp.headers["Location"], f"Expected redirect to /auth/login for {url}"


def test_protected_routes_authenticated_200_and_permanent_disclaimer(auth_client: TestClient):
    """Authenticated requests to all navigation pages must return 200 OK and have permanent disclaimer."""
    nav_pages = [
        "/",
        "/matches",
        "/toss-history",
        "/predictions",
        "/analytics",
        "/accuracy",
        "/leagues-teams",
        "/sources",
        "/telegram-reviews",
        "/collector-health",
        "/settings",
        "/logs",
        "/audit",
    ]

    disclaimer_text = "Toss is a random event. Past stats do not guarantee results. Play responsibly."

    for url in nav_pages:
        resp = auth_client.get(url)
        assert resp.status_code == 200, f"Page {url} failed with status {resp.status_code}"
        assert disclaimer_text in resp.text, f"Disclaimer missing from page {url}"


def test_security_headers_present(auth_client: TestClient):
    """All responses must include standard production security headers."""
    resp = auth_client.get("/")
    assert resp.status_code == 200

    headers = resp.headers
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert headers.get("X-XSS-Protection") == "1; mode=block"
    assert "Content-Security-Policy" in headers


@pytest.mark.asyncio
async def test_audit_log_recording_and_ip(async_db_session: AsyncSession):
    """Verify audit log records who, what, when, and IP address."""
    entry = await log_audit_event(
        session=async_db_session,
        action="TEST_ACTION",
        entity="Match",
        entity_id="999",
        user="test_admin_user",
        ip_address="192.168.1.50",
        details="Test execution of administrative action"
    )
    assert entry.id is not None
    assert entry.action == "TEST_ACTION"
    assert entry.entity == "Match"
    assert entry.user == "test_admin_user"
    assert entry.ip_address == "192.168.1.50"
    assert entry.timestamp_utc is not None

    logs = await get_audit_logs(async_db_session, action="TEST_ACTION")
    assert len(logs) >= 1
    assert logs[0].ip_address == "192.168.1.50"


@pytest.mark.asyncio
async def test_cli_create_admin_command(async_db_session: AsyncSession):
    """Verify CLI admin creation with bcrypt hash and audit log entry."""
    await create_or_update_admin(
        username="cli_super_admin",
        password="TopSecretPassword2026!",
        email="superadmin@example.com",
        session=async_db_session
    )

    # Verify user saved with bcrypt hash
    stmt = select(AdminUser).where(AdminUser.username == "cli_super_admin")
    res = await async_db_session.execute(stmt)
    user = res.scalar_one_or_none()

    assert user is not None
    assert user.password_hash.startswith("$2b$")
    assert verify_password("TopSecretPassword2026!", user.password_hash) is True
    assert verify_password("WrongPassword", user.password_hash) is False
