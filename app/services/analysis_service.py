"""Longitudinal toss analytics, head-to-head empirical stats, and venue tendencies."""

from typing import Dict, Any, List, Optional
from sqlalchemy import select, or_, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Match, TossResult, Team, HistoricalMatchStatistic
from app.services.accuracy_service import (
    calculate_wilson_score_interval,
    binomial_test_against_baseline
)


async def get_team_toss_stats(
    session: AsyncSession,
    team_name_or_id: str,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Retrieve empirical historical toss statistics for a team across verified data.
    """
    query = (
        select(HistoricalMatchStatistic)
        .where(
            or_(
                HistoricalMatchStatistic.team_a_name.ilike(f"%{team_name_or_id}%"),
                HistoricalMatchStatistic.team_b_name.ilike(f"%{team_name_or_id}%"),
            )
        )
        .order_by(HistoricalMatchStatistic.match_date.desc())
        .limit(limit)
    )
    result = await session.execute(query)
    matches = result.scalars().all()

    total = len(matches)
    if total == 0:
        return {
            "team": team_name_or_id,
            "sample_size": 0,
            "toss_wins": 0,
            "toss_losses": 0,
            "win_rate_pct": 50.0,
            "wilson_ci_95": (0.0, 0.0),
            "binomial_test": {
                "p_value": 1.0,
                "is_significant": False,
                "conclusion": "No historical matches recorded."
            },
            "bat_first_count": 0,
            "bowl_first_count": 0,
            "is_sample_sufficient": False,
            "disclaimer": "Toss is a random event. Past stats do not guarantee results. Play responsibly."
        }

    wins = sum(1 for m in matches if team_name_or_id.lower() in m.toss_winner_name.lower())
    losses = total - wins
    win_rate = round(wins / total * 100.0, 2)
    ci_l, ci_u = calculate_wilson_score_interval(wins, total, 0.95)
    binom = binomial_test_against_baseline(wins, total, 0.50)

    bat_count = sum(1 for m in matches if (team_name_or_id.lower() in m.toss_winner_name.lower() and m.toss_decision == "BAT"))
    bowl_count = sum(1 for m in matches if (team_name_or_id.lower() in m.toss_winner_name.lower() and m.toss_decision == "BOWL"))

    return {
        "team": team_name_or_id,
        "sample_size": total,
        "toss_wins": wins,
        "toss_losses": losses,
        "win_rate_pct": win_rate,
        "wilson_ci_95": (ci_l, ci_u),
        "binomial_test": binom,
        "bat_first_count": bat_count,
        "bowl_first_count": bowl_count,
        "is_sample_sufficient": total >= 30,
        "disclaimer": "Toss is a random event. Past stats do not guarantee results. Play responsibly."
    }


async def get_h2h_toss_stats(
    session: AsyncSession,
    team_a_name: str,
    team_b_name: str
) -> Dict[str, Any]:
    """
    Retrieve direct head-to-head toss history between two teams.
    """
    query = (
        select(HistoricalMatchStatistic)
        .where(
            or_(
                and_(
                    HistoricalMatchStatistic.team_a_name.ilike(f"%{team_a_name}%"),
                    HistoricalMatchStatistic.team_b_name.ilike(f"%{team_b_name}%")
                ),
                and_(
                    HistoricalMatchStatistic.team_a_name.ilike(f"%{team_b_name}%"),
                    HistoricalMatchStatistic.team_b_name.ilike(f"%{team_a_name}%")
                ),
            )
        )
        .order_by(HistoricalMatchStatistic.match_date.desc())
    )
    res = await session.execute(query)
    matches = res.scalars().all()

    total = len(matches)
    wins_a = sum(1 for m in matches if team_a_name.lower() in m.toss_winner_name.lower())
    wins_b = sum(1 for m in matches if team_b_name.lower() in m.toss_winner_name.lower())

    ci_l, ci_u = calculate_wilson_score_interval(wins_a, total, 0.95) if total > 0 else (0.0, 0.0)
    binom = binomial_test_against_baseline(wins_a, total, 0.50) if total > 0 else {"p_value": 1.0, "is_significant": False, "conclusion": "No H2H matches."}

    return {
        "team_a": team_a_name,
        "team_b": team_b_name,
        "total_h2h_matches": total,
        "wins_team_a": wins_a,
        "wins_team_b": wins_b,
        "win_rate_a_pct": round(wins_a / total * 100.0, 2) if total > 0 else 50.0,
        "win_rate_b_pct": round(wins_b / total * 100.0, 2) if total > 0 else 50.0,
        "wilson_ci_95": (ci_l, ci_u),
        "binomial_test": binom,
        "is_sample_sufficient": total >= 30,
        "disclaimer": "Toss is a random event. Past stats do not guarantee results. Play responsibly."
    }

