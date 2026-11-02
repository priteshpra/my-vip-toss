"""Tests for Cryptographic Prediction Hashing and Pre-Toss Immutability."""

import pytest
from datetime import datetime, timezone, timedelta
from app.services.prediction_service import generate_prediction_hash


def test_hash_determinism():
    """Exact same inputs must yield identical SHA-256 cryptographic seal."""
    dt = datetime(2026, 11, 2, 10, 0, 0, tzinfo=timezone.utc)
    h1 = generate_prediction_hash("match_100", "team_csk", "PLAY", dt, "v1", "@tipster")
    h2 = generate_prediction_hash("match_100", "team_csk", "PLAY", dt, "v1", "@tipster")
    assert h1 == h2
    assert len(h1) == 64  # Hex-encoded SHA-256 length


def test_hash_avalanche_effect():
    """Altering any parameter (e.g., team ID or timestamp) must completely change the hash."""
    dt = datetime(2026, 11, 2, 10, 0, 0, tzinfo=timezone.utc)
    h_csk = generate_prediction_hash("match_100", "team_csk", "PLAY", dt, "v1", "@tipster")
    h_mi = generate_prediction_hash("match_100", "team_mi", "PLAY", dt, "v1", "@tipster")
    h_skip = generate_prediction_hash("match_100", "team_csk", "SKIP", dt, "v1", "@tipster")

    assert h_csk != h_mi
    assert h_csk != h_skip


def test_hash_different_timestamp():
    """Predictions locked even one second apart must have distinct hashes."""
    dt1 = datetime(2026, 11, 2, 10, 0, 0, tzinfo=timezone.utc)
    dt2 = datetime(2026, 11, 2, 10, 0, 1, tzinfo=timezone.utc)
    h1 = generate_prediction_hash("match_100", "team_csk", "PLAY", dt1, "v1", "@tipster")
    h2 = generate_prediction_hash("match_100", "team_csk", "PLAY", dt2, "v1", "@tipster")
    assert h1 != h2

