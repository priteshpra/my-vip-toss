"""Ground truth official toss results recording and automatic post-toss evaluation service."""

import io
import csv
from datetime import datetime, date
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy import select, or_, and_, desc, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Match, TossResult, Prediction, PredictionOutcome, Team, League, Venue
from app.schemas import TossResultCreate
from app.db import utc_now, format_ist


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

    # 4. Automatically score all source snapshots for this match
    try:
        from app.services.source_service import score_source_snapshots_for_match
        await score_source_snapshots_for_match(session, match.id, winner_team_id)
    except Exception as exc:
        logger.warning(f"Error scoring source snapshots for match {match.id}: {exc}")

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


async def get_toss_history(
    session: AsyncSession,
    league_id: Optional[int] = None,
    team_id: Optional[int] = None,
    venue_id: Optional[int] = None,
    outcome_filter: Optional[str] = None,
    source_filter: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    page: int = 1,
    page_size: int = 50
) -> Dict[str, Any]:
    """
    Retrieve permanently stored completed tosses with comprehensive match, prediction,
    and outcome metadata, supporting filtering and pagination.
    """
    # Base query on TossResult joined with Match
    query = (
        select(TossResult, Match)
        .join(Match, TossResult.match_id == Match.id)
        .options(
            selectinload(Match.league),
            selectinload(Match.team_a),
            selectinload(Match.team_b),
            selectinload(Match.venue),
            selectinload(Match.predictions).selectinload(Prediction.outcome),
            selectinload(TossResult.winner_team)
        )
        .order_by(Match.start_time_utc.desc())
    )

    # Apply filters
    if league_id:
        query = query.where(Match.league_id == league_id)
    if team_id:
        query = query.where(or_(Match.team_a_id == team_id, Match.team_b_id == team_id))
    if venue_id:
        query = query.where(Match.venue_id == venue_id)
    if source_filter:
        query = query.where(TossResult.source == source_filter)
    if date_from:
        dt_start = datetime.combine(date_from, datetime.min.time())
        query = query.where(Match.start_time_utc >= dt_start)
    if date_to:
        dt_end = datetime.combine(date_to, datetime.max.time())
        query = query.where(Match.start_time_utc <= dt_end)

    res = await session.execute(query)
    all_rows = res.all()

    # Filter by outcome if requested
    filtered_items = []
    for toss, m in all_rows:
        latest_pred = m.predictions[-1] if m.predictions else None
        outcome_val = "UNRATED"
        if latest_pred:
            if latest_pred.outcome:
                outcome_val = latest_pred.outcome.result
            elif latest_pred.lean == "NO_EDGE":
                outcome_val = "SKIPPED"

        if outcome_filter and outcome_filter != "ALL":
            if outcome_val != outcome_filter:
                continue

        filtered_items.append({
            "match_id": m.id,
            "match_date_utc": m.start_time_utc,
            "match_date_ist": format_ist(m.start_time_utc) if m.start_time_utc else "-",
            "league_name": m.league.name if m.league else "Local/Unknown",
            "team_a_name": m.team_a.name if m.team_a else str(m.team_a_id),
            "team_b_name": m.team_b.name if m.team_b else str(m.team_b_id),
            "venue_name": m.venue.name if m.venue else "Standard Ground",
            "toss_winner_name": toss.winner_team.name if toss.winner_team else str(toss.winner_team_id),
            "toss_decision": toss.decision.upper(),
            "toss_source": toss.source,
            "telegram_message_id": toss.telegram_message_id,
            "received_at_utc": toss.received_at_utc,
            "received_at_ist": format_ist(toss.received_at_utc),
            "prediction": latest_pred,
            "prediction_lean": latest_pred.lean if latest_pred else "NONE",
            "prediction_probability": latest_pred.probability if latest_pred else None,
            "prediction_ci_display": f"[{round(latest_pred.ci_low * 100, 0):.0f}% - {round(latest_pred.ci_high * 100, 0):.0f}%]" if latest_pred and latest_pred.ci_low is not None else "N/A",
            "prediction_hash": latest_pred.hash if latest_pred else "-",
            "outcome_result": outcome_val,
            "brier_score": latest_pred.outcome.brier_score if latest_pred and latest_pred.outcome else None
        })

    total_count = len(filtered_items)
    offset = (page - 1) * page_size
    paged_items = filtered_items[offset:offset + page_size]

    return {
        "items": paged_items,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total_count + page_size - 1) // page_size)
    }


def export_toss_history_csv(history_items: List[Dict[str, Any]]) -> str:
    """Generate CSV export of completed toss history records with honesty disclaimer."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["CRICKET TOSS ANALYTICS - AUDITED TOSS HISTORY"])
    writer.writerow(["DISCLAIMER: Toss is a random event. Past stats do not guarantee results. Play responsibly."])
    writer.writerow([f"Exported Records: {len(history_items)}", f"Generated: {datetime.now().isoformat()}"])
    writer.writerow([])

    headers = [
        "Match ID",
        "Match Date (IST)",
        "League / Tournament",
        "Team A",
        "Team B",
        "Venue",
        "Locked Pre-Toss Call",
        "Win Probability",
        "95% Wilson CI",
        "SHA-256 Seal",
        "Official Toss Winner",
        "Toss Decision",
        "Evaluation Outcome",
        "Verification Source",
        "Recorded (IST)"
    ]
    writer.writerow(headers)

    for r in history_items:
        writer.writerow([
            r.get("match_id"),
            r.get("match_date_ist"),
            r.get("league_name"),
            r.get("team_a_name"),
            r.get("team_b_name"),
            r.get("venue_name"),
            r.get("prediction_lean"),
            f"{(r.get('prediction_probability') * 100):.1f}%" if r.get("prediction_probability") else "-",
            r.get("prediction_ci_display", "N/A"),
            r.get("prediction_hash", "-"),
            r.get("toss_winner_name"),
            r.get("toss_decision"),
            r.get("outcome_result"),
            r.get("toss_source"),
            r.get("received_at_ist")
        ])

    writer.writerow([])
    writer.writerow(["PERMANENT AUDIT DISCLAIMER: Toss is a random event. Past stats do not guarantee results. Play responsibly."])
    return output.getvalue()
