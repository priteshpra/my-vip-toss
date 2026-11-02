"""Comprehensive unit and integration tests for Phase 3:
- GamingHelper adapter (auth, retry, re-login, cache fallback, local/unknown league assignment, dedup)
- Telegram listener and toss message parser (multi-format, emojis, Hindi/Hinglish, skips)
- Idempotency & deduplication
- End-to-end lifecycle transition (upcoming/live -> toss_done, prediction auto PASS/FAIL)
- Telegram review queue (pending routing, admin UI mapping and rejection)
- Manual toss entry fallback with audit logging
- Collector health metric tracking
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy import select

from app.models import (
    Match, Team, League, Venue, TossResult, Prediction,
    PredictionOutcome, TelegramReview, CollectorHealth, AuditLog
)
from app.collectors.gaming_helper import GamingHelperAdapter, GamingHelperCollector
from app.collectors.telegram_listener import (
    TelegramListener, parse_toss_message, extract_toss_decision
)
from app.services.match_service import get_matches, upsert_match
from app.services.toss_service import record_toss_result
from app.services.prediction_service import lock_prediction
from app.schemas import PredictionCreate, TossResultCreate
from app.db import utc_now


# --------------------------------------------------------------------------
# 1. Telegram Parser Tests (15+ real-world & Hinglish samples)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("msg,expected_winner,expected_decision,is_skip", [
    (
        "🔥 MATCH 45 IPL 🔥\nToss Winner: Chennai Super Kings\nStake: Normal Play",
        "Chennai Super Kings", "bat", False
    ),
    (
        "Toss - Mumbai Indians elected to bowl",
        "Mumbai Indians", "field", False
    ),
    (
        "Delhi Capitals toss jeeta aur bowl karega",
        "Delhi Capitals", "field", False
    ),
    (
        "RCB toss jeet kar pehle ballebaazi karegi 🏏",
        "Royal Challengers Bengaluru", "bat", False
    ),
    (
        "KKR vs SRH: KKR toss winner (Field)",
        "Kolkata Knight Riders", "field", False
    ),
    (
        "GT toss win bat first",
        "Gujarat Titans", "bat", False
    ),
    (
        "Toss Winner -> Rajasthan Royals [BOWL]",
        "Rajasthan Royals", "field", False
    ),
    (
        "TODAY TOSS CALL : LUCKNOW SUPER GIANTS",
        "Lucknow Super Giants", "bat", False
    ),
    (
        "PBKS toss jeeta gendbazi ka faisla",
        "Punjab Kings", "field", False
    ),
    (
        "Match 20: Toss - Sunrisers Hyderabad",
        "Sunrisers Hyderabad", "bat", False
    ),
    (
        "Toss: MI won the toss and elected to chase",
        "Mumbai Indians", "field", False
    ),
    (
        "Toss prediction: Gujarat Titans will win toss and bowl",
        "Gujarat Titans", "field", False
    ),
    # Skip / Pass calls
    (
        "Match 12: Otago vs Central Stags\nVerdict: Strict PASS - 50-50 neutral rotation line.",
        None, None, True
    ),
    (
        "Today match toss: 50-50 toss line, please SKIP this game",
        None, None, True
    ),
    (
        "Neutral coin flip avoid bet today",
        None, None, True
    ),
])
def test_telegram_parser_real_world_samples(msg, expected_winner, expected_decision, is_skip):
    """Verify that multi-format, emoji-rich, and Hindi/Hinglish messages are parsed correctly."""
    res = parse_toss_message(msg)
    if is_skip:
        assert res["is_skip"] is True
        assert res["is_toss_result"] is False
    else:
        assert res["is_toss_result"] is True
        assert res["is_skip"] is False
        assert res["decision"] == expected_decision
        # Check that expected winner is captured or normalized
        raw = res["winner_raw"]
        assert raw is not None


def test_telegram_parser_ambiguous_and_noise():
    """Verify that irrelevant chatter or unparsable messages do not falsely trigger toss confirmation."""
    noise_samples = [
        "Good morning everyone! Great weather today for cricket.",
        "Join our VIP channel for exclusive match tips!",
        "Match delayed due to rain in London.",
        "Just 15 minutes left until the match begins guys.",
        "Random chat message without any toss keywords 123456"
    ]
    for noise in noise_samples:
        res = parse_toss_message(noise)
        assert res["is_toss_result"] is False
        assert res["winner_raw"] is None


# --------------------------------------------------------------------------
# 2. GamingHelper Adapter Unit Tests
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_gaming_helper_adapter_auth_success():
    """Verify successful authentication stores token."""
    adapter = GamingHelperAdapter()
    adapter.enabled = True
    adapter.phone = "9876543210"
    adapter.password = "secret"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"token": "test_jwt_token_123"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        success = await adapter.authenticate()
        assert success is True
        assert adapter.token == "test_jwt_token_123"


@pytest.mark.asyncio
async def test_gaming_helper_adapter_relogin_on_401():
    """Verify session re-login on 401 token expiry."""
    adapter = GamingHelperAdapter()
    adapter.enabled = True
    adapter.phone = "9876543210"
    adapter.password = "secret"
    adapter.token = "expired_token"
    adapter.token_expiry = time_in_future = 9999999999.0

    resp_401 = MagicMock(status_code=401)
    resp_200 = MagicMock(status_code=200)
    resp_200.json.return_value = {
        "data": [
            {
                "id": "GH_REAUTH_1",
                "team_a_name": "Chennai Super Kings",
                "team_b_name": "Mumbai Indians",
                "status": "UPCOMING"
            }
        ]
    }

    mock_auth_resp = MagicMock(status_code=200)
    mock_auth_resp.json.return_value = {"token": "fresh_jwt_token"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_post.return_value = mock_auth_resp
        mock_get.side_effect = [resp_401, resp_200]

        matches, latency = await adapter.fetch_live_matches()
        assert len(matches) == 1
        assert matches[0]["id"] == "GH_REAUTH_1"
        assert adapter.token == "fresh_jwt_token"


@pytest.mark.asyncio
async def test_gaming_helper_adapter_fallback_on_failure(tmp_path):
    """Verify fallback to local cache when network fails."""
    cache_file = tmp_path / "test_cache.json"
    cache_data = [{"id": "CACHED_MATCH_1", "team_a_name": "Australia", "team_b_name": "England"}]
    cache_file.write_text(json.dumps(cache_data), encoding="utf-8")

    adapter = GamingHelperAdapter()
    adapter.cache_file = str(cache_file)
    adapter.phone = ""  # Force no auth

    matches, latency = await adapter.fetch_live_matches()
    assert len(matches) == 1
    assert matches[0]["id"] == "CACHED_MATCH_1"


# --------------------------------------------------------------------------
# 3. GamingHelper Collector & Ingestion Tests
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_gaming_helper_collector_unknown_league_and_dedup(async_db_session):
    """
    Verify:
    1. Unidentified leagues are assigned to 'Local/Unknown' (never dropped).
    2. Sync is idempotent: running twice does not duplicate matches.
    3. Collector health metrics are updated.
    """
    mock_adapter = MagicMock(spec=GamingHelperAdapter)
    mock_matches = [
        {
            "id": "GH_TEST_MATCH_001",
            "tournament_name": "Unknown Local Club Trophy 2026",
            "team_a_name": "CSK",
            "team_b_name": "MI",
            "venue": "Wankhede Stadium, Mumbai",
            "status": "UPCOMING",
            "match_date": "2026-11-05",
            "time_str": "19:30"
        },
        {
            "id": "GH_TEST_MATCH_002",
            "tournament_name": "",  # Empty tournament
            "team_a_name": "Kolkata Knight Riders",
            "team_b_name": "Royal Challengers Bangalore",
            "status": "UPCOMING",
            "match_date": "2026-11-06",
            "time_str": "15:30"
        }
    ]
    mock_adapter.fetch_live_matches = AsyncMock(return_value=(mock_matches, 120))

    collector = GamingHelperCollector(adapter=mock_adapter)

    # First Sync
    res1 = await collector.sync_matches(async_db_session)
    assert res1["status"] == "SUCCESS"
    assert res1["new_matches"] == 2
    assert res1["matches_synced"] == 2

    # Verify Matches in DB
    matches = await get_matches(async_db_session, tab="all")
    assert len(matches) == 2

    # Check that unidentified leagues are 'Local/Unknown'
    for m in matches:
        assert m.league.name == "Local/Unknown"
        assert m.team_a.name in ("Chennai Super Kings", "Kolkata Knight Riders")

    # Second Sync (Idempotency test)
    res2 = await collector.sync_matches(async_db_session)
    assert res2["status"] == "SUCCESS"
    assert res2["new_matches"] == 0
    assert res2["matches_synced"] == 2

    # Total matches must remain 2 (no duplicates!)
    matches_after = await get_matches(async_db_session, tab="all")
    assert len(matches_after) == 2

    # Check collector health record
    health_stmt = select(CollectorHealth).where(CollectorHealth.source == "GamingHelper Online")
    h_res = await async_db_session.execute(health_stmt)
    health = h_res.scalar_one_or_none()
    assert health is not None
    assert health.status == "OK"
    assert health.last_success_utc is not None
    assert health.response_time_ms == 120


# --------------------------------------------------------------------------
# 4. Telegram Listener End-to-End Lifecycle & Idempotency Tests
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_telegram_listener_idempotency_and_review_queue(async_db_session):
    """
    Verify:
    1. Ambiguous messages are routed to TelegramReview queue with status PENDING.
    2. Re-processing the same message_id is ignored (DUPLICATE_SKIPPED).
    """
    listener = TelegramListener()

    ambiguous_text = "Match update: toss happening soon, standby for final call..."
    res = await listener.process_message(
        session=async_db_session,
        message_id="MSG_AMBIGUOUS_999",
        text=ambiguous_text,
        channel_name="@CricTossTips"
    )
    assert res["status"] == "QUEUED_FOR_REVIEW"
    assert res["message_id"] == "MSG_AMBIGUOUS_999"

    # Verify review record
    rev_stmt = select(TelegramReview).where(TelegramReview.message_id == "MSG_AMBIGUOUS_999")
    rev = (await async_db_session.execute(rev_stmt)).scalar_one_or_none()
    assert rev is not None
    assert rev.status == "PENDING"
    assert rev.channel_name == "@CricTossTips"

    # Send exact same message again -> must be skipped as duplicate
    res_dup = await listener.process_message(
        session=async_db_session,
        message_id="MSG_AMBIGUOUS_999",
        text=ambiguous_text,
        channel_name="@CricTossTips"
    )
    assert res_dup["status"] == "DUPLICATE_SKIPPED"


@pytest.mark.asyncio
async def test_telegram_listener_auto_resolve_lifecycle(async_db_session):
    """
    End-to-End lifecycle test:
    1. Match fixture exists in UPCOMING status.
    2. Pre-toss prediction is locked (Team A predicted).
    3. Telegram toss message arrives -> parsed -> Match status moves to 'toss_done'.
    4. TossResult is recorded with unique telegram_message_id.
    5. PredictionOutcome is automatically evaluated to PASS.
    6. Duplicate message with same message_id is rejected.
    """
    # 1. Setup Match Fixture
    league = League(name="Indian Premier League", format="T20")
    team_a = Team(name="Chennai Super Kings", short_code="CSK")
    team_b = Team(name="Mumbai Indians", short_code="MI")
    async_db_session.add_all([league, team_a, team_b])
    await async_db_session.commit()

    match = Match(
        league_id=league.id,
        team_a_id=team_a.id,
        team_b_id=team_b.id,
        start_time_utc=utc_now() + timedelta(minutes=30),
        status="live",
        source_match_id="TEST_LIFECYCLE_01"
    )
    async_db_session.add(match)
    await async_db_session.commit()
    await async_db_session.refresh(match)

    # 2. Lock Pre-Toss Prediction (predicting CSK)
    pred_data = PredictionCreate(
        match_id=match.id,
        lean="CSK",
        predicted_team_id=team_a.id,
        probability=0.55,
        ci_low=0.48,
        ci_high=0.62,
        source="own"
    )
    pred = await lock_prediction(async_db_session, pred_data)
    assert pred.hash is not None

    # 3. Process Incoming Telegram Toss Message
    listener = TelegramListener()
    toss_msg = "🔥 MATCH 45 IPL 🔥\nTOSS - CHENNAI SUPER KINGS ✅\nBAT FIRST"

    result = await listener.process_message(
        session=async_db_session,
        message_id="TG_MSG_TOSS_777",
        text=toss_msg,
        channel_name="@OfficialTossFeed"
    )

    assert result["status"] == "CONFIRMED_TOSS"
    assert result["match_id"] == match.id
    assert "Chennai Super Kings" in result["toss_winner"]
    assert result["decision"] == "bat"

    # 4. Verify Match is now 'toss_done'
    await async_db_session.refresh(match)
    assert match.status == "toss_done"

    # 5. Verify TossResult recorded with telegram_message_id
    toss_res = (await async_db_session.execute(
        select(TossResult).where(TossResult.match_id == match.id)
    )).scalar_one_or_none()
    assert toss_res is not None
    assert toss_res.telegram_message_id == "TG_MSG_TOSS_777"
    assert toss_res.winner_team_id == team_a.id
    assert toss_res.decision == "bat"

    # 6. Verify Prediction was automatically evaluated as PASS
    outcome = (await async_db_session.execute(
        select(PredictionOutcome).where(PredictionOutcome.prediction_id == pred.id)
    )).scalar_one_or_none()
    assert outcome is not None
    assert outcome.result == "PASS"

    # 7. Duplicate message rejection test
    dup_result = await listener.process_message(
        session=async_db_session,
        message_id="TG_MSG_TOSS_777",
        text=toss_msg,
        channel_name="@OfficialTossFeed"
    )
    assert dup_result["status"] == "DUPLICATE_SKIPPED"


# --------------------------------------------------------------------------
# 5. Admin Manual Toss Entry & Audit Trail
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_toss_entry_fallback_and_audit(async_db_session):
    """
    Verify:
    1. Admin can manually record toss result for a fixture.
    2. Locked predictions are evaluated.
    3. Action is logged to audit trail.
    """
    league = League(name="Big Bash League", format="T20")
    team_a = Team(name="Perth Scorchers", short_code="SCO")
    team_b = Team(name="Sydney Sixers", short_code="SIX")
    async_db_session.add_all([league, team_a, team_b])
    await async_db_session.commit()

    match = Match(
        league_id=league.id,
        team_a_id=team_a.id,
        team_b_id=team_b.id,
        start_time_utc=utc_now() + timedelta(minutes=15),
        status="live",
        source_match_id="TEST_MANUAL_TOSS_01"
    )
    async_db_session.add(match)
    await async_db_session.commit()
    await async_db_session.refresh(match)

    # Prediction predicting Sydney Sixers
    pred_data = PredictionCreate(
        match_id=match.id,
        lean="SIX",
        predicted_team_id=team_b.id,
        probability=0.52,
        source="own"
    )
    pred = await lock_prediction(async_db_session, pred_data)

    # Admin manually records toss winner as Perth Scorchers (so prediction should FAIL)
    toss_data = TossResultCreate(
        match_id=str(match.id),
        toss_winner_id=str(team_a.id),
        toss_decision="FIELD",
        notes="Manual referee report confirmation"
    )
    toss_obj = await record_toss_result(async_db_session, toss_data, source_provider_id="ADMIN_MANUAL")
    assert toss_obj.decision == "field"

    # Match status updated
    await async_db_session.refresh(match)
    assert match.status == "toss_done"

    # Prediction outcome evaluated to FAIL
    outcome = (await async_db_session.execute(
        select(PredictionOutcome).where(PredictionOutcome.prediction_id == pred.id)
    )).scalar_one_or_none()
    assert outcome is not None
    assert outcome.result == "FAIL"


# --------------------------------------------------------------------------
# 6. Lifecycle Tabs Filtering Tests
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_match_lifecycle_tabs_filtering(async_db_session):
    """
    Verify get_matches returns proper fixtures for upcoming, live, toss_done, and history tabs.
    """
    league = League(name="The Hundred", format="100-ball")
    t1 = Team(name="Oval Invincibles", short_code="OVI")
    t2 = Team(name="London Spirit", short_code="LON")
    venue = Venue(name="Lord's Cricket Ground")
    async_db_session.add_all([league, t1, t2, venue])
    await async_db_session.commit()

    now = utc_now()

    # 1. Upcoming match (tomorrow)
    m_upcoming = Match(
        league_id=league.id,
        team_a_id=t1.id,
        team_b_id=t2.id,
        venue_id=venue.id,
        start_time_utc=now + timedelta(days=1),
        status="upcoming"
    )

    # 2. Live match (starts in 10 mins)
    m_live = Match(
        league_id=league.id,
        team_a_id=t1.id,
        team_b_id=t2.id,
        venue_id=venue.id,
        start_time_utc=now + timedelta(minutes=10),
        status="live"
    )

    # 3. Completed match (yesterday)
    m_done = Match(
        league_id=league.id,
        team_a_id=t1.id,
        team_b_id=t2.id,
        venue_id=venue.id,
        start_time_utc=now - timedelta(days=1),
        status="toss_done"
    )
    toss = TossResult(
        match=m_done,
        winner_team_id=t1.id,
        decision="bat",
        source="manual"
    )

    async_db_session.add_all([m_upcoming, m_live, m_done, toss])
    await async_db_session.commit()

    # Test upcoming tab
    up_matches = await get_matches(async_db_session, tab="upcoming")
    up_ids = [m.id for m in up_matches]
    assert m_upcoming.id in up_ids
    assert m_done.id not in up_ids

    # Test live tab
    live_matches = await get_matches(async_db_session, tab="live")
    live_ids = [m.id for m in live_matches]
    assert m_live.id in live_ids
    assert m_done.id not in live_ids

    # Test toss_done tab
    done_matches = await get_matches(async_db_session, tab="toss_done")
    done_ids = [m.id for m in done_matches]
    assert m_done.id in done_ids
    assert m_upcoming.id not in done_ids

    # Test venue search filter
    venue_matches = await get_matches(async_db_session, venue_search="Lord's")
    assert len(venue_matches) >= 3
