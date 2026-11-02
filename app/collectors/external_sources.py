"""External source collectors with adapter pattern for multi-source toss intelligence.

Strictly adheres to:
- Ethical web collection: public endpoints only, timeouts, no bypass of logins, paywalls, or captchas.
- Graceful degradation: failing sources are marked 'DEGRADED' or 'OFFLINE' and never crash the pipeline.
- Append-only immutability: captured snapshots are never overwritten or mutated.
- Telegram tipster parsing with review queue routing for ambiguous posts.
- Cross-source entity normalization using RapidFuzz.
"""

import abc
import json
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, Union
import httpx
from loguru import logger
from sqlalchemy import select, or_, and_, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ExternalSource, SourceSnapshot, Match, Team, TelegramReview, CollectorHealth
)
from app.services.normalizer import normalizer, clean_text
from app.db import utc_now, ensure_utc


class SourceCollectionError(Exception):
    """Raised when an external source collection encounters a fatal error."""
    pass


# --------------------------------------------------------------------------
# 1. Base Source Adapter
# --------------------------------------------------------------------------
class BaseSourceAdapter(abc.ABC):
    """Abstract base adapter for external toss data and intelligence sources."""

    def __init__(self, source: ExternalSource):
        self.source = source
        self.rules: Dict[str, Any] = {}
        if source.parse_rules:
            try:
                self.rules = json.loads(source.parse_rules)
            except Exception as e:
                logger.warning(f"Malformed parse_rules JSON for source '{source.name}': {e}")
                self.rules = {}

    @abc.abstractmethod
    async def fetch_raw_data(self) -> Any:
        """Fetch raw payload from external provider or endpoint."""
        pass

    @abc.abstractmethod
    def parse_snapshots(self, raw_data: Any) -> List[Dict[str, Any]]:
        """
        Parse raw payload into normalized snapshot candidate dictionaries.
        Each dictionary should contain:
        - team_a: Optional[str]
        - team_b: Optional[str]
        - match_hint: Optional[str]
        - match_id: Optional[int]
        - lean: Optional[str]
        - market: Optional[str]
        - session_notes: Optional[str]
        - raw: str
        """
        pass

    async def collect(self, session: AsyncSession) -> List[SourceSnapshot]:
        """Fetch, parse, resolve matches, and save append-only snapshots."""
        start_time = utc_now()
        snapshots_created: List[SourceSnapshot] = []

        try:
            raw_data = await self.fetch_raw_data()
            parsed_items = self.parse_snapshots(raw_data)

            for item in parsed_items:
                match_id, canon_lean = await resolve_match_and_lean(
                    session=session,
                    team_a_hint=item.get("team_a"),
                    team_b_hint=item.get("team_b"),
                    match_hint=item.get("match_hint"),
                    raw_lean=item.get("lean"),
                    direct_match_id=item.get("match_id")
                )

                snap = SourceSnapshot(
                    source_id=self.source.id,
                    match_id=match_id,
                    lean=canon_lean or item.get("lean"),
                    market=item.get("market"),
                    session_notes=item.get("session_notes"),
                    raw=str(item.get("raw") or ""),
                    fetched_at_utc=utc_now()
                )
                session.add(snap)
                snapshots_created.append(snap)

            # Mark source as healthy
            self.source.health = "HEALTHY"
            elapsed_ms = int((utc_now() - start_time).total_seconds() * 1000)

            # Log collector health
            health_log = CollectorHealth(
                source=f"source:{self.source.name}",
                status="OK",
                last_run_utc=utc_now(),
                last_success_utc=utc_now(),
                response_time_ms=elapsed_ms,
                message=f"Successfully extracted {len(snapshots_created)} snapshot(s)."
            )
            session.add(health_log)
            await session.commit()

            logger.info(f"Source '{self.source.name}' ingested {len(snapshots_created)} snapshot(s).")
            return snapshots_created

        except Exception as exc:
            logger.warning(f"Error collecting from source '{self.source.name}': {exc}")
            self.source.health = "DEGRADED"
            elapsed_ms = int((utc_now() - start_time).total_seconds() * 1000)

            health_log = CollectorHealth(
                source=f"source:{self.source.name}",
                status="DEGRADED",
                last_run_utc=utc_now(),
                last_error_utc=utc_now(),
                last_error=str(exc),
                response_time_ms=elapsed_ms,
                message=f"Collection failed: {str(exc)}"
            )
            session.add(health_log)
            await session.commit()
            return []


