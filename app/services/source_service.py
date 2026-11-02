"""Multi-source toss intelligence, dynamic configuration, snapshot capture, and audited accuracy scoring service.

Strict adherence to honesty rules:
- Toss is a random Bernoulli event (p = 0.50). Never promise guarantees.
- Rank sources by measured accuracy ONLY when sample size n >= 30; otherwise flag 'Insufficient data'.
- Snapshots are strictly append-only and cryptographically auditable.
- Source consensus is tracked objectively with Wilson 95% confidence intervals and 50% baseline tests.
"""

from typing import List, Dict, Any, Optional, Tuple
import json
from datetime import datetime, timezone
from sqlalchemy import select, func, distinct, desc, or_, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models import (
    ExternalSource, SourceSnapshot, SourceOutcome, Match, Team, TossResult, Prediction, CollectorHealth
)
from app.services.accuracy_service import (
    calculate_wilson_score_interval, binomial_test_against_baseline, get_accuracy_metrics_for_source
)
from app.services.audit_service import record_audit_log
from app.services.normalizer import normalizer, clean_text
from app.db import utc_now, ensure_utc, format_ist
from app.schemas import AccuracyMetrics, WilsonConfidenceInterval, BinomialBaselineTest


# --------------------------------------------------------------------------
# 1. Source CRUD & Dynamic Configuration (No Code Changes Required)
# --------------------------------------------------------------------------
async def get_all_sources(session: AsyncSession) -> List[ExternalSource]:
    """Retrieve all configured external sources."""
    query = select(ExternalSource).order_by(ExternalSource.name)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_source_by_id(session: AsyncSession, source_id: int) -> Optional[ExternalSource]:
    """Retrieve a single external source by its primary key ID."""
    return await session.get(ExternalSource, source_id)


async def create_external_source(
    session: AsyncSession,
    name: str,
    source_type: str,
    url: Optional[str] = None,
    parse_rules: Optional[str] = None,
    poll_interval: int = 300,
    enabled: bool = True,
    admin_user: Optional[str] = None,
    client_ip: Optional[str] = None
) -> ExternalSource:
    """Create a new external source entry dynamically from admin UI."""
    clean_name = name.strip()
    # Check if duplicate name exists
    existing_stmt = select(ExternalSource).where(ExternalSource.name == clean_name)
    existing_res = await session.execute(existing_stmt)
    if existing_res.scalar_one_or_none():
        raise ValueError(f"Source with name '{clean_name}' already exists.")

    # Validate JSON parse rules if provided
    if parse_rules and parse_rules.strip():
        try:
            json.loads(parse_rules)
        except Exception as e:
            raise ValueError(f"Invalid parse_rules JSON: {str(e)}")

    source = ExternalSource(
        name=clean_name,
        type=source_type.strip(),
        url=url.strip() if url else None,
        parse_rules=parse_rules.strip() if parse_rules else None,
        poll_interval=poll_interval,
        enabled=enabled,
        health="HEALTHY"
    )
    session.add(source)
    await session.commit()
    await session.refresh(source)

    await record_audit_log(
        session=session,
        action="CREATE_SOURCE",
        entity="ExternalSource",
        entity_id=str(source.id),
        user=admin_user,
        ip_address=client_ip,
        details=f"Created external source '{source.name}' (type={source.type}, enabled={source.enabled})"
    )

    logger.info(f"Admin '{admin_user}' created external source '{source.name}' [ID: {source.id}].")
    return source


