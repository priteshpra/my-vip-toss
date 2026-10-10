"""Unit tests for Repository CRUD and transaction operations."""

import pytest
from datetime import datetime
from core.models.schemas import (
    Match, TossResult, MarketSnapshot, PredictionSnapshot, PredictionEvaluation,
    MatchFormat, MatchStatus, TossDecision, VerificationStatus, AssessmentStatus, MarketType
)
from core.timezone import now_ist, parse_to_ist


def test_match_and_toss_result_lifecycle(test_repo):
    # Fetch seeded match
    match = test_repo.get_match_by_id("match_20261010_csk_mi")
    assert match is not None
    assert match["team_a_name"] == "Chennai Super Kings"
    assert match["team_b_name"] == "Mumbai Indians"
    assert match["status"] == "UPCOMING"

    # Confirm toss result for the match
    toss_res = TossResult(
        match_id="match_20261010_csk_mi",
        toss_winner_id="team_csk",
        toss_decision=TossDecision.BAT,
        match_winner_id=None,
        official_source_url="https://cricdata.local/proof",
        source_provider_id="cricket_data_org",
        verified_at_ist=now_ist(),
        verification_status=VerificationStatus.VERIFIED
    )
    test_repo.upsert_toss_result(toss_res)

    # Verify match status automatically transitions to TOSS_COMPLETED
    updated_match = test_repo.get_match_by_id("match_20261010_csk_mi")
    assert updated_match["status"] == "TOSS_COMPLETED"
    assert updated_match["toss_winner_name"] == "Chennai Super Kings"


def test_market_snapshot_storage(test_repo):
    snap = MarketSnapshot(
        match_id="match_20261010_csk_mi",
        provider_id="the_odds_api",
        bookmaker="Pinnacle",
        market_type=MarketType.TOSS_WINNER,
        team_a_odds=1.91,
        team_b_odds=1.91,
        team_a_implied_prob=0.50,
        team_b_implied_prob=0.50,
        margin_percentage=4.71,
        snapshot_timestamp_ist=parse_to_ist("2026-10-10T18:00:00+05:30")
    )
    snap_id = test_repo.insert_market_snapshot(snap)
    assert snap_id > 0

    latest = test_repo.get_latest_market_snapshot("match_20261010_csk_mi", "TOSS_WINNER")
    assert latest is not None
    assert latest["bookmaker"] == "Pinnacle"
    assert latest["team_a_odds"] == 1.91


def test_prediction_snapshot_and_evaluation(test_repo):
    pred = PredictionSnapshot(
        match_id="match_20261010_csk_mi",
        model_version_id="v1.0_bernoulli_baseline",
        assessment_status=AssessmentStatus.NO_VALIDATED_ADVANTAGE,
        supporting_evidence=["Historical balance over 35 matches"],
        contradicting_evidence=["No empirical skew detected"],
        sample_size_evaluated=35,
        snapshot_timestamp_ist=now_ist()
    )
    pred_id = test_repo.insert_prediction_snapshot(pred)
    assert pred_id > 0

    latest = test_repo.get_latest_prediction_snapshot("match_20261010_csk_mi")
    assert latest is not None
    assert latest["assessment_status"] == "NO_VALIDATED_ADVANTAGE"
    assert "Historical balance" in latest["supporting_evidence"][0]

    # Evaluate prediction
    evaluation = PredictionEvaluation(
        prediction_snapshot_id=pred_id,
        match_id="match_20261010_csk_mi",
        actual_toss_winner_id="team_csk",
        predicted_favored_id=None,
        is_correct=None,  # Inconclusive/neutral prediction
        brier_score=0.25,  # (0.50 - 1.0)^2 = 0.25 standard neutral baseline
        log_loss=0.6931,   # -ln(0.50) = 0.6931
        evaluated_at_ist=now_ist()
    )
    eval_id = test_repo.insert_prediction_evaluation(evaluation)
    assert eval_id > 0
