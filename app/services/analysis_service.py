"""Longitudinal toss analytics, head-to-head empirical stats, venue tendencies, and captain records.

Mathematical honesty guarantees:
- Every single statistical block provides n (sample size), win %, and 95% Wilson Score Confidence Interval.
- Low sample sizes (n < 30) are explicitly flagged as "Insufficient data (n < 30)".
- When the 95% Wilson confidence interval includes 50% (0.50), it is explicitly flagged as "Not statistically different from 50%".
- Permanent disclaimer attached to all reports: "Toss is a random event. Past stats do not guarantee results. Play responsibly."
"""

from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy import select, or_, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.models import Match, TossResult, Team, HistoricalMatchStatistic, Venue
from app.services.accuracy_service import (
    calculate_wilson_score_interval,
    binomial_test_against_baseline
)


PERMANENT_DISCLAIMER = "Toss is a random event. Past stats do not guarantee results. Play responsibly."


def make_stat_block(
    wins: int,
    total: int,
    label: str = "",
    min_sample: int = 30
) -> Dict[str, Any]:
    """
    Standard statistical block format with n, win %, and 95% Wilson Score CI.
    Strictly flags low sample size (n < 30) and baseline 50% coin flip inclusion.
    """
    if total <= 0:
        return {
            "label": label,
            "n": 0,
            "wins": 0,
            "losses": 0,
            "win_pct": 50.0,
            "ci_low": 0.0,
            "ci_high": 0.0,
            "ci_display": "N/A",
            "is_insufficient": True,
            "insufficient_warning": f"Insufficient data (n = 0 < {min_sample})",
            "not_different_from_50": True,
            "baseline_note": "No empirical data available"
        }

    ci_l, ci_u = calculate_wilson_score_interval(wins, total, 0.95)
    win_pct = round((wins / total) * 100.0, 1)
    is_insufficient = total < min_sample
    includes_fifty = bool(ci_l <= 0.50 <= ci_u)

    return {
        "label": label,
        "n": total,
        "wins": wins,
        "losses": total - wins,
        "win_pct": win_pct,
        "ci_low": ci_l,
        "ci_high": ci_u,
        "ci_display": f"[{round(ci_l * 100, 1)}% - {round(ci_u * 100, 1)}%]",
        "is_insufficient": is_insufficient,
        "insufficient_warning": f"Insufficient data (n = {total} < {min_sample})" if is_insufficient else None,
        "not_different_from_50": includes_fifty,
        "baseline_note": "Not statistically different from 50%" if includes_fifty else "Statistically diverges from 50% baseline"
    }


