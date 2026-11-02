"""Database seeder with synthetic fixtures, historical toss records, and market odds."""

import os
import json
import sqlite3
from typing import Optional, Dict, Any
from core.database.connection import init_db, get_connection
from core.database.repository import Repository
from core.models.schemas import (
    Tournament, Venue, Team, Match, TossResult, MarketSnapshot,
    MatchFormat, TournamentCategory, MatchStatus, TossDecision, MarketType
)
from core.timezone import parse_to_ist

SYNTHETIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "synthetic")


def seed_synthetic_data(db_path: Optional[str] = None) -> Dict[str, int]:
    """Seed the database with synthetic data for local development and testing."""
    init_db(db_path)
    conn = get_connection(db_path)
    repo = Repository(conn)

    stats = {
        "tournaments": 0,
        "teams": 0,
        "venues": 0,
        "matches": 0,
        "historical_tosses": 0,
        "market_snapshots": 0
    }

    # 1. Ingest Sample Fixtures
    fixtures_file = os.path.join(SYNTHETIC_DIR, "sample_fixtures.json")
    if os.path.exists(fixtures_file):
        with open(fixtures_file, "r", encoding="utf-8") as f:
            fixtures = json.load(f)

        for fix in fixtures:
            # Upsert Tournament
            t_data = fix["tournament"]
            repo.upsert_tournament(Tournament(
                id=t_data["id"],
                name=t_data["name"],
                category=TournamentCategory(t_data.get("category", "OTHER")),
                format=MatchFormat(t_data.get("format", "T20"))
            ))
            stats["tournaments"] += 1

            # Upsert Teams
            for team_key in ("team_a", "team_b"):
                tm = fix[team_key]
                repo.upsert_team(Team(
                    id=tm["id"],
                    name=tm["name"],
                    short_code=tm["short_code"],
                    category=t_data.get("category", "OTHER"),
                    aliases=tm.get("aliases", [])
                ))
                stats["teams"] += 1

            # Upsert Venue
            if "venue" in fix and fix["venue"]:
                vn = fix["venue"]
                repo.upsert_venue(Venue(
                    id=vn["id"],
                    name=vn["name"],
                    city=vn.get("city"),
                    country=vn.get("country")
                ))
                stats["venues"] += 1

            # Upsert Match
            match_obj = Match(
                id=fix["id"],
                external_provider_id=fix.get("external_provider_id"),
                tournament_id=t_data["id"],
                team_a_id=fix["team_a"]["id"],
                team_b_id=fix["team_b"]["id"],
                venue_id=fix["venue"]["id"] if "venue" in fix and fix["venue"] else None,
                match_format=MatchFormat(fix["match_format"]),
                scheduled_start_ist=parse_to_ist(fix["scheduled_start_ist"]),
                expected_toss_ist=parse_to_ist(fix.get("expected_toss_ist")),
                status=MatchStatus(fix["status"]),
                data_source_id=fix["data_source_id"],
                last_updated_ist=parse_to_ist(fix["scheduled_start_ist"]),
                data_freshness_status="FRESH"
            )
            repo.upsert_match(match_obj)
            stats["matches"] += 1

            # If Toss Result is already confirmed (e.g. for completed match)
            if "toss_result" in fix and fix["toss_result"]:
                tr = fix["toss_result"]
                repo.upsert_toss_result(TossResult(
                    match_id=fix["id"],
                    toss_winner_id=tr["toss_winner_id"],
                    toss_decision=TossDecision(tr["toss_decision"]),
                    official_source_url=tr.get("official_source_url"),
                    source_provider_id=fix["data_source_id"],
                    verified_at_ist=parse_to_ist(tr["verified_at_ist"])
                ))

    # 2. Ingest Historical Toss Database
    hist_file = os.path.join(SYNTHETIC_DIR, "sample_historical_toss.json")
    if os.path.exists(hist_file):
        with open(hist_file, "r", encoding="utf-8") as f:
            historical = json.load(f)

        for h in historical:
            repo.insert_historical_match(
                hist_id=h["id"],
                match_date=h["date"],
                tournament_id=h.get("tournament"),
                match_format=h.get("format", "T20"),
                team_a_id=h["team_a"],
                team_b_id=h["team_b"],
                venue_id=h.get("venue"),
                toss_winner_id=h["toss_winner"],
                toss_decision=h.get("toss_decision"),
                source="synthetic_historical_archive"
            )
            stats["historical_tosses"] += 1

    # 3. Ingest Market Odds
    odds_file = os.path.join(SYNTHETIC_DIR, "sample_market_odds.json")
    if os.path.exists(odds_file):
        with open(odds_file, "r", encoding="utf-8") as f:
            odds_list = json.load(f)

        for o in odds_list:
            repo.insert_market_snapshot(MarketSnapshot(
                match_id=o["match_id"],
                provider_id=o["provider_id"],
                bookmaker=o["bookmaker"],
                market_type=MarketType(o["market_type"]),
                team_a_odds=o["team_a_odds"],
                team_b_odds=o["team_b_odds"],
                team_a_implied_prob=o["team_a_implied_prob"],
                team_b_implied_prob=o["team_b_implied_prob"],
                margin_percentage=o["margin_percentage"],
                snapshot_timestamp_ist=parse_to_ist(o["snapshot_timestamp_ist"]),
                is_pre_toss=o.get("is_pre_toss", True),
                is_stale=o.get("is_stale", False)
            ))
            stats["market_snapshots"] += 1

    conn.close()
    return stats


if __name__ == "__main__":
    res = seed_synthetic_data()
    print("Seeding complete:", res)
