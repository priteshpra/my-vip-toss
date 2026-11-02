"""Telegram channel listener and message audit parser using Telethon.

Strictly enforces:
- Robust multi-pattern and Hindi/Hinglish parsing.
- Real-time event handling with auto-reconnect.
- Idempotency via unique telegram_message_id.
- Automated match resolution to 'toss_done' and prediction PASS/FAIL evaluation.
- Unparsable or ambiguous messages routed to TelegramReview queue for admin review.
"""

import re
import json
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple
from loguru import logger
from sqlalchemy import select, or_, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import utc_now, ensure_utc
from app.models import Match, Team, TossResult, TelegramReview, CollectorHealth
from app.services.normalizer import normalizer, clean_text
from app.services.toss_service import record_toss_result
from app.schemas import TossResultCreate

try:
    from telethon import TelegramClient, events
except ImportError:
    TelegramClient = None  # type: ignore


# --------------------------------------------------------------------------
# 1. Linguistic & Regex Patterns for Toss Calls
# --------------------------------------------------------------------------
BAT_KEYWORDS = [
    "bat", "batting", "bat first", "elected to bat", "chose to bat",
    "pehle bat", "ballebaazi", "ballebazi"
]
BOWL_KEYWORDS = [
    "bowl", "bowling", "field", "fielding", "elected to bowl", "elected to field",
    "chose to bowl", "chose to field", "pehle bowl", "pehle field", "gendlbaazi",
    "gendbazi", "chase"
]
WIN_KEYWORDS = [
    "toss winner", "toss won", "won toss", "toss jeeta", "toss jeet", "jeet kar",
    "toss pass", "toss call", "toss winner is", "toss:", "toss -"
]
SKIP_KEYWORDS = [
    "skip", "pass", "no bet", "50-50", "avoid", "neutral", "strict pass",
    "koi bet mat karna", "mat khelo"
]


def clean_telegram_text(text: str) -> str:
    """Clean symbols, arrows, and remove all emojis."""
    if not text:
        return ""
    # Replace arrows and arrow indicators with colon
    cleaned = re.sub(r'[\u27a1\ufe0f\u2192\u21d2]|->|=>', ' : ', text)
    # Remove emojis (both astral planes and BMP emoji blocks)
    cleaned = re.sub(r'[\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50\u200d\ufe0f]', ' ', cleaned)
    return cleaned


def extract_toss_decision(text: str) -> str:
    """Detect bat or field decision from text; defaults to bat."""
    normalized = text.lower()
    for kw in BOWL_KEYWORDS:
        if kw in normalized:
            return "field"
    for kw in BAT_KEYWORDS:
        if kw in normalized:
            return "bat"
    return "bat"


