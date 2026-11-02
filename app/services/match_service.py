"""Match management, querying, and filtering service."""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy import select, or_, and_, desc
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Match, Team, League, Venue, TossResult, Prediction, PredictionOutcome
from app.db import utc_to_ist, format_ist, utc_now


async def get_matches(
    session: AsyncSession,
    status: Optional[str] = None,
    tab: Optional[str] = None,
    tournament_id: Optional[str] = None,
    search: Optional[str] = None,
    venue_search: Optional[str] = None,
    outcome_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> List[Match]:
    """
    Retrieve matches with lifecycle tab filtering, tournament, search, and outcome filtering.
    Tabs: 'upcoming', 'live', 'toss_done', 'history'.
    """
    now = utc_now()
    query = (
        select(Match)
        .options(
            joinedload(Match.league),
            joinedload(Match.venue),
            joinedload(Match.team_a),
            joinedload(Match.team_b),
            joinedload(Match.toss_result).joinedload(TossResult.winner_team),
            selectinload(Match.predictions).joinedload(Prediction.predicted_team),
            selectinload(Match.predictions).joinedload(Prediction.outcome),
        )
    )

    # 1. Lifecycle Tab Filtering
    if tab == "upcoming":
        query = query.where(
            Match.status.in_(("upcoming", "UPCOMING")),
            Match.start_time_utc > now,
            Match.toss_result == None
        ).order_by(Match.start_time_utc.asc())
    elif tab == "live":
        from datetime import timedelta
        cutoff = now + timedelta(minutes=45)
        query = query.where(
            or_(
                Match.status.in_(("live", "LIVE", "toss_pending", "TOSS_PENDING")),
                and_(Match.start_time_utc <= cutoff, Match.status != "toss_done")
            ),
            Match.toss_result == None
        ).order_by(Match.start_time_utc.asc())
    elif tab == "toss_done":
        query = query.where(
            or_(Match.status.in_(("toss_done", "TOSS_COMPLETED")), Match.toss_result != None)
        ).order_by(desc(Match.start_time_utc))
    elif tab in ("history", "all"):
        query = query.order_by(desc(Match.start_time_utc))
    elif status:
        query = query.where(Match.status == status.lower()).order_by(desc(Match.start_time_utc))
    else:
        query = query.order_by(desc(Match.start_time_utc))

    # 2. Tournament / League Filtering
    if tournament_id:
        if str(tournament_id).isdigit():
            query = query.where(Match.league_id == int(tournament_id))
        else:
            query = query.join(Match.league).where(League.name == tournament_id)

    # 3. Team / Keyword Search
    if search:
        from sqlalchemy.orm import aliased
        TeamA = aliased(Team, name="team_a_search")
        TeamB = aliased(Team, name="team_b_search")
        s = f"%{search.strip()}%"
        query = (
            query
            .outerjoin(TeamA, Match.team_a_id == TeamA.id)
            .outerjoin(TeamB, Match.team_b_id == TeamB.id)
            .where(
                or_(
                    TeamA.name.ilike(s),
                    TeamB.name.ilike(s),
                    Match.gaming_helper_ref.ilike(s),
                    Match.source_match_id.ilike(s),
                )
            )
        )

    # 4. Venue Search
    if venue_search:
        v_str = f"%{venue_search.strip()}%"
        query = query.join(Match.venue).where(Venue.name.ilike(v_str))

    # 5. Outcome Filter (PASS / FAIL / SKIPPED)
    if outcome_filter:
        query = query.join(Match.predictions).join(Prediction.outcome).where(
            PredictionOutcome.result == outcome_filter.upper()
        )

    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    return list(result.unique().scalars().all())


async def get_match_by_id(session: AsyncSession, match_id: str) -> Optional[Match]:
    """Retrieve full match details with all related entities."""
    conditions = []
    if str(match_id).isdigit():
        conditions.append(Match.id == int(match_id))
    conditions.append(Match.source_match_id == str(match_id))
    conditions.append(Match.gaming_helper_ref == str(match_id))

    query = (
        select(Match)
        .options(
            joinedload(Match.league),
            joinedload(Match.venue),
            joinedload(Match.team_a),
            joinedload(Match.team_b),
            joinedload(Match.toss_result).joinedload(TossResult.winner_team),
            selectinload(Match.predictions).joinedload(Prediction.predicted_team),
            selectinload(Match.predictions).joinedload(Prediction.outcome),
        )
        .where(or_(*conditions))
    )
    result = await session.execute(query)
    return result.unique().scalar_one_or_none()


async def upsert_match(
    session: AsyncSession,
    match_data: Dict[str, Any]
) -> Match:
    """Insert or update match schedule from an external ingestion collector."""
    match_id = match_data.get("id")
    existing = None
    if match_id and str(match_id).isdigit():
        stmt = select(Match).where(Match.id == int(match_id))
        res = await session.execute(stmt)
        existing = res.scalar_one_or_none()
    elif match_data.get("gaming_helper_ref"):
        stmt = select(Match).where(Match.gaming_helper_ref == str(match_data["gaming_helper_ref"]))
        res = await session.execute(stmt)
        existing = res.scalar_one_or_none()
    elif match_data.get("source_match_id"):
        stmt = select(Match).where(Match.source_match_id == str(match_data["source_match_id"]))
        res = await session.execute(stmt)
        existing = res.scalar_one_or_none()

    if existing:
        for k, v in match_data.items():
            if k not in ("id", "match_format", "data_source_id", "match_title") and hasattr(existing, k) and v is not None:
                setattr(existing, k, v)
        await session.commit()
        await session.refresh(existing)
        return existing
    else:
        data_clean = dict(match_data)
        if "scheduled_start_utc" in data_clean and "start_time_utc" not in data_clean:
            data_clean["start_time_utc"] = data_clean.pop("scheduled_start_utc")
        if "tournament_id" in data_clean and "league_id" not in data_clean:
            data_clean["league_id"] = data_clean.pop("tournament_id")
        for key in ["match_format", "data_source_id", "match_title"]:
            data_clean.pop(key, None)

        new_match = Match(**data_clean)
        session.add(new_match)
        await session.commit()
        await session.refresh(new_match)
        return new_match