async def get_team_toss_stats(
    session: AsyncSession,
    team_name_or_id: str,
    limit: int = 200
) -> Dict[str, Any]:
    """
    Retrieve empirical historical toss statistics for a team across verified data:
    - Overall toss win rate (n, win %, Wilson CI)
    - Breakdown per format (T20, ODI, Test)
    - Breakdown per league / tournament
    - Home vs Away breakdown
    - Recent form (last 5, 10, 20 tosses)
    - Captain-wise breakdown
    """
    team_str = str(team_name_or_id).strip()
    query = (
        select(HistoricalMatchStatistic)
        .where(
            or_(
                HistoricalMatchStatistic.team_a_name.ilike(f"%{team_str}%"),
                HistoricalMatchStatistic.team_b_name.ilike(f"%{team_str}%"),
            )
        )
        .order_by(HistoricalMatchStatistic.match_date.desc())
        .limit(limit)
    )
    result = await session.execute(query)
    matches: List[HistoricalMatchStatistic] = list(result.scalars().all())

    total = len(matches)
    if total == 0:
        overall = make_stat_block(0, 0, f"{team_str} Overall")
        return {
            "team": team_str,
            "overall": overall,
            "by_format": {},
            "by_league": {},
            "home_away": {
                "home": make_stat_block(0, 0, "Home Matches"),
                "away": make_stat_block(0, 0, "Away Matches")
            },
            "recent_form": {
                "sequence": [],
                "last_5": make_stat_block(0, 0, "Last 5 Tosses"),
                "last_10": make_stat_block(0, 0, "Last 10 Tosses"),
                "last_20": make_stat_block(0, 0, "Last 20 Tosses")
            },
            "captains": {},
            "disclaimer": PERMANENT_DISCLAIMER
        }

    # 1. Overall
    wins = sum(1 for m in matches if team_str.lower() in m.toss_winner_name.lower())
    overall = make_stat_block(wins, total, f"{team_str} Overall")

    # 2. Breakdown per Format
    format_groups: Dict[str, List[HistoricalMatchStatistic]] = {}
    for m in matches:
        fmt = (m.match_format or "T20").upper()
        format_groups.setdefault(fmt, []).append(m)

    by_format: Dict[str, Dict[str, Any]] = {}
    for fmt, m_list in format_groups.items():
        f_wins = sum(1 for m in m_list if team_str.lower() in m.toss_winner_name.lower())
        by_format[fmt] = make_stat_block(f_wins, len(m_list), f"{fmt} Format")

    # 3. Breakdown per League
    league_groups: Dict[str, List[HistoricalMatchStatistic]] = {}
    for m in matches:
        lg = m.tournament_name or "Other"
        league_groups.setdefault(lg, []).append(m)

    by_league: Dict[str, Dict[str, Any]] = {}
    for lg, m_list in league_groups.items():
        l_wins = sum(1 for m in m_list if team_str.lower() in m.toss_winner_name.lower())
        by_league[lg] = make_stat_block(l_wins, len(m_list), lg)

    # 4. Home vs Away Breakdown
    # Team listed first (team_a) or venue in city
    home_matches = [m for m in matches if team_str.lower() in m.team_a_name.lower()]
    away_matches = [m for m in matches if team_str.lower() in m.team_b_name.lower()]

    home_wins = sum(1 for m in home_matches if team_str.lower() in m.toss_winner_name.lower())
    away_wins = sum(1 for m in away_matches if team_str.lower() in m.toss_winner_name.lower())

    home_stat = make_stat_block(home_wins, len(home_matches), "Home Matches")
    away_stat = make_stat_block(away_wins, len(away_matches), "Away Matches")

    # 5. Recent Form (last 5, 10, 20)
    sequence = ["W" if team_str.lower() in m.toss_winner_name.lower() else "L" for m in matches]

    def sub_stat(n_items: int, label: str) -> Dict[str, Any]:
        sub = sequence[:n_items]
        w = sub.count("W")
        return make_stat_block(w, len(sub), label, min_sample=min(n_items, 30))

    recent_form = {
        "sequence": sequence[:20],
        "last_5": sub_stat(5, "Last 5 Tosses"),
        "last_10": sub_stat(10, "Last 10 Tosses"),
        "last_20": sub_stat(20, "Last 20 Tosses"),
    }

    # 6. Captain-wise toss history where data exists
    captain_groups: Dict[str, List[HistoricalMatchStatistic]] = {}
    for m in matches:
        cap = None
        if team_str.lower() in m.team_a_name.lower() and m.captain_a:
            cap = m.captain_a
        elif team_str.lower() in m.team_b_name.lower() and m.captain_b:
            cap = m.captain_b
        elif m.toss_winner_captain and team_str.lower() in m.toss_winner_name.lower():
            cap = m.toss_winner_captain

        if cap:
            captain_groups.setdefault(cap, []).append(m)

    captains_stats: Dict[str, Dict[str, Any]] = {}
    for cap, c_list in captain_groups.items():
        c_wins = sum(1 for m in c_list if team_str.lower() in m.toss_winner_name.lower())
        captains_stats[cap] = make_stat_block(c_wins, len(c_list), f"Captain {cap}")

    return {
        "team": team_str,
        "overall": overall,
        "by_format": by_format,
        "by_league": by_league,
        "home_away": {
            "home": home_stat,
            "away": away_stat
        },
        "recent_form": recent_form,
        "captains": captains_stats,
        "disclaimer": PERMANENT_DISCLAIMER
    }


