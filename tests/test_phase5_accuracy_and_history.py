"""Comprehensive unit and integration test suite for Phase 5:
- Lean generation logic (honest NO EDGE default vs statistically significant edge)
- Pre-toss prediction locking with SHA-256 cryptographic seal
- Strict immutability guards (preventing UPDATE and DELETE on Prediction via ReadOnlyError)
- Blocking post-event prediction creation (after match scheduled start or after toss completion)
- Comprehensive audit logging for all prediction and manual call events
- Brier score and probability calibration table calculations
- Streaks calculation (max win, max loss, current streak)
- Rolling window hit-rate calculations (30 and 100 matches)
- Toss History persistence, filtering, and CSV export
- Totals Dashboard aggregation (macro KPIs, per league, per team, timeframe)
- Accuracy / Truth Dashboard web view with honesty banner and 50% baseline comparison
"""

import io
import csv
import json
import pytest
from datetime import datetime, date, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.testclient import TestClient

from app.models import (
    Match, Team, League, Venue, TossResult,
    Prediction, PredictionOutcome, AuditLog, ReadOnlyError
)
from app.services.prediction_service import (
    generate_lean_from_analysis,
    lock_match_prediction,
    generate_prediction_hash,
    verify_prediction_integrity
)
from app.services.accuracy_service import (
    calculate_brier_score,
    calculate_calibration_table,
    calculate_streaks_from_results,
    calculate_rolling_hit_rates,
    get_accuracy_dashboard_data
)
from app.services.toss_service import (
    get_toss_history,
    export_toss_history_csv,
    record_toss_result
)
from app.services.totals_service import (
    get_totals_overview,
    get_totals_by_league,
    get_totals_by_team,
    get_totals_by_timeframe
)
from app.schemas import TossResultCreate
from app.config import settings
from app.auth.security import create_signed_session_token
from app.db import utc_now


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
async def setup_test_match_and_teams(session: AsyncSession, hours_from_now: float = 2.0) -> Match:
    """Helper to set up a test match fixture with teams and league."""
    # Check or create league
    res_l = await session.execute(select(League).where(League.name == "Test Premier League"))
    league = res_l.scalar_one_or_none()
    if not league:
        league = League(name="Test Premier League", format="T20")
        session.add(league)

    # Check or create teams
    res_a = await session.execute(select(Team).where(Team.name == "Alpha Kings"))
    team_a = res_a.scalar_one_or_none()
    if not team_a:
        team_a = Team(name="Alpha Kings", short_code="AK")
        session.add(team_a)

    res_b = await session.execute(select(Team).where(Team.name == "Beta Warriors"))
    team_b = res_b.scalar_one_or_none()
    if not team_b:
        team_b = Team(name="Beta Warriors", short_code="BW")
        session.add(team_b)

    await session.commit()
    await session.refresh(league)
    await session.refresh(team_a)
    await session.refresh(team_b)

    start_time = utc_now() + timedelta(hours=hours_from_now)
    match = Match(
        league_id=league.id,
        team_a_id=team_a.id,
        team_b_id=team_b.id,
        start_time_utc=start_time,
        status="upcoming"
    )
    session.add(match)
    await session.commit()
    await session.refresh(match)
    return match


