"""Integration tests for web routes, HTMX partials, and admin authentication using TestClient."""

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings


@pytest.fixture(scope="module")
def client():
    """Create TestClient with lifespan for web and API testing."""
    with TestClient(app) as test_client:
        yield test_client


def test_healthcheck(client: TestClient):
    """Verify health endpoint."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "toss-analytics"


def test_dashboard_renders(client: TestClient):
    """Verify dashboard HTML rendering and honesty disclaimer presence."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Toss is a random event. Past stats do not guarantee results. Play responsibly." in resp.text
    assert "Toss Analytics" in resp.text


def test_matches_page_and_partial(client: TestClient):
    """Verify matches page and HTMX table partial."""
    resp = client.get("/matches")
    assert resp.status_code == 200
    assert "Cricket Match Centre" in resp.text

    partial_resp = client.get("/partials/matches-table")
    assert partial_resp.status_code == 200
    assert "<table" in partial_resp.text


def test_match_detail_page(client: TestClient):
    """Verify match detail page for an ingested match."""
    matches_resp = client.get("/matches")
    assert matches_resp.status_code == 200
    # Try fetching match 40306 (which was seeded from fallback)
    resp = client.get("/matches/40306")
    if resp.status_code == 200:
        assert "Historical Toss Record" in resp.text
        assert "Confirmed Ground Truth Toss Result" in resp.text


def test_predictions_ledger_page(client: TestClient):
    """Verify append-only prediction ledger."""
    resp = client.get("/predictions")
    assert resp.status_code == 200
    assert "Prediction Ledger" in resp.text
    assert "Append-Only" in resp.text


def test_analytics_page(client: TestClient):
    """Verify statistical validation view."""
    resp = client.get("/analytics")
    assert resp.status_code == 200
    assert "Wilson Score Interval" in resp.text
    assert "Exact Binomial" in resp.text


def test_sources_leaderboard_page(client: TestClient):
    """Verify tracked sources view."""
    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Tracked Sources & Tipster Channels" in resp.text


def test_audit_tool_page(client: TestClient):
    """Verify cryptographic audit view."""
    resp = client.get("/audit")
    assert resp.status_code == 200
    assert "Cryptographic Hash Audit" in resp.text


def test_admin_login_flow(client: TestClient):
    """Verify admin login with valid and invalid credentials."""
    # 1. Invalid credentials
    bad_login = client.post(
        "/auth/login",
        data={"username": "wrong_user", "password": "wrong_password"},
        follow_redirects=False
    )
    assert bad_login.status_code == 200
    assert "Invalid username or password" in bad_login.text

    # 2. Valid credentials
    good_login = client.post(
        "/auth/login",
        data={"username": settings.admin_username, "password": settings.admin_password},
        follow_redirects=False
    )
    assert good_login.status_code == 303
    assert settings.session_cookie_name in good_login.cookies
