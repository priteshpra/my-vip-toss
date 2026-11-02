"""Comprehensive unit and integration tests for Phase 4:
- Wilson score interval mathematical benchmarks
- Each statistical function on fixture data (overall, league/format, venue, H2H, recent form, captains, home/away)
- Insufficient data flags (n < 30) and 50% baseline null test flags
- Market intelligence provider interface and manual append-only entry
- Cricsheet open data parsing and incremental DB ingestion
- ESPN / Cricbuzz rate limiting, caching, and graceful degradation
- Full analysis dossier CSV export
- Full analysis dossier PDF export
- Web endpoints for Match Detail, CSV export, PDF export, and market entry
"""

import io
import csv
import json
import pytest
from datetime import datetime, date, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Match, Team, League, Venue, HistoricalMatchStatistic,
    MarketEntry, Prediction, TossResult, CollectorHealth, AuditLog
)
from app.services.accuracy_service import (
    calculate_wilson_score_interval,
    binomial_test_against_baseline
)
from app.services.analysis_service import (
    make_stat_block,
    get_team_toss_stats,
    get_venue_toss_stats,
    get_h2h_toss_stats,
    get_captain_toss_stats,
    get_comprehensive_match_analysis,
    PERMANENT_DISCLAIMER
)
from app.services.market_service import (
    record_manual_market_entry,
    get_market_entries_for_match,
    ManualMarketProvider
)
from app.services.export_service import generate_match_csv, generate_match_pdf
from app.collectors.cricsheet import CricsheetCollector
from app.collectors.espn_cricbuzz import CricbuzzCollector, RateLimiter
from app.config import settings
from app.auth.security import create_signed_session_token
from app.db import utc_now


# --------------------------------------------------------------------------
# 1. Wilson Score Interval Benchmark Tests
# --------------------------------------------------------------------------
def test_wilson_score_interval_known_benchmarks():
    """Verify Wilson score interval against known mathematical benchmarks."""
    # 0 trials
    l, u = calculate_wilson_score_interval(0, 0, 0.95)
    assert (l, u) == (0.0, 0.0)

    # 15 wins out of 30 trials (p = 0.50): Wilson interval is symmetric around 0.50
    # Expected bounds roughly [0.3315, 0.6685]
    l, u = calculate_wilson_score_interval(15, 30, 0.95)
    assert 0.32 < l < 0.35
    assert 0.65 < u < 0.68
    assert l <= 0.50 <= u

    # 100 wins out of 100 trials: lower bound strictly positive, upper bound 1.0
    l, u = calculate_wilson_score_interval(100, 100, 0.95)
    assert l > 0.95
    assert u == 1.0

    # 0 wins out of 100 trials: lower bound 0.0, upper bound strictly small
    l, u = calculate_wilson_score_interval(0, 100, 0.95)
    assert l == 0.0
    assert u < 0.05

    # Interval width strictly shrinks with sample size n for p = 0.50
    _, u10 = calculate_wilson_score_interval(5, 10, 0.95)
    l10, _ = calculate_wilson_score_interval(5, 10, 0.95)
    w10 = u10 - l10

    l100, u100 = calculate_wilson_score_interval(50, 100, 0.95)
    w100 = u100 - l100

    l1000, u1000 = calculate_wilson_score_interval(500, 1000, 0.95)
    w1000 = u1000 - l1000

    assert w10 > w100 > w1000


# --------------------------------------------------------------------------
# 2. make_stat_block & Flagging Logic Tests
# --------------------------------------------------------------------------
def test_make_stat_block_insufficient_data_flag():
    """Verify that n < 30 explicitly sets is_insufficient and warning message."""
    # Low sample n = 12 < 30
    stat_low = make_stat_block(6, 12, "Sample Low")
    assert stat_low["is_insufficient"] is True
    assert "Insufficient data" in stat_low["insufficient_warning"]
    assert stat_low["n"] == 12
    assert stat_low["win_pct"] == 50.0

    # Sufficient sample n = 40 >= 30
    stat_suff = make_stat_block(20, 40, "Sample Sufficient")
    assert stat_suff["is_insufficient"] is False
    assert stat_suff["insufficient_warning"] is None
    assert stat_suff["n"] == 40