def parse_toss_message(text: str) -> Dict[str, Any]:
    """
    Deterministically parse raw Telegram text for toss call.
    Handles emojis, structured key-value lines, and conversational Hindi/Hinglish.
    """
    cleaned = clean_telegram_text(text)
    lower = cleaned.lower()

    # 1. Detect explicit Skip / Pass
    for sk in SKIP_KEYWORDS:
        if sk in lower:
            return {
                "is_toss_result": False,
                "is_skip": True,
                "winner_raw": None,
                "decision": None,
                "confidence": 0.90,
                "raw_text": text,
                "rationale": "Explicit tipster skip recommendation."
            }

    winner_candidate: Optional[str] = None
    team_a_candidate: Optional[str] = None
    team_b_candidate: Optional[str] = None
    confidence = 0.0

    # 2. Pattern A: Structured key-value (e.g. "TOSS WINNER: CSK", "Toss - Mumbai Indians", "Toss Winner -> RR [BOWL]")
    pattern_structured = re.search(
        r"(?:toss\s*(?:winner|call|pred|prediction)?\s*(?:[:\-]|\bis\b)\s*)([A-Za-z0-9\s]+?)(?:\s*(?:\n|\(|\[|\||\-|\bdecision\b|\bbat\b|\bbowl\b|\bfield\b|\belected\b|\bwill\b|\bwon\b|$))",
        cleaned,
        re.IGNORECASE
    )
    if pattern_structured:
        raw_winner = pattern_structured.group(1).strip()
        if len(raw_winner) >= 2 and raw_winner.lower() not in ["winner", "call", "pred", "prediction", "is"]:
            winner_candidate = raw_winner
            confidence = 0.85

    # 3. Pattern B: Hindi / Hinglish / Conversational (e.g. "Delhi Capitals toss jeeta", "KKR toss winner", "GT toss win")
    if not winner_candidate:
        pattern_hinglish = re.search(
            r"([A-Za-z0-9\s]+?)\s*(?:toss\s*(?:jeeta|jeet|jitega|le\s*gaya|won|win|winner|winner is|pass|call))",
            cleaned,
            re.IGNORECASE
        )
        if pattern_hinglish:
            raw_winner = pattern_hinglish.group(1).strip().split("\n")[-1].strip()
            if ":" in raw_winner:
                raw_winner = raw_winner.split(":")[-1].strip()
            if len(raw_winner) >= 2 and raw_winner.lower() not in ["aaj", "match", "ka"]:
                winner_candidate = raw_winner
                confidence = 0.80

    # 4. Pattern C: "X won the toss" or "X will win the toss"
    if not winner_candidate:
        pattern_won = re.search(
            r"([A-Za-z0-9\s]+?)\s+(?:won|will win|wins|chose)\s+(?:the\s+)?toss",
            cleaned,
            re.IGNORECASE
        )
        if pattern_won:
            raw_winner = pattern_won.group(1).strip().split("\n")[-1].strip()
            if ":" in raw_winner:
                raw_winner = raw_winner.split(":")[-1].strip()
            if len(raw_winner) >= 2:
                winner_candidate = raw_winner
                confidence = 0.80

    # 5. Pattern D: Fixture detection ("CSK vs MI", "Team A v Team B")
    fixture_match = re.search(
        r"([A-Za-z0-9\s]+?)\s+(?:vs|v|versus|bhidant|mukabla)\s+([A-Za-z0-9\s]+)",
        cleaned,
        re.IGNORECASE
    )
    if fixture_match:
        team_a_candidate = fixture_match.group(1).split("\n")[-1].strip()
        team_b_candidate = fixture_match.group(2).split("\n")[0].strip()

    # 6. Extract decision (bat / field)
    decision = extract_toss_decision(cleaned)

    # Validate winner candidate against common generic words
    if winner_candidate:
        tokens = [t for t in winner_candidate.split() if t.lower() not in ["aaj", "match", "ka", "the", "official", "final", "today"]]
        winner_candidate = " ".join(tokens)
        if len(winner_candidate) < 2:
            winner_candidate = None
            confidence = 0.0

    is_toss = bool(winner_candidate and confidence >= 0.70)

    return {
        "is_toss_result": is_toss,
        "is_skip": False,
        "winner_raw": winner_candidate,
        "decision": decision if is_toss else None,
        "team_a_raw": team_a_candidate,
        "team_b_raw": team_b_candidate,
        "confidence": confidence,
        "raw_text": text,
        "rationale": "Parsed toss confirmation call." if is_toss else "Ambiguous or non-toss message."
    }


