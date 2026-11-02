"""Longitudinal totals dashboard service: overall, league, team, timeframe, streaks, and charts."""

from typing import Dict, Any, List, Optional
from datetime import datetime, date
from collections import defaultdict
from sqlalchemy import select, func, or_, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Match, TossResult, Prediction, PredictionOutcome,
    Team, League, Venue, HistoricalMatchStatistic
)
from app.services.accuracy_service import (
    calculate_wilson_score_interval,
    calculate_streaks_from_results,
    calculate_brier_score
)
from app.services.analysis_service import make_stat_block, PERMANENT_DISCLAIMER


async def get_totals_overview(session: AsyncSession) -> Dict[str, Any]:
    """
    Compute macro aggregate statistics across the entire database:
    - Total matches recorded
    - Total official tosses verified
    - Bat vs Field split
    - Prediction performance (calls taken vs skipped, win rate, 95% Wilson CI)
    - Streaks
    """
    # 1. Total matches
    m_count_q = await session.execute(select(func.count(Match.id)))
    total_matches = m_count_q.scalar() or 0

    # 2. Total completed tosses
    tosses_q = await session.execute(select(TossResult.decision))
    decisions = [str(d).upper() for d in tosses_q.scalars().all()]
    total_tosses = len(decisions)
    bat_count = sum(1 for d in decisions if "BAT" in d)
    field_count = sum(1 for d in decisions if "FIELD" in d or "BOWL" in d)

    bat_pct = round(bat_count / total_tosses * 100.0, 1) if total_tosses > 0 else 50.0
    field_pct = round(field_count / total_tosses * 100.0, 1) if total_tosses > 0 else 50.0

    # 3. Predictions and outcomes
    preds_q = await session.execute(
        select(Prediction.lean, PredictionOutcome.result, Prediction.locked_at_utc)
        .outerjoin(PredictionOutcome, Prediction.id == PredictionOutcome.prediction_id)
        .order_by(Prediction.locked_at_utc.asc())
    )
    pred_rows = preds_q.all()

    total_predictions = len(pred_rows)
    calls_taken = 0
    calls_skipped = 0
    wins = 0
    losses = 0
    results_ordered: List[str] = []

    for lean, outcome_res, _ in pred_rows:
        if lean == "NO_EDGE" or outcome_res == "SKIPPED":
            calls_skipped += 1
            continue
        calls_taken += 1
        if outcome_res == "PASS":
            wins += 1
            results_ordered.append("PASS")
        elif outcome_res == "FAIL":
            losses += 1
            results_ordered.append("FAIL")

    evaluated_calls = wins + losses
    win_pct = round(wins / evaluated_calls * 100.0, 1) if evaluated_calls > 0 else 50.0
    ci_low, ci_high = calculate_wilson_score_interval(wins, evaluated_calls, 0.95)
    ci_display = f"[{round(ci_low * 100, 1)}% - {round(ci_high * 100, 1)}%]" if evaluated_calls > 0 else "N/A"

    # 4. Streaks
    streaks = calculate_streaks_from_results(results_ordered)

    return {
        "total_matches": total_matches,
        "total_tosses": total_tosses,
        "bat_count": bat_count,
        "field_count": field_count,
        "bat_pct": bat_pct,
        "field_pct": field_pct,
        "total_predictions": total_predictions,
        "calls_taken": calls_taken,
        "calls_skipped": calls_skipped,
        "wins": wins,
        "losses": losses,
        "evaluated_calls": evaluated_calls,
        "win_pct": win_pct,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "ci_display": ci_display,
        "streaks": streaks,
        "disclaimer": PERMANENT_DISCLAIMER
    }


async def get_totals_by_league(session: AsyncSession) -> List[Dict[str, Any]]:
    """Aggregate toss counts and decisions grouped by competition league."""
    # Group by Match.league_id
    stmt = (
        select(League.name, TossResult.decision, PredictionOutcome.result)
        .join(Match, League.id == Match.league_id)
        .join(TossResult, Match.id == TossResult.match_id)
        .outerjoin(Prediction, Match.id == Prediction.match_id)
        .outerjoin(PredictionOutcome, Prediction.id == PredictionOutcome.prediction_id)
    )
    res = await session.execute(stmt)
    rows = res.all()

    grouped = defaultdict(lambda: {
        "total_tosses": 0,
        "bat_count": 0,
        "field_count": 0,
        "wins": 0,
        "losses": 0,
        "skips": 0
    })

    for lg_name, decision, result_val in rows:
        item = grouped[lg_name or "Local/Unknown"]
        item["total_tosses"] += 1
        d_str = str(decision).upper()
        if "BAT" in d_str:
            item["bat_count"] += 1
        else:
            item["field_count"] += 1

        if result_val == "PASS":
            item["wins"] += 1
        elif result_val == "FAIL":
            item["losses"] += 1
        elif result_val == "SKIPPED":
            item["skips"] += 1

    leagues_summary = []
    for lg_name, data in sorted(grouped.items(), key=lambda x: x[1]["total_tosses"], reverse=True):
        t = data["total_tosses"]
        bat_pct = round(data["bat_count"] / t * 100.0, 1) if t > 0 else 50.0
        field_pct = round(data["field_count"] / t * 100.0, 1) if t > 0 else 50.0

        eval_total = data["wins"] + data["losses"]
        pred_win_pct = round(data["wins"] / eval_total * 100.0, 1) if eval_total > 0 else 50.0
        ci_l, ci_u = calculate_wilson_score_interval(data["wins"], eval_total, 0.95)

        leagues_summary.append({
            "league_name": lg_name,
            "total_tosses": t,
            "bat_count": data["bat_count"],
            "field_count": data["field_count"],
            "bat_pct": bat_pct,
            "field_pct": field_pct,
            "calls_taken": eval_total,
            "wins": data["wins"],
            "losses": data["losses"],
            "pred_win_pct": pred_win_pct,
            "ci_display": f"[{round(ci_l * 100, 1)}% - {round(ci_u * 100, 1)}%]" if eval_total > 0 else "N/A",
            "is_insufficient": eval_total < 30
        })

    return leagues_summary


