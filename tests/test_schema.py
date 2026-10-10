"""Unit tests for SQLite schema, foreign keys, and integrity constraints."""

import pytest
import sqlite3


def test_tables_and_pragmas(test_conn):
    # Verify Foreign Keys are strictly enabled
    cur = test_conn.cursor()
    cur.execute("PRAGMA foreign_keys")
    assert cur.fetchone()[0] == 1

    # Verify key tables exist
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    expected = {
        "data_sources", "source_update_logs", "tournaments", "venues", "teams",
        "matches", "toss_results", "historical_match_statistics",
        "market_snapshots", "model_versions", "prediction_snapshots",
        "prediction_evaluations", "telegram_channel_records"
    }
    assert expected.issubset(tables)


def test_foreign_key_enforcement(test_conn):
    cur = test_conn.cursor()
    # Attempt inserting a match referencing non-existent tournament
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("""
            INSERT INTO matches (
                id, tournament_id, team_a_id, team_b_id, match_format,
                scheduled_start_ist, status, data_source_id, last_updated_ist
            ) VALUES (
                'match_invalid', 'non_existent_tour', 'team_csk', 'team_mi',
                'T20', '2026-10-10 19:30:00', 'UPCOMING', 'cricket_data_org', '2026-10-10 12:00:00'
            )
        """)


def test_unique_constraint_prediction_snapshots(test_conn):
    cur = test_conn.cursor()
    # Inserting first snapshot
    cur.execute("""
        INSERT INTO prediction_snapshots (
            match_id, model_version_id, assessment_status, snapshot_timestamp_ist
        ) VALUES (
            'match_20261010_csk_mi', 'v1.0_bernoulli_baseline', 'NO_VALIDATED_ADVANTAGE', '2026-10-10 18:00:00+05:30'
        )
    """)

    # Inserting duplicate snapshot with same match_id, model, and timestamp must fail
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("""
            INSERT INTO prediction_snapshots (
                match_id, model_version_id, assessment_status, snapshot_timestamp_ist
            ) VALUES (
                'match_20261010_csk_mi', 'v1.0_bernoulli_baseline', 'PRE_TOSS_ESTIMATE_AVAILABLE', '2026-10-10 18:00:00+05:30'
            )
        """)