# --------------------------------------------------------------------------
# 1. Lean Generation Logic Tests (Honest Default vs Significant Edge)
# --------------------------------------------------------------------------
def test_generate_lean_honest_no_edge_default():
    """Verify that inconclusive data or 50% baseline null test results in NO EDGE - skip."""
    # Inconclusive analysis where CI includes 0.50
    mock_analysis = {
        "team_a_name": "Alpha Kings",
        "team_b_name": "Beta Warriors",
        "h2h_stats": {
            "stat_a": {"n": 20, "win_pct": 50.0, "ci_low": 0.30, "ci_high": 0.70, "not_different_from_50": True},
            "stat_b": {"n": 20, "win_pct": 50.0, "ci_low": 0.30, "ci_high": 0.70, "not_different_from_50": True}
        },
        "stats_a": {
            "overall": {"n": 40, "win_pct": 52.5, "ci_low": 0.37, "ci_high": 0.67, "not_different_from_50": True},
            "by_format": {"T20": {"n": 30, "win_pct": 53.3, "ci_low": 0.36, "ci_high": 0.70, "not_different_from_50": True}}
        },
        "stats_b": {
            "overall": {"n": 38, "win_pct": 47.4, "ci_low": 0.32, "ci_high": 0.63, "not_different_from_50": True},
            "by_format": {"T20": {"n": 28, "win_pct": 46.4, "ci_low": 0.29, "ci_high": 0.65, "not_different_from_50": True}}
        }
    }

    lean_result = generate_lean_from_analysis(mock_analysis)
    assert lean_result["lean"] == "NO_EDGE"
    assert lean_result["action_type"] == "SKIP"
    assert lean_result["probability"] == 0.50
    assert lean_result["ci_low"] == 0.50
    assert lean_result["ci_high"] == 0.50
    assert lean_result["has_edge"] is False
    assert "NO EDGE - skip" in lean_result["rationale"]


def test_generate_lean_statistically_significant_edge():
    """Verify that a large sample with CI strictly above 50% produces an actionable PLAY lean."""
    # Substantial sample n = 100 with 68% win rate: CI strictly > 0.50
    mock_analysis = {
        "team_a_name": "Alpha Kings",
        "team_b_name": "Beta Warriors",
        "h2h_stats": {
            "stat_a": {"n": 50, "win_pct": 68.0, "ci_low": 0.54, "ci_high": 0.79, "not_different_from_50": False},
            "stat_b": {"n": 50, "win_pct": 32.0, "ci_low": 0.21, "ci_high": 0.46, "not_different_from_50": False}
        },
        "stats_a": {
            "overall": {"n": 100, "win_pct": 62.0, "ci_low": 0.52, "ci_high": 0.71, "not_different_from_50": False},
            "by_format": {"T20": {"n": 80, "win_pct": 60.0, "ci_low": 0.49, "ci_high": 0.70, "not_different_from_50": True}}
        },
        "stats_b": {
            "overall": {"n": 90, "win_pct": 40.0, "ci_low": 0.30, "ci_high": 0.51, "not_different_from_50": True},
            "by_format": {}
        }
    }

    lean_result = generate_lean_from_analysis(mock_analysis)
    assert lean_result["lean"] == "TeamA"
    assert lean_result["action_type"] == "PLAY"
    assert lean_result["predicted_team_name"] == "Alpha Kings"
    assert lean_result["probability"] > 0.50
    assert lean_result["ci_low"] > 0.50
    assert lean_result["has_edge"] is True


# --------------------------------------------------------------------------
# 2. Prediction Locking & SHA-256 Hashing Tests
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_lock_match_prediction_pre_match(async_db_session: AsyncSession):
    """Verify pre-match prediction locking generates SHA-256 seal and audit log."""
    match = await setup_test_match_and_teams(async_db_session, hours_from_now=3.0)

    pred = await lock_match_prediction(
        session=async_db_session,
        match_id=match.id,
        lean="NO_EDGE",
        probability=0.50,
        ci_low=0.50,
        ci_high=0.50,
        source="manual",
        admin_user="test_admin",
        client_ip="192.168.1.10",
        punter_rationale="Inconclusive toss history",
        strict=True
    )

    assert pred.id is not None
    assert pred.match_id == match.id
    assert pred.lean == "NO_EDGE"
    assert len(pred.hash) == 64  # SHA-256 length
    assert pred.is_pre_toss is True

    # Outcome should be auto-registered as SKIPPED
    outcome_q = await async_db_session.execute(
        select(PredictionOutcome).where(PredictionOutcome.prediction_id == pred.id)
    )
    outcome = outcome_q.scalar_one_or_none()
    assert outcome is not None
    assert outcome.result == "SKIPPED"

    # Verify AuditLog entry
    audit_q = await async_db_session.execute(
        select(AuditLog).where(AuditLog.entity_id == str(pred.id))
    )
    audit = audit_q.scalar_one_or_none()
    assert audit is not None
    assert audit.user == "test_admin"
    assert audit.ip_address == "192.168.1.10"
    assert audit.action == "MANUAL_CALL_LOCKED"


