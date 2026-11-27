"""Unit tests for v7 independent-pillar toss engine and data-sufficiency guards."""

import json
import sqlite3
import pytest

from core.engine import historical_archive, pattern_engine
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

    assert verdict["engine_version"] == 8
    assert verdict["action"] == "SKIP"
    assert verdict["recommended_team"] is None
    assert verdict["pillars_aligned"] < 2


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


def test_historical_archive_merges_multiyear_dedups_and_filters_bad_rows(tmp_path):
    """Multi-year archive merges 2024-2026 records, deduplicates, and drops synthetic/corrupt rows."""
    log_path = tmp_path / "toss_results_log.json"
    archive_path = tmp_path / "historical_toss_archive.json"
    db_path = tmp_path / "toss_analytics.db"

    log_records = [
        {"match_date": "2026-11-20", "tournament": "IPL", "team_a": "MI", "team_b": "CSK", "actual_winner": "MI"},
    ]
    archive_records = [
        {"match_date": "2025-04-10", "tournament": "IPL", "team_a": "MI", "team_b": "CSK", "actual_winner": "CSK"},
        # Duplicate of log_records (same date + pair) should not double-count
        {"match_date": "2026-11-20", "tournament": "IPL", "team_a": "CSK", "team_b": "MI", "actual_winner": "CSK"},
    ]
    log_path.write_text(json.dumps(log_records), encoding="utf-8")
    archive_path.write_text(json.dumps(archive_records), encoding="utf-8")

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE historical_match_statistics ("
        "id TEXT, match_date TEXT, tournament_name TEXT, team_a_name TEXT, team_b_name TEXT, "
        "toss_winner_name TEXT, toss_decision TEXT, match_format TEXT)"
    )
    db_rows = [
        ("r1", "2024-04-01", "IPL", "MI", "CSK", "MI", "Bat", "T20"),
        # Polluted rows that must be rejected:
        ("bad1", "2025-01-02", "league_intl", "team_csk", "team_mi", "team_mi", "Bat", "T20"),
        ("bad2", "2026-10-30", "T20", "India", "India", "India", "Bat", "T20"),
        ("bad3", "2026-10-30", "T20", "A", "B", "Skipped (50-50 Neutral)", "Bat", "T20"),
        ("bad4", "2026-11-01", "Hong Kong Sixes 2026", "India", "Pakistan", "India", "Bat", "T20"),
    ]
    conn.executemany("INSERT INTO historical_match_statistics VALUES (?,?,?,?,?,?,?,?)", db_rows)
    conn.commit()
    conn.close()

    merged = historical_archive.load_merged_toss_records(
        log_file=str(log_path),
        archive_file=str(archive_path),
        db_file=str(db_path),
    )

    # Should contain exactly 3 valid unique matches: 2026-11-20 (from log), 2025-04-10, 2024-04-01
    assert len(merged) == 3
    assert [r["match_date"] for r in merged] == ["2026-11-20", "2025-04-10", "2024-04-01"]
    assert merged[0]["actual_winner"] == "MI"


def test_post_toss_telegram_result_does_not_trigger_pillar2(tmp_path, monkeypatch):
    """Post-toss Telegram WON THE TOSS feed must never inflate pre-toss Pillar 2 consensus."""
    log_path = tmp_path / "toss_results_log.json"
    locked_path = tmp_path / "toss_predictions_locked.json"

    records = [
        {"team_a": "Alpha", "team_b": "Beta", "actual_winner": "Beta"},
        {"team_a": "Alpha", "team_b": "Beta", "actual_winner": "Beta"},
        {"team_a": "Beta", "team_b": "X1", "actual_winner": "Beta"},
        {"team_a": "Beta", "team_b": "X2", "actual_winner": "X2"},
        {"team_a": "Alpha", "team_b": "X3", "actual_winner": "Alpha"},
        {"team_a": "Alpha", "team_b": "X4", "actual_winner": "X4"},
        {"team_a": "Alpha", "team_b": "X5", "actual_winner": "X5"},
    ]
    log_path.write_text(json.dumps(records), encoding="utf-8")
    locked_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pattern_engine, "LOG_FILE", str(log_path))
    monkeypatch.setattr(toss_load_provider, "LOG_FILE", str(log_path))
    monkeypatch.setattr(toss_load_provider, "LOCKED_PREDICTIONS_FILE", str(locked_path))
    monkeypatch.setattr(
        toss_load_provider,
        "get_memoized_tg_feed",
        lambda: [{"winner_raw": "Beta", "decision": "Bowl", "post_iso": "2026-11-24T10:00:00Z"}],
    )

    verdict = toss_load_provider.compute_multi_source_toss_load(
        team_a="Alpha",
        team_b="Beta",
        odds_a=98,
        odds_b=80,
        tournament="Test T20",
        match_time="12:00 PM IST",
        match_id="test_no_tg_result_abuse",
    )

    assert not any("Telegram" in p for p in verdict["active_pillars_list"])


