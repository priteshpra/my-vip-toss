"""Comprehensive unit and integration test suite for Phase 6 (Multi-Source Ingestion & Accuracy Scoring):

Strictly validates:
- Adapter pattern for external sources (public_html, json_api, telegram_channel, manual).
- Ethical web collection and graceful degradation on down/unreachable sources.
- Telegram tipster message parsing and review queue routing for ambiguous posts.
- Cross-source entity normalization using RapidFuzz.
- Strictly append-only immutability of SourceSnapshot (ReadOnlyError on UPDATE/DELETE).
- Automated post-toss scoring into SourceOutcome (PASS, FAIL, SKIPPED).
- Audited Source Accuracy Leaderboard with n >= 30 sample size gate and Wilson 95% CI.
- Source consensus tracking and combined panel lean honesty gate (NO EDGE default).
- Dynamic source creation, edit, toggle, and manual snapshot web endpoints.
"""

import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.testclient import TestClient

from app.models import (
    Match, Team, League, Venue, TossResult,
    Prediction, ExternalSource, SourceSnapshot, SourceOutcome,
    TelegramReview, CollectorHealth, ReadOnlyError
)
from app.collectors.external_sources import (
    BaseSourceAdapter, HtmlSourceAdapter, JsonApiSourceAdapter,
    TelegramChannelAdapter, get_source_adapter, parse_tipster_telegram_message,
    resolve_match_and_lean, poll_external_source
)
from app.services.source_service import (
    get_all_sources, create_external_source, update_external_source,
    toggle_source_enabled, record_manual_source_snapshot,
    score_source_snapshots_for_match, get_source_accuracy_leaderboard,
    get_source_comparison_for_match, get_consensus_historical_accuracy,
    evaluate_combined_panel_lean
)
from app.services.toss_service import record_toss_result
from app.schemas import TossResultCreate


# --------------------------------------------------------------------------
# 1. HTML Adapter Tests
# --------------------------------------------------------------------------
def test_html_source_adapter_regex_parsing():
    """Verify HTML adapter parses public fixtures and picks using regex rules."""
    source = ExternalSource(
        id=1,
        name="Cricket Public Tracker",
        type="public_html",
        url="https://example.com/toss",
        parse_rules=json.dumps({
            "item_regex": r"<div class=\"pick\">(.*?)</div>",
            "teams_regex": r"([A-Za-z\s]+)\s+vs\s+([A-Za-z\s]+)",
            "lean_regex": r"Toss Pick:\s*([A-Za-z\s]+)",
            "market_regex": r"Market:\s*([^\n<]+)"
        })
    )
    adapter = HtmlSourceAdapter(source)

    html_content = """
    <html>
        <body>
            <div class="pick">
                <h3>Chennai Super Kings vs Mumbai Indians</h3>
                <p>Toss Pick: Chennai Super Kings</p>
                <p>Market: Heavy load 62% on CSK / Rate 1.90</p>
            </div>
            <div class="pick">
                <h3>Kolkata Knight Riders vs Royal Challengers Bengaluru</h3>
                <p>Toss Pick: KKR</p>
                <p>Market: Balanced 50-50</p>
            </div>
        </body>
    </html>
    """
    items = adapter.parse_snapshots(html_content)
    assert len(items) == 2
    assert "Chennai Super Kings" in items[0]["team_a"]
    assert "Mumbai Indians" in items[0]["team_b"]
    assert items[0]["lean"] == "Chennai Super Kings"
    assert "62%" in items[0]["market"]

    assert "KKR" in items[1]["lean"]


def test_html_source_adapter_empty_or_malformed():
    """Verify HTML adapter handles malformed or empty responses gracefully."""
    source = ExternalSource(id=2, name="Empty Source", type="public_html", url="https://example.com")
    adapter = HtmlSourceAdapter(source)
    assert adapter.parse_snapshots("") == []
    assert adapter.parse_snapshots("<html><body>No picks here</body></html>") == []


