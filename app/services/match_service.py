"""Match management, querying, and filtering service."""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import select, or_, and_, desc
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Match, Team, Tournament, Venue, TossResult, PredictionSnapshot
from app.db import utc_to_ist, format_ist, utc_now


async def get_matches(
    session: AsyncSession,
    status: Optional[str] = None,
    tournament_id: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> List[Match]:
    """Retrieve matches with optional status, tournament, and search filtering."""
    query = (
        select(Match)
        .options(
            joinedload(Match.tournament),
            joinedload(Match.venue),
            joinedload(Match.team_a),
            joinedload(Match.team_b),
            joinedload(Match.toss_result),
            selectinload(Match.predictions),
        )
        .order_by(desc(Match.scheduled_start_utc))
    )

    if status:
        query = query.where(Match.status == status)
    if tournament_id:
        query = query.where(Match.tournament_id == tournament_id)
    if search:
        s = f"%{search.strip()}%"
        query = query.join(Match.team_a).join(Match.team_b).where(
            or_(
                Match.match_title.ilike(s),
                Match.id.ilike(s),
                Match.tournament_id.ilike(s),
            )
        )

    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    return list(result.unique().scalars().all())


async def get_match_by_id(session: AsyncSession, match_id: str) -> Optional[Match]:
    """Retrieve full match details with all related entities."""
    query = (
        select(Match)
        .options(
            joinedload(Match.tournament),
            joinedload(Match.venue),
            joinedload(Match.team_a),
            joinedload(Match.team_b),
            joinedload(Match.toss_result).joinedload(TossResult.toss_winner),
            selectinload(Match.predictions).joinedload(PredictionSnapshot.predicted_team),
            selectinload(Match.predictions).joinedload(PredictionSnapshot.evaluation),
        )
        .where(Match.id == match_id)
    )
    result = await session.execute(query)
    return result.unique().scalar_one_or_none()


async def upsert_match(
    session: AsyncSession,
    match_data: Dict[str, Any]
) -> Match:
    """Insert or update match schedule from an external ingestion collector."""
    match_id = match_data["id"]
    stmt = select(Match).where(Match.id == match_id)
    res = await session.execute(stmt)
    existing = res.scalar_one_or_none()

    if existing:
        for k, v in match_data.items():
            if k != "id" and hasattr(existing, k) and v is not None:
                setattr(existing, k, v)
        existing.last_updated_utc = utc_now()
        await session.commit()
        await session.refresh(existing)
        return existing
    else:
        new_match = Match(**match_data)
        new_match.last_updated_utc = utc_now()
        session.add(new_match)
        await session.commit()
        await session.refresh(new_match)
        return new_match