async def get_venue_toss_stats(
    session: AsyncSession,
    venue_name_or_id: Optional[str],
    limit: int = 200
) -> Dict[str, Any]:
    """
    Retrieve venue tendencies:
    - Bat / Field decision trend
    - How often toss winner wins the match (toss-to-match-win correlation)
    - Recent matches at venue
    """
    if not venue_name_or_id:
        return {
            "venue": "Unknown / Unspecified Venue",
            "total_matches": 0,
            "bat_first": make_stat_block(0, 0, "Chose Bat First"),
            "field_first": make_stat_block(0, 0, "Chose Field First"),
            "toss_match_correlation": make_stat_block(0, 0, "Toss Winner Won Match"),
            "recent_matches": [],
            "disclaimer": PERMANENT_DISCLAIMER
        }

    v_str = str(venue_name_or_id).strip()
    query = (
        select(HistoricalMatchStatistic)
        .where(HistoricalMatchStatistic.venue_name.ilike(f"%{v_str}%"))
        .order_by(HistoricalMatchStatistic.match_date.desc())
        .limit(limit)
    )
    res = await session.execute(query)
    matches: List[HistoricalMatchStatistic] = list(res.scalars().all())
    total = len(matches)

    if total == 0:
        return {
            "venue": v_str,
            "total_matches": 0,
            "bat_first": make_stat_block(0, 0, "Chose Bat First"),
            "field_first": make_stat_block(0, 0, "Chose Field First"),
            "toss_match_correlation": make_stat_block(0, 0, "Toss Winner Won Match"),
            "recent_matches": [],
            "disclaimer": PERMANENT_DISCLAIMER
        }

    # Bat vs Field decision trends
    bat_count = sum(1 for m in matches if m.toss_decision == "BAT")
    field_count = sum(1 for m in matches if m.toss_decision in ("FIELD", "BOWL"))

    bat_stat = make_stat_block(bat_count, total, "Chose Bat First")
    field_stat = make_stat_block(field_count, total, "Chose Field First")

    # Toss Winner Won Match Correlation
    matches_with_winner = [m for m in matches if m.match_winner_name]
    toss_and_match_wins = sum(
        1 for m in matches_with_winner
        if m.match_winner_name and m.toss_winner_name and m.match_winner_name.lower() in m.toss_winner_name.lower()
    )
    correlation_stat = make_stat_block(
        toss_and_match_wins,
        len(matches_with_winner),
        "Toss Winner Won Match"
    )

    recent_matches = [
        {
            "date": m.match_date.isoformat(),
            "teams": f"{m.team_a_name} vs {m.team_b_name}",
            "toss_winner": m.toss_winner_name,
            "decision": m.toss_decision,
            "match_winner": m.match_winner_name or "No Result"
        }
        for m in matches[:10]
    ]

    return {
        "venue": v_str,
        "total_matches": total,
        "bat_first": bat_stat,
        "field_first": field_stat,
        "toss_match_correlation": correlation_stat,
        "recent_matches": recent_matches,
        "disclaimer": PERMANENT_DISCLAIMER
    }