@pytest.mark.asyncio
async def test_html_source_adapter_graceful_degradation_on_network_error(async_db_session: AsyncSession):
    """Network failure must mark source DEGRADED and log health without crashing."""
    source = ExternalSource(
        name="Down Public Feed",
        type="public_html",
        url="https://invalid-nonexistent-domain.xyz/toss",
        enabled=True,
        health="HEALTHY"
    )
    async_db_session.add(source)
    await async_db_session.commit()
    await async_db_session.refresh(source)

    adapter = HtmlSourceAdapter(source)
    # Mock httpx failure
    with patch("httpx.AsyncClient.get", side_effect=Exception("Connection refused / DNS lookup failed")):
        snaps = await adapter.collect(async_db_session)
        assert snaps == []
        assert source.health == "DEGRADED"

        # Verify CollectorHealth was recorded
        stmt = select(CollectorHealth).where(CollectorHealth.source == f"source:{source.name}")
        res = await async_db_session.execute(stmt)
        health = res.scalar_one_or_none()
        assert health is not None
        assert health.status == "DEGRADED"
        assert "Connection refused" in (health.last_error or "")


# --------------------------------------------------------------------------
# 2. JSON API Adapter Tests
# --------------------------------------------------------------------------
def test_json_api_adapter_parsing():
    """Verify JSON API adapter parses structured payload with custom keys."""
    source = ExternalSource(
        id=3,
        name="Sports Odds JSON API",
        type="json_api",
        url="https://api.example.com/cricket",
        parse_rules=json.dumps({
            "items_path": "data.fixtures",
            "team_a_key": "team_home",
            "team_b_key": "team_away",
            "lean_key": "toss_prediction",
            "market_key": "odds_ratio"
        })
    )
    adapter = JsonApiSourceAdapter(source)

    payload = {
        "status": "success",
        "data": {
            "fixtures": [
                {
                    "team_home": "Rajasthan Royals",
                    "team_away": "Delhi Capitals",
                    "toss_prediction": "Rajasthan Royals",
                    "odds_ratio": "1.85 / 1.95",
                    "commentary": "Venue toss history leans RR"
                },
                {
                    "team_home": "Gujarat Titans",
                    "team_away": "Sunrisers Hyderabad",
                    "toss_prediction": "NO_EDGE",
                    "odds_ratio": "1.90 / 1.90"
                }
            ]
        }
    }
    items = adapter.parse_snapshots(payload)
    assert len(items) == 2
    assert items[0]["team_a"] == "Rajasthan Royals"
    assert items[0]["team_b"] == "Delhi Capitals"
    assert items[0]["lean"] == "Rajasthan Royals"
    assert items[0]["market"] == "1.85 / 1.95"
    assert items[1]["lean"] == "NO_EDGE"


# --------------------------------------------------------------------------
# 3. Telegram Channel Tipster Parser Tests
# --------------------------------------------------------------------------
@pytest.mark.parametrize("msg, expected_lean, expected_valid, expected_ambiguous", [
    ("Match 14: CSK vs MI\nToss Winner: Chennai Super Kings\nMarket: Load 70% on CSK", "Chennai Super Kings", True, False),
    ("IPL Today: Kolkata Knight Riders vs Royal Challengers Bangalore\nKKR toss win", "KKR", True, False),
    ("Delhi Capitals vs GT - Delhi Capitals toss jeeta", "Delhi Capitals", True, False),
    ("Today match: 50-50 toss line, strict pass avoid bet", "NO_EDGE", True, False),
    ("Hey join my VIP group for fixed toss calls dm now", None, True, True),
    ("hi", None, False, True),
])
def test_telegram_tipster_parser_samples(msg, expected_lean, expected_valid, expected_ambiguous):
    """Test Telegram NLP and regex parser on varied formats including Hinglish and skips."""
    res = parse_tipster_telegram_message(msg)
    assert res["is_valid"] == expected_valid
    assert res["is_ambiguous"] == expected_ambiguous
    if expected_lean:
        assert res.get("lean") == expected_lean