# --------------------------------------------------------------------------
# 3. Immutability Tests (Attempted Edit/Delete Must Fail)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_prediction_immutability_enforcement(async_db_session: AsyncSession):
    """Verify that predictions cannot be updated or deleted retrospectively."""
    match1 = await setup_test_match_and_teams(async_db_session, hours_from_now=4.0)
    pred1 = await lock_match_prediction(
        session=async_db_session,
        match_id=match1.id,
        lean="NO_EDGE",
        probability=0.50,
        source="own",
        strict=True
    )

    # Attempted update must raise ReadOnlyError
    pred1.lean = "TeamA"
    with pytest.raises(ReadOnlyError) as exc_info:
        await async_db_session.commit()
    assert "append-only" in str(exc_info.value).lower()

    await async_db_session.rollback()

    match2 = await setup_test_match_and_teams(async_db_session, hours_from_now=5.0)
    pred2 = await lock_match_prediction(
        session=async_db_session,
        match_id=match2.id,
        lean="TeamB",
        probability=0.55,
        source="own",
        strict=True
    )

    # Attempted delete must raise ReadOnlyError
    with pytest.raises(ReadOnlyError) as exc_info:
        await async_db_session.delete(pred2)
        await async_db_session.commit()
    assert "append-only" in str(exc_info.value).lower()

    await async_db_session.rollback()


# --------------------------------------------------------------------------
# 4. Blocking Post-Event Prediction Creation Tests
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_block_prediction_creation_after_match_start(async_db_session: AsyncSession):
    """Attempting to create/lock a prediction after scheduled match start must fail."""
    match = await setup_test_match_and_teams(async_db_session, hours_from_now=-1.0)  # Started 1 hour ago

    with pytest.raises(ValueError) as exc_info:
        await lock_match_prediction(
            session=async_db_session,
            match_id=match.id,
            lean="TeamA",
            strict=True
        )

    assert "scheduled start time has already elapsed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_block_prediction_creation_after_toss_done(async_db_session: AsyncSession):
    """Attempting to create/lock a prediction when match status is toss_done must fail."""
    match = await setup_test_match_and_teams(async_db_session, hours_from_now=2.0)
    match.status = "toss_done"
    await async_db_session.commit()

    with pytest.raises(ValueError) as exc_info:
        await lock_match_prediction(
            session=async_db_session,
            match_id=match.id,
            lean="TeamA",
            strict=True
        )

    assert "toss result has already been recorded" in str(exc_info.value)


# --------------------------------------------------------------------------
# 5. Brier Score & Probability Calibration Tests
# --------------------------------------------------------------------------
def test_brier_score_calibration_benchmarks():
    """Verify Brier score on benchmark probability-outcome pairs."""
    # Perfect forecast: predicted 1.0 on hit, 0.0 on miss -> Brier score = 0.0
    perfect_pairs = [(1.0, 1), (0.0, 0)]
    assert calculate_brier_score(perfect_pairs) == 0.0

    # Completely wrong forecast: predicted 1.0 on miss, 0.0 on hit -> Brier score = 1.0
    wrong_pairs = [(1.0, 0), (0.0, 1)]
    assert calculate_brier_score(wrong_pairs) == 1.0

    # Constant fair coin guess (predicting 0.50) -> Brier score = 0.250
    neutral_pairs = [(0.50, 1), (0.50, 0), (0.50, 1), (0.50, 0)]
    assert calculate_brier_score(neutral_pairs) == 0.25


