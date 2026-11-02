"""Market intelligence, rate movements, and session commentary provider service.

Strict rules:
- No reliable free public API exists for exchange rate flows.
- Pluggable market_provider interface.
- Manual entry fallback in admin UI.
- All entries are timestamped (UTC) and append-only (immutable).
- NEVER fabricate, extrapolate, or synthesize market data.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models import MarketEntry, Match, AuditLog
from app.services.audit_service import log_audit_event
from app.db import utc_now


class BaseMarketProvider(ABC):
    """Abstract interface for pluggable betting exchange / market intelligence feeds."""

    @abstractmethod
    async def fetch_market_entries(
        self,
        session: AsyncSession,
        match: Match
    ) -> List[Dict[str, Any]]:
        """Fetch market entries for the given match fixture."""
        pass


class ManualMarketProvider(BaseMarketProvider):
    """Retrieves verified human-entered market intelligence."""

    async def fetch_market_entries(
        self,
        session: AsyncSession,
        match: Match
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(MarketEntry)
            .where(MarketEntry.match_id == match.id)
            .order_by(desc(MarketEntry.fetched_at_utc))
        )
        res = await session.execute(stmt)
        entries = res.scalars().all()
        return [
            {
                "id": e.id,
                "source": e.source,
                "team_lean": e.team_lean,
                "load_market": e.load_market,
                "odds_a": e.odds_a,
                "odds_b": e.odds_b,
                "notes": e.notes,
                "fetched_at_utc": e.fetched_at_utc.isoformat()
            }
            for e in entries
        ]


class PluggableExchangeProvider(BaseMarketProvider):
    """
    Pluggable external provider interface (e.g. Betfair, Pinnacle, Bet365 if API keys supplied).
    Defaults to empty list; never fabricates fake numbers.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    async def fetch_market_entries(
        self,
        session: AsyncSession,
        match: Match
    ) -> List[Dict[str, Any]]:
        if not self.api_key:
            return []
        # External provider implementation hook
        return []


async def record_manual_market_entry(
    session: AsyncSession,
    match_id: int,
    source: str,
    team_lean: Optional[str] = None,
    load_market: Optional[str] = None,
    odds_a: Optional[float] = None,
    odds_b: Optional[float] = None,
    notes: Optional[str] = None,
    admin_user: str = "admin",
    client_ip: Optional[str] = None
) -> MarketEntry:
    """
    Append a verified market intelligence entry to the immutable ledger.
    Never fabricated; records who, what, when.
    """
    stmt = select(Match).where(Match.id == match_id)
    res = await session.execute(stmt)
    match = res.scalar_one_or_none()
    if not match:
        raise ValueError(f"Match #{match_id} not found.")

    entry = MarketEntry(
        match_id=match.id,
        source=source.strip() or "Manual Admin Entry",
        team_lean=team_lean.strip() if team_lean else None,
        load_market=load_market.strip() if load_market else None,
        odds_a=odds_a,
        odds_b=odds_b,
        notes=notes.strip() if notes else None,
        fetched_at_utc=utc_now()
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)

    # Immutable audit trail
    await log_audit_event(
        session=session,
        action="RECORD_MARKET_ENTRY",
        entity="MarketEntry",
        entity_id=str(entry.id),
        user=admin_user,
        ip_address=client_ip,
        details=f"Recorded market entry for Match #{match_id}: lean={team_lean}, source={source}"
    )

    return entry


async def get_market_entries_for_match(
    session: AsyncSession,
    match_id: int
) -> List[MarketEntry]:
    """Retrieve all append-only market entries for a match."""
    stmt = (
        select(MarketEntry)
        .where(MarketEntry.match_id == match_id)
        .order_by(desc(MarketEntry.fetched_at_utc))
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())