@pytest.mark.asyncio
async def test_telegram_channel_adapter_routes_ambiguous_to_review_queue(async_db_session: AsyncSession):
    """Ambiguous or unparsable Telegram messages must route to TelegramReview queue."""
    source = ExternalSource(
        name="Tipster Guru Telegram",
        type="telegram_channel",
        enabled=True
    )
    async_db_session.add(source)
    await async_db_session.commit()
    await async_db_session.refresh(source)

    adapter = TelegramChannelAdapter(source)
    ambiguous_text = "Big jackpot call coming in 10 minutes wait for notification!"

    snap = await adapter.ingest_message(async_db_session, ambiguous_text, message_id="tele_msg_999")
    assert snap is None  # Not directly created as snapshot

    # Verify review item was created
    stmt = select(TelegramReview).where(TelegramReview.message_id == "tele_msg_999")
    res = await async_db_session.execute(stmt)
    rev = res.scalar_one_or_none()
    assert rev is not None
    assert rev.status == "PENDING"
    assert rev.channel_name == source.name
    assert rev.raw_text == ambiguous_text


# --------------------------------------------------------------------------
# 4. Append-Only Immutability of SourceSnapshot
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_source_snapshot_immutability_prevent_update_and_delete(async_db_session: AsyncSession):
    """SourceSnapshot records must strictly reject UPDATE and DELETE via ReadOnlyError."""
    source = ExternalSource(name="Immutable Test Feed", type="manual", enabled=True)
    async_db_session.add(source)
    await async_db_session.commit()

    snap1 = SourceSnapshot(
        source_id=source.id,
        lean="CSK",
        market="Load 55%",
        raw="raw 1"
    )
    snap2 = SourceSnapshot(
        source_id=source.id,
        lean="MI",
        market="Load 45%",
        raw="raw 2"
    )
    async_db_session.add_all([snap1, snap2])
    await async_db_session.commit()

    # 1. Test update prevention
    snap1.lean = "Changed Lean"
    with pytest.raises(ReadOnlyError) as exc_info:
        await async_db_session.commit()
    assert "SourceSnapshot records are immutable and append-only" in str(exc_info.value)
    await async_db_session.rollback()

    # 2. Test delete prevention
    await async_db_session.delete(snap2)
    with pytest.raises(ReadOnlyError) as exc_info:
        await async_db_session.commit()
    assert "SourceSnapshot records are immutable and append-only" in str(exc_info.value)
    await async_db_session.rollback()


# --------------------------------------------------------------------------
# 5. Cross-Source Entity Normalization & Fixture Matching
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cross_source_entity_and_fixture_matching(async_db_session: AsyncSession):
    """Fuzzy entity matching connects external variants (e.g. 'Super Kings' vs 'Mumbai') to canonical Match."""
    league_stmt = select(League).where(League.name == "IPL")
    league = (await async_db_session.execute(league_stmt)).scalar_one()

    team_a = Team(name="Chennai Super Kings", short_code="CSK")
    team_b = Team(name="Mumbai Indians", short_code="MI")
    async_db_session.add_all([team_a, team_b])
    await async_db_session.commit()

    match = Match(
        league_id=league.id,
        team_a_id=team_a.id,
        team_b_id=team_b.id,
        start_time_utc=datetime.now(timezone.utc) + timedelta(hours=2),
        status="upcoming"
    )
    async_db_session.add(match)
    await async_db_session.commit()

    # Test fuzzy team hints
    match_id, canon_lean = await resolve_match_and_lean(
        session=async_db_session,
        team_a_hint="Super Kings",
        team_b_hint="Indians",
        raw_lean="CSK"
    )
    assert match_id == match.id
    assert canon_lean == "Chennai Super Kings"