# --------------------------------------------------------------------------
# 2. Public HTML Scraper Adapter
# --------------------------------------------------------------------------
class HtmlSourceAdapter(BaseSourceAdapter):
    """
    Public HTML scraper adapter.
    Uses respectful public HTTP requests with strict timeouts.
    Never bypasses logins, captchas, or anti-bot protections.
    """

    async def fetch_raw_data(self) -> str:
        if not self.source.url:
            raise ValueError(f"HTML source '{self.source.name}' has no URL configured.")

        timeout = float(self.rules.get("timeout_seconds", 10.0))
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TossAnalytics/1.0 (Public Research Collector)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            resp = await client.get(self.source.url)
            resp.raise_for_status()
            return resp.text

    def parse_snapshots(self, raw_data: str) -> List[Dict[str, Any]]:
        """Parse HTML using configured regex patterns or standard item patterns."""
        results: List[Dict[str, Any]] = []
        if not raw_data:
            return results

        # 1. Pattern-based rule extraction
        item_regex = self.rules.get("item_regex")
        teams_regex = self.rules.get("teams_regex") or r"([A-Za-z0-9\s]+)\s+(?:vs|v|-)\s+([A-Za-z0-9\s]+)"
        lean_regex = self.rules.get("lean_regex") or r"(?:toss|pick|lean|winner|call):\s*([A-Za-z0-9\s]+)"
        market_regex = self.rules.get("market_regex") or r"(?:market|load|rate|odds):\s*([^\n<]+)"
        notes_regex = self.rules.get("notes_regex")

        if item_regex:
            # Segment into blocks
            blocks = re.findall(item_regex, raw_data, flags=re.DOTALL | re.IGNORECASE)
            for block in blocks:
                block_text = block if isinstance(block, str) else " ".join(block)
                t_match = re.search(teams_regex, block_text, re.IGNORECASE)
                l_match = re.search(lean_regex, block_text, re.IGNORECASE)
                m_match = re.search(market_regex, block_text, re.IGNORECASE)
                n_match = re.search(notes_regex, block_text, re.IGNORECASE) if notes_regex else None

                team_a = t_match.group(1).strip() if t_match else None
                team_b = t_match.group(2).strip() if t_match and len(t_match.groups()) >= 2 else None
                lean = l_match.group(1).strip() if l_match else None
                market = m_match.group(1).strip() if m_match else None
                notes = n_match.group(1).strip() if n_match else None

                if lean:
                    results.append({
                        "team_a": team_a,
                        "team_b": team_b,
                        "match_hint": f"{team_a} vs {team_b}" if (team_a and team_b) else None,
                        "lean": lean,
                        "market": market,
                        "session_notes": notes,
                        "raw": block_text[:1000]
                    })
        else:
            # Fallback line-by-line / text scanning
            lines = raw_data.splitlines()
            current_team_a = None
            current_team_b = None

            for line in lines:
                clean_line = clean_text(line)
                if not clean_line:
                    continue

                t_match = re.search(teams_regex, clean_line, re.IGNORECASE)
                if t_match:
                    current_team_a = t_match.group(1).strip()
                    current_team_b = t_match.group(2).strip() if len(t_match.groups()) >= 2 else None

                l_match = re.search(lean_regex, clean_line, re.IGNORECASE)
                if l_match and (current_team_a or current_team_b):
                    lean = l_match.group(1).strip()
                    m_match = re.search(market_regex, clean_line, re.IGNORECASE)
                    market = m_match.group(1).strip() if m_match else None

                    results.append({
                        "team_a": current_team_a,
                        "team_b": current_team_b,
                        "match_hint": f"{current_team_a} vs {current_team_b}",
                        "lean": lean,
                        "market": market,
                        "session_notes": clean_line,
                        "raw": line[:500]
                    })
                    current_team_a = None
                    current_team_b = None

        return results