def test_empirical_rule_backtest_returns_structured_rows(tmp_path):
    """Empirical backtest runs chronologically without lookahead and summarizes rule stats."""
    log_path = tmp_path / "toss_results_log.json"
    archive_path = tmp_path / "historical_toss_archive.json"
    db_path = tmp_path / "none.db"

    records = [
        {"match_date": "2026-11-04", "team_a": "A", "team_b": "B", "actual_winner": "A"},
        {"match_date": "2026-11-03", "team_a": "A", "team_b": "B", "actual_winner": "A"},
        {"match_date": "2026-11-02", "team_a": "A", "team_b": "B", "actual_winner": "A"},
    ]
    log_path.write_text(json.dumps(records), encoding="utf-8")
    archive_path.write_text("[]", encoding="utf-8")

    bt = historical_archive.run_empirical_rule_backtest(
        log_file=str(log_path),
        archive_file=str(archive_path),
        db_file=str(db_path),
    )
    assert bt["matches_evaluated"] == 3
    h2h_row = next(r for r in bt["rules"] if r["Rule"] == "H2H_DOMINANCE")
    assert h2h_row["Wins"] == 1
    assert h2h_row["Total"] == 1
    assert h2h_row["Win %"] == 100.0


def test_value_lean_unlocks_on_two_independent_pillars_with_win_pct(tmp_path, monkeypatch):
    """Two genuine independent pillars (Form + Pattern, 63%+) unlock VALUE_LEAN with real Win% stats."""
    log_path = tmp_path / "toss_results_log.json"
    locked_path = tmp_path / "toss_predictions_locked.json"

    # Both teams have >= 5 matches, Beta has 4/5 recent (80% overall) + 3W momentum, Alpha has 2W-3L (40%, 1L)
    records = [
        {"team_a": "Beta", "team_b": "X1", "actual_winner": "Beta"},
        {"team_a": "Beta", "team_b": "X2", "actual_winner": "Beta"},
        {"team_a": "Beta", "team_b": "X3", "actual_winner": "Beta"},
        {"team_a": "Beta", "team_b": "X4", "actual_winner": "X4"},
        {"team_a": "Beta", "team_b": "X5", "actual_winner": "Beta"},
        {"team_a": "Alpha", "team_b": "Y1", "actual_winner": "Y1"},
        {"team_a": "Alpha", "team_b": "Y2", "actual_winner": "Alpha"},
        {"team_a": "Alpha", "team_b": "Y3", "actual_winner": "Y3"},
        {"team_a": "Alpha", "team_b": "Y4", "actual_winner": "Alpha"},
        {"team_a": "Alpha", "team_b": "Y5", "actual_winner": "Y5"},
    ]
    log_path.write_text(json.dumps(records), encoding="utf-8")
    locked_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pattern_engine, "LOG_FILE", str(log_path))
    monkeypatch.setattr(toss_load_provider, "LOG_FILE", str(log_path))
    monkeypatch.setattr(toss_load_provider, "LOCKED_PREDICTIONS_FILE", str(locked_path))

    verdict = toss_load_provider.compute_multi_source_toss_load(
        team_a="Alpha",
        team_b="Beta",
        odds_a=98,
        odds_b=80,
        tournament="Test T20",
        match_time="12:00 PM IST",
        match_id="test_value_lean_2_pillars",
    )

    assert verdict["engine_version"] == 8
    assert verdict["action"] == "PLAY"
    assert verdict["verdict_tier"] == "VALUE_LEAN"
    assert verdict["recommended_team"] == "Beta"
    assert 63 <= verdict["confidence_pct"] < 70
    assert verdict["team_b_win_pct"] == 80.0
    assert verdict["team_a_win_pct"] == 40.0
    assert "80%" in verdict["fav_win_pct_summary"]


def test_fuzzy_match_blocks_a_team_u19_and_state_leakage():
    """Senior teams must never pollute A-team, U19, Women, or State team Win% records."""
    assert toss_load_provider.match_teams_fuzzy("Pakistan", "Pakistan A") is False
    assert toss_load_provider.match_teams_fuzzy("Bangladesh", "Bangladesh A") is False
    assert toss_load_provider.match_teams_fuzzy("India", "India Women") is False
    assert toss_load_provider.match_teams_fuzzy("India", "India Under-19") is False
    assert toss_load_provider.match_teams_fuzzy("Australia", "South Australia") is False
    assert toss_load_provider.match_teams_fuzzy("Kathmandu Gurkhas", "Kathmandu Gorkhas") is True




