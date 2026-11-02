"""Ground truth official toss results recording and automatic post-toss evaluation service."""

from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Match, TossResult, PredictionSnapshot, PredictionEvaluation
from app.schemas import TossResultCreate
from app.db import utc_now


async def record_toss_result(
    session: AsyncSession,
    data: TossResultCreate,
    source_provider_id: str = "OFFICIAL"
) -> TossResult:
    """
    Record verified official toss result for a match, update match status,
    and trigger automated evaluation for all locked pre-toss predictions.
    """
    stmt = select(Match).where(Match.id == data.match_id)
    res = await session.execute(stmt)
    match = res.scalar_one_or_none()
    if not match:
        raise ValueError(f"Match {data.match_id} not found.")

    # 1. Upsert official TossResult
    stmt_toss = select(TossResult).where(TossResult.match_id == data.match_id)
    res_toss = await session.execute(stmt_toss)
    existing_toss = res_toss.scalar_one_or_none()

    if existing_toss:
        existing_toss.toss_winner_id = data.toss_winner_id
        existing_toss.toss_decision = data.toss_decision.upper()
        existing_toss.match_winner_id = data.match_winner_id
        existing_toss.official_source_url = data.official_source_url
        existing_toss.source_provider_id = source_provider_id
        existing_toss.verified_at_utc = utc_now()
        existing_toss.verification_status = "VERIFIED"
        existing_toss.notes = data.notes
        toss_obj = existing_toss
    else:
        toss_obj = TossResult(
            match_id=data.match_id,
            toss_winner_id=data.toss_winner_id,
            toss_decision=data.toss_decision.upper(),
            match_winner_id=data.match_winner_id,
            official_source_url=data.official_source_url,
            source_provider_id=source_provider_id,
            verified_at_utc=utc_now(),
            verification_status="VERIFIED",
            notes=data.notes
        )
        session.add(toss_obj)

    # 2. Update Match status
    match.status = "TOSS_COMPLETED"
    match.last_updated_utc = utc_now()

    await session.commit()
    await session.refresh(toss_obj)

    # 3. Automatically evaluate all locked predictions for this match
    await evaluate_predictions_for_match(session, data.match_id, data.toss_winner_id)

    return toss_obj


async def evaluate_predictions_for_match(
    session: AsyncSession,
    match_id: str,
    actual_toss_winner_id: str
) -> List[PredictionEvaluation]:
    """
    Evaluate all locked pre-toss predictions against confirmed toss winner.
    Strictly post-event objective verification.
    """
    stmt = (
        select(PredictionSnapshot)
        .options(selectinload(PredictionSnapshot.evaluation))
        .where(PredictionSnapshot.match_id == match_id)
    )
    res = await session.execute(stmt)
    snapshots = res.scalars().all()

    evaluations: List[PredictionEvaluation] = []

    for snap in snapshots:
        # Check if already evaluated
        if snap.evaluation:
            continue

        is_correct: Optional[bool] = None
        brier: Optional[float] = None

        if snap.action_type == "PLAY":
            if snap.predicted_team_id:
                is_correct = (snap.predicted_team_id == actual_toss_winner_id)
                # If probabilistic estimate was provided for the winner
                if is_correct and snap.estimated_prob_a is not None:
                    brier = round((snap.estimated_prob_a - 1.0)**2, 4)
                elif not is_correct and snap.estimated_prob_a is not None:
                    brier = round((snap.estimated_prob_a - 0.0)**2, 4)
        else:
            # SKIP / PASS call (neutral 50-50 recognition)
            is_correct = None

        eval_record = PredictionEvaluation(
            prediction_snapshot_id=snap.id,
            match_id=match_id,
            actual_toss_winner_id=actual_toss_winner_id,
            predicted_team_id=snap.predicted_team_id,
            is_correct=is_correct,
            brier_score=brier,
            evaluated_at_utc=utc_now(),
            notes="Evaluated against confirmed official toss outcome."
        )
        session.add(eval_record)
        evaluations.append(eval_record)

    if evaluations:
        await session.commit()

    return evaluations