async def update_external_source(
    session: AsyncSession,
    source_id: int,
    name: str,
    source_type: str,
    url: Optional[str] = None,
    parse_rules: Optional[str] = None,
    poll_interval: int = 300,
    enabled: bool = True,
    admin_user: Optional[str] = None,
    client_ip: Optional[str] = None
) -> ExternalSource:
    """Update external source configuration without code modifications."""
    source = await session.get(ExternalSource, source_id)
    if not source:
        raise ValueError(f"External source with ID {source_id} not found.")

    clean_name = name.strip()
    if clean_name != source.name:
        dup_check = await session.execute(select(ExternalSource).where(ExternalSource.name == clean_name))
        if dup_check.scalar_one_or_none():
            raise ValueError(f"Another source with name '{clean_name}' already exists.")

    if parse_rules and parse_rules.strip():
        try:
            json.loads(parse_rules)
        except Exception as e:
            raise ValueError(f"Invalid parse_rules JSON: {str(e)}")

    source.name = clean_name
    source.type = source_type.strip()
    source.url = url.strip() if url else None
    source.parse_rules = parse_rules.strip() if parse_rules else None
    source.poll_interval = poll_interval
    source.enabled = enabled

    await session.commit()
    await session.refresh(source)

    await record_audit_log(
        session=session,
        action="UPDATE_SOURCE",
        entity="ExternalSource",
        entity_id=str(source.id),
        user=admin_user,
        ip_address=client_ip,
        details=f"Updated external source '{source.name}' (type={source.type}, enabled={source.enabled})"
    )

    logger.info(f"Admin '{admin_user}' updated external source '{source.name}' [ID: {source.id}].")
    return source


async def toggle_source_enabled(
    session: AsyncSession,
    source_id: int,
    admin_user: Optional[str] = None,
    client_ip: Optional[str] = None
) -> ExternalSource:
    """Toggle source enabled/disabled state."""
    source = await session.get(ExternalSource, source_id)
    if not source:
        raise ValueError(f"External source with ID {source_id} not found.")

    source.enabled = not source.enabled
    await session.commit()
    await session.refresh(source)

    await record_audit_log(
        session=session,
        action="TOGGLE_SOURCE",
        entity="ExternalSource",
        entity_id=str(source.id),
        user=admin_user,
        ip_address=client_ip,
        details=f"Toggled source '{source.name}' enabled status to {source.enabled}"
    )
    return source


# --------------------------------------------------------------------------
# 2. Append-Only Snapshot Capture & Manual Entry
# --------------------------------------------------------------------------
async def record_manual_source_snapshot(
    session: AsyncSession,
    match_id: int,
    source_id: int,
    lean: str,
    market: Optional[str] = None,
    session_notes: Optional[str] = None,
    admin_user: Optional[str] = None,
    client_ip: Optional[str] = None
) -> SourceSnapshot:
    """
    Record an append-only snapshot for a fixture from a tracked source or admin.
    Strictly immutable once committed.
    """
    match = await session.get(Match, match_id)
    if not match:
        raise ValueError(f"Match with ID {match_id} does not exist.")

    source = await session.get(ExternalSource, source_id)
    if not source:
        raise ValueError(f"Source with ID {source_id} does not exist.")

    snapshot = SourceSnapshot(
        source_id=source.id,
        match_id=match.id,
        lean=lean.strip(),
        market=market.strip() if market else None,
        session_notes=session_notes.strip() if session_notes else None,
        raw=f"Manual admin entry by {admin_user or 'admin'}: lean={lean}, market={market}",
        fetched_at_utc=utc_now()
    )
    session.add(snapshot)
    await session.commit()
    await session.refresh(snapshot)

    await record_audit_log(
        session=session,
        action="RECORD_SOURCE_SNAPSHOT",
        entity="SourceSnapshot",
        entity_id=str(snapshot.id),
        user=admin_user,
        ip_address=client_ip,
        details=f"Recorded snapshot for match {match_id} from source '{source.name}' with lean='{lean}'"
    )

    logger.info(f"Recorded snapshot ID {snapshot.id} for match {match_id} from source '{source.name}'.")
    return snapshot


