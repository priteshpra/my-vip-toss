"""Ground truth official toss results recording and automatic post-toss evaluation service."""

from typing import Optional, List
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Match, TossResult, Prediction, PredictionOutcome, Team
from app.schemas import TossResultCreate
from app.db import utc_now


async def record_toss_result(
    session: AsyncSession,
    data: TossResultCreate,
    source_provider_id: str = "manual"
) -> TossResult:
    """
    Record verified official toss result for a match, update match status to toss_done,
    and trigger automated evaluation for all locked pre-toss predictions.
    """
    conditions = []
    if str(data.match_id).isdigit():
        conditions.append(Match.id == int(data.match_id))
    conditions.append(Match.source_match_id == str(data.match_id))
    conditions.append(Match.gaming_helper_ref == str(data.match_id))

    stmt = select(Match).where(or_(*conditions))
    res = await session.execute(stmt)
    match = res.scalar_one_or_none()
    if not match:
        raise ValueError(f"Match {data.match_id} not found.")

    # Resolve winner team integer ID if string was passed
    winner_team_id = None
    if str(data.toss_winner_id).isdigit():
        winner_team_id = int(data.toss_winner_id)
    else:
        stmt_t = select(Team).where(Team.name == str(data.toss_winner_id))
        res_t = await session.execute(stmt_t)
        t_obj = res_t.scalar_one_or_none()
        if t_obj:
            winner_team_id = t_obj.id
        else:
            t_obj = Team(name=str(data.toss_winner_id), short_code=str(data.toss_winner_id)[:3].upper())
            session.add(t_obj)
            await session.commit()
            await session.refresh(t_obj)
            winner_team_id = t_obj.id

    decision = "bat" if "bat" in data.toss_decision.lower() else "field"

    # 1. Upsert official TossResult
    stmt_toss = select(TossResult).where(TossResult.match_id == match.id)
    res_toss = await session.execute(stmt_toss)
    existing_toss = res_toss.scalar_one_or_none()

    if existing_toss:
        existing_toss.winner_team_id = winner_team_id
        existing_toss.decision = decision
        existing_toss.source = source_provider_id
        existing_toss.official_source_url = data.official_source_url
        existing_toss.received_at_utc = utc_now()
        existing_toss.notes = data.notes
        toss_obj = existing_toss
    else:
        toss_obj = TossResult(
            match_id=match.id,
            winner_team_id=winner_team_id,
            decision=decision,
            source=source_provider_id,
            received_at_utc=utc_now(),
            official_source_url=data.official_source_url,
            notes=data.notes
        )
        session.add(toss_obj)

    # 2. Update Match status
    match.status = "toss_done"

    await session.commit()
    await session.refresh(toss_obj)

    # 3. Automatically evaluate all locked predictions for this match
    await evaluate_predictions_for_match(session, match.id, winner_team_id)

    return toss_obj


async def evaluate_predictions_for_match(
    session: AsyncSession,
    match_id: int,
    actual_winner_team_id: int
) -> List[PredictionOutcome]:
    """
    Evaluate all locked pre-toss predictions against confirmed toss winner.
    Strictly post-event objective verification.
    """
    stmt = (
        select(Prediction)
        .options(selectinload(Prediction.outcome))
        .where(Prediction.match_id == match_id)
    )
    res = await session.execute(stmt)
    snapshots = res.scalars().all()

    evaluations: List[PredictionOutcome] = []

    for snap in snapshots:
        if snap.outcome:
            continue

        result = "PASS"
        brier = None

        if snap.lean == "NO_EDGE":
            result = "SKIPPED"
        else:
            is_match = (snap.predicted_team_id == actual_winner_team_id)
            result = "PASS" if is_match else "FAIL"
            prob = snap.probability or 0.50
            target = 1.0 if is_match else 0.0
            brier = round((prob - target) ** 2, 4)

        outcome_record = PredictionOutcome(
            prediction_id=snap.id,
            result=result,
            brier_score=brier,
            evaluated_at_utc=utc_now()
        )
        session.add(outcome_record)
        evaluations.append(outcome_record)

    if evaluations:
        await session.commit()

    return evaluations