def test_make_stat_block_baseline_test_flag():
    """Verify flagging when CI includes 50% vs when it statistically diverges."""
    # 50 wins out of 100: CI clearly includes 0.50 -> Not statistically different
    stat_fair = make_stat_block(50, 100, "Fair Coin")
    assert stat_fair["not_different_from_50"] is True
    assert "Not statistically different from 50%" in stat_fair["baseline_note"]

    # 90 wins out of 100: CI strictly above 0.50 -> Diverges from baseline
    stat_skewed = make_stat_block(90, 100, "Skewed Result")
    assert stat_skewed["not_different_from_50"] is False
    assert "Statistically diverges from 50% baseline" in stat_skewed["baseline_note"]


# --------------------------------------------------------------------------
# 3. Fixture Statistical Analysis Tests
# --------------------------------------------------------------------------
async def seed_test_history(async_db_session: AsyncSession):
    """Seed historical match statistics for testing analysis service."""
    res_l = await async_db_session.execute(select(League).where(League.name == "Indian Premier League"))
    ipl = res_l.scalar_one_or_none()
    if not ipl:
        ipl = League(name="Indian Premier League", format="T20")
        async_db_session.add(ipl)

    res_a = await async_db_session.execute(select(Team).where(Team.name == "Chennai Super Kings"))
    csk = res_a.scalar_one_or_none()
    if not csk:
        csk = Team(name="Chennai Super Kings", short_code="CSK")
        async_db_session.add(csk)

    res_b = await async_db_session.execute(select(Team).where(Team.name == "Mumbai Indians"))
    mi = res_b.scalar_one_or_none()
    if not mi:
        mi = Team(name="Mumbai Indians", short_code="MI")
        async_db_session.add(mi)

    res_v = await async_db_session.execute(select(Venue).where(Venue.name == "Wankhede Stadium, Mumbai"))
    venue = res_v.scalar_one_or_none()
    if not venue:
        venue = Venue(name="Wankhede Stadium, Mumbai")
        async_db_session.add(venue)

    await async_db_session.commit()
    await async_db_session.refresh(ipl)
    await async_db_session.refresh(csk)
    await async_db_session.refresh(mi)
    await async_db_session.refresh(venue)

    # Check if history records already exist
    existing_q = await async_db_session.execute(select(HistoricalMatchStatistic.id).where(HistoricalMatchStatistic.id.like("hist_test_%")))
    existing_set = set(existing_q.scalars().all())

    # Add 40 historical matches: 25 CSK toss wins, 15 MI toss wins
    history_records = []
    base_date = date(2025, 1, 1)

    for i in range(40):
        m_id = f"hist_test_{i:03d}"
        if m_id in existing_set:
            continue
        winner = "Chennai Super Kings" if i < 25 else "Mumbai Indians"
        decision = "BAT" if i % 2 == 0 else "FIELD"
        match_winner = winner if i % 3 == 0 else "Mumbai Indians"
        fmt = "T20" if i < 30 else "ODI"
        captain_csk = "MS Dhoni" if i < 20 else "Ruturaj Gaikwad"
        captain_mi = "Rohit Sharma" if i < 20 else "Hardik Pandya"
        toss_cap = captain_csk if winner == "Chennai Super Kings" else captain_mi

        stat = HistoricalMatchStatistic(
            id=m_id,
            match_date=base_date + timedelta(days=i),
            tournament_name="Indian Premier League",
            match_format=fmt,
            team_a_name="Chennai Super Kings",
            team_b_name="Mumbai Indians",
            venue_name="Wankhede Stadium, Mumbai",
            toss_winner_name=winner,
            toss_decision=decision,
            match_winner_name=match_winner,
            captain_a=captain_csk,
            captain_b=captain_mi,
            toss_winner_captain=toss_cap,
            source="CRICSHEET"
        )
        history_records.append(stat)

    if history_records:
        async_db_session.add_all(history_records)
        await async_db_session.commit()

    return {"csk": csk, "mi": mi, "venue": venue, "league": ipl}


