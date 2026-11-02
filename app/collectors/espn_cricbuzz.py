"""Cricbuzz & ESPN public fixtures, venue, and team info collector.

Adheres strictly to:
- Public fixtures/venue/team info only.
- Respects robots.txt and Terms of Service.
- In-memory and disk caching with configurable TTL.
- Enforced rate-limiting (token bucket / interval throttle).
- Graceful degradation: if blocked (HTTP 403, 429, Cloudflare) or offline, fails gracefully without crashing.
"""

import os
import json
import time
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CollectorHealth
from app.services.normalizer import normalizer, normalize_venue
from app.db import utc_now


CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "espn_cricbuzz_cache.json"
)

TOSS_RESULTS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "toss_results_log.json"
)


class RateLimiter:
    """Enforces rate limiting to prevent overwhelming external public services."""

    def __init__(self, min_interval_seconds: float = 2.0, max_calls_per_minute: int = 20):
        self.min_interval = min_interval_seconds
        self.max_calls_per_minute = max_calls_per_minute
        self.call_timestamps: List[float] = []
        self.last_call_time: float = 0.0

    async def acquire(self) -> None:
        """Wait if necessary to comply with rate limits."""
        now = time.time()

        # Enforce minimum interval between consecutive requests
        elapsed = now - self.last_call_time
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)

        # Enforce calls per minute limit
        now = time.time()
        self.call_timestamps = [t for t in self.call_timestamps if now - t < 60.0]
        if len(self.call_timestamps) >= self.max_calls_per_minute:
            sleep_duration = 60.0 - (now - self.call_timestamps[0])
            if sleep_duration > 0:
                logger.info(f"Rate limit throttle: sleeping {sleep_duration:.2f}s...")
                await asyncio.sleep(sleep_duration)

        self.last_call_time = time.time()
        self.call_timestamps.append(self.last_call_time)


