"""GamingHelper collector and adapter with exponential backoff, session re-login, and offline caching.

Adheres strictly to honesty rules:
- No captcha / anti-bot bypass.
- Configurable endpoints and selectors via settings.
- Respectful retries with exponential backoff and timeouts.
- Matches with unidentified leagues are assigned to 'Local/Unknown' (never dropped).
"""

import os
import json
import time
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo
import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import utc_now, ensure_utc
from app.models import Match, Team, League, Venue, MarketEntry, CollectorHealth, Prediction
from app.services.normalizer import normalizer, normalize_venue
from app.services.match_service import upsert_match
from app.services.prediction_service import lock_prediction
from app.schemas import PredictionCreate

IST_TZ = ZoneInfo("Asia/Kolkata")
LOCAL_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "gaming_helper_matches.json"
)


def parse_match_start_utc(date_str: Optional[str], time_str: Optional[str]) -> datetime:
    """
    Parse date string (e.g. '2026-11-01') and time string (e.g. '02:30 AM IST') into UTC datetime.
    Falls back gracefully to current UTC time if unparseable.
    """
    now = utc_now()
    if not date_str:
        return now

    d_clean = date_str.strip()
    # Try parsing full ISO format
    try:
        dt = datetime.fromisoformat(d_clean)
        return ensure_utc(dt)
    except Exception:
        pass

    # Extract date parts (YYYY-MM-DD)
    try:
        date_part = datetime.strptime(d_clean, "%Y-%m-%d").date()
    except Exception:
        date_part = now.date()

    if not time_str:
        dt_naive = datetime.combine(date_part, datetime.min.time())
        return dt_naive.replace(tzinfo=timezone.utc)

    # Clean time string, e.g. "02:30 AM IST" -> "02:30 AM"
    t_clean = time_str.upper().replace("IST", "").strip()
    time_part = None

    for fmt in ["%I:%M %p", "%I:%M%p", "%H:%M", "%I %p"]:
        try:
            time_part = datetime.strptime(t_clean, fmt).time()
            break
        except Exception:
            continue

    if not time_part:
        time_part = datetime.min.time()

    dt_ist = datetime.combine(date_part, time_part, tzinfo=IST_TZ)
    return dt_ist.astimezone(timezone.utc)


class GamingHelperAdapter:
    """
    Adapter pattern implementation for GamingHelper live bet feeds.
    Handles authentication, exponential backoff retries, session re-login on expiry,
    and automatic caching of last good data.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        login_url: Optional[str] = None,
        live_url: Optional[str] = None,
        cache_file: Optional[str] = None,
    ):
        self.base_url = base_url or settings.gaming_helper_base_url
        self.login_url = login_url or settings.gaming_helper_login_url
        self.live_url = live_url or settings.gaming_helper_live_url
        self.cache_file = cache_file or LOCAL_CACHE_FILE
        self.phone = settings.gaming_helper_phone
        self.password = settings.gaming_helper_password
        self.enabled = settings.gaming_helper_enabled
        self.timeout = settings.gaming_helper_timeout
        self.max_retries = settings.gaming_helper_max_retries
        self.backoff_factor = settings.gaming_helper_backoff_factor
        self.token: Optional[str] = None
        self.token_expiry: float = 0

    async def authenticate(self) -> bool:
        """
        Authenticate against GamingHelper login endpoint and store access token.
        """
        if not self.phone or not self.password:
            logger.debug("GamingHelper credentials not configured in environment.")
            return False

        payload = {"phone": self.phone.strip(), "password": self.password.strip()}
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TossAnalytics/1.0",
            "Accept": "application/json",
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
                    resp = await client.post(self.login_url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        token = (
                            data.get("newAccessToken")
                            or data.get("access-token")
                            or data.get("token")
                            or data.get("data", {}).get("token")
                        )
                        if token:
                            self.token = str(token)
                            # Default 2-hour token validity
                            self.token_expiry = time.time() + 7200
                            logger.info("Successfully authenticated with GamingHelper API.")
                            return True
                    logger.warning(
                        f"GamingHelper login attempt {attempt} returned HTTP {resp.status_code}: {resp.text[:100]}"
                    )
            except Exception as e:
                logger.warning(f"GamingHelper login connection attempt {attempt} failed: {e}")

            if attempt < self.max_retries:
                await asyncio.sleep(self.backoff_factor ** attempt)

        return False

    async def fetch_live_matches(self) -> Tuple[List[Dict[str, Any]], Optional[int]]:
        """
        Fetch matches from live feed with exponential backoff and session re-login.
        Returns (matches_list, response_time_ms).
        Falls back to last cached good data if network/API fails.
        """
        start_time = time.time()

        if self.enabled and self.phone and self.password:
            for attempt in range(1, self.max_retries + 1):
                # Ensure token is valid or re-authenticate
                if not self.token or time.time() >= self.token_expiry:
                    auth_ok = await self.authenticate()
                    if not auth_ok:
                        logger.warning("Authentication failed; falling back to cached snapshot.")
                        break

                headers = {
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TossAnalytics/1.0",
                    "Accept": "application/json",
                }

                try:
                    async with httpx.AsyncClient(timeout=self.timeout, verify=False) as client:
                        resp = await client.get(self.live_url, headers=headers)

                        # Handle token expiration: re-login once
                        if resp.status_code in (401, 403):
                            logger.info("GamingHelper token expired (HTTP 401/403); re-authenticating...")
                            self.token = None
                            await self.authenticate()
                            continue

                        if resp.status_code == 200:
                            latency_ms = int((time.time() - start_time) * 1000)
                            data = resp.json()
                            raw_matches = (
                                data.get("data")
                                or data.get("bets")
                                or data.get("matches")
                                or (data if isinstance(data, list) else [])
                            )
                            if isinstance(raw_matches, list) and len(raw_matches) > 0:
                                self.save_to_cache(raw_matches)
                                return raw_matches, latency_ms
                except Exception as e:
                    logger.warning(f"GamingHelper poll attempt {attempt} failed: {e}")

                if attempt < self.max_retries:
                    await asyncio.sleep(self.backoff_factor ** attempt)

        # Fallback to local cache
        cached = self.load_from_cache()
        latency_ms = int((time.time() - start_time) * 1000)
        return cached, latency_ms

    def save_to_cache(self, data: List[Dict[str, Any]]) -> None:
        """Cache last good data locally for offline resilience."""
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to cache GamingHelper matches to {self.cache_file}: {e}")

    def load_from_cache(self) -> List[Dict[str, Any]]:
        """Load local fallback match records from cache."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        logger.info(f"Loaded {len(data)} matches from local GamingHelper cache.")
                        return data
            except Exception as e:
                logger.error(f"Error reading local GamingHelper cache: {e}")
        return []