# --------------------------------------------------------------------------
# 3. JSON API Adapter
# --------------------------------------------------------------------------
class JsonApiSourceAdapter(BaseSourceAdapter):
    """
    Public JSON API adapter for structured feeds.
    """

    async def fetch_raw_data(self) -> Any:
        if not self.source.url:
            raise ValueError(f"JSON API source '{self.source.name}' has no URL configured.")

        timeout = float(self.rules.get("timeout_seconds", 10.0))
        headers = {
            "User-Agent": "TossAnalytics/1.0 (Public Research Collector)",
            "Accept": "application/json"
        }

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            resp = await client.get(self.source.url)
            resp.raise_for_status()
            return resp.json()

    def parse_snapshots(self, raw_data: Any) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if not raw_data:
            return results

        # Traverse items path if configured (e.g. "data.matches" or "items")
        items_path = self.rules.get("items_path", "")
        items = raw_data
        if items_path and isinstance(raw_data, dict):
            for part in items_path.split("."):
                if part and isinstance(items, dict):
                    items = items.get(part, [])

        if not isinstance(items, list):
            if isinstance(items, dict):
                items = [items]
            else:
                return results

        team_a_key = self.rules.get("team_a_key", "team_a")
        team_b_key = self.rules.get("team_b_key", "team_b")
        lean_key = self.rules.get("lean_key", "lean")
        market_key = self.rules.get("market_key", "market")
        notes_key = self.rules.get("notes_key", "notes")
        match_id_key = self.rules.get("match_id_key", "match_id")

        for item in items:
            if not isinstance(item, dict):
                continue

            lean = item.get(lean_key) or item.get("pick") or item.get("toss_winner") or item.get("team_lean")
            if not lean:
                continue

            team_a = item.get(team_a_key) or item.get("team1")
            team_b = item.get(team_b_key) or item.get("team2")
            market = item.get(market_key) or item.get("load") or item.get("odds")
            notes = item.get(notes_key) or item.get("commentary")
            m_id = item.get(match_id_key)

            results.append({
                "team_a": str(team_a) if team_a else None,
                "team_b": str(team_b) if team_b else None,
                "match_id": int(m_id) if m_id and str(m_id).isdigit() else None,
                "match_hint": f"{team_a} vs {team_b}" if (team_a and team_b) else None,
                "lean": str(lean).strip(),
                "market": str(market).strip() if market else None,
                "session_notes": str(notes).strip() if notes else None,
                "raw": json.dumps(item)
            })

        return results


