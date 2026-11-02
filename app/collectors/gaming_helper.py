"""GamingHelper collector with async httpx, safe credential handling, and offline fallback."""

import os
import json
import httpx
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from loguru import logger
from app.config import settings

API_BASE = "https://gaminglive.gaminghelperonline.com/api"
LOCAL_FALLBACK_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "gaming_helper_matches.json"
)


class GamingHelperCollector:
    """Async collector for GamingHelper matches with fallback support."""

    def __init__(self):
        self.phone = settings.gaming_helper_phone
        self.password = settings.gaming_helper_password
        self.enabled = settings.gaming_helper_enabled
        self.token: Optional[str] = None

    async def authenticate(self) -> bool:
        """Authenticate with GamingHelper API if credentials provided."""
        if not self.phone or not self.password:
            logger.debug("GamingHelper credentials not set; skipping live authentication.")
            return False

        url = f"{API_BASE}/users/login"
        payload = {"phone": self.phone.strip(), "password": self.password.strip()}
        headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

        try:
            async with httpx.AsyncClient(timeout=12.0, verify=False) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    self.token = data.get("newAccessToken") or data.get("access-token") or data.get("token")
                    if self.token:
                        logger.info("Successfully authenticated with GamingHelper API.")
                        return True
                logger.warning(f"GamingHelper login HTTP {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            logger.warning(f"GamingHelper login failed: {e}")
        return False

    async def fetch_live_bets(self) -> List[Dict[str, Any]]:
        """Fetch live matches or fall back to local snapshot."""
        if self.enabled and self.phone and self.password:
            if not self.token:
                await self.authenticate()
            if self.token:
                url = f"{API_BASE}/live-bet"
                headers = {"Authorization": f"Bearer {self.token}", "User-Agent": "Mozilla/5.0"}
                try:
                    async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
                        resp = await client.get(url, headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            bets = data.get("data", []) or data.get("bets", [])
                            if isinstance(bets, list) and len(bets) > 0:
                                return bets
                except Exception as e:
                    logger.warning(f"Failed to fetch live bets from GamingHelper: {e}")

        # Fallback to local cached data
        return self.load_local_fallback()

    def load_local_fallback(self) -> List[Dict[str, Any]]:
        """Load local fallback match records from data/ directory."""
        if os.path.exists(LOCAL_FALLBACK_FILE):
            try:
                with open(LOCAL_FALLBACK_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        logger.info(f"Loaded {len(data)} matches from GamingHelper fallback cache.")
                        return data
            except Exception as e:
                logger.error(f"Error reading local GamingHelper cache: {e}")
        return []