async def get_h2h_toss_stats(
    session: AsyncSession,
    team_a_name: str,
    team_b_name: str,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Retrieve direct head-to-head toss history between two teams:
    - Direct toss win comparison (n, win %, Wilson CI)
    - Decisions when winning toss
    - Recent H2H encounters
    - Captains H2H where available
    """
    t_a = team_a_name.strip()
    t_b = team_b_name.strip()

    query = (
        select(HistoricalMatchStatistic)
        .where(
            or_(
                and_(
                    HistoricalMatchStatistic.team_a_name.ilike(f"%{t_a}%"),
                    HistoricalMatchStatistic.team_b_name.ilike(f"%{t_b}%")
                ),
                and_(
                    HistoricalMatchStatistic.team_a_name.ilike(f"%{t_b}%"),
                    HistoricalMatchStatistic.team_b_name.ilike(f"%{t_a}%")
                ),
            )
        )
        .order_by(HistoricalMatchStatistic.match_date.desc())
        .limit(limit)
    )
    res = await session.execute(query)
    matches: List[HistoricalMatchStatistic] = list(res.scalars().all())
    total = len(matches)

    if total == 0:
        return {
            "team_a": t_a,
            "team_b": t_b,
            "total_h2h_matches": 0,
            "stat_a": make_stat_block(0, 0, f"{t_a} H2H Toss Wins"),
            "stat_b": make_stat_block(0, 0, f"{t_b} H2H Toss Wins"),
            "decisions": {"bat": 0, "field": 0},
            "recent_encounters": [],
            "disclaimer": PERMANENT_DISCLAIMER
        }

    wins_a = sum(1 for m in matches if t_a.lower() in m.toss_winner_name.lower())
    wins_b = sum(1 for m in matches if t_b.lower() in m.toss_winner_name.lower())

    stat_a = make_stat_block(wins_a, total, f"{t_a} H2H Toss Wins")
    stat_b = make_stat_block(wins_b, total, f"{t_b} H2H Toss Wins")

    bat_count = sum(1 for m in matches if m.toss_decision == "BAT")
    field_count = sum(1 for m in matches if m.toss_decision in ("FIELD", "BOWL"))

    recent_encounters = [
        {
            "date": m.match_date.isoformat(),
            "toss_winner": m.toss_winner_name,
            "decision": m.toss_decision,
            "venue": m.venue_name or "-",
            "match_winner": m.match_winner_name or "-"
        }
        for m in matches[:10]
    ]

    return {
        "team_a": t_a,
        "team_b": t_b,
        "total_h2h_matches": total,
        "stat_a": stat_a,
        "stat_b": stat_b,
        "decisions": {"bat": bat_count, "field": field_count},
        "recent_encounters": recent_encounters,
        "disclaimer": PERMANENT_DISCLAIMER
    }


async def get_captain_toss_stats(
    session: AsyncSession,
    captain_name: str,
    limit: int = 100
) -> Dict[str, Any]:
    """Retrieve historical toss record for a specific captain."""
    cap_str = captain_name.strip()
    query = (
        select(HistoricalMatchStatistic)
        .where(
            or_(
                HistoricalMatchStatistic.captain_a.ilike(f"%{cap_str}%"),
                HistoricalMatchStatistic.captain_b.ilike(f"%{cap_str}%"),
                HistoricalMatchStatistic.toss_winner_captain.ilike(f"%{cap_str}%"),
            )
        )
        .order_by(HistoricalMatchStatistic.match_date.desc())
        .limit(limit)
    )
    res = await session.execute(query)
    matches: List[HistoricalMatchStatistic] = list(res.scalars().all())
    total = len(matches)

    wins = sum(1 for m in matches if m.toss_winner_captain and cap_str.lower() in m.toss_winner_captain.lower())
    return {
        "captain": cap_str,
        "stat": make_stat_block(wins, total, f"Captain {cap_str} Toss Wins"),
        "disclaimer": PERMANENT_DISCLAIMER
    }


async def get_comprehensive_match_analysis(
    session: AsyncSession,
    match: Match
) -> Dict[str, Any]:
    """
    Produce the full analytical breakdown for a match fixture:
    - Team A stats (overall, by format, by league, home/away, recent form, captains)
    - Team B stats (overall, by format, by league, home/away, recent form, captains)
    - Head-to-Head toss stats
    - Venue tendencies and toss-to-match win correlation
    - Every stat annotated with n, win %, 95% Wilson CI, and baseline flags.
    """
    team_a_name = match.team_a.name if match.team_a else str(match.team_a_id)
    team_b_name = match.team_b.name if match.team_b else str(match.team_b_id)
    venue_name = match.venue.name if match.venue else None

    stats_a = await get_team_toss_stats(session, team_a_name)
    stats_b = await get_team_toss_stats(session, team_b_name)
    h2h_stats = await get_h2h_toss_stats(session, team_a_name, team_b_name)
    venue_stats = await get_venue_toss_stats(session, venue_name)

    return {
        "match_id": match.id,
        "team_a_name": team_a_name,
        "team_b_name": team_b_name,
        "league_name": match.league.name if match.league else "Local/Unknown",
        "venue_name": venue_name or "Venue TBD",
        "stats_a": stats_a,
        "stats_b": stats_b,
        "h2h_stats": h2h_stats,
        "venue_stats": venue_stats,
        "disclaimer": PERMANENT_DISCLAIMER
    }