def test_calibration_table_structure():
    """Verify calibration table partitions probability buckets correctly."""
    pairs = [
        (0.52, 1), (0.53, 0),  # 50-54%
        (0.57, 1), (0.58, 1), (0.59, 0),  # 55-59%
        (0.62, 1), (0.64, 1),  # 60-64%
        (0.72, 1)  # 70%+
    ]
    table = calculate_calibration_table(pairs)

    assert len(table) == 5
    bucket_50 = next(b for b in table if "50%" in b["bucket"])
    assert bucket_50["count"] == 2
    assert bucket_50["actual_win_rate_pct"] == 50.0

    bucket_55 = next(b for b in table if "55%" in b["bucket"])
    assert bucket_55["count"] == 3
    assert bucket_55["actual_win_rate_pct"] == 66.7

    bucket_70 = next(b for b in table if "70%" in b["bucket"])
    assert bucket_70["count"] == 1
    assert bucket_70["actual_win_rate_pct"] == 100.0


# --------------------------------------------------------------------------
# 6. Streaks Calculation Tests
# --------------------------------------------------------------------------
def test_streaks_calculation_known_sequence():
    """Verify max win streak, max loss streak, and current streak."""
    # Sequence: PASS, PASS, PASS, FAIL, FAIL, PASS, PASS
    seq = ["PASS", "PASS", "PASS", "FAIL", "FAIL", "PASS", "PASS"]
    streaks = calculate_streaks_from_results(seq)

    assert streaks["max_win_streak"] == 3
    assert streaks["max_loss_streak"] == 2
    assert streaks["current_streak_count"] == 2
    assert streaks["current_streak_type"] == "PASS"
    assert streaks["current_streak_display"] == "+2 (PASS)"

    # Sequence ending in losses
    seq_loss = ["PASS", "PASS", "FAIL", "FAIL", "FAIL", "FAIL"]
    streaks_loss = calculate_streaks_from_results(seq_loss)
    assert streaks_loss["max_win_streak"] == 2
    assert streaks_loss["max_loss_streak"] == 4
    assert streaks_loss["current_streak_count"] == 4
    assert streaks_loss["current_streak_type"] == "FAIL"
    assert streaks_loss["current_streak_display"] == "-4 (FAIL)"


# --------------------------------------------------------------------------
# 7. Rolling Hit-Rate Calculations
# --------------------------------------------------------------------------
def test_rolling_hit_rate_windows():
    """Verify rolling 30-match calculation."""
    # 40 matches: first 20 PASS, next 20 FAIL
    dated_outcomes = []
    base_date = date(2025, 1, 1)
    for i in range(40):
        res = "PASS" if i < 20 else "FAIL"
        dated_outcomes.append((base_date + timedelta(days=i), res))

    rolling = calculate_rolling_hit_rates(dated_outcomes, windows=[30])
    r30 = rolling["rolling_30"]

    # 40 items with window 30 -> 11 rolling points
    assert len(r30) == 11
    # Point at index 30 has 20 PASS and 10 FAIL -> 20/30 = 66.7%
    assert r30[0]["hit_rate"] == 66.7
    # Last point has 10 PASS and 20 FAIL -> 10/30 = 33.3%
    assert r30[-1]["hit_rate"] == 33.3