# --------------------------------------------------------------------------
# 3. Post-Toss Automated Source Accuracy Scoring
# --------------------------------------------------------------------------
async def score_source_snapshots_for_match(
    session: AsyncSession,
    match_id: int,
    actual_winner_team_id: int
) -> List[SourceOutcome]:
    """
    After each toss result, automatically evaluate all source snapshots for the fixture.
    Grades each snapshot as PASS, FAIL, or SKIPPED.
    Append-only: creates SourceOutcome entries.
    """
    # 1. Fetch match and winner team
    match_stmt = (
        select(Match)
        .options(selectinload(Match.team_a), selectinload(Match.team_b))
        .where(Match.id == match_id)
    )
    match_res = await session.execute(match_stmt)
    match = match_res.scalar_one_or_none()
    if not match:
        return []

    winner_team = await session.get(Team, actual_winner_team_id)
    winner_name = winner_team.name if winner_team else ""
    team_a_name = match.team_a.name if match.team_a else ""
    team_b_name = match.team_b.name if match.team_b else ""

    # 2. Fetch all snapshots for this match
    stmt = (
        select(SourceSnapshot)
        .options(selectinload(SourceSnapshot.outcome), selectinload(SourceSnapshot.source_ref))
        .where(SourceSnapshot.match_id == match_id)
    )
    res = await session.execute(stmt)
    snapshots = res.scalars().all()

    outcomes_created: List[SourceOutcome] = []

    for snap in snapshots:
        if snap.outcome:
            # Already evaluated
            continue

        lean_str = (snap.lean or "").strip()
        result = "PASS"

        if not lean_str or lean_str.upper() in ("SKIP", "NO_EDGE", "NEUTRAL", "PASS"):
            result = "SKIPPED"
        else:
            # Check if lean matches the actual winner
            norm_lean, _ = normalizer.normalize_team_name(lean_str)
            is_winner = False

            if winner_name and (norm_lean == winner_name or lean_str.lower() in winner_name.lower()):
                is_winner = True
            elif snap.lean == "TeamA" and actual_winner_team_id == match.team_a_id:
                is_winner = True
            elif snap.lean == "TeamB" and actual_winner_team_id == match.team_b_id:
                is_winner = True

            # Check if lean matches the losing team
            losing_team_id = match.team_b_id if actual_winner_team_id == match.team_a_id else match.team_a_id
            losing_name = team_b_name if actual_winner_team_id == match.team_a_id else team_a_name

            if is_winner:
                result = "PASS"
            elif losing_name and (norm_lean == losing_name or lean_str.lower() in losing_name.lower()):
                result = "FAIL"
            elif snap.lean == "TeamA" and losing_team_id == match.team_a_id:
                result = "FAIL"
            elif snap.lean == "TeamB" and losing_team_id == match.team_b_id:
                result = "FAIL"
            else:
                # Ambiguous or non-matching entity
                result = "SKIPPED"

        outcome = SourceOutcome(
            snapshot_id=snap.id,
            result=result,
            evaluated_at_utc=utc_now()
        )
        session.add(outcome)
        outcomes_created.append(outcome)

    if outcomes_created:
        await session.commit()
        logger.info(f"Evaluated {len(outcomes_created)} source snapshot(s) for match {match_id}.")

    return outcomes_created