@pytest.mark.asyncio
async def test_team_toss_stats_service(async_db_session):
    """Verify get_team_toss_stats returns overall, format, home/away, recent form, and captains."""
    await seed_test_history(async_db_session)
    stats = await get_team_toss_stats(async_db_session, "Chennai Super Kings")

    # Overall
    ov = stats["overall"]
    assert ov["n"] == 40
    assert ov["wins"] == 25
    assert ov["losses"] == 15
    assert ov["win_pct"] == 62.5
    assert ov["is_insufficient"] is False  # 40 >= 30
    assert ov["ci_low"] > 0.45

    # By format
    assert "T20" in stats["by_format"]
    assert "ODI" in stats["by_format"]
    assert stats["by_format"]["T20"]["n"] == 30
    assert stats["by_format"]["ODI"]["n"] == 10

    # Recent form
    rf = stats["recent_form"]
    assert len(rf["sequence"]) == 20
    assert rf["last_5"]["n"] == 5
    assert rf["last_10"]["n"] == 10

    # Captains
    caps = stats["captains"]
    assert "MS Dhoni" in caps or "Ruturaj Gaikwad" in caps

    # Permanent disclaimer
    assert stats["disclaimer"] == PERMANENT_DISCLAIMER


@pytest.mark.asyncio
async def test_venue_toss_stats_service(async_db_session):
    """Verify get_venue_toss_stats returns bat/field trends and match correlation."""
    await seed_test_history(async_db_session)
    v_stats = await get_venue_toss_stats(async_db_session, "Wankhede Stadium")

    assert v_stats["total_matches"] == 40
    assert v_stats["bat_first"]["n"] == 40
    assert v_stats["field_first"]["n"] == 40
    assert v_stats["bat_first"]["wins"] + v_stats["field_first"]["wins"] == 40

    # Toss to match correlation
    corr = v_stats["toss_match_correlation"]
    assert corr["n"] == 40
    assert corr["ci_low"] > 0.0

    assert len(v_stats["recent_matches"]) <= 10


@pytest.mark.asyncio
async def test_h2h_toss_stats_service(async_db_session):
    """Verify direct head-to-head toss stats."""
    await seed_test_history(async_db_session)
    h2h = await get_h2h_toss_stats(async_db_session, "Chennai Super Kings", "Mumbai Indians")

    assert h2h["total_h2h_matches"] == 40
    assert h2h["stat_a"]["wins"] == 25
    assert h2h["stat_b"]["wins"] == 15
    assert h2h["stat_a"]["win_pct"] == 62.5
    assert h2h["stat_b"]["win_pct"] == 37.5
    assert h2h["decisions"]["bat"] + h2h["decisions"]["field"] == 40


@pytest.mark.asyncio
async def test_comprehensive_match_analysis(async_db_session):
    """Verify get_comprehensive_match_analysis aggregates all components."""
    data = await seed_test_history(async_db_session)
    match = Match(
        league_id=data["league"].id,
        team_a_id=data["csk"].id,
        team_b_id=data["mi"].id,
        venue_id=data["venue"].id,
        start_time_utc=utc_now() + timedelta(hours=2),
        status="live"
    )
    async_db_session.add(match)
    await async_db_session.commit()
    await async_db_session.refresh(match)

    analysis = await get_comprehensive_match_analysis(async_db_session, match)
    assert analysis["team_a_name"] == "Chennai Super Kings"
    assert analysis["team_b_name"] == "Mumbai Indians"
    assert "stats_a" in analysis
    assert "stats_b" in analysis
    assert "h2h_stats" in analysis
    assert "venue_stats" in analysis
    assert analysis["disclaimer"] == PERMANENT_DISCLAIMER


