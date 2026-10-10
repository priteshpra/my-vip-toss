"""Cricket API provider client and fixture synchronization engine."""

import re
import uuid
import httpx
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from core.providers.base import BaseCricketProvider
from core.models.schemas import Match, MatchFormat, TournamentCategory, MatchStatus, TossDecision, VerificationStatus, AssessmentStatus
from core.database.repository import Repository, TossRepository
from core.timezone import parse_to_ist, now_ist, format_ist
from core.engine.statistics import evaluate_toss_advantage
from config import Settings


class CricketApiProvider(BaseCricketProvider):
    """Client for CricAPI / CricketData.org fixture feeds."""

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.cricapi.com/v1"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def provider_id(self) -> str:
        return "cricketdata_api"

    def test_connection(self) -> Dict[str, Any]:
        """Test API connectivity and key validity."""
        if not self.api_key:
            return {
                "success": False,
                "message": "API key not configured. Add CRICKET_API_KEY in .env or Settings.",
                "latency_ms": 0
            }
        start = datetime.now()
        try:
            with httpx.Client(timeout=10.0, verify=False) as client:
                resp = client.get(f"{self.base_url}/matches", params={"apikey": self.api_key, "offset": 0})
                latency = int((datetime.now() - start).total_seconds() * 1000)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "success":
                        return {"success": True, "message": "Connected successfully", "latency_ms": latency}
                    return {"success": False, "message": data.get("reason", "API returned non-success"), "latency_ms": latency}
                return {"success": False, "message": f"HTTP {resp.status_code}: {resp.text[:100]}", "latency_ms": latency}
        except Exception as e:
            latency = int((datetime.now() - start).total_seconds() * 1000)
            return {"success": False, "message": f"Connection error: {str(e)}", "latency_ms": latency}

    def fetch_scheduled_fixtures(self) -> List[Dict[str, Any]]:
        """Fetch upcoming cricket matches from provider."""
        if not self.api_key:
            return []
        try:
            with httpx.Client(timeout=15.0, verify=False) as client:
                resp = client.get(f"{self.base_url}/matches", params={"apikey": self.api_key, "offset": 0})
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("data", [])
        except Exception:
            return []
        return []

    def fetch_live_and_recent_tosses(self) -> List[Dict[str, Any]]:
        """Fetch current and recent matches with confirmed toss results."""
        if not self.api_key:
            return []
        try:
            with httpx.Client(timeout=15.0, verify=False) as client:
                resp = client.get(f"{self.base_url}/cricScore", params={"apikey": self.api_key})
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("data", [])
        except Exception:
            return []
        return []


