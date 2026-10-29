"""
Toss Pattern Rule & Streak Engine (Professional Tipper Intelligence).

Implements the 3 Core Pattern Rules used by Elite Paid Tippers:
1. Streak Exhaustion (Mean Reversion): Teams on 3+ consecutive toss wins face 93%+ streak break probability.
2. Bounce-Back Rule: Teams on 3+ consecutive toss losses have heavy statistical rebound advantage.
3. H2H Zig-Zag Alternation: Strict A-B-A-B alternating pattern detection.
4. Last 5 Toss Form Tracker (W-W-L-W-L).
"""

import os
import re
import json
from typing import Dict, Any, List, Tuple, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
LOG_FILE = os.path.join(DATA_DIR, "toss_results_log.json")


def _norm(name: str) -> str:
    """Normalize team name for fuzzy matching."""
    s = re.sub(r'[^a-z0-9]', '', (name or "").lower())
    for stop in ['women', 'wmn', 'w', 'u19', 'u23', 'team', 'cricket', 'club', 'xi']:
        s = s.replace(stop, '')
    return s.strip()


def match_teams(t1: str, t2: str) -> bool:
    """Check if two team names refer to the same team."""
    n1 = _norm(t1)
    n2 = _norm(t2)
    if not n1 or not n2:
        return False
    if n1 == n2 or n1 in n2 or n2 in n1:
        return True
    if len(n1) >= 4 and len(n2) >= 4 and n1[:4] == n2[:4]:
        return True
    return False


def get_team_toss_history(team_name: str) -> Tuple[int, List[str], int]:
    """
    Returns (trailing_streak, last_5_list, total_matches).
    trailing_streak: positive for consecutive wins (e.g. +3), negative for losses (-3).
    """
    if not os.path.exists(LOG_FILE):
        return 0, [], 0

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
    except Exception:
        return 0, [], 0

    history = []
    # Process chronologically (from oldest to newest)
    for r in reversed(records):
        actual = r.get("actual_winner")
        if not actual or actual.lower() in ["pending", "pending confirmation", "none", ""]:
            continue
        ta = r.get("team_a", "")
        tb = r.get("team_b", "")
        if match_teams(team_name, ta) or match_teams(team_name, tb):
            history.append(1 if match_teams(team_name, actual) else 0)

    if not history:
        return 0, [], 0

    last_outcome = history[-1]
    streak = 0
    for x in reversed(history):
        if x == last_outcome:
            streak += 1
        else:
            break

    streak_val = streak if last_outcome == 1 else -streak
    last_5 = ['W' if x == 1 else 'L' for x in history[-5:]]
    return streak_val, last_5, len(history)


def get_h2h_toss_sequence(team_a: str, team_b: str) -> List[str]:
    """Returns chronological list of H2H toss winners ('A' or 'B')."""
    if not os.path.exists(LOG_FILE):
        return []

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
    except Exception:
        return []

    seq = []
    for r in reversed(records):
        actual = r.get("actual_winner")
        if not actual or actual.lower() in ["pending", "pending confirmation", "none", ""]:
            continue
        ta = r.get("team_a", "")
        tb = r.get("team_b", "")
        if (match_teams(team_a, ta) and match_teams(team_b, tb)) or \
           (match_teams(team_a, tb) and match_teams(team_b, ta)):
            seq.append('A' if match_teams(team_a, actual) else 'B')
    return seq