async def get_totals_by_team(
    session: AsyncSession,
    limit: int = 30
) -> List[Dict[str, Any]]:
    """
    Retrieve team-wise toss win/loss records combining historical match statistics
    and live verified tosses, with n, win %, and 95% Wilson CI.
    """
    # 1. From HistoricalMatchStatistic
    hist_q = await session.execute(
        select(
            HistoricalMatchStatistic.team_a_name,
            HistoricalMatchStatistic.team_b_name,
            HistoricalMatchStatistic.toss_winner_name
        )
    )
    hist_rows = hist_q.all()

    team_stats = defaultdict(lambda: {"total": 0, "wins": 0})

    for ta, tb, winner in hist_rows:
        team_stats[ta]["total"] += 1
        team_stats[tb]["total"] += 1
        if winner == ta:
            team_stats[ta]["wins"] += 1
        elif winner == tb:
            team_stats[tb]["wins"] += 1

    # 2. From live Match + TossResult
    live_q = await session.execute(
        select(Match.team_a_id, Match.team_b_id, TossResult.winner_team_id)
        .join(TossResult, Match.id == TossResult.match_id)
    )
    live_rows = live_q.all()

    # Get team names
    teams_q = await session.execute(select(Team.id, Team.name))
    team_names = {row[0]: row[1] for row in teams_q.all()}

    for aid, bid, wid in live_rows:
        name_a = team_names.get(aid, str(aid))
        name_b = team_names.get(bid, str(bid))
        name_w = team_names.get(wid, str(wid))

        team_stats[name_a]["total"] += 1
        team_stats[name_b]["total"] += 1
        if name_w == name_a:
            team_stats[name_a]["wins"] += 1
        elif name_w == name_b:
            team_stats[name_b]["wins"] += 1

    # Format into stat blocks
    leaderboard = []
    for team_name, data in team_stats.items():
        if data["total"] < 5:
            continue
        stat = make_stat_block(data["wins"], data["total"], label=team_name, min_sample=30)
        leaderboard.append({
            "team_name": team_name,
            "total_matches": stat["n"],
            "toss_wins": stat["wins"],
            "toss_losses": stat["losses"],
            "win_pct": stat["win_pct"],
            "ci_display": stat["ci_display"],
            "ci_low": stat["ci_low"],
            "ci_high": stat["ci_high"],
            "is_insufficient": stat["is_insufficient"],
            "not_different_from_50": stat["not_different_from_50"]
        })

    # Sort by total sample size descending
    leaderboard.sort(key=lambda x: x["total_matches"], reverse=True)
    return leaderboard[:limit]


async def get_totals_by_timeframe(
    session: AsyncSession,
    interval: str = "month"
) -> List[Dict[str, Any]]:
    """
    Group completed tosses and prediction outcomes by period (month or week) for chart rendering.
    """
    # Query live tosses
    stmt = (
        select(Match.start_time_utc, TossResult.decision, PredictionOutcome.result)
        .join(TossResult, Match.id == TossResult.match_id)
        .outerjoin(Prediction, Match.id == Prediction.match_id)
        .outerjoin(PredictionOutcome, Prediction.id == PredictionOutcome.prediction_id)
        .order_by(Match.start_time_utc.asc())
    )
    res = await session.execute(stmt)
    rows = res.all()

    periods = defaultdict(lambda: {
        "total_tosses": 0,
        "bat_count": 0,
        "field_count": 0,
        "wins": 0,
        "losses": 0
    })

    for dt_val, decision, outcome in rows:
        if not dt_val:
            continue
        period_key = dt_val.strftime("%Y-%m") if interval == "month" else dt_val.strftime("%Y-W%W")
        p_data = periods[period_key]
        p_data["total_tosses"] += 1
        d_str = str(decision).upper()
        if "BAT" in d_str:
            p_data["bat_count"] += 1
        else:
            p_data["field_count"] += 1

        if outcome == "PASS":
            p_data["wins"] += 1
        elif outcome == "FAIL":
            p_data["losses"] += 1

    chart_series = []
    for p_key, data in sorted(periods.items()):
        eval_total = data["wins"] + data["losses"]
        pred_acc = round(data["wins"] / eval_total * 100.0, 1) if eval_total > 0 else 50.0
        chart_series.append({
            "period": p_key,
            "total_tosses": data["total_tosses"],
            "bat_count": data["bat_count"],
            "field_count": data["field_count"],
            "calls_taken": eval_total,
            "wins": data["wins"],
            "losses": data["losses"],
            "hit_rate_pct": pred_acc
        })

    return chart_series