# --------------------------------------------------------------------------
# 4. Source Accuracy Leaderboard & Ranking (n >= 30 Gate)
# --------------------------------------------------------------------------
async def get_source_accuracy_leaderboard(session: AsyncSession) -> List[Dict[str, Any]]:
    """
    Objective, audited source leaderboard.
    
    Ranking Rules:
    - Only rank sources with sample size n >= 30 by measured accuracy.
    - Sources with n < 30 are flagged with 'Insufficient data' and displayed below sufficient sources.
    - Includes 95% Wilson Confidence Interval and exact binomial test vs 50% fair coin baseline.
    """
    sources_res = await session.execute(select(ExternalSource).order_by(ExternalSource.name))
    sources = list(sources_res.scalars().all())

    sufficient_list: List[Dict[str, Any]] = []
    insufficient_list: List[Dict[str, Any]] = []

    for src in sources:
        # Query snapshots with outcomes for this source
        stmt = (
            select(SourceOutcome.result)
            .join(SourceSnapshot, SourceOutcome.snapshot_id == SourceSnapshot.id)
            .where(SourceSnapshot.source_id == src.id)
        )
        res = await session.execute(stmt)
        outcomes = [r[0] for r in res.all()]

        total_calls = len(outcomes)
        play_calls = sum(1 for o in outcomes if o in ("PASS", "FAIL"))
        skip_calls = sum(1 for o in outcomes if o == "SKIPPED")
        wins = sum(1 for o in outcomes if o == "PASS")
        losses = sum(1 for o in outcomes if o == "FAIL")
        evaluated_calls = play_calls  # n

        win_pct = round((wins / evaluated_calls * 100.0), 1) if evaluated_calls > 0 else 0.0

        ci_low, ci_high = calculate_wilson_score_interval(wins, evaluated_calls, 0.95)
        ci_str = f"[{round(ci_low * 100, 1)}% - {round(ci_high * 100, 1)}%]"

        binom = binomial_test_against_baseline(wins, evaluated_calls, 0.50)
        is_sufficient = evaluated_calls >= 30

        status_label = "SUFFICIENT (n>=30)" if is_sufficient else "INSUFFICIENT (n<30)"

        entry = {
            "source_id": src.id,
            "source_name": src.name,
            "source_type": src.type,
            "url": src.url,
            "enabled": src.enabled,
            "health": src.health,
            "total_calls": total_calls,
            "play_calls": play_calls,
            "skip_calls": skip_calls,
            "evaluated_calls": evaluated_calls,  # n
            "wins": wins,
            "losses": losses,
            "measured_accuracy_pct": win_pct,
            "wilson_ci_lower": ci_low,
            "wilson_ci_upper": ci_high,
            "wilson_display": ci_str,
            "wilson_interval_95": {
                "lower_bound": ci_low,
                "upper_bound": ci_high,
                "display_str": ci_str
            },
            "p_value": binom["p_value"],
            "is_significant": binom["is_significant"],
            "conclusion": binom["conclusion"],
            "binomial_test": {
                "p_value": binom["p_value"],
                "is_significant": binom["is_significant"],
                "conclusion": binom["conclusion"],
                "sample_proportion": binom["sample_proportion"]
            },
            "is_sufficient": is_sufficient,
            "status_label": status_label,
            "rankable": is_sufficient
        }

        if is_sufficient:
            sufficient_list.append(entry)
        else:
            insufficient_list.append(entry)

    # Rank Tier 1 (sufficient sample) by measured accuracy (descending), then n (descending)
    sufficient_list.sort(key=lambda s: (s["measured_accuracy_pct"], s["evaluated_calls"]), reverse=True)

    # Tier 2 (insufficient sample) sorted by sample size n descending (not ranked above Tier 1)
    insufficient_list.sort(key=lambda s: s["evaluated_calls"], reverse=True)

    # Assign rank numbers only to sufficient sources
    rank = 1
    for s in sufficient_list:
        s["rank"] = f"#{rank}"
        rank += 1

    for s in insufficient_list:
        s["rank"] = "Unranked (n < 30)"

    return sufficient_list + insufficient_list


# Backwards-compatible alias for existing tests and routes
get_source_leaderboard = get_source_accuracy_leaderboard


