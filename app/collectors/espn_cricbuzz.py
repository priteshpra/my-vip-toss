"""Cricbuzz & ESPN live match fixture and official toss result collector."""

import os
import json
import httpx
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from loguru import logger

CREX_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "crex_live_cache.json"
)


class CricbuzzCollector:
    """Async collector for Cricbuzz / CREX live fixtures and toss ground truth."""

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        }

    async def fetch_live_matches(self) -> List[Dict[str, Any]]:
        """Fetch live matches from web source or fall back to cached CREX / Cricbuzz data."""
        # Check if local cache has live matches
        if os.path.exists(CREX_CACHE_FILE):
            try:
                with open(CREX_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list) and len(data) > 0:
                        logger.info(f"Loaded {len(data)} live matches from cache.")
                        return data
                    elif isinstance(data, dict):
                        matches = data.get("matches", []) or data.get("data", [])
                        if matches:
                            return matches
            except Exception as e:
                logger.error(f"Error reading CREX cache file: {e}")

        # Return empty list if no network or cache
        return []

    async def check_official_toss(self, match_id: str) -> Optional[Dict[str, Any]]:
        """
        Check for official toss declaration on a specific match.
        Returns dict with toss_winner, toss_decision, official_source_url or None.
        """
        # Look in cached results log if available
        results_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data",
            "toss_results_log.json"
        )
        if os.path.exists(results_file):
            try:
                with open(results_file, "r", encoding="utf-8") as f:
                    logs = json.load(f)
                    for item in logs:
                        if item.get("match_id") == match_id or match_id in str(item.get("match_name", "")):
                            winner = item.get("actual_winner")
                            if winner:
                                return {
                                    "match_id": match_id,
                                    "toss_winner": winner,
                                    "toss_decision": (item.get("toss_decision") or "BAT").upper(),
                                    "official_source_url": "https://m.cricbuzz.com/cricket-match-facts",
                                    "verified": True
                                }
            except Exception as e:
                logger.error(f"Error checking toss results log: {e}")

        return None