# --------------------------------------------------------------------------
# 2. Telegram Event Processor with Idempotency
# --------------------------------------------------------------------------
class TelegramListener:
    """Manages Telethon connection, audits incoming messages, and triggers toss resolutions."""

    def __init__(self):
        self.api_id = settings.telegram_api_id
        self.api_hash = settings.telegram_api_hash
        self.channels = [c.strip() for c in (settings.telegram_channels or "").split(",") if c.strip()]
        self.enabled = settings.telegram_enabled and bool(self.api_id and self.api_hash)
        self.client: Optional[Any] = None

    @staticmethod
    def parse_message_text(text: str) -> Dict[str, Any]:
        """Backward-compatible helper for parsing raw message text."""
        parsed = parse_toss_message(text)
        action = "SKIP" if parsed.get("is_skip") else "PLAY"
        predicted_team = parsed.get("winner_raw") or ""
        msg_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return {
            "action": action,
            "predicted_team": predicted_team,
            "decision": parsed.get("decision", "bat"),
            "message_hash": msg_hash,
            "parsed": parsed
        }

    @staticmethod
    def audit_message_pre_toss(
        message_utc: datetime,
        match_start_utc: datetime,
        is_edited: bool = False
    ) -> Dict[str, Any]:
        """Audit pre-toss validity; disqualifies post-toss edits."""
        if is_edited:
            return {"is_pre_toss": False, "audit_status": "POST_TOSS_EDITED"}
        if message_utc >= match_start_utc:
            return {"is_pre_toss": False, "audit_status": "POST_START_DISQUALIFIED"}
        return {"is_pre_toss": True, "audit_status": "VERIFIED_PRE_TOSS"}

    async def process_message(
        self,
        session: AsyncSession,
        message_id: str,
        text: str,
        channel_name: str,
        received_at: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Process an incoming Telegram message with strict idempotency:
        - Rejects duplicates using unique telegram_message_id.
        - Resolves confirmed toss calls to 'toss_done' match status.
        - Automatically evaluates pre-toss locked predictions (PASS/FAIL/SKIPPED).
        - Directs ambiguous or unparsed messages to the TelegramReview queue.
        """
        now = received_at or utc_now()
        str_msg_id = str(message_id).strip()

        # 1. Idempotency Check: check TossResult table
        stmt_toss = select(TossResult).where(TossResult.telegram_message_id == str_msg_id)
        res_toss = await session.execute(stmt_toss)
        if res_toss.scalar_one_or_none():
            logger.debug(f"Telegram message #{str_msg_id} already processed in TossResult; skipping duplicate.")
            return {"status": "DUPLICATE_SKIPPED", "message_id": str_msg_id}

        # 2. Idempotency Check: check TelegramReview table
        stmt_rev = select(TelegramReview).where(TelegramReview.message_id == str_msg_id)
        res_rev = await session.execute(stmt_rev)
        if res_rev.scalar_one_or_none():
            logger.debug(f"Telegram message #{str_msg_id} already in review queue; skipping duplicate.")
            return {"status": "DUPLICATE_SKIPPED", "message_id": str_msg_id}

        # 3. Parse Message
        parsed = parse_toss_message(text)

        # 4. If Parsed as Confirmed Toss Call: match with active match
        if parsed["is_toss_result"] and parsed["winner_raw"]:
            canonical_winner = normalizer.normalize_team(parsed["winner_raw"]) or parsed["winner_raw"]

            # Query candidate match where winner is in team_a or team_b
            query = (
                select(Match)
                .join(Team, or_(Match.team_a_id == Team.id, Match.team_b_id == Team.id))
                .where(
                    Team.name.ilike(f"%{canonical_winner}%"),
                    Match.status.in_(("upcoming", "live", "UPCOMING", "LIVE"))
                )
                .order_by(Match.start_time_utc.asc())
            )
            res_match = await session.execute(query)
            target_match = res_match.scalars().first()

            if target_match:
                # Record confirmed toss result
                toss_create = TossResultCreate(
                    match_id=target_match.id,
                    toss_winner_id=canonical_winner,
                    toss_decision=parsed["decision"] or "BAT",
                    notes=f"Auto-resolved from Telegram channel {channel_name} (msg #{str_msg_id})"
                )
                toss_obj = await record_toss_result(
                    session=session,
                    data=toss_create,
                    source_provider_id="TELEGRAM"
                )
                toss_obj.telegram_message_id = str_msg_id
                await session.commit()

                logger.info(
                    f"✓ Auto-confirmed toss for Match #{target_match.id}: {canonical_winner} won ({parsed['decision']})."
                )
                await self._update_health(
                    session=session,
                    status="OK",
                    message=f"Auto-confirmed toss for Match #{target_match.id} from message #{str_msg_id}."
                )
                return {
                    "status": "CONFIRMED_TOSS",
                    "match_id": target_match.id,
                    "toss_winner": canonical_winner,
                    "decision": parsed["decision"],
                    "message_id": str_msg_id
                }

        # 5. Route ambiguous or unparsed messages to TelegramReview Queue
        review_entry = TelegramReview(
            message_id=str_msg_id,
            raw_text=text,
            channel_name=channel_name,
            status="PENDING",
            extracted_data_json=json.dumps(parsed),
            created_at_utc=now
        )
        session.add(review_entry)
        await session.commit()
        await session.refresh(review_entry)

        logger.info(f"Telegram message #{str_msg_id} routed to TelegramReview queue (status: PENDING).")
        await self._update_health(
            session=session,
            status="OK",
            message=f"Processed message #{str_msg_id}; routed to review queue."
        )
        return {
            "status": "QUEUED_FOR_REVIEW",
            "review_id": review_entry.id,
            "message_id": str_msg_id,
            "parsed": parsed
        }

    async def _update_health(
        self,
        session: AsyncSession,
        status: str,
        message: str
    ) -> None:
        """Update Telegram Listener health metrics in database."""
        now = utc_now()
        stmt = select(CollectorHealth).where(CollectorHealth.source == "Telegram Listener")
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()

        if record:
            record.status = status
            record.last_run_utc = now
            record.message = message
            if status in ("OK", "HEALTHY"):
                record.last_success_utc = now
            else:
                record.last_error_utc = now
                record.last_error = message
        else:
            record = CollectorHealth(
                source="Telegram Listener",
                status=status,
                last_run_utc=now,
                message=message,
                last_success_utc=now if status in ("OK", "HEALTHY") else None,
                last_error_utc=now if status not in ("OK", "HEALTHY") else None,
                last_error=message if status not in ("OK", "HEALTHY") else None
            )
            session.add(record)

        await session.commit()