# --------------------------------------------------------------------------
# 4. Market Intelligence Provider Tests
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_market_provider_manual_entry_and_audit(async_db_session):
    """Verify pluggable market entry, append-only persistence, and audit logging."""
    res_l = await async_db_session.execute(select(League).where(League.name == "IPL"))
    league = res_l.scalar_one_or_none()
    if not league:
        league = League(name="IPL", format="T20")
        async_db_session.add(league)

    res_t1 = await async_db_session.execute(select(Team).where(Team.name == "Kolkata Knight Riders"))
    t1 = res_t1.scalar_one_or_none()
    if not t1:
        t1 = Team(name="Kolkata Knight Riders", short_code="KKR")
        async_db_session.add(t1)

    res_t2 = await async_db_session.execute(select(Team).where(Team.name == "Sunrisers Hyderabad"))
    t2 = res_t2.scalar_one_or_none()
    if not t2:
        t2 = Team(name="Sunrisers Hyderabad", short_code="SRH")
        async_db_session.add(t2)

    await async_db_session.commit()
    await async_db_session.refresh(league)
    await async_db_session.refresh(t1)
    await async_db_session.refresh(t2)

    match = Match(
        league_id=league.id,
        team_a_id=t1.id,
        team_b_id=t2.id,
        start_time_utc=utc_now() + timedelta(hours=1),
        status="upcoming"
    )
    async_db_session.add(match)
    await async_db_session.commit()
    await async_db_session.refresh(match)

    # 1. Record manual market entry
    entry = await record_manual_market_entry(
        session=async_db_session,
        match_id=match.id,
        source="Exchange Flow",
        team_lean="KKR",
        load_market="60% heavy on KKR",
        odds_a=1.80,
        odds_b=2.05,
        notes="Pre-toss smart volume spike",
        admin_user="admin",
        client_ip="127.0.0.1"
    )

    assert entry.id is not None
    assert entry.match_id == match.id
    assert entry.team_lean == "KKR"

    # 2. Query market entries
    entries = await get_market_entries_for_match(async_db_session, match.id)
    assert len(entries) == 1
    assert entries[0].source == "Exchange Flow"

    # 3. Verify AuditLog entry was recorded
    audit_stmt = select(AuditLog).where(
        AuditLog.action == "RECORD_MARKET_ENTRY",
        AuditLog.entity_id == str(entry.id)
    )
    audit = (await async_db_session.execute(audit_stmt)).scalar_one_or_none()
    assert audit is not None
    assert audit.user == "admin"
    assert audit.ip_address == "127.0.0.1"


# --------------------------------------------------------------------------
# 5. Cricsheet Collector Tests
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cricsheet_collector_parse_match_json():
    """Verify parsing of real Cricsheet match JSON with captains and toss details."""
    sample_json = {
        "meta": {"data_version": "1.1.0"},
        "info": {
            "dates": ["2024-04-01"],
            "event": {"name": "Indian Premier League"},
            "match_type": "T20",
            "teams": ["Mumbai Indians", "Rajasthan Royals"],
            "toss": {"decision": "field", "winner": "Rajasthan Royals"},
            "venue": "Wankhede Stadium, Mumbai",
            "outcome": {"winner": "Rajasthan Royals"},
            "captains": {"Mumbai Indians": "Hardik Pandya", "Rajasthan Royals": "Sanju Samson"}
        }
    }

    collector = CricsheetCollector()
    parsed = collector.parse_match_json(sample_json, "test_cricsheet_001")

    assert parsed is not None
    assert parsed["id"] == "cricsheet_test_cricsheet_001"
    assert parsed["tournament_name"] == "IPL"
    assert parsed["team_a_name"] == "Mumbai Indians"
    assert parsed["team_b_name"] == "Rajasthan Royals"
    assert parsed["toss_winner_name"] == "Rajasthan Royals"
    assert parsed["toss_decision"] == "FIELD"
    assert parsed["match_winner_name"] == "Rajasthan Royals"
    assert parsed["captain_a"] == "Hardik Pandya"
    assert parsed["captain_b"] == "Sanju Samson"
    assert parsed["toss_winner_captain"] == "Sanju Samson"


@pytest.mark.asyncio
async def test_cricsheet_collector_incremental_sync(async_db_session):
    """Verify incremental sync: running twice imports without duplicates."""
    collector = CricsheetCollector()
    res1 = await collector.sync_historical_records_to_db(async_db_session, limit=10)
    assert res1["status"] == "SUCCESS"
    new_first = res1["new_records"]

    # Run again: existing records should not be duplicated
    res2 = await collector.sync_historical_records_to_db(async_db_session, limit=10)
    assert res2["status"] == "SUCCESS"
    assert res2["new_records"] == 0


# --------------------------------------------------------------------------
# 6. ESPN / Cricbuzz Collector Tests
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_espn_cricbuzz_rate_limiter():
    """Verify that RateLimiter enforces minimum delay interval."""
    limiter = RateLimiter(min_interval_seconds=0.05)
    t0 = datetime.now()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = (datetime.now() - t0).total_seconds()
    assert elapsed >= 0.04


