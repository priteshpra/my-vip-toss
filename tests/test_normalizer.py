"""Unit tests for RapidFuzz Team & League Normalizer with alias lookup and gender guarding."""

import pytest
from app.services.normalizer import (
    EntityNormalizer,
    clean_text,
    is_women_entity,
    normalizer
)


def test_clean_text_utility():
    """Verify clean_text removes emojis and special characters cleanly."""
    raw = "🇮🇳 Chennai Super Kings (CSK)!! 🏆"
    cleaned = clean_text(raw)
    assert "Chennai Super Kings" in cleaned
    assert "CSK" in cleaned
    assert "🇮🇳" not in cleaned
    assert "🏆" not in cleaned


def test_exact_team_aliases():
    """Verify standard short codes and aliases resolve to canonical teams."""
    assert normalizer.normalize_team("CSK") == "Chennai Super Kings"
    assert normalizer.normalize_team("MI") == "Mumbai Indians"
    assert normalizer.normalize_team("RCB") == "Royal Challengers Bengaluru"
    assert normalizer.normalize_team("KKR") == "Kolkata Knight Riders"
    assert normalizer.normalize_team("IND") == "India"
    assert normalizer.normalize_team("AUS") == "Australia"
    assert normalizer.normalize_team("Blackcaps") == "New Zealand"


def test_fuzzy_team_matching():
    """Verify fuzzy matching resolves typos to canonical team names."""
    assert normalizer.normalize_team("Chenai Super Kngs") == "Chennai Super Kings"
    assert normalizer.normalize_team("Mumbay Indians") == "Mumbai Indians"
    assert normalizer.normalize_team("Perth Scorchrs") == "Perth Scorchers"
    assert normalizer.normalize_team("Kolkata Knight Riderz") == "Kolkata Knight Riders"


def test_gender_guarding():
    """
    Critical guard: Men's and Women's teams must never be falsely matched.
    """
    assert is_women_entity("Sydney Sixers Women") is True
    assert is_women_entity("Sydney Sixers") is False

    # Sixers Women must resolve to Sydney Sixers Women, NOT Sydney Sixers
    assert normalizer.normalize_team("Sydney Sixers Women") == "Sydney Sixers Women"
    assert normalizer.normalize_team("Sydney Sixers") == "Sydney Sixers"

    # Match test between men and women teams must strictly return False
    assert normalizer.match_teams("Sydney Sixers", "Sydney Sixers Women") is False
    assert normalizer.match_teams("Brisbane Heat", "Brisbane Heat Women") is False


def test_league_normalization():
    """Verify resolution of standard leagues: IPL, BBL, WBBL, NPL, ICC ODI, ICC T20I, Emirates D10."""
    assert normalizer.normalize_league("Indian Premier League") == "IPL"
    assert normalizer.normalize_league("TATA IPL 2026") == "IPL"
    assert normalizer.normalize_league("KFC Big Bash League") == "BBL"
    assert normalizer.normalize_league("Women's Big Bash League") == "WBBL"
    assert normalizer.normalize_league("Nepal Premier League") == "NPL"
    assert normalizer.normalize_league("ICC Men's ODI World Cup") == "ICC ODI"
    assert normalizer.normalize_league("ICC Men's T20 World Cup") == "ICC T20I"
    assert normalizer.normalize_league("Emirates D10 Tournament") == "Emirates D10"
    assert normalizer.normalize_league("Random Local Village Cup") == "Local/Unknown"


def test_dynamic_registration():
    """Test dynamically registering custom teams and aliases."""
    custom_norm = EntityNormalizer()
    custom_norm.register_team("Otago Volts", ["Otago", "OV", "Volts"])
    assert custom_norm.normalize_team("OV") == "Otago Volts"
    assert custom_norm.normalize_team("Otago") == "Otago Volts"
    assert custom_norm.normalize_team("Otagoo Vlts") == "Otago Volts"

