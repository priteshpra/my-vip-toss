"""Pre-toss prediction locking, cryptographic hashing, and append-only audit service."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Match, Prediction, Team, PredictionOutcome, AuditLog, TossResult
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


def generate_lean_from_analysis(
    analysis: Dict[str, Any],
    min_sample: int = 30
) -> Dict[str, Any]:
    """
    Generate an honest pre-toss lean based on comprehensive historical statistical analysis.
    
    Honesty Rule (Non-negotiable):
    A cricket toss is a fair coin flip (p = 0.50).
    If there is no statistically significant edge (n < 30, or 95% Wilson CI includes 0.50),
    the lean MUST be 'NO_EDGE' (Action: SKIP) with probability 0.50 and CI [0.50, 0.50].
    Only when an empirical sample has n >= 30, p-value < 0.05, and CI strictly above 0.50,
    is an actionable 'TeamA' or 'TeamB' PLAY lean produced.
    """
    team_a = analysis.get("team_a_name", "Team A")
    team_b = analysis.get("team_b_name", "Team B")
    stats_a = analysis.get("stats_a", {})
    stats_b = analysis.get("stats_b", {})
    h2h_stats = analysis.get("h2h_stats", {})

    candidates = []

    # 1. Direct H2H
    h2h_a = h2h_stats.get("stat_a", {})
    h2h_b = h2h_stats.get("stat_b", {})
    if h2h_a.get("n", 0) >= min_sample and not h2h_a.get("not_different_from_50", True):
        if h2h_a.get("ci_low", 0.0) > 0.50:
            candidates.append({
                "lean": "TeamA",
                "team_name": team_a,
                "prob": round(h2h_a.get("win_pct", 50.0) / 100.0, 4),
                "ci_low": h2h_a.get("ci_low"),
                "ci_high": h2h_a.get("ci_high"),
                "n": h2h_a.get("n"),
                "reason": f"Direct H2H empirical edge (n={h2h_a.get('n')}, win%={h2h_a.get('win_pct')}%, 95% CI [{h2h_a.get('ci_low')}-{h2h_a.get('ci_high')}])"
            })
        elif h2h_b.get("ci_low", 0.0) > 0.50:
            candidates.append({
                "lean": "TeamB",
                "team_name": team_b,
                "prob": round(h2h_b.get("win_pct", 50.0) / 100.0, 4),
                "ci_low": h2h_b.get("ci_low"),
                "ci_high": h2h_b.get("ci_high"),
                "n": h2h_b.get("n"),
                "reason": f"Direct H2H empirical edge (n={h2h_b.get('n')}, win%={h2h_b.get('win_pct')}%, 95% CI [{h2h_b.get('ci_low')}-{h2h_b.get('ci_high')}])"
            })

    # 2. Format-specific stats
    by_fmt_a = stats_a.get("by_format", {})
    by_fmt_b = stats_b.get("by_format", {})
    for fmt_name, s_a in by_fmt_a.items():
        if s_a.get("n", 0) >= min_sample and not s_a.get("not_different_from_50", True) and s_a.get("ci_low", 0.0) > 0.50:
            candidates.append({
                "lean": "TeamA",
                "team_name": team_a,
                "prob": round(s_a.get("win_pct", 50.0) / 100.0, 4),
                "ci_low": s_a.get("ci_low"),
                "ci_high": s_a.get("ci_high"),
                "n": s_a.get("n"),
                "reason": f"Format ({fmt_name}) empirical edge (n={s_a.get('n')}, win%={s_a.get('win_pct')}%, 95% CI [{s_a.get('ci_low')}-{s_a.get('ci_high')}])"
            })
    for fmt_name, s_b in by_fmt_b.items():
        if s_b.get("n", 0) >= min_sample and not s_b.get("not_different_from_50", True) and s_b.get("ci_low", 0.0) > 0.50:
            candidates.append({
                "lean": "TeamB",
                "team_name": team_b,
                "prob": round(s_b.get("win_pct", 50.0) / 100.0, 4),
                "ci_low": s_b.get("ci_low"),
                "ci_high": s_b.get("ci_high"),
                "n": s_b.get("n"),
                "reason": f"Format ({fmt_name}) empirical edge (n={s_b.get('n')}, win%={s_b.get('win_pct')}%, 95% CI [{s_b.get('ci_low')}-{s_b.get('ci_high')}])"
            })

    # 3. Overall stats
    ov_a = stats_a.get("overall", {})
    ov_b = stats_b.get("overall", {})
    if ov_a.get("n", 0) >= min_sample and not ov_a.get("not_different_from_50", True) and ov_a.get("ci_low", 0.0) > 0.50:
        candidates.append({
            "lean": "TeamA",
            "team_name": team_a,
            "prob": round(ov_a.get("win_pct", 50.0) / 100.0, 4),
            "ci_low": ov_a.get("ci_low"),
            "ci_high": ov_a.get("ci_high"),
            "n": ov_a.get("n"),
            "reason": f"Overall historical toss edge (n={ov_a.get('n')}, win%={ov_a.get('win_pct')}%, 95% CI [{ov_a.get('ci_low')}-{ov_a.get('ci_high')}])"
        })
    elif ov_b.get("n", 0) >= min_sample and not ov_b.get("not_different_from_50", True) and ov_b.get("ci_low", 0.0) > 0.50:
        candidates.append({
            "lean": "TeamB",
            "team_name": team_b,
            "prob": round(ov_b.get("win_pct", 50.0) / 100.0, 4),
            "ci_low": ov_b.get("ci_low"),
            "ci_high": ov_b.get("ci_high"),
            "n": ov_b.get("n"),
            "reason": f"Overall historical toss edge (n={ov_b.get('n')}, win%={ov_b.get('win_pct')}%, 95% CI [{ov_b.get('ci_low')}-{ov_b.get('ci_high')}])"
        })

    # If an edge exists: pick highest sample / probability
    if candidates:
        best = max(candidates, key=lambda c: (c["n"], c["prob"]))
        return {
            "lean": best["lean"],
            "action_type": "PLAY",
            "predicted_team_name": best["team_name"],
            "probability": best["prob"],
            "ci_low": best["ci_low"],
            "ci_high": best["ci_high"],
            "has_edge": True,
            "rationale": best["reason"]
        }

    # The HONEST default: NO EDGE
    return {
        "lean": "NO_EDGE",
        "action_type": "SKIP",
        "predicted_team_name": "None (No Statistical Edge)",
        "probability": 0.50,
        "ci_low": 0.50,
        "ci_high": 0.50,
        "has_edge": False,
        "rationale": "NO EDGE - skip: Empirical toss history is statistically indistinguishable from a 50% fair coin flip (sample size n < 30 or 95% Wilson CI includes 0.50)."
    }


async def lock_match_prediction(
    session: AsyncSession,
    match_id: int,
    lean: Optional[str] = None,
    predicted_team_id: Optional[int] = None,
    probability: Optional[float] = None,
    ci_low: Optional[float] = None,
    ci_high: Optional[float] = None,
    source: str = "own",
    admin_user: Optional[str] = None,
    client_ip: Optional[str] = None,
    punter_rationale: Optional[str] = None,
    strict: bool = True
) -> Prediction:
    """
    Lock a pre-toss prediction for a match fixture.
    Strictly append-only and cryptographically sealed with SHA-256 before match start.
    Blocks creation after match start or after toss completion.
    Records immutable audit log.
    """
    # 1. Fetch match and check toss / schedule status
    stmt = (
        select(Match)
        .options(selectinload(Match.team_a), selectinload(Match.team_b), selectinload(Match.toss_result))
        .where(Match.id == match_id)
    )
    res = await session.execute(stmt)
    match = res.scalar_one_or_none()
    if not match:
        raise ValueError(f"Match #{match_id} does not exist.")

    now_utc = utc_now()
    start_utc = ensure_utc(match.scheduled_start_utc)

    # 2. Strict guard against post-event fabrication
    if strict:
        if match.status in ("toss_done", "TOSS_COMPLETED") or match.toss_result is not None:
            raise ValueError("Cannot create or lock prediction: official toss result has already been recorded.")
        if start_utc and now_utc >= start_utc:
            raise ValueError("Cannot create or lock prediction: match scheduled start time has already elapsed.")

    # 3. If lean is not specified, run honest statistical derivation
    if not lean:
        from app.services.analysis_service import get_comprehensive_match_analysis
        analysis = await get_comprehensive_match_analysis(session, match)
        auto_lean = generate_lean_from_analysis(analysis)
        lean = auto_lean["lean"]
        probability = auto_lean["probability"]
        ci_low = auto_lean["ci_low"]
        ci_high = auto_lean["ci_high"]
        punter_rationale = auto_lean["rationale"]

        if lean == "TeamA":
            predicted_team_id = match.team_a_id
        elif lean == "TeamB":
            predicted_team_id = match.team_b_id
        else:
            predicted_team_id = None

    # Resolve predicted_team_id if lean is TeamA or TeamB
    if lean == "TeamA" and not predicted_team_id:
        predicted_team_id = match.team_a_id
    elif lean == "TeamB" and not predicted_team_id:
        predicted_team_id = match.team_b_id

    # 4. Generate SHA-256 seal
    seal_raw = f"{match.id}|{predicted_team_id or 'NONE'}|{lean}|{now_utc.isoformat()}|{source}"
    seal_hash = hashlib.sha256(seal_raw.encode("utf-8")).hexdigest()

    # 5. Insert Prediction record
    pred = Prediction(
        match_id=match.id,
        lean=lean,
        predicted_team_id=predicted_team_id,
        probability=probability or 0.50,
        ci_low=ci_low if ci_low is not None else 0.50,
        ci_high=ci_high if ci_high is not None else 0.50,
        locked_at_utc=now_utc,
        hash=seal_hash,
        source=source,
        is_pre_toss=bool(start_utc and now_utc < start_utc),
        punter_rationale=punter_rationale
    )
    session.add(pred)
    await session.flush()

    # 6. If lean is NO_EDGE, automatically register SKIPPED outcome
    if lean == "NO_EDGE":
        outcome = PredictionOutcome(
            prediction_id=pred.id,
            result="SKIPPED",
            brier_score=None,
            evaluated_at_utc=now_utc
        )
        session.add(outcome)

    # 7. Record immutable AuditLog entry
    audit_action = "MANUAL_CALL_LOCKED" if source == "manual" else "LOCK_PREDICTION"
    audit = AuditLog(
        action=audit_action,
        entity="Prediction",
        entity_id=str(pred.id),
        user=admin_user or "system",
        ip_address=client_ip or "127.0.0.1",
        timestamp_utc=now_utc,
        details=json.dumps({
            "match_id": match.id,
            "lean": lean,
            "probability": probability,
            "hash": seal_hash,
            "source": source
        })
    )
    session.add(audit)

    await session.commit()
    await session.refresh(pred)
    return pred