# --------------------------------------------------------------------------
# 8. Toss History Persistence, Filtering, & CSV Export
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_toss_history_filtering_and_csv(async_db_session: AsyncSession):
    """Verify toss history records, query filters, and CSV export."""
    match = await setup_test_match_and_teams(async_db_session, hours_from_now=1.0)

    # 1. Lock a prediction
    pred = await lock_match_prediction(
        session=async_db_session,
        match_id=match.id,
        lean="TeamA",
        predicted_team_id=match.team_a_id,
        probability=0.60,
        source="own",
        strict=True
    )

    # 2. Record toss outcome (Team A won and bat)
    toss = await record_toss_result(
        session=async_db_session,
        data=TossResultCreate(
            match_id=match.id,
            toss_winner_id=match.team_a_id,
            toss_decision="BAT",
            official_source_url="https://referee.test/toss"
        ),
        source_provider_id="telegram"
    )

    async_db_session.expire_all()

    # 3. Retrieve history
    history = await get_toss_history(async_db_session, outcome_filter="PASS")
    assert history["total_count"] >= 1
    item = next((it for it in history["items"] if it["match_id"] == match.id), None)
    assert item is not None
    assert item["toss_winner_name"] == "Alpha Kings"
    assert item["toss_decision"] == "BAT"
    assert item["outcome_result"] == "PASS"
    assert item["toss_source"] == "telegram"
    assert item["prediction_lean"] == "TeamA"

    # 4. Generate CSV export
    csv_str = export_toss_history_csv(history["items"])
    assert "CRICKET TOSS ANALYTICS - AUDITED TOSS HISTORY" in csv_str
    assert "Toss is a random event." in csv_str
    assert "Alpha Kings" in csv_str
    assert "BAT" in csv_str

    # Parse CSV structure
    rows = list(csv.reader(io.StringIO(csv_str)))
    assert len(rows) > 4


# --------------------------------------------------------------------------
# 9. Totals Dashboard Aggregation Tests
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_totals_service_aggregations(async_db_session: AsyncSession):
    """Verify macro totals overview, league breakdown, and team statistics."""
    match = await setup_test_match_and_teams(async_db_session, hours_from_now=1.0)
    await record_toss_result(
        session=async_db_session,
        data=TossResultCreate(
            match_id=match.id,
            toss_winner_id=match.team_a_id,
            toss_decision="FIELD"
        ),
        source_provider_id="manual"
    )

    overview = await get_totals_overview(async_db_session)
    assert overview["total_matches"] >= 1
    assert overview["total_tosses"] >= 1
    assert overview["field_count"] >= 1
    assert overview["field_pct"] > 0

    leagues = await get_totals_by_league(async_db_session)
    assert len(leagues) >= 1
    assert leagues[0]["league_name"] == "Test Premier League"

    teams = await get_totals_by_team(async_db_session)
    assert isinstance(teams, list)


# --------------------------------------------------------------------------
# 10. Web Endpoints for Phase 5 (Toss History, Totals, Accuracy)
# --------------------------------------------------------------------------
def test_phase5_web_endpoints(client: TestClient):
    """Verify authenticated access to Phase 5 web routes."""
    # Authenticate client with admin session cookie
    token = create_signed_session_token({"username": settings.admin_username})
    client.cookies.set(settings.session_cookie_name, token)

    # 1. Toss History page
    resp_history = client.get("/toss-history")
    assert resp_history.status_code == 200
    assert "Audited Toss History" in resp_history.text
    assert "Toss is a random event." in resp_history.text

    # 2. Toss History CSV export
    resp_csv = client.get("/toss-history/export/csv")
    assert resp_csv.status_code == 200
    assert resp_csv.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in resp_csv.headers["content-disposition"]
    assert "CRICKET TOSS ANALYTICS - AUDITED TOSS HISTORY" in resp_csv.text

    # 3. Totals Dashboard
    resp_totals = client.get("/totals")
    assert resp_totals.status_code == 200
    assert "Longitudinal Totals & Trends" in resp_totals.text
    assert "Completed Tosses" in resp_totals.text
    assert "Toss is a random event." in resp_totals.text

    # 4. Accuracy & Truth Dashboard
    resp_accuracy = client.get("/accuracy")
    assert resp_accuracy.status_code == 200
    assert "Accuracy & Mathematical Truth" in resp_accuracy.text
    assert "Wilson Score Interval" in resp_accuracy.text
    assert "Probability Calibration Table" in resp_accuracy.text
    assert "Toss is a random event." in resp_accuracy.text

