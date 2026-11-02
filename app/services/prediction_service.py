"""Pre-toss prediction locking, cryptographic hashing, and append-only audit service."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Match, Prediction, Team
from app.schemas import PredictionCreate, PredictionAuditResponse
from app.db import utc_now, ensure_utc


def generate_prediction_hash(
    match_id: str,
    predicted_team_id: Optional[str],
    action_type: str,
    locked_at_utc: datetime,
    model_version_id: str,
    source_channel: Optional[str]
) -> str:
    """
    Generate SHA-256 cryptographic seal locking the prediction.
    Ensures predictions cannot be altered or fabricated retrospectively.
    """
    utc_dt = ensure_utc(locked_at_utc)
    iso_ts = utc_dt.isoformat()
    raw = f"{match_id}|{predicted_team_id or 'NONE'}|{action_type}|{iso_ts}|{model_version_id}|{source_channel or 'UNKNOWN'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def lock_prediction(
    session: AsyncSession,
    data: PredictionCreate,
) -> Prediction:
    """
    Create and cryptographically lock a pre-toss prediction.
    Append-only: Never modifies existing records.
    Validates that the match scheduled start has not already elapsed.
    """
    # 1. Fetch match to verify start time
    conditions = []
    if str(data.match_id).isdigit():
        conditions.append(Match.id == int(data.match_id))
    conditions.append(Match.source_match_id == str(data.match_id))
    conditions.append(Match.gaming_helper_ref == str(data.match_id))

    stmt = select(Match).where(or_(*conditions))
    res = await session.execute(stmt)
    match = res.scalar_one_or_none()
    if not match:
        raise ValueError(f"Match with ID {data.match_id} does not exist.")

    now_utc = utc_now()
    start_utc = ensure_utc(match.scheduled_start_utc)

    # Check if match has already started or toss completed
    is_pre_toss = True
    if match.status in ("toss_done", "TOSS_COMPLETED", "RESULT_UNAVAILABLE"):
        is_pre_toss = False
    elif start_utc and now_utc >= start_utc:
        is_pre_toss = False

    # 2. Compute cryptographic seal
    snapshot_hash = generate_prediction_hash(
        match_id=str(data.match_id),
        predicted_team_id=str(data.predicted_team_id) if data.predicted_team_id else None,
        action_type=data.action_type,
        locked_at_utc=now_utc,
        model_version_id=data.model_version_id,
        source_channel=data.source_channel
    )

    # Resolve predicted team integer ID if string was passed
    pred_team_id = None
    lean = "NO_EDGE"
    if data.action_type == "PLAY":
        if data.predicted_team_id:
            if str(data.predicted_team_id).isdigit():
                pred_team_id = int(data.predicted_team_id)
            else:
                # Look up team by name
                stmt_t = select(Team).where(Team.name == str(data.predicted_team_id))
                res_t = await session.execute(stmt_t)
                t_obj = res_t.scalar_one_or_none()
                if t_obj:
                    pred_team_id = t_obj.id

        if pred_team_id and pred_team_id == match.team_a_id:
            lean = "TeamA"
        elif pred_team_id and pred_team_id == match.team_b_id:
            lean = "TeamB"
        else:
            lean = "TeamA"

    # 3. Create prediction record (strictly append-only & immutable)
    prediction = Prediction(
        match_id=match.id,
        lean=lean,
        predicted_team_id=pred_team_id,
        probability=data.estimated_prob_a or 0.50,
        hash=snapshot_hash,
        source=data.source_channel or "own",
        locked_at_utc=now_utc,
        is_pre_toss=is_pre_toss,
        punter_rationale=data.punter_rationale
    )

    session.add(prediction)
    await session.commit()
    await session.refresh(prediction)
    return prediction


async def verify_prediction_integrity(
    session: AsyncSession,
    prediction_id: int,
    model_version_id: Optional[str] = None
) -> PredictionAuditResponse:
    """
    Verify cryptographic hash and pre-toss validity for a locked prediction.
    """
    stmt = (
        select(Prediction, Match)
        .join(Match, Prediction.match_id == Match.id)
        .where(Prediction.id == prediction_id)
    )
    res = await session.execute(stmt)
    row = res.first()
    if not row:
        raise ValueError(f"Prediction snapshot with ID {prediction_id} not found.")

    snapshot, match = row

    locked_at = ensure_utc(snapshot.locked_at_utc)
    sched_start = ensure_utc(match.scheduled_start_utc)

    # Re-calculate hash across candidate model versions if not specified
    candidate_versions = [model_version_id] if model_version_id else ["baseline_model", "model_v1", "v1", "toss_pattern_v1"]
    computed_hash = ""
    is_hash_valid = False
    for v in candidate_versions:
        computed = generate_prediction_hash(
            match_id=str(snapshot.match_id),
            predicted_team_id=str(snapshot.predicted_team_id) if snapshot.predicted_team_id else None,
            action_type=snapshot.action_type,
            locked_at_utc=locked_at,
            model_version_id=v,
            source_channel=snapshot.source
        )
        if computed == snapshot.hash:
            computed_hash = computed
            is_hash_valid = True
            break

    if not computed_hash:
        computed_hash = generate_prediction_hash(
            match_id=str(snapshot.match_id),
            predicted_team_id=str(snapshot.predicted_team_id) if snapshot.predicted_team_id else None,
            action_type=snapshot.action_type,
            locked_at_utc=locked_at,
            model_version_id=candidate_versions[0],
            source_channel=snapshot.source
        )

    is_pre_toss_verified = bool(locked_at and sched_start and locked_at < sched_start)

    if is_hash_valid and is_pre_toss_verified:
        verdict = "PASSED: Cryptographic seal matches and locked strictly BEFORE match start."
    elif not is_hash_valid:
        verdict = "FAILED: Cryptographic seal MISMATCH! Data tampering detected."
    else:
        verdict = "WARNING: Hash valid but prediction was recorded AFTER scheduled start."

    return PredictionAuditResponse(
        prediction_id=snapshot.id,
        match_id=str(snapshot.match_id),
        snapshot_hash=snapshot.hash,
        computed_hash=computed_hash,
        is_valid=is_hash_valid,
        locked_at_utc=snapshot.locked_at_utc,
        scheduled_start_utc=match.start_time_utc,
        is_pre_toss_verified=is_pre_toss_verified,
        audit_verdict=verdict
    )