# --------------------------------------------------------------------------
# 5. Match Detail Source Comparison & Consensus Signal
# --------------------------------------------------------------------------
async def get_source_comparison_for_match(
    session: AsyncSession,
    match_id: int,
    model_lean: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate Source Comparison table data for a fixture:
    - Lists each source's pick, market load, and fetched timestamp.
    - Shows agree/conflict status against the mathematical model prediction.
    - Computes the aggregate consensus signal across external providers.
    """
    stmt = (
        select(SourceSnapshot)
        .options(selectinload(SourceSnapshot.source_ref), selectinload(SourceSnapshot.outcome))
        .where(SourceSnapshot.match_id == match_id)
        .order_by(SourceSnapshot.fetched_at_utc.asc())
    )
    res = await session.execute(stmt)
    snapshots = res.scalars().all()

    match = await session.get(Match, match_id)
    team_a_name = match.team_a.name if (match and match.team_a) else "Team A"
    team_b_name = match.team_b.name if (match and match.team_b) else "Team B"

    items = []
    counts: Dict[str, int] = {team_a_name: 0, team_b_name: 0, "SKIP": 0}

    for snap in snapshots:
        source_name = snap.source_ref.name if snap.source_ref else "Unknown Source"
        lean_raw = (snap.lean or "").strip()

        # Agreement / conflict with model
        agreement_status = "NEUTRAL"
        if model_lean and model_lean not in ("NO_EDGE", "SKIP"):
            if lean_raw.lower() in model_lean.lower() or model_lean.lower() in lean_raw.lower():
                agreement_status = "AGREES"
            elif lean_raw.upper() in ("SKIP", "NO_EDGE", "NEUTRAL"):
                agreement_status = "NEUTRAL"
            else:
                agreement_status = "CONFLICTS"
        elif model_lean in ("NO_EDGE", "SKIP"):
            agreement_status = "MODEL_SKIPPED"

        # Tally for consensus
        norm_l, _ = normalizer.normalize_team_name(lean_raw)
        if norm_l == team_a_name or lean_raw.lower() in team_a_name.lower():
            counts[team_a_name] = counts.get(team_a_name, 0) + 1
        elif norm_l == team_b_name or lean_raw.lower() in team_b_name.lower():
            counts[team_b_name] = counts.get(team_b_name, 0) + 1
        else:
            counts["SKIP"] = counts.get("SKIP", 0) + 1

        items.append({
            "id": snap.id,
            "source_name": source_name,
            "lean": snap.lean or "None",
            "market": snap.market or "—",
            "session_notes": snap.session_notes or "—",
            "fetched_at_ist": format_ist(snap.fetched_at_utc),
            "agreement_status": agreement_status,
            "outcome": snap.outcome.result if snap.outcome else None
        })

    # Determine consensus
    total_sources = len(snapshots)
    consensus_lean = "NO_CONSENSUS"
    consensus_team = None
    consensus_count = 0

    if total_sources > 0:
        c_a = counts.get(team_a_name, 0)
        c_b = counts.get(team_b_name, 0)

        if c_a > c_b and c_a > total_sources / 2:
            consensus_lean = "TeamA"
            consensus_team = team_a_name
            consensus_count = c_a
        elif c_b > c_a and c_b > total_sources / 2:
            consensus_lean = "TeamB"
            consensus_team = team_b_name
            consensus_count = c_b
        elif c_a == c_b and c_a > 0:
            consensus_lean = "SPLIT"
        else:
            consensus_lean = "NO_CONSENSUS"

    consensus_summary = {
        "total_sources": total_sources,
        "team_a_votes": counts.get(team_a_name, 0),
        "team_b_votes": counts.get(team_b_name, 0),
        "skip_votes": counts.get("SKIP", 0),
        "consensus_lean": consensus_lean,
        "consensus_team": consensus_team,
        "consensus_count": consensus_count,
        "display_text": (
            f"{consensus_count} of {total_sources} sources lean {consensus_team}"
            if consensus_team else "No clear majority consensus across external sources."
        )
    }

    return {
        "snapshots": items,
        "consensus": consensus_summary
    }


# --------------------------------------------------------------------------
# 6. Consensus Signal Historical Backtest & Combined Lean Evaluation
# --------------------------------------------------------------------------
async def get_consensus_historical_accuracy(session: AsyncSession) -> Dict[str, Any]:
    """
    Backtested empirical hit-rate of the external source consensus signal.
    Computes measured accuracy, 95% Wilson CI, and exact binomial test vs 50% baseline.
    """
    # 1. Fetch completed matches that had source snapshots
    stmt = (
        select(Match)
        .options(selectinload(Match.toss_result), selectinload(Match.source_snapshots), selectinload(Match.team_a), selectinload(Match.team_b))
        .where(Match.status == "toss_done")
    )
    res = await session.execute(stmt)
    completed_matches = res.scalars().all()

    wins = 0
    losses = 0
    ties = 0

    for m in completed_matches:
        if not m.toss_result or not m.source_snapshots:
            continue

        team_a_name = m.team_a.name if m.team_a else ""
        team_b_name = m.team_b.name if m.team_b else ""
        winner_id = m.toss_result.winner_team_id

        # Tally snapshot votes
        votes_a = 0
        votes_b = 0
        for s in m.source_snapshots:
            l_str = (s.lean or "").strip()
            norm_l, _ = normalizer.normalize_team_name(l_str)
            if norm_l == team_a_name or l_str.lower() in team_a_name.lower():
                votes_a += 1
            elif norm_l == team_b_name or l_str.lower() in team_b_name.lower():
                votes_b += 1

        if votes_a == 0 and votes_b == 0:
            continue

        if votes_a > votes_b:
            # Consensus picked Team A
            if winner_id == m.team_a_id:
                wins += 1
            else:
                losses += 1
        elif votes_b > votes_a:
            # Consensus picked Team B
            if winner_id == m.team_b_id:
                wins += 1
            else:
                losses += 1
        else:
            ties += 1

    total_evaluated = wins + losses
    win_pct = round((wins / total_evaluated * 100.0), 1) if total_evaluated > 0 else 0.0

    ci_low, ci_high = calculate_wilson_score_interval(wins, total_evaluated, 0.95)
    binom = binomial_test_against_baseline(wins, total_evaluated, 0.50)
    is_sufficient = total_evaluated >= 30

    return {
        "total_calls": total_evaluated + ties,
        "evaluated_calls": total_evaluated,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "measured_accuracy_pct": win_pct,
        "wilson_ci_lower": ci_low,
        "wilson_ci_upper": ci_high,
        "wilson_display": f"[{round(ci_low * 100, 1)}% - {round(ci_high * 100, 1)}%]",
        "p_value": binom["p_value"],
        "is_significant": binom["is_significant"],
        "is_sufficient": is_sufficient,
        "status_label": "SUFFICIENT (n>=30)" if is_sufficient else "INSUFFICIENT (n<30)"
    }


def evaluate_combined_panel_lean(
    model_lean_info: Dict[str, Any],
    consensus_info: Dict[str, Any],
    consensus_historical: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Combines statistical model analysis + external source consensus.
    
    Honesty Rule (Non-negotiable):
    The panel lean may combine own stats + source consensus ONLY if the combined
    backtested hit-rate shows a statistically significant edge over 50% (p < 0.05, n >= 30);
    otherwise, the lean MUST remain 'NO EDGE - skip'.
    """
    model_has_edge = model_lean_info.get("has_edge", False)
    consensus_team = consensus_info.get("consensus_team")
    consensus_has_edge = (
        consensus_historical.get("is_sufficient", False) and
        consensus_historical.get("is_significant", False) and
        consensus_historical.get("wilson_ci_lower", 0.0) > 0.50
    )

    # Case 1: Both model and source consensus have proven edge and agree on the same team
    if model_has_edge and consensus_has_edge and consensus_team:
        model_team = model_lean_info.get("predicted_team_name")
        if model_team and consensus_team and (consensus_team.lower() in model_team.lower() or model_team.lower() in consensus_team.lower()):
            return {
                "lean": model_lean_info.get("lean"),
                "action_type": "PLAY",
                "predicted_team_name": model_team,
                "probability": model_lean_info.get("probability"),
                "ci_low": model_lean_info.get("ci_low"),
                "ci_high": model_lean_info.get("ci_high"),
                "has_edge": True,
                "rationale": f"CONFLUENCE: Empirical stats (p<0.05, n>=30) align with validated source consensus on {model_team}."
            }

    # Case 2: Only statistical model has proven empirical edge (and consensus does not contradict)
    if model_has_edge:
        return model_lean_info

    # Case 3: Only consensus has proven empirical edge (n >= 30, p < 0.05) and model is neutral
    if consensus_has_edge and consensus_team:
        return {
            "lean": "TeamA" if consensus_info.get("consensus_lean") == "TeamA" else "TeamB",
            "action_type": "PLAY",
            "predicted_team_name": consensus_team,
            "probability": round(consensus_historical.get("measured_accuracy_pct", 50.0) / 100.0, 4),
            "ci_low": consensus_historical.get("wilson_ci_lower"),
            "ci_high": consensus_historical.get("wilson_ci_upper"),
            "has_edge": True,
            "rationale": f"Source consensus empirical edge (n={consensus_historical.get('evaluated_calls')}, p<0.05) leaning {consensus_team}."
        }

    # Strict honest default
    return {
        "lean": "NO_EDGE",
        "action_type": "SKIP",
        "predicted_team_name": "None (No Statistical Edge)",
        "probability": 0.50,
        "ci_low": 0.50,
        "ci_high": 0.50,
        "has_edge": False,
        "rationale": "NO EDGE - skip: Neither empirical statistics nor source consensus exhibit a statistically significant edge over a 50% coin flip (n < 30 or p >= 0.05)."
    }