class GamingHelperCollector:
    """
    Orchestrates GamingHelper ingestion:
    - Normalizes teams, leagues, and venues.
    - Assigns unidentifiable leagues to 'Local/Unknown' (never drops matches).
    - Upserts matches without duplicates.
    - Updates collector health metrics.
    """

    def __init__(self, adapter: Optional[GamingHelperAdapter] = None):
        self.adapter = adapter or GamingHelperAdapter()

    def load_local_fallback(self) -> List[Dict[str, Any]]:
        """Backward-compatible alias for local fallback."""
        return self.adapter.load_from_cache()

    async def sync_matches(self, session: AsyncSession) -> Dict[str, Any]:
        """
        Fetch all matches from GamingHelper and upsert into database.
        Returns diagnostic summary dictionary.
        """
        matches_data, latency_ms = await self.adapter.fetch_live_matches()
        if not matches_data:
            await self._update_health(
                session=session,
                status="DEGRADED",
                latency_ms=latency_ms,
                message="No matches received from GamingHelper live feed or cache."
            )
            return {"status": "NO_DATA", "matches_synced": 0}

        synced_count = 0
        new_count = 0
        now_utc = utc_now()

        for m in matches_data:
            ref_id = str(m.get("id", "")).strip()
            if not ref_id:
                continue

            # 1. Normalise League Name (Rule 2: Unidentified go to 'Local/Unknown')
            raw_tourn = m.get("tournament_name") or m.get("tournament") or ""
            canonical_league = normalizer.normalize_league(raw_tourn)
            if not canonical_league:
                canonical_league = "Local/Unknown"

            # Get or create League
            stmt_l = select(League).where(League.name == canonical_league)
            res_l = await session.execute(stmt_l)
            league_obj = res_l.scalar_one_or_none()
            if not league_obj:
                league_obj = League(name=canonical_league, format="T20")
                session.add(league_obj)
                await session.commit()
                await session.refresh(league_obj)

            # 2. Normalise Teams
            raw_a = m.get("team_a_name") or m.get("team_a") or "Team A"
            raw_b = m.get("team_b_name") or m.get("team_b") or "Team B"

            canonical_a = normalizer.normalize_team(raw_a) or raw_a.strip()
            canonical_b = normalizer.normalize_team(raw_b) or raw_b.strip()

            team_objs = {}
            for t_name, short_hint in [(canonical_a, m.get("team_a_short")), (canonical_b, m.get("team_b_short"))]:
                stmt_t = select(Team).where(Team.name == t_name)
                res_t = await session.execute(stmt_t)
                t_obj = res_t.scalar_one_or_none()
                if not t_obj:
                    code = (short_hint or t_name[:3]).upper()
                    t_obj = Team(name=t_name, short_code=code)
                    session.add(t_obj)
                    await session.commit()
                    await session.refresh(t_obj)
                team_objs[t_name] = t_obj

            team_a_obj = team_objs[canonical_a]
            team_b_obj = team_objs[canonical_b]

            # 3. Normalise Venue
            raw_venue = m.get("venue")
            venue_id = None
            if raw_venue:
                norm_v = normalize_venue(raw_venue)
                if norm_v:
                    stmt_v = select(Venue).where(Venue.name == norm_v)
                    res_v = await session.execute(stmt_v)
                    v_obj = res_v.scalar_one_or_none()
                    if not v_obj:
                        v_obj = Venue(name=norm_v)
                        session.add(v_obj)
                        await session.commit()
                        await session.refresh(v_obj)
                    venue_id = v_obj.id

            # 4. Parse Start Time in UTC
            start_utc = parse_match_start_utc(m.get("match_date"), m.get("time_str"))

            # Determine initial status
            raw_status = str(m.get("status", "UPCOMING")).upper()
            if raw_status in ("TOSS_DONE", "TOSS_COMPLETED"):
                match_status = "toss_done"
            elif start_utc <= now_utc or m.get("is_live") == 1:
                match_status = "live"
            else:
                match_status = "upcoming"

            # 5. Check if match already exists to avoid duplicates
            stmt_exist = select(Match).where(
                (Match.gaming_helper_ref == ref_id) | (Match.source_match_id == ref_id)
            )
            res_exist = await session.execute(stmt_exist)
            existing_match = res_exist.scalar_one_or_none()

            match_data = {
                "gaming_helper_ref": ref_id,
                "source_match_id": ref_id,
                "league_id": league_obj.id,
                "team_a_id": team_a_obj.id,
                "team_b_id": team_b_obj.id,
                "venue_id": venue_id,
                "start_time_utc": start_utc,
                "status": match_status if not existing_match or existing_match.status != "toss_done" else "toss_done",
                "odds_a": float(m.get("odds_a", 100)),
                "odds_b": float(m.get("odds_b", 100)),
            }

            match_obj = await upsert_match(session, match_data)
            synced_count += 1
            if not existing_match:
                new_count += 1

            # 6. Record market intelligence entry (append-only)
            market_entry = MarketEntry(
                match_id=match_obj.id,
                source="GamingHelper Online",
                team_lean=m.get("recommended_team"),
                load_market=m.get("market_load_text"),
                odds_a=float(m.get("odds_a", 100)),
                odds_b=float(m.get("odds_b", 100)),
                notes=m.get("punter_rationale")
            )
            session.add(market_entry)
            await session.commit()

            # 7. Lock pre-toss prediction if recommendation exists and not already locked
            rec_team = m.get("recommended_team")
            action = m.get("action_type") or ("PLAY" if rec_team else "SKIP")
            if rec_team or action == "SKIP":
                pred_team_id = None
                if action == "PLAY" and rec_team:
                    matched_rec = normalizer.normalize_team(rec_team) or rec_team
                    pred_team_obj = team_objs.get(matched_rec)
                    if pred_team_obj:
                        pred_team_id = pred_team_obj.id

                # Only lock if no prediction exists for this match
                stmt_p = select(Prediction.id).where(Prediction.match_id == match_obj.id)
                p_res = await session.execute(stmt_p)
                if not p_res.scalars().first():
                    try:
                        pred_create = PredictionCreate(
                            match_id=match_obj.id,
                            model_version_id="toss_pattern_v1",
                            source_channel="GamingHelper Online",
                            action_type=action,
                            predicted_team_id=pred_team_id,
                            estimated_prob_a=float(m.get("confidence_pct", 50)) / 100.0,
                            punter_rationale=m.get("punter_rationale")
                        )
                        await lock_prediction(session, pred_create)
                    except Exception as e:
                        logger.debug(f"Prediction lock skipped for match {match_obj.id}: {e}")

        # Update collector health
        await self._update_health(
            session=session,
            status="OK",
            latency_ms=latency_ms,
            message=f"Successfully synced {synced_count} matches ({new_count} new) from GamingHelper."
        )

        return {
            "status": "SUCCESS",
            "matches_synced": synced_count,
            "new_matches": new_count,
            "latency_ms": latency_ms
        }

    async def _update_health(
        self,
        session: AsyncSession,
        status: str,
        latency_ms: Optional[int],
        message: str
    ) -> None:
        """Update collector health record in database with success/error timestamps."""
        now = utc_now()
        stmt = select(CollectorHealth).where(CollectorHealth.source == "GamingHelper Online")
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()

        if record:
            record.status = status
            record.last_run_utc = now
            record.response_time_ms = latency_ms
            record.message = message
            if status in ("OK", "HEALTHY"):
                record.last_success_utc = now
            else:
                record.last_error_utc = now
                record.last_error = message
        else:
            record = CollectorHealth(
                source="GamingHelper Online",
                status=status,
                last_run_utc=now,
                response_time_ms=latency_ms,
                message=message,
                last_success_utc=now if status in ("OK", "HEALTHY") else None,
                last_error_utc=now if status not in ("OK", "HEALTHY") else None,
                last_error=message if status not in ("OK", "HEALTHY") else None
            )
            session.add(record)

        await session.commit()