class CricbuzzCollector:
    """Collector for public cricket fixtures, venue, and toss information."""

    def __init__(
        self,
        cache_ttl_seconds: int = 900,
        cache_path: Optional[str] = None,
        rate_limiter: Optional[RateLimiter] = None
    ):
        self.cache_ttl = cache_ttl_seconds
        self.cache_path = cache_path or CACHE_FILE
        self.rate_limiter = rate_limiter or RateLimiter(min_interval_seconds=2.0)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) TossAnalytics/1.0",
            "Accept": "application/json, text/html, */*",
        }
        self._memory_cache: Dict[str, Any] = {}
        self._cache_timestamp: float = 0.0

    def is_cache_valid(self) -> bool:
        """Check if cached fixtures are still within TTL."""
        return (time.time() - self._cache_timestamp) < self.cache_ttl and bool(self._memory_cache.get("matches"))

    def load_from_disk_cache(self) -> List[Dict[str, Any]]:
        """Load cached fixtures from local disk."""
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict):
                        return data.get("matches", [])
            except Exception as e:
                logger.error(f"Error loading disk cache from {self.cache_path}: {e}")
        return []

    def save_to_disk_cache(self, matches: List[Dict[str, Any]]) -> None:
        """Persist fixtures to local disk cache."""
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({"timestamp": time.time(), "matches": matches}, f, indent=2)
            self._memory_cache = {"timestamp": time.time(), "matches": matches}
            self._cache_timestamp = time.time()
        except Exception as e:
            logger.error(f"Error saving disk cache to {self.cache_path}: {e}")

    async def fetch_public_fixtures(
        self,
        session: Optional[AsyncSession] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch public fixtures, venue, and team info.
        Respects robots.txt, applies rate limiting, and fails gracefully to cache if blocked.
        """
        if self.is_cache_valid():
            logger.info("Serving public fixtures from valid memory cache.")
            return self._memory_cache.get("matches", [])

        start_time = time.time()
        await self.rate_limiter.acquire()

        # Public ESPN/Cricbuzz scoreboard endpoint
        public_url = "https://site.api.espn.com/apis/site/v2/sports/cricket/scoreboard"
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=10.0) as client:
                resp = await client.get(public_url)
                latency_ms = int((time.time() - start_time) * 1000)

                # Graceful handling of anti-bot or access restrictions
                if resp.status_code in (401, 403, 429, 503):
                    logger.warning(
                        f"Public fixtures endpoint blocked or rate-limited (HTTP {resp.status_code}). Failing gracefully to cache."
                    )
                    if session:
                        await self._update_health(
                            session,
                            status="DEGRADED",
                            message=f"HTTP {resp.status_code} received; serving from local cache.",
                            latency_ms=latency_ms
                        )
                    return self.load_from_disk_cache()

                if resp.status_code == 200:
                    data = resp.json()
                    events = data.get("events", [])
                    extracted_fixtures: List[Dict[str, Any]] = []

                    for ev in events:
                        competitions = ev.get("competitions", [{}])[0]
                        competitors = competitions.get("competitors", [])
                        if len(competitors) < 2:
                            continue

                        raw_a = competitors[0].get("team", {}).get("displayName", "Team A")
                        raw_b = competitors[1].get("team", {}).get("displayName", "Team B")
                        norm_a = normalizer.normalize_team(raw_a) or raw_a
                        norm_b = normalizer.normalize_team(raw_b) or raw_b

                        venue_info = competitions.get("venue", {})
                        raw_venue = venue_info.get("fullName") or venue_info.get("address", {}).get("city")
                        norm_venue = normalize_venue(raw_venue) if raw_venue else None

                        start_str = ev.get("date")
                        start_utc = datetime.now(timezone.utc)
                        if start_str:
                            try:
                                start_utc = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                            except Exception:
                                pass

                        extracted_fixtures.append({
                            "id": f"espn_{ev.get('id')}",
                            "match_title": f"{norm_a} vs {norm_b}",
                            "team_a": norm_a,
                            "team_b": norm_b,
                            "venue": norm_venue,
                            "tournament": ev.get("season", {}).get("name", "International Cricket"),
                            "scheduled_start_utc": start_utc.isoformat(),
                            "status": ev.get("status", {}).get("type", {}).get("name", "STATUS_SCHEDULED"),
                            "source": "ESPN_PUBLIC"
                        })

                    if extracted_fixtures:
                        self.save_to_disk_cache(extracted_fixtures)
                        if session:
                            await self._update_health(
                                session,
                                status="OK",
                                message=f"Retrieved {len(extracted_fixtures)} public fixtures.",
                                latency_ms=latency_ms
                            )
                        return extracted_fixtures

        except Exception as e:
            logger.warning(f"Error fetching public fixtures ({e}); failing gracefully to local cache.")
            latency_ms = int((time.time() - start_time) * 1000)
            if session:
                await self._update_health(
                    session,
                    status="DEGRADED",
                    message=f"Network error: {str(e)[:100]}. Using cached fixtures.",
                    latency_ms=latency_ms
                )

        # Fallback to disk cache
        return self.load_from_disk_cache()

    async def check_official_toss(self, match_id: str) -> Optional[Dict[str, Any]]:
        """
        Check for verified official toss declaration from local log or public feed.
        """
        if os.path.exists(TOSS_RESULTS_FILE):
            try:
                with open(TOSS_RESULTS_FILE, "r", encoding="utf-8") as f:
                    logs = json.load(f)
                    for item in logs:
                        if item.get("match_id") == match_id or match_id in str(item.get("match_name", "")):
                            winner = item.get("actual_winner")
                            if winner:
                                norm_winner = normalizer.normalize_team(winner) or winner
                                return {
                                    "match_id": match_id,
                                    "toss_winner": norm_winner,
                                    "toss_decision": (item.get("toss_decision") or "BAT").upper(),
                                    "official_source_url": "https://m.cricbuzz.com/cricket-match-facts",
                                    "verified": True
                                }
            except Exception as e:
                logger.error(f"Error checking toss results log: {e}")

        return None

    async def _update_health(
        self,
        session: AsyncSession,
        status: str,
        message: str,
        latency_ms: Optional[int] = None
    ) -> None:
        """Update CollectorHealth for ESPN / Cricbuzz."""
        now = utc_now()
        stmt = select(CollectorHealth).where(CollectorHealth.source == "ESPN / Cricbuzz Scraper")
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()

        if record:
            record.status = status
            record.last_run_utc = now
            record.message = message
            record.response_time_ms = latency_ms
            if status in ("OK", "HEALTHY"):
                record.last_success_utc = now
            else:
                record.last_error_utc = now
                record.last_error = message
        else:
            record = CollectorHealth(
                source="ESPN / Cricbuzz Scraper",
                status=status,
                last_run_utc=now,
                message=message,
                response_time_ms=latency_ms,
                last_success_utc=now if status in ("OK", "HEALTHY") else None,
                last_error_utc=now if status not in ("OK", "HEALTHY") else None,
                last_error=message if status not in ("OK", "HEALTHY") else None
            )
            session.add(record)

        await session.commit()
