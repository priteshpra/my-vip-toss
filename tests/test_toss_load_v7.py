"""Unit tests for v7 independent-pillar toss engine and data-sufficiency guards."""

import json
import pytest

from core.engine import pattern_engine
from core.providers import toss_load_provider


def test_single_streak_cannot_triple_count_into_play(tmp_path, monkeypatch):
    """A lone 3-win streak without external/H2H anchor must SKIP, not trigger 3 pillars."""
    log_path = tmp_path / "toss_results_log.json"
    locked_path = tmp_path / "toss_predictions_locked.json"

    # Team B has 3 straight wins; Team A has 4 matches (2W-2L), no H2H, dummy odds
    records = [
        {"team_a": "Alpha", "team_b": "Other1", "actual_winner": "Alpha"},
        {"team_a": "Alpha", "team_b": "Other2", "actual_winner": "Other2"},
        {"team_a": "Alpha", "team_b": "Other3", "actual_winner": "Alpha"},
        {"team_a": "Alpha", "team_b": "Other4", "actual_winner": "Other4"},
        {"team_a": "Beta", "team_b": "Other5", "actual_winner": "Beta"},
        {"team_a": "Beta", "team_b": "Other6", "actual_winner": "Beta"},
        {"team_a": "Beta", "team_b": "Other7", "actual_winner": "Beta"},
    ]
    log_path.write_text(json.dumps(records), encoding="utf-8")
    locked_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pattern_engine, "LOG_FILE", str(log_path))
    monkeypatch.setattr(toss_load_provider, "LOG_FILE", str(log_path))
    monkeypatch.setattr(toss_load_provider, "LOCKED_PREDICTIONS_FILE", str(locked_path))
    monkeypatch.setattr(toss_load_provider, "get_memoized_tg_feed", lambda: [])

    verdict = toss_load_provider.compute_multi_source_toss_load(
        team_a="Alpha",
        team_b="Beta",
        odds_a=98,
        odds_b=80,
        tournament="Test T20",
        match_time="12:00 PM IST",
        match_id="test_triple_count",
    )

    assert verdict["engine_version"] == 7
    assert verdict["action"] == "SKIP"
    assert verdict["recommended_team"] is None
    assert verdict["pillars_aligned"] < 3


def test_insufficient_opponent_history_is_skipped(tmp_path, monkeypatch):
    """When one team has < 3 recorded matches, streak/form must not create a PLAY lock."""
    log_path = tmp_path / "toss_results_log.json"
    locked_path = tmp_path / "toss_predictions_locked.json"

    records = [
        {"team_a": "TeamTwo", "team_b": "X1", "actual_winner": "TeamTwo"},
        {"team_a": "TeamTwo", "team_b": "X2", "actual_winner": "TeamTwo"},
        {"team_a": "TeamTwo", "team_b": "X3", "actual_winner": "TeamTwo"},
        {"team_a": "TeamTwo", "team_b": "X4", "actual_winner": "TeamTwo"},
    ]
    log_path.write_text(json.dumps(records), encoding="utf-8")
    locked_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pattern_engine, "LOG_FILE", str(log_path))
    monkeypatch.setattr(toss_load_provider, "LOG_FILE", str(log_path))
    monkeypatch.setattr(toss_load_provider, "LOCKED_PREDICTIONS_FILE", str(locked_path))
    monkeypatch.setattr(toss_load_provider, "get_memoized_tg_feed", lambda: [])

    verdict = toss_load_provider.compute_multi_source_toss_load(
        team_a="NewTeam",
        team_b="TeamTwo",
        odds_a=98,
        odds_b=80,
        tournament="Test T20",
        match_time="07:30 PM IST",
        match_id="test_missing_history",
    )

    assert verdict["action"] == "SKIP"
    assert verdict["recommended_team"] is None
    assert verdict["pattern_rule_name"] == "BALANCED_ROTATION"


def test_independent_multi_source_pillars_qualify_without_100_pct_claim(tmp_path, monkeypatch):
    """Genuine independent signals (form + market divergence + H2H + pattern) qualify as PLAY without '100% SATIK' wording."""
    log_path = tmp_path / "toss_results_log.json"
    locked_path = tmp_path / "toss_predictions_locked.json"

    # Both teams have >= 5 matches; Beta has strong edge + H2H dominance + winning momentum (3W)
    records = [
        # Reverse-chronological order (newest first)
        {"team_a": "Alpha", "team_b": "Beta", "actual_winner": "Beta"},
        {"team_a": "Beta", "team_b": "Other1", "actual_winner": "Beta"},
        {"team_a": "Alpha", "team_b": "Beta", "actual_winner": "Beta"},
        {"team_a": "Beta", "team_b": "Other2", "actual_winner": "Other2"},
        {"team_a": "Beta", "team_b": "Other3", "actual_winner": "Beta"},
        {"team_a": "Alpha", "team_b": "Other4", "actual_winner": "Alpha"},
        {"team_a": "Alpha", "team_b": "Other5", "actual_winner": "Other5"},
        {"team_a": "Alpha", "team_b": "Other6", "actual_winner": "Other6"},
        {"team_a": "Alpha", "team_b": "Other7", "actual_winner": "Other7"},
        {"team_a": "Alpha", "team_b": "Other8", "actual_winner": "Other8"},
    ]
    log_path.write_text(json.dumps(records), encoding="utf-8")
    locked_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pattern_engine, "LOG_FILE", str(log_path))
    monkeypatch.setattr(toss_load_provider, "LOG_FILE", str(log_path))
    monkeypatch.setattr(toss_load_provider, "LOCKED_PREDICTIONS_FILE", str(locked_path))
    monkeypatch.setattr(toss_load_provider, "get_memoized_tg_feed", lambda: [])

    verdict = toss_load_provider.compute_multi_source_toss_load(
        team_a="Alpha",
        team_b="Beta",
        odds_a=120,
        odds_b=65,
        tournament="Test T20",
        match_time="07:30 PM IST",
        match_id="test_true_multi_source",
    )

    assert verdict["action"] == "PLAY"
    assert verdict["recommended_team"] == "Beta"
    assert verdict["confidence_pct"] >= 70
    assert verdict["pillars_aligned"] >= 3
    assert "100% SATIK" not in verdict["stake_rating"].upper()
    assert "100% SATIK" not in verdict["consensus_badge"].upper()