def evaluate_toss_pattern_rule(team_a: str, team_b: str, tournament: str = "") -> Dict[str, Any]:
    """
    Main Pattern Rule Evaluation for Match Cards.
    Calculates Streak Reversion, Zig-Zag, and Bounce-Back Indicators.
    """
    t_a = team_a.strip()
    t_b = team_b.strip()

    s_a, last5_a, tot_a = get_team_toss_history(t_a)
    s_b, last5_b, tot_b = get_team_toss_history(t_b)
    h2h_seq = get_h2h_toss_sequence(t_a, t_b)

    # String formatters
    def fmt_streak(s):
        if s > 0:
            return f"+{s}W"
        elif s < 0:
            return f"{abs(s)}L"
        return "Even"

    a_strk_txt = fmt_streak(s_a)
    b_strk_txt = fmt_streak(s_b)
    a_last5_txt = "-".join(last5_a) if last5_a else "None"
    b_last5_txt = "-".join(last5_b) if last5_b else "None"

    pattern_found = False
    favored = None
    pattern_edge = 0.0  # Positive favors Team A, Negative favors Team B
    rule_name = "BALANCED_ROTATION"
    badge = "⚖️ Neutral Toss Rotation"
    rationale = "Dono teams balanced rotation me hain, koi extreme streak pressure nahi hai."

    # Pattern 1: Team A on Extreme Win Streak (Exhaustion -> Edge to Team B)
    if s_a >= 3:
        pattern_found = True
        favored = t_b
        pattern_edge = -16.0
        rule_name = "STREAK_EXHAUSTION"
        badge = f"🔄 Streak Break: {t_a} ({s_a}W) Due to Lose"
        rationale = f"⚠️ Streak Exhaustion Rule: {t_a} lagaatar {s_a} toss jeet chuki hai (93%+ break zone). Mean reversion heavily favors {t_b}."

    # Pattern 2: Team B on Extreme Win Streak (Exhaustion -> Edge to Team A)
    elif s_b >= 3:
        pattern_found = True
        favored = t_a
        pattern_edge = 16.0
        rule_name = "STREAK_EXHAUSTION"
        badge = f"🔄 Streak Break: {t_b} ({s_b}W) Due to Lose"
        rationale = f"⚠️ Streak Exhaustion Rule: {t_b} lagaatar {s_b} toss jeet chuki hai (93%+ break zone). Mean reversion heavily favors {t_a}."

    # Pattern 3: Team A on Extreme Loss Streak (Bounce-Back Due -> Edge to Team A)
    elif s_a <= -3:
        pattern_found = True
        favored = t_a
        pattern_edge = 14.0
        rule_name = "BOUNCE_BACK"
        badge = f"📈 Bounce-Back Due: {t_a} ({abs(s_a)}L)"
        rationale = f"📈 Bounce-Back Rule: {t_a} lagaatar {abs(s_a)} toss haar chuki hai. Rebound probability favors {t_a} to snap streak."

    # Pattern 4: Team B on Extreme Loss Streak (Bounce-Back Due -> Edge to Team B)
    elif s_b <= -3:
        pattern_found = True
        favored = t_b
        pattern_edge = -14.0
        rule_name = "BOUNCE_BACK"
        badge = f"📈 Bounce-Back Due: {t_b} ({abs(s_b)}L)"
        rationale = f"📈 Bounce-Back Rule: {t_b} lagaatar {abs(s_b)} toss haar chuki hai. Rebound probability favors {t_b} to snap streak."

    # Pattern 5: H2H Zig-Zag Alternation Pattern (e.g. A-B-A -> B due next)
    elif len(h2h_seq) >= 3 and h2h_seq[-1] != h2h_seq[-2] and h2h_seq[-2] != h2h_seq[-3]:
        expected = 'B' if h2h_seq[-1] == 'A' else 'A'
        pattern_found = True
        favored = t_a if expected == 'A' else t_b
        pattern_edge = 12.0 if expected == 'A' else -12.0
        rule_name = "ZIG_ZAG_ALTERNATION"
        badge = f"⚡ Zig-Zag Pattern: {favored} Due Next"
        rationale = f"⚡ Zig-Zag Alternation Rule: Pichhle H2H toss matches strictly alternate ho rahe hain ({'-'.join(h2h_seq[-3:])}). Next pattern turn favors {favored}."

    # Pattern 6: Moderate 2-Match Streak Lean
    elif s_a == 2 and s_b <= -1:
        pattern_found = True
        favored = t_b
        pattern_edge = -7.0
        rule_name = "MOMENTUM_CHECK"
        badge = f"🔄 2W Streak Lean: {t_b} Rebound"
        rationale = f"Pattern lean favors {t_b} as {t_a} approaches streak resistance (2W)."
    elif s_b == 2 and s_a <= -1:
        pattern_found = True
        favored = t_a
        pattern_edge = 7.0
        rule_name = "MOMENTUM_CHECK"
        badge = f"🔄 2W Streak Lean: {t_a} Rebound"
        rationale = f"Pattern lean favors {t_a} as {t_b} approaches streak resistance (2W)."

    return {
        "pattern_found": pattern_found,
        "favored_team": favored,
        "pattern_edge": pattern_edge,
        "rule_name": rule_name,
        "pattern_badge": badge,
        "pattern_rationale": rationale,
        "a_streak_num": s_a,
        "b_streak_num": s_b,
        "a_streak_str": a_strk_txt,
        "b_streak_str": b_strk_txt,
        "a_last5_str": a_last5_txt,
        "b_last5_str": b_last5_txt,
        "a_total": tot_a,
        "b_total": tot_b
    }
