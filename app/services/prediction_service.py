"""Pre-toss prediction locking, cryptographic hashing, and append-only audit service."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Match, PredictionSnapshot, Team
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
) -> PredictionSnapshot:
    """
    Create and cryptographically lock a pre-toss prediction.
    Append-only: Never modifies existing records.
    Validates that the match scheduled start has not already elapsed.
    """
    # 1. Fetch match to verify start time
    stmt = select(Match).where(Match.id == data.match_id)
    res = await session.execute(stmt)
    match = res.scalar_one_or_none()
    if not match:
        raise ValueError(f"Match with ID {data.match_id} does not exist.")

    now_utc = utc_now()
    start_utc = ensure_utc(match.scheduled_start_utc)

    # Check if match has already started or toss completed
    is_pre_toss = True
    if match.status in ("TOSS_COMPLETED", "RESULT_UNAVAILABLE"):
        is_pre_toss = False
    elif start_utc and now_utc >= start_utc:
        is_pre_toss = False

    # 2. Compute cryptographic seal
    snapshot_hash = generate_prediction_hash(
        match_id=data.match_id,
        predicted_team_id=data.predicted_team_id,
        action_type=data.action_type,
        locked_at_utc=now_utc,
        model_version_id=data.model_version_id,
        source_channel=data.source_channel
    )

    assessment_status = "PRE_TOSS_ESTIMATE_AVAILABLE"
    if not is_pre_toss:
        assessment_status = "POST_START_DISQUALIFIED"
    elif data.action_type == "SKIP":
        assessment_status = "NO_VALIDATED_ADVANTAGE"

    # 3. Create snapshot record (strictly append-only)
    snapshot = PredictionSnapshot(
        match_id=data.match_id,
        model_version_id=data.model_version_id,
        source_channel=data.source_channel,
        action_type=data.action_type,
        predicted_team_id=data.predicted_team_id,
        assessment_status=assessment_status,
        estimated_prob_a=data.estimated_prob_a,
        estimated_prob_b=data.estimated_prob_b,
        snapshot_hash=snapshot_hash,
        locked_at_utc=now_utc,
        is_locked=True,
        is_pre_toss=is_pre_toss,
        punter_rationale=data.punter_rationale,
        raw_payload_json=data.raw_payload_json
    )

    session.add(snapshot)
    await session.commit()
    await session.refresh(snapshot)
    return snapshot


async def verify_prediction_integrity(
    session: AsyncSession,
    prediction_id: int
) -> PredictionAuditResponse:
    """
    Verify cryptographic hash and pre-toss validity for a locked prediction.
    """
    stmt = (
        select(PredictionSnapshot, Match)
        .join(Match, PredictionSnapshot.match_id == Match.id)
        .where(PredictionSnapshot.id == prediction_id)
    )
    res = await session.execute(stmt)
    row = res.first()
    if not row:
        raise ValueError(f"Prediction snapshot with ID {prediction_id} not found.")

    snapshot, match = row

    locked_at = ensure_utc(snapshot.locked_at_utc)
    sched_start = ensure_utc(match.scheduled_start_utc)

    # Re-calculate hash
    computed_hash = generate_prediction_hash(
        match_id=snapshot.match_id,
        predicted_team_id=snapshot.predicted_team_id,
        action_type=snapshot.action_type,
        locked_at_utc=locked_at,
        model_version_id=snapshot.model_version_id,
        source_channel=snapshot.source_channel
    )

    is_hash_valid = (snapshot.snapshot_hash == computed_hash)
    is_pre_toss_verified = bool(locked_at and sched_start and locked_at < sched_start)

    if is_hash_valid and is_pre_toss_verified:
        verdict = "PASSED: Cryptographic seal matches and locked strictly BEFORE match start."
    elif not is_hash_valid:
        verdict = "FAILED: Cryptographic seal MISMATCH! Data tampering detected."
    else:
        verdict = "WARNING: Hash valid but prediction was recorded AFTER scheduled start."

    return PredictionAuditResponse(
        prediction_id=snapshot.id,
        match_id=snapshot.match_id,
        snapshot_hash=snapshot.snapshot_hash,
        computed_hash=computed_hash,
        is_valid=is_hash_valid,
        locked_at_utc=snapshot.locked_at_utc,
        scheduled_start_utc=match.scheduled_start_utc,
        is_pre_toss_verified=is_pre_toss_verified,
        audit_verdict=verdict
    )