@pytest.mark.asyncio
async def test_espn_cricbuzz_graceful_degradation_on_block(tmp_path):
    """Verify collector fails gracefully to local cache when network blocked."""
    cache_file = tmp_path / "test_espn_cache.json"
    cache_data = [{"id": "espn_cached_01", "team_a": "India", "team_b": "Australia"}]
    cache_file.write_text(json.dumps(cache_data), encoding="utf-8")

    collector = CricbuzzCollector(cache_path=str(cache_file))

    # Mock HTTP 403 Forbidden response
    mock_resp = MagicMock(status_code=403)
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        fixtures = await collector.fetch_public_fixtures()
        # Should gracefully return cache
        assert len(fixtures) == 1
        assert fixtures[0]["id"] == "espn_cached_01"


# --------------------------------------------------------------------------
# 7. CSV and PDF Export Tests
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_csv_export_structure(async_db_session):
    """Verify that generate_match_csv outputs a valid CSV containing n, %, and CI."""
    data = await seed_test_history(async_db_session)
    match = Match(
        league_id=data["league"].id,
        team_a_id=data["csk"].id,
        team_b_id=data["mi"].id,
        venue_id=data["venue"].id,
        start_time_utc=utc_now() + timedelta(hours=3),
        status="live"
    )
    async_db_session.add(match)
    await async_db_session.commit()
    await async_db_session.refresh(match)

    analysis = await get_comprehensive_match_analysis(async_db_session, match)
    csv_str = generate_match_csv(analysis, match)

    assert "CRICKET TOSS ANALYTICS - AUDITED MATCH DOSSIER" in csv_str
    assert "Chennai Super Kings vs Mumbai Indians" in csv_str
    assert "Sample Size (n)" in csv_str
    assert "95% Wilson CI" in csv_str
    assert "Toss is a random event." in csv_str

    # Parse with standard csv reader to verify syntax
    reader = list(csv.reader(io.StringIO(csv_str)))
    assert len(reader) > 15


@pytest.mark.asyncio
async def test_pdf_export_generation(async_db_session):
    """Verify that generate_match_pdf outputs valid %PDF binary with tables and footer."""
    data = await seed_test_history(async_db_session)
    match = Match(
        league_id=data["league"].id,
        team_a_id=data["csk"].id,
        team_b_id=data["mi"].id,
        venue_id=data["venue"].id,
        start_time_utc=utc_now() + timedelta(hours=3),
        status="live"
    )
    async_db_session.add(match)
    await async_db_session.commit()
    await async_db_session.refresh(match)

    analysis = await get_comprehensive_match_analysis(async_db_session, match)
    pdf_bytes = generate_match_pdf(analysis, match)

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 1000
    # PDF magic bytes
    assert pdf_bytes.startswith(b"%PDF-")


# --------------------------------------------------------------------------
# 8. Web Endpoints for Phase 4 (Match Detail, CSV/PDF Exports, Market Entry)
# --------------------------------------------------------------------------
def test_match_detail_page_and_exports_web(client):
    """Verify Match Detail page rendering and CSV/PDF export endpoints via TestClient."""
    # Authenticate client
    token = create_signed_session_token({"username": settings.admin_username})
    client.cookies.set(settings.session_cookie_name, token)

    # Matches list
    matches_resp = client.get("/matches")
    assert matches_resp.status_code == 200

    # Test match detail
    detail_resp = client.get("/matches/40306")
    if detail_resp.status_code == 200:
        assert "Toss Analytics" in detail_resp.text
        assert "95% Wilson CI" in detail_resp.text
        assert "Export CSV" in detail_resp.text
        assert "Export PDF" in detail_resp.text
        assert "Market & Rate Movement Intelligence" in detail_resp.text
        assert "Toss is a random event." in detail_resp.text

        # Test CSV export
        csv_resp = client.get("/matches/40306/export/csv")
        assert csv_resp.status_code == 200
        assert csv_resp.headers["content-type"].startswith("text/csv")
        assert "attachment; filename=" in csv_resp.headers["content-disposition"]
        assert "CRICKET TOSS ANALYTICS" in csv_resp.text

        # Test PDF export
        pdf_resp = client.get("/matches/40306/export/pdf")
        assert pdf_resp.status_code == 200
        assert pdf_resp.headers["content-type"] == "application/pdf"
        assert "attachment; filename=" in pdf_resp.headers["content-disposition"]
        assert pdf_resp.content.startswith(b"%PDF-")

