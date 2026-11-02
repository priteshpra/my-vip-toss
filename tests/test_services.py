"""Integration tests for match, toss, prediction locking, and evaluation services."""

import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from app.db import Base
from app.models import Team, Tournament, DataSource, Match, PredictionSnapshot
from app.schemas import PredictionCreate, TossResultCreate
from app.services.match_service import upsert_match, get_match_by_id, get_matches
from app.services.prediction_service import lock_prediction, verify_prediction_integrity
from app.services.toss_service import record_toss_result
from app.services.accuracy_service import get_accuracy_metrics_for_source
from app.services.source_service import get_source_leaderboard


@pytest_asyncio.fixture
async def async_test_session():
    """Create in-memory SQLite database session for isolated service testing."""
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        # Seed base data
        session.add(DataSource(id="test_feed", name="Test Feed", provider_type="API"))
        session.add(Tournament(id="ipl_test", name="IPL Test", category="LEAGUE"))
        session.add(Team(id="csk", name="Chennai Super Kings", short_code="CSK"))
        session.add(Team(id="mi", name="Mumbai Indians", short_code="MI"))
        await session.commit()
        yield session

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_upsert_and_retrieve_match(async_test_session: AsyncSession):
    """Test match creation, retrieval, and status tracking."""
    start_utc = datetime.now(timezone.utc) + timedelta(hours=3)
    match_dict = {
        "id": "match_test_001",
        "tournament_id": "ipl_test",
        "team_a_id": "csk",
        "team_b_id": "mi",
        "match_format": "T20",
        "match_title": "CSK vs MI",
        "scheduled_start_utc": start_utc,
        "status": "UPCOMING",
        "data_source_id": "test_feed",
        "odds_a": 95.0,
        "odds_b": 85.0
    }
    match = await upsert_match(async_test_session, match_dict)
    assert match.id == "match_test_001"

    retrieved = await get_match_by_id(async_test_session, "match_test_001")
    assert retrieved is not None
    assert retrieved.match_title == "CSK vs MI"
    assert retrieved.status == "UPCOMING"


@pytest.mark.asyncio
async def test_prediction_locking_and_evaluation(async_test_session: AsyncSession):
    """
    Test pre-toss prediction locking with cryptographic hash,
    followed by ground truth toss resolution and automatic evaluation.
    """
    # 1. Create upcoming match (start in 2 hours)
    start_utc = datetime.now(timezone.utc) + timedelta(hours=2)
    await upsert_match(async_test_session, {
        "id": "match_eval_1",
        "tournament_id": "ipl_test",
        "team_a_id": "csk",
        "team_b_id": "mi",
        "match_format": "T20",
        "match_title": "CSK vs MI",
        "scheduled_start_utc": start_utc,
        "status": "UPCOMING",
        "data_source_id": "test_feed"
    })

    # 2. Lock Prediction before match start
    pred_data = PredictionCreate(
        match_id="match_eval_1",
        model_version_id="model_v1",
        source_channel="@vip_cricket_toss",
        action_type="PLAY",
        predicted_team_id="csk",
        estimated_prob_a=0.58,
        punter_rationale="Longitudinal H2H calling rotation favors CSK"
    )
    pred_snapshot = await lock_prediction(async_test_session, pred_data)
    assert pred_snapshot.id is not None
    assert pred_snapshot.is_locked is True
    assert pred_snapshot.is_pre_toss is True
    assert len(pred_snapshot.snapshot_hash) == 64

    # 3. Verify cryptographic audit passes
    audit = await verify_prediction_integrity(async_test_session, pred_snapshot.id)
    assert audit.is_valid is True
    assert audit.is_pre_toss_verified is True
    assert "PASSED" in audit.audit_verdict

    # 4. Record Ground Truth Official Toss Outcome (CSK won toss and bat)
    toss_data = TossResultCreate(
        match_id="match_eval_1",
        toss_winner_id="csk",
        toss_decision="BAT",
        official_source_url="https://cricbuzz.com/match/1"
    )
    toss_res = await record_toss_result(async_test_session, toss_data, source_provider_id="CRICBUZZ")
    assert toss_res.toss_winner_id == "csk"

    # 5. Check match status is now TOSS_COMPLETED
    async_test_session.expire_all()
    match = await get_match_by_id(async_test_session, "match_eval_1")
    assert match.status == "TOSS_COMPLETED"

    # 6. Check Prediction was automatically evaluated as HIT (True)
    assert len(match.predictions) == 1
    eval_record = match.predictions[0].evaluation
    assert eval_record is not None
    assert eval_record.is_correct is True
    assert eval_record.brier_score is not None

    # 7. Check accuracy service computes correct stats
    metrics = await get_accuracy_metrics_for_source(async_test_session, source_channel="@vip_cricket_toss")
    assert metrics.wins == 1
    assert metrics.losses == 0
    assert metrics.evaluated_calls == 1
    assert metrics.measured_accuracy_pct == 100.0


@pytest.mark.asyncio
async def test_post_match_prediction_disqualified(async_test_session: AsyncSession):
    """Predictions locked after scheduled match start must be flagged is_pre_toss=False."""
    past_start = datetime.now(timezone.utc) - timedelta(minutes=30)
    await upsert_match(async_test_session, {
        "id": "match_past_01",
        "tournament_id": "ipl_test",
        "team_a_id": "csk",
        "team_b_id": "mi",
        "match_format": "T20",
        "match_title": "CSK vs MI",
        "scheduled_start_utc": past_start,
        "status": "UPCOMING",
        "data_source_id": "test_feed"
    })

    pred_data = PredictionCreate(
        match_id="match_past_01",
        action_type="PLAY",
        predicted_team_id="mi"
    )
    snapshot = await lock_prediction(async_test_session, pred_data)
    assert snapshot.is_pre_toss is False
    assert snapshot.assessment_status == "POST_START_DISQUALIFIED"