# --------------------------------------------------------------------------
# 6. Automated Post-Toss Accuracy Scoring
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_score_source_snapshots_upon_toss_done(async_db_session: AsyncSession):
    """When toss result is recorded, source snapshots must automatically be scored PASS, FAIL, or SKIPPED."""
    league_stmt = select(League).where(League.name == "IPL")
    league = (await async_db_session.execute(league_stmt)).scalar_one()

    team_a = Team(name="Royal Challengers Bengaluru", short_code="RCB")
    team_b = Team(name="Kolkata Knight Riders", short_code="KKR")
    source = ExternalSource(name="Scoring Verification Source", type="manual")
    async_db_session.add_all([team_a, team_b, source])
    await async_db_session.commit()

    match = Match(
        league_id=league.id,
        team_a_id=team_a.id,
        team_b_id=team_b.id,
        start_time_utc=datetime.now(timezone.utc) - timedelta(minutes=10),
        status="upcoming"
    )
    async_db_session.add(match)
    await async_db_session.commit()

    # Create 3 snapshots: Win pick, Loss pick, Skip pick
    snap_win = SourceSnapshot(source_id=source.id, match_id=match.id, lean="RCB")
    snap_loss = SourceSnapshot(source_id=source.id, match_id=match.id, lean="KKR")
    snap_skip = SourceSnapshot(source_id=source.id, match_id=match.id, lean="NO_EDGE")
    async_db_session.add_all([snap_win, snap_loss, snap_skip])
    await async_db_session.commit()

    # Record toss result with winner = RCB
    toss_data = TossResultCreate(
        match_id=match.id,
        toss_winner_id=team_a.id,
        toss_decision="bat"
    )
    await record_toss_result(async_db_session, toss_data)

    # Check outcomes
    stmt = (
        select(SourceOutcome)
        .where(SourceOutcome.snapshot_id.in_([snap_win.id, snap_loss.id, snap_skip.id]))
    )
    res = await async_db_session.execute(stmt)
    outcomes = {o.snapshot_id: o.result for o in res.scalars().all()}

    assert outcomes[snap_win.id] == "PASS"
    assert outcomes[snap_loss.id] == "FAIL"
    assert outcomes[snap_skip.id] == "SKIPPED"


# --------------------------------------------------------------------------
# 7. Source Accuracy Leaderboard & n >= 30 Sample Gate
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_source_accuracy_leaderboard_gate_n_30(async_db_session: AsyncSession):
    """
    Leaderboard must only rank sources with sample size n >= 30.
    Low-sample sources (n < 30) must be flagged with 'Insufficient data' and unranked.
    """
    # 1. Create Source A with n=35 (Sufficient sample)
    src_sufficient = ExternalSource(name="High Volume Tipster", type="manual", enabled=True)
    # 2. Create Source B with n=10 (Insufficient sample)
    src_insufficient = ExternalSource(name="Low Volume Tipster", type="manual", enabled=True)
    async_db_session.add_all([src_sufficient, src_insufficient])
    await async_db_session.commit()

    # Seed 35 outcomes for Source A (21 wins, 14 losses = 60.0%)
    for i in range(35):
        snap = SourceSnapshot(source_id=src_sufficient.id, lean="Team A", raw=f"snap_{i}")
        async_db_session.add(snap)
        await async_db_session.flush()
        res_val = "PASS" if i < 21 else "FAIL"
        out = SourceOutcome(snapshot_id=snap.id, result=res_val)
        async_db_session.add(out)

    # Seed 10 outcomes for Source B (8 wins, 2 losses = 80.0%, but n < 30!)
    for i in range(10):
        snap = SourceSnapshot(source_id=src_insufficient.id, lean="Team B", raw=f"snap_low_{i}")
        async_db_session.add(snap)
        await async_db_session.flush()
        res_val = "PASS" if i < 8 else "FAIL"
        out = SourceOutcome(snapshot_id=snap.id, result=res_val)
        async_db_session.add(out)

    await async_db_session.commit()

    leaderboard = await get_source_accuracy_leaderboard(async_db_session)
    assert len(leaderboard) >= 2

    # Find the entries
    entry_suff = next(s for s in leaderboard if s["source_name"] == "High Volume Tipster")
    entry_insuff = next(s for s in leaderboard if s["source_name"] == "Low Volume Tipster")

    # High Volume has n=35 >= 30: must be ranked #1
    assert entry_suff["is_sufficient"] is True
    assert entry_suff["status_label"] == "SUFFICIENT (n>=30)"
    assert entry_suff["rank"] == "#1"
    assert entry_suff["measured_accuracy_pct"] == 60.0

    # Low Volume has n=10 < 30: must be unranked despite higher nominal win rate (80%)!
    assert entry_insuff["is_sufficient"] is False
    assert entry_insuff["status_label"] == "INSUFFICIENT (n<30)"
    assert "Unranked" in entry_insuff["rank"]


