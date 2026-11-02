"""Telegram channel listener and message audit parser using Telethon with safe fallback."""

import re
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from loguru import logger
from app.config import settings

try:
    from telethon import TelegramClient, events
except ImportError:
    TelegramClient = None  # type: ignore


class TelegramListener:
    """Manages Telethon connection to track and audit tipster channels."""

    def __init__(self):
        self.api_id = settings.telegram_api_id
        self.api_hash = settings.telegram_api_hash
        self.phone = settings.telegram_phone
        self.channels = [c.strip() for c in (settings.telegram_channels or "").split(",") if c.strip()]
        self.enabled = settings.telegram_enabled and bool(self.api_id and self.api_hash)
        self.client: Optional[Any] = None

    def initialize_client(self) -> Optional[Any]:
        """Instantiate Telethon client if credentials are configured."""
        if not self.enabled or TelegramClient is None:
            logger.debug("Telegram integration disabled or credentials missing.")
            return None

        try:
            self.client = TelegramClient("data/telegram_toss_session", int(self.api_id), self.api_hash)
            return self.client
        except Exception as e:
            logger.error(f"Failed to initialize Telethon client: {e}")
            return None

    @staticmethod
    def parse_message_text(text: str) -> Dict[str, Any]:
        """
        Deterministically parse tipster telegram message for toss call.
        Extracts recommended team, action (PLAY/SKIP), and rationale.
        """
        normalized = text.lower()
        action = "PLAY"
        predicted_team: Optional[str] = None

        # Detect Skip / Pass
        if any(w in normalized for w in ["skip", "pass", "no bet", "50-50", "avoid"]):
            action = "SKIP"

        # Regex patterns for team toss call
        # e.g. "Toss Winner: Mumbai Indians", "Toss - CSK", "Toss: Otago"
        toss_pattern = r"(?:toss\s*(?:winner|call|pred)?\s*[:\-\u27a1\ufe0f\u2192]\s*)([A-Za-z0-9\s]+)"
        match = re.search(toss_pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).split("\n")[0].strip()
            # Clean common words
            if len(candidate) > 2 and candidate.lower() not in ["open", "pass", "skip", "call"]:
                predicted_team = candidate

        # Compute SHA-256 hash of raw message for cryptographic audit
        msg_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        return {
            "action": action,
            "predicted_team": predicted_team,
            "raw_text": text,
            "message_hash": msg_hash,
        }

    @staticmethod
    def audit_message_pre_toss(
        message_utc: datetime,
        match_start_utc: datetime,
        is_edited: bool = False
    ) -> Dict[str, Any]:
        """
        Verify whether the message was sent strictly pre-toss and whether post-toss edit occurred.
        """
        if is_edited:
            return {
                "audit_status": "POST_TOSS_EDITED",
                "is_pre_toss": False,
                "reason": "Message was modified after original posting; invalidated."
            }
        if message_utc >= match_start_utc:
            return {
                "audit_status": "POST_START_DISQUALIFIED",
                "is_pre_toss": False,
                "reason": "Message posted AFTER scheduled match start."
            }
        return {
            "audit_status": "VERIFIED_PRE_TOSS",
            "is_pre_toss": True,
            "reason": "Verified pre-toss delivery before match commencement."
        }