# --------------------------------------------------------------------------
# 4. Telegram Channel Tipster Adapter
# --------------------------------------------------------------------------
class TelegramChannelAdapter(BaseSourceAdapter):
    """
    Adapter for monitoring and parsing Telegram tipper channels/groups.
    Integrates message parsing heuristics and review queue routing for ambiguous posts.
    """

    async def fetch_raw_data(self) -> List[Dict[str, Any]]:
        """
        Fetches pending or raw messages designated for this channel.
        Can be populated via incoming webhook or direct channel poll.
        """
        # If payload was supplied directly via parse_rules mock or memory:
        if "mock_messages" in self.rules:
            return self.rules["mock_messages"]
        return []

    def parse_snapshots(self, raw_data: Any) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        messages = raw_data if isinstance(raw_data, list) else [raw_data]

        for msg in messages:
            text = msg.get("text") if isinstance(msg, dict) else str(msg)
            msg_id = msg.get("message_id", "msg_0") if isinstance(msg, dict) else "msg_0"

            parsed = parse_tipster_telegram_message(text)
            if not parsed.get("is_valid"):
                continue

            if parsed.get("is_ambiguous"):
                # Flagged for manual review
                logger.info(f"Telegram post marked ambiguous: {text[:50]}...")
                continue

            results.append({
                "team_a": parsed.get("team_a"),
                "team_b": parsed.get("team_b"),
                "match_hint": parsed.get("match_hint"),
                "lean": parsed.get("lean"),
                "market": parsed.get("market"),
                "session_notes": parsed.get("notes"),
                "raw": text
            })

        return results

    async def ingest_message(
        self,
        session: AsyncSession,
        raw_text: str,
        message_id: str
    ) -> Optional[SourceSnapshot]:
        """
        Ingest a single message received from a Telegram tipper channel.
        If parsable and matched -> creates SourceSnapshot.
        If unparsable or ambiguous -> routes to TelegramReview queue for admin resolution.
        """
        parsed = parse_tipster_telegram_message(raw_text)

        if parsed.get("is_ambiguous") or not parsed.get("is_valid"):
            # Queue for manual admin review
            review_item = TelegramReview(
                raw_text=raw_text,
                message_id=message_id,
                channel_name=self.source.name,
                status="PENDING",
                extracted_data_json=json.dumps(parsed),
                created_at_utc=utc_now()
            )
            session.add(review_item)
            await session.commit()
            logger.info(f"Ambiguous Telegram tip from '{self.source.name}' routed to review queue.")
            return None

        # Resolve match
        match_id, canon_lean = await resolve_match_and_lean(
            session=session,
            team_a_hint=parsed.get("team_a"),
            team_b_hint=parsed.get("team_b"),
            match_hint=parsed.get("match_hint"),
            raw_lean=parsed.get("lean")
        )

        snapshot = SourceSnapshot(
            source_id=self.source.id,
            match_id=match_id,
            lean=canon_lean or parsed.get("lean"),
            market=parsed.get("market"),
            session_notes=parsed.get("notes"),
            raw=raw_text[:1000],
            fetched_at_utc=utc_now()
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot


# --------------------------------------------------------------------------
# 5. Manual Source Adapter
# --------------------------------------------------------------------------
class ManualSourceAdapter(BaseSourceAdapter):
    """Manual intelligence entry adapter."""

    async def fetch_raw_data(self) -> Any:
        return []

    def parse_snapshots(self, raw_data: Any) -> List[Dict[str, Any]]:
        return []


# --------------------------------------------------------------------------
# 6. Adapter Factory & Helper Functions
# --------------------------------------------------------------------------
def get_source_adapter(source: ExternalSource) -> BaseSourceAdapter:
    """Return configured adapter instance based on source type."""
    stype = (source.type or "").lower().strip()
    if stype in ("public_html", "html"):
        return HtmlSourceAdapter(source)
    elif stype in ("json_api", "json"):
        return JsonApiSourceAdapter(source)
    elif stype in ("telegram_channel", "telegram"):
        return TelegramChannelAdapter(source)
    elif stype in ("manual", "internal"):
        return ManualSourceAdapter(source)
    else:
        # Default fallback
        return HtmlSourceAdapter(source)


def parse_tipster_telegram_message(text: str) -> Dict[str, Any]:
    """
    Robust tipster message parser extracting match fixture, lean, and market loads.
    Handles emojis, Hindi/Hinglish keywords, and ambiguous calls.
    """
    if not text or len(text.strip()) < 5:
        return {"is_valid": False, "is_ambiguous": True, "reason": "Text too short"}

    cleaned = clean_text(text)
    lower = cleaned.lower()

    # 1. Check for explicit skips / neutral
    if any(k in lower for k in ["50-50", "skip", "pass", "no bet", "neutral", "avoid", "mat khelo"]):
        return {
            "is_valid": True,
            "is_ambiguous": False,
            "is_skip": True,
            "lean": "NO_EDGE",
            "market": None,
            "notes": "Explicit tipster skip recommendation",
            "match_hint": None,
            "team_a": None,
            "team_b": None
        }

    # 2. Extract match fixture: Team A vs Team B
    match_rgx = re.search(r"([A-Za-z0-9\s]+?)\s+(?:vs|v|-)\s+([A-Za-z0-9\s]+?)(?:\n|\:|\-|$)", text, re.IGNORECASE)
    team_a = match_rgx.group(1).strip() if match_rgx else None
    team_b = match_rgx.group(2).strip() if match_rgx and len(match_rgx.groups()) >= 2 else None

    # 3. Extract toss lean / call
    lean_candidate = None
    lean_rgx = re.search(
        r"(?:toss\s*(?:winner|call|pick|pred|prediction)?\s*(?:[:\-]|\bis\b)\s*)([A-Za-z0-9\s]+?)(?:\n|\(|\[|\||\-|\bdecision\b|\bbat\b|\bbowl\b|\bfield\b|\bload\b|$)",
        text,
        re.IGNORECASE
    )
    if lean_rgx:
        cand = lean_rgx.group(1).strip()
        if len(cand) >= 2 and cand.lower() not in ["winner", "call", "pred", "prediction"]:
            lean_candidate = cand

    if not lean_candidate:
        # Hinglish pattern: e.g. "Delhi Capitals toss jeeta", "CSK toss win", "MI toss call"
        hinglish_rgx = re.search(
            r"([A-Za-z0-9\s]+?)\s*(?:toss\s*(?:jeeta|jeet|jitega|le\s*gaya|won|win|winner|call|pass))",
            text,
            re.IGNORECASE
        )
        if hinglish_rgx:
            cand = hinglish_rgx.group(1).strip().split("\n")[-1].strip()
            if ":" in cand:
                cand = cand.split(":")[-1].strip()
            if len(cand) >= 2 and cand.lower() not in ["aaj", "match", "ka"]:
                lean_candidate = cand

    # 4. Extract market load / rate
    market_candidate = None
    market_rgx = re.search(
        r"(?:load|market|rate|odds|bhav):\s*([^\n<]+)",
        text,
        re.IGNORECASE
    )
    if market_rgx:
        market_candidate = market_rgx.group(1).strip()

    # Validate lean candidate against noise keywords and verify team relevance
    if lean_candidate:
        noise = {"vip", "group", "dm", "now", "fixed", "join", "call", "calls", "channel", "link", "admin", "contact"}
        words = set(lean_candidate.lower().split())
        if words.intersection(noise) or len(lean_candidate.split()) > 4:
            norm_t = normalizer.normalize_team(lean_candidate)
            if not norm_t:
                lean_candidate = None
        elif not (team_a or team_b):
            norm_t = normalizer.normalize_team(lean_candidate)
            if not norm_t:
                lean_candidate = None

    if not lean_candidate:
        return {
            "is_valid": True,
            "is_ambiguous": True,
            "reason": "Could not identify definitive toss pick"
        }

    return {
        "is_valid": True,
        "is_ambiguous": False,
        "team_a": team_a,
        "team_b": team_b,
        "match_hint": f"{team_a} vs {team_b}" if (team_a and team_b) else None,
        "lean": lean_candidate,
        "market": market_candidate,
        "notes": text[:200]
    }


# --------------------------------------------------------------------------
# 7. Entity & Match Resolution
# --------------------------------------------------------------------------
async def resolve_match_and_lean(
    session: AsyncSession,
    team_a_hint: Optional[str] = None,
    team_b_hint: Optional[str] = None,
    match_hint: Optional[str] = None,
    raw_lean: Optional[str] = None,
    direct_match_id: Optional[int] = None
) -> Tuple[Optional[int], Optional[str]]:
    """
    Cross-source fixture matching using RapidFuzz normalizer.
    Resolves the external snapshot to an existing database Match and normalizes the lean.
    """
    target_match: Optional[Match] = None

    # 1. Direct ID match if supplied
    if direct_match_id:
        target_match = await session.get(Match, direct_match_id)

    # 2. Match by Team Hints
    if not target_match and (team_a_hint or team_b_hint or match_hint):
        norm_a = normalizer.normalize_team_name(team_a_hint)[0] if team_a_hint else None
        norm_b = normalizer.normalize_team_name(team_b_hint)[0] if team_b_hint else None

        # Fetch upcoming and live fixtures
        stmt = (
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(Match.status.in_(["upcoming", "live", "toss_done"]))
            .order_by(desc(Match.start_time_utc))
        )
        res = await session.execute(stmt)
        candidates = res.scalars().all()

        for cand in candidates:
            cand_a = cand.team_a.name if cand.team_a else ""
            cand_b = cand.team_b.name if cand.team_b else ""

            # Check exact or normalized team matches
            if norm_a and norm_b:
                if (norm_a == cand_a and norm_b == cand_b) or (norm_a == cand_b and norm_b == cand_a):
                    target_match = cand
                    break

            # Fallback: check match hint string
            if match_hint and cand_a.lower() in match_hint.lower() and cand_b.lower() in match_hint.lower():
                target_match = cand
                break

    # 3. Canonicalize the lean string against the resolved match
    canonical_lean: Optional[str] = None
    if raw_lean:
        clean_l = raw_lean.strip()
        if clean_l.upper() in ("SKIP", "NO_EDGE", "NEUTRAL", "PASS"):
            canonical_lean = "NO_EDGE"
        elif target_match:
            team_a_name = target_match.team_a.name if target_match.team_a else "Team A"
            team_b_name = target_match.team_b.name if target_match.team_b else "Team B"

            norm_lean, _ = normalizer.normalize_team_name(clean_l)
            if norm_lean == team_a_name or clean_l.lower() in team_a_name.lower():
                canonical_lean = team_a_name
            elif norm_lean == team_b_name or clean_l.lower() in team_b_name.lower():
                canonical_lean = team_b_name
            else:
                canonical_lean = clean_l
        else:
            norm_lean, _ = normalizer.normalize_team_name(clean_l)
            canonical_lean = norm_lean or clean_l

    match_id = target_match.id if target_match else None
    return match_id, canonical_lean


# --------------------------------------------------------------------------
# 8. High-Level Ingestion Orchestrators
# --------------------------------------------------------------------------
async def poll_external_source(
    session: AsyncSession,
    source: ExternalSource
) -> List[SourceSnapshot]:
    """Execute collection cycle for a single external source."""
    if not source.enabled:
        logger.info(f"Source '{source.name}' is disabled; skipping poll.")
        return []

    adapter = get_source_adapter(source)
    return await adapter.collect(session)


async def poll_all_enabled_sources(session: AsyncSession) -> Dict[str, Any]:
    """Poll all active external sources concurrently or sequentially."""
    stmt = select(ExternalSource).where(ExternalSource.enabled == True)
    res = await session.execute(stmt)
    sources = res.scalars().all()

    total_snapshots = 0
    statuses = {}

    for src in sources:
        try:
            snaps = await poll_external_source(session, src)
            total_snapshots += len(snaps)
            statuses[src.name] = {"status": "OK", "snapshots": len(snaps)}
        except Exception as e:
            logger.error(f"Poll failed for source '{src.name}': {e}")
            statuses[src.name] = {"status": "ERROR", "error": str(e)}

    return {
        "sources_polled": len(sources),
        "total_snapshots": total_snapshots,
        "details": statuses
    }


# Backwards-compatible alias for existing imports
class ExternalSourceCollector:
    """Compatibility wrapper for legacy external prediction ingestion."""

    @staticmethod
    async def ingest_external_prediction(
        session: AsyncSession,
        payload: Dict[str, Any]
    ) -> Any:
        from app.services.prediction_service import lock_prediction
        from app.schemas import PredictionCreate

        match_id = str(payload.get("match_id", "")).strip()
        if not match_id:
            raise ValueError("Payload missing match_id.")

        pred_data = PredictionCreate(
            match_id=match_id,
            model_version_id=payload.get("model_version_id", "external_feed_v1"),
            source_channel=payload.get("source_channel") or "External Feed",
            action_type=payload.get("action_type") or "PLAY",
            predicted_team_id=payload.get("predicted_team_id"),
            estimated_prob_a=payload.get("estimated_prob_a"),
            estimated_prob_b=payload.get("estimated_prob_b"),
            punter_rationale=payload.get("rationale"),
            raw_payload_json=str(payload)
        )
        return await lock_prediction(session, pred_data)