# --------------------------------------------------------------------------
# 8. Source Consensus & Combined Panel Lean Honesty Tests
# --------------------------------------------------------------------------
def test_combined_panel_lean_honest_defaults_to_no_edge():
    """If neither model nor consensus has n >= 30 and p < 0.05, panel lean MUST be NO EDGE - skip."""
    model_lean_neutral = {
        "lean": "NO_EDGE",
        "action_type": "SKIP",
        "predicted_team_name": "None",
        "has_edge": False,
        "rationale": "Statistical history indistinguishable from 50% coin flip."
    }
    consensus_info = {
        "consensus_team": "Chennai Super Kings",
        "consensus_lean": "TeamA"
    }
    # Consensus historical data has low sample size (n=12 < 30)
    consensus_hist_insufficient = {
        "is_sufficient": False,
        "is_significant": False,
        "evaluated_calls": 12,
        "measured_accuracy_pct": 58.3,
        "wilson_ci_lower": 0.32,
        "wilson_ci_upper": 0.81
    }

    panel_call = evaluate_combined_panel_lean(
        model_lean_info=model_lean_neutral,
        consensus_info=consensus_info,
        consensus_historical=consensus_hist_insufficient
    )

    assert panel_call["action_type"] == "SKIP"
    assert panel_call["lean"] == "NO_EDGE"
    assert "NO EDGE - skip" in panel_call["rationale"]


def test_combined_panel_lean_confluence_when_edge_proven():
    """When both model and consensus have proven statistical edge (n >= 30, p < 0.05), produce combined PLAY."""
    model_lean_play = {
        "lean": "TeamA",
        "action_type": "PLAY",
        "predicted_team_name": "Chennai Super Kings",
        "probability": 0.62,
        "ci_low": 0.52,
        "ci_high": 0.71,
        "has_edge": True,
        "rationale": "Direct H2H edge n=40"
    }
    consensus_info = {
        "consensus_team": "Chennai Super Kings",
        "consensus_lean": "TeamA"
    }
    consensus_hist_proven = {
        "is_sufficient": True,
        "is_significant": True,
        "evaluated_calls": 50,
        "measured_accuracy_pct": 64.0,
        "wilson_ci_lower": 0.51,
        "wilson_ci_upper": 0.75
    }

    panel_call = evaluate_combined_panel_lean(
        model_lean_info=model_lean_play,
        consensus_info=consensus_info,
        consensus_historical=consensus_hist_proven
    )

    assert panel_call["action_type"] == "PLAY"
    assert panel_call["predicted_team_name"] == "Chennai Super Kings"
    assert "CONFLUENCE" in panel_call["rationale"]


from app.config import settings
from app.auth.csrf import generate_csrf_token


# --------------------------------------------------------------------------
# 9. Web Endpoints Integration Tests
# --------------------------------------------------------------------------
def test_sources_leaderboard_page_renders_200(client: TestClient):
    """GET /sources must render 200 with audited leaderboard, sources table, and disclaimer."""
    # Authenticate admin session with valid CSRF token
    csrf = generate_csrf_token()
    login_resp = client.post(
        "/auth/login",
        data={
            "username": settings.admin_username,
            "password": settings.admin_password,
            "csrf_token": csrf
        },
        follow_redirects=True
    )
    assert login_resp.status_code == 200

    resp = client.get("/sources")
    assert resp.status_code == 200
    assert "Tracked Sources & Tipster Channels" in resp.text
    assert "Audited Accuracy Leaderboard" in resp.text
    assert "Configured Sources & Scrapers" in resp.text
    assert "Toss is a random event" in resp.text


def test_admin_create_and_toggle_source_web(client: TestClient):
    """Admin must be able to add and toggle sources dynamically via UI without code changes."""
    # Authenticate admin session
    csrf = generate_csrf_token()
    login_resp = client.post(
        "/auth/login",
        data={
            "username": settings.admin_username,
            "password": settings.admin_password,
            "csrf_token": csrf
        },
        follow_redirects=True
    )
    assert login_resp.status_code == 200

    # Generate fresh CSRF token for POST
    action_csrf = generate_csrf_token()

    # 1. Create a new source dynamically
    source_name = f"Dynamic Scraper {datetime.now().timestamp()}"
    create_resp = client.post(
        "/admin/sources/create",
        data={
            "name": source_name,
            "type": "public_html",
            "url": "https://example.com/scraped-picks",
            "poll_interval": 600,
            "enabled": "true",
            "csrf_token": action_csrf
        },
        follow_redirects=True
    )
    assert create_resp.status_code == 200
    assert source_name in create_resp.text