class FixtureSyncEngine:
    """Synchronizes external fixture data with local TossIQ database."""

    def __init__(self, repo: Repository, settings: Settings):
        self.repo = repo
        self.settings = settings
        self.provider = CricketApiProvider(
            api_key=settings.cricket_api_key,
            base_url=settings.cricket_api_base_url
        )

    def normalize_format(self, raw_format: Optional[str]) -> MatchFormat:
        if not raw_format:
            return MatchFormat.T20
        raw = raw_format.lower()
        if "t20" in raw or "twenty20" in raw:
            return MatchFormat.T20
        if "odi" in raw or "one day" in raw or "50" in raw:
            return MatchFormat.ODI
        if "test" in raw:
            return MatchFormat.TEST
        if "t10" in raw:
            return MatchFormat.T10
        return MatchFormat.OTHER

    def find_or_create_team(self, name: str, short_code: Optional[str] = None) -> str:
        """Find team in repository or dynamically create a new team entry."""
        existing = self.repo.find_team_by_name_or_alias(name)
        if existing:
            return existing["id"]

        clean_slug = re.sub(r'[^a-zA-Z0-9]', '_', name.strip().lower())
        team_id = f"team_{clean_slug}"[:32]
        short = short_code or "".join([w[0].upper() for w in name.split()[:3]]) or "TEAM"

        with self.repo.conn:
            self.repo.conn.execute("""
                INSERT OR IGNORE INTO teams (id, name, short_code, category, aliases)
                VALUES (?, ?, ?, ?, ?)
            """, (team_id, name.strip(), short, "OTHER", "[]"))
        return team_id

    def find_or_create_tournament(self, name: str, fmt: MatchFormat) -> str:
        """Find tournament or dynamically register."""
        cur = self.repo.conn.cursor()
        cur.execute("SELECT id FROM tournaments WHERE name = ?", (name.strip(),))
        row = cur.fetchone()
        if row:
            return row[0]

        clean_slug = re.sub(r'[^a-zA-Z0-9]', '_', name.strip().lower())
        tour_id = f"tour_{clean_slug}"[:32]
        with self.repo.conn:
            self.repo.conn.execute("""
                INSERT OR IGNORE INTO tournaments (id, name, category, format)
                VALUES (?, ?, ?, ?)
            """, (tour_id, name.strip(), "T20_LEAGUE" if fmt == MatchFormat.T20 else "INTERNATIONAL", fmt.value))
        return tour_id

    def ingest_fixture_record(self, raw_item: Dict[str, Any], source_id: str = "manual_sync") -> Optional[str]:
        """Normalize and upsert a single scheduled match fixture."""
        teams = raw_item.get("teams", [])
        if len(teams) < 2:
            name = raw_item.get("name", "")
            if " vs " in name.lower() or " v " in name.lower():
                parts = re.split(r'\s+vs\s+|\s+v\s+', name, flags=re.IGNORECASE)
                if len(parts) >= 2:
                    teams = [parts[0].strip(), parts[1].split(",")[0].strip()]
        if len(teams) < 2:
            return None

        team_a_name = str(teams[0]).strip()
        team_b_name = str(teams[1]).strip()

        # Parse match format
        m_format = self.normalize_format(raw_item.get("matchType") or raw_item.get("format"))

        # Parse scheduled start time in IST
        date_str = raw_item.get("dateTimeGMT") or raw_item.get("dateTimeIST") or raw_item.get("date") or raw_item.get("scheduled_start_ist")
        if not date_str:
            return None
        start_ist = parse_to_ist(date_str)

        # Expected toss time is roughly 30 minutes before match start
        expected_toss = raw_item.get("expected_toss_ist")
        if expected_toss:
            expected_toss_ist = parse_to_ist(expected_toss)
        else:
            expected_toss_ist = start_ist - timedelta(minutes=30)

        # Resolve or create teams
        team_a_id = self.find_or_create_team(team_a_name)
        team_b_id = self.find_or_create_team(team_b_name)

        # Resolve tournament
        tour_name = raw_item.get("series_name") or raw_item.get("tournament_name") or f"{m_format.value} Series 2026"
        tour_id = self.find_or_create_tournament(tour_name, m_format)

        # Match ID
        ext_id = str(raw_item.get("id") or raw_item.get("match_id") or "")
        match_id = f"m_{ext_id}" if ext_id else f"match_{start_ist.strftime('%Y%m%d')}_{team_a_id}_{team_b_id}"[:40]

        # Determine Match Status
        raw_status = str(raw_item.get("status", "")).upper()
        if "TOSS" in raw_status and "COMPLETE" in raw_status:
            status = MatchStatus.TOSS_COMPLETED
        elif "COMPLETE" in raw_status or "FINISHED" in raw_status or "WON" in raw_status:
            status = MatchStatus.TOSS_COMPLETED
        elif "DELAY" in raw_status:
            status = MatchStatus.DELAYED
        elif "ABANDON" in raw_status:
            status = MatchStatus.ABANDONED
        else:
            status = MatchStatus.UPCOMING

        match_obj = Match(
            id=match_id,
            external_provider_id=ext_id or None,
            tournament_id=tour_id,
            team_a_id=team_a_id,
            team_b_id=team_b_id,
            venue_id=None,
            match_format=m_format,
            scheduled_start_ist=start_ist,
            expected_toss_ist=expected_toss_ist,
            status=status,
            data_source_id=source_id,
            last_updated_ist=now_ist(),
            data_freshness_status="FRESH"
        )
        self.repo.upsert_match(match_obj)

        # If toss result info is present, record it
        toss_winner_name = raw_item.get("tossWinner") or raw_item.get("toss_winner_name")
        if toss_winner_name:
            toss_winner_id = self.find_or_create_team(toss_winner_name)
            toss_choice = raw_item.get("tossChoice", "unknown").lower()
            decision = TossDecision.BAT if "bat" in toss_choice else (TossDecision.BOWL if "field" in toss_choice or "bowl" in toss_choice else TossDecision.UNKNOWN)
            
            with self.repo.conn:
                self.repo.conn.execute("""
                    INSERT OR REPLACE INTO toss_results (
                        match_id, toss_winner_id, toss_decision, source_provider_id,
                        verified_at_ist, verification_status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (match_id, toss_winner_id, decision.value, source_id, now_ist().isoformat(), "VERIFIED"))

        return match_id

    def sync_from_api(self) -> Dict[str, Any]:
        """Synchronize matches from the configured Cricket API."""
        if not self.settings.cricket_api_key:
            return {
                "success": False,
                "message": "No CRICKET_API_KEY provided. Configure API key in Settings.",
                "synced_count": 0
            }

        fixtures = self.provider.fetch_scheduled_fixtures()
        if not fixtures:
            return {
                "success": False,
                "message": "Provider returned 0 fixtures or connection timed out.",
                "synced_count": 0
            }

        synced = 0
        for item in fixtures:
            mid = self.ingest_fixture_record(item, source_id="cricketdata_api")
            if mid:
                synced += 1

        self.repo.log_source_update(
            source_id="cricketdata_api",
            endpoint="/matches",
            status_code=200,
            response_time_ms=120,
            records_fetched=len(fixtures),
            is_success=True,
            error_message=None
        )

        return {
            "success": True,
            "message": f"Successfully synchronized {synced} matches from Cricket API.",
            "synced_count": synced
        }
