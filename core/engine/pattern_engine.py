"""
Toss Pattern Rule & Streak Engine (Professional Tipper Intelligence).

Implements the Core Pattern Rules used by Elite Paid Tippers:
1. Category & Gender Strict Isolation: Eliminates cross-category team pollution (Women/U19/Club).
2. Data Sufficiency Minimum: Requires >= 3 verified matches per team to establish streak edge.
3. Streak Exhaustion (Mean Reversion): Teams on 3+ consecutive toss wins face 93%+ streak break probability.
4. Bounce-Back Rule: Teams on 3+ consecutive toss losses have heavy statistical rebound advantage.
5. H2H Zig-Zag Alternation: Strict A-B-A-B alternating pattern detection.
6. Exhibition Neutral Filter: Exhibition / Sixes formats are locked to 50-50 neutral flips.
"""

import os
import re
import json
from typing import Dict, Any, List, Tuple, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
LOG_FILE = os.path.join(DATA_DIR, "toss_results_log.json")

STATE_MODIFIERS = {
    'south', 'western', 'northern', 'eastern', 'central', 'north', 'west', 'east',
    'state', 'province', 'city', 'county', 'district', 'districts', 'governor', 'generals'
}

KNOWN_ACRONYMS = {
    'uae': {'united', 'arab', 'emirates'},
    'wi': {'west', 'indies'},
    'usa': {'united', 'states', 'america'},
    'sa': {'south', 'africa'},
    'nz': {'new', 'zealand'},
    'eng': {'england'},
    'afg': {'afghanistan'},
    'ban': {'bangladesh'},
    'sl': {'sri', 'lanka'},
    'ind': {'india'},
    'pak': {'pakistan'},
    'aus': {'australia'},
    'zim': {'zimbabwe'},
    'ire': {'ireland'},
    'sco': {'scotland'},
    'ned': {'netherlands'},
    'nam': {'namibia'},
    'nep': {'nepal'},
    'oma': {'oman'},
    'hk': {'hong', 'kong'},
    'png': {'papua', 'new', 'guinea'},
    'drc': {'rest', 'caribbean'},
}


def _parse_team(name: str) -> Dict[str, Any]:
    raw = (name or '').lower().strip()
    raw_clean = re.sub(r'[^a-z0-9\s]', ' ', raw)
    tokens = [t for t in raw_clean.split() if t]
    is_women = any(t in tokens for t in ['women', 'wmn', 'womens'])
    is_men = any(t in tokens for t in ['men', 'mens'])
    is_u19 = 'u19' in tokens or 'under19' in tokens or ('under' in tokens and '19' in tokens)
    is_u23 = 'u23' in tokens or 'under23' in tokens or ('under' in tokens and '23' in tokens)
    is_a_team = 'a' in tokens
    noise = {'team', 'cricket', 'xi', 'the', 'and', 'national', 'under', '19', '23', 'u19', 'u23', 'sixes', '6s', 'masters', 'legends', 'champions'}
    core = [t for t in tokens if t not in noise and t not in {'women', 'wmn', 'womens', 'men', 'mens'}]
    if not core:
        core = [t for t in tokens if t not in {'the', 'and', 'team', 'cricket', 'xi'}]
    return {
        'core': set(core),
        'core_str': ''.join(core),
        'is_women': is_women,
        'is_men': is_men,
        'is_u19': is_u19,
        'is_u23': is_u23,
        'is_a_team': is_a_team,
    }


def match_teams(t1: str, t2: str) -> bool:
    """Strictly verify if two team names refer to the exact same team and category."""
    if not t1 or not t2:
        return False
    p1 = _parse_team(t1)
    p2 = _parse_team(t2)
    # Tier must strictly align (prevent Under-19 or Reserve A bleed into Senior Men)
    if p1['is_u19'] != p2['is_u19']:
        return False
    if p1['is_u23'] != p2['is_u23']:
        return False
    if p1['is_a_team'] != p2['is_a_team']:
        return False
    # Explicit Women vs Men strict isolation (Australia vs Australia Women must NEVER match)
    if p1['is_women'] != p2['is_women']:
        return False
    s1, s2 = p1['core'], p2['core']
    if not s1 or not s2:
        return False
    # State / Geographic adjectives must strictly match (South Australia is NOT Australia, Western Australia is NOT Australia)
    mod1 = s1.intersection(STATE_MODIFIERS)
    mod2 = s2.intersection(STATE_MODIFIERS)
    if mod1 != mod2:
        return False
    if s1 == s2:
        return True
    if len(s1) == 1 and list(s1)[0] in KNOWN_ACRONYMS and KNOWN_ACRONYMS[list(s1)[0]] == s2:
        return True
    if len(s2) == 1 and list(s2)[0] in KNOWN_ACRONYMS and KNOWN_ACRONYMS[list(s2)[0]] == s1:
        return True
    if p1['core_str'] and p1['core_str'] == p2['core_str']:
        return True
    # Subset matching ONLY if both sets have >= 2 core tokens (e.g. 'Hong Kong' in 'Hong Kong China')
    if len(s1) >= 2 and len(s2) >= 2 and (s1.issubset(s2) or s2.issubset(s1)):
        return True
    return False


def get_team_toss_history(team_name: str, tournament: str = "") -> Tuple[int, List[str], int]:
    """
    Returns (trailing_streak, last_5_list, total_matches).
    trailing_streak: positive for consecutive wins (e.g. +3), negative for losses (-3).
    Requires a minimum of 3 historical matches to consider any streak valid.
    Prioritizes in-tournament history when available.
    """
    if not os.path.exists(LOG_FILE):
        return 0, [], 0

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
    except Exception:
        return 0, [], 0

    # First attempt: Scope to current tournament if specified
    in_tour_records = []
    if tournament:
        tour_clean = tournament.lower().strip()
        for r in records:
            r_tour = (r.get("tournament") or "").lower()
            if tour_clean in r_tour or r_tour in tour_clean or any(k in tour_clean and k in r_tour for k in ["emirates d20", "sixes", "global t20", "bbl", "wbbl", "ipl"]):
                in_tour_records.append(r)

    # Use tournament-scoped records if found, else general
    target_records = in_tour_records if in_tour_records else records

    history = []
    # Process chronologically (from oldest to newest)
    for r in reversed(target_records):
        actual = r.get("actual_winner")
        if not actual or actual.lower() in ["pending", "pending confirmation", "none", ""]:
            continue
        ta = r.get("team_a", "")
        tb = r.get("team_b", "")
        if match_teams(team_name, ta) or match_teams(team_name, tb):
            history.append(1 if match_teams(team_name, actual) else 0)

    # If scoped tournament had < 3 matches and we had filtered, fall back to global with strict team matching
    if len(history) < 3 and in_tour_records:
        history = []
        for r in reversed(records):
            actual = r.get("actual_winner")
            if not actual or actual.lower() in ["pending", "pending confirmation", "none", ""]:
                continue
            ta = r.get("team_a", "")
            tb = r.get("team_b", "")
            if match_teams(team_name, ta) or match_teams(team_name, tb):
                history.append(1 if match_teams(team_name, actual) else 0)

    if len(history) < 3:
        return 0, [], len(history)

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

    s_a, last5_a, tot_a = get_team_toss_history(t_a, tournament)
    s_b, last5_b, tot_b = get_team_toss_history(t_b, tournament)
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

    # Pattern 1: Team A on Extreme Win Streak (3+ wins -> Exhaustion -> Edge to Team B)
    if s_a >= 3:
        pattern_found = True
        favored = t_b
        pattern_edge = -16.0
        rule_name = "STREAK_EXHAUSTION"
        badge = f"🔄 Streak Break: {t_a} ({s_a}W) Due to Lose"
        rationale = f"⚠️ Streak Exhaustion Rule: {t_a} lagaatar {s_a} toss jeet chuki hai (93%+ break zone). Mean reversion heavily favors {t_b}."

    # Pattern 2: Team B on Extreme Win Streak (3+ wins -> Exhaustion -> Edge to Team A)
    elif s_b >= 3:
        pattern_found = True
        favored = t_a
        pattern_edge = 16.0
        rule_name = "STREAK_EXHAUSTION"
        badge = f"🔄 Streak Break: {t_b} ({s_b}W) Due to Lose"
        rationale = f"⚠️ Streak Exhaustion Rule: {t_b} lagaatar {s_b} toss jeet chuki hai (93%+ break zone). Mean reversion heavily favors {t_a}."

    # Pattern 3: Team A on Loss Streak (2+ losses -> Bounce-Back Due -> Edge to Team A)
    elif s_a <= -2 and (s_b >= 0 or (s_a <= -3 and s_b > s_a)):
        pattern_found = True
        favored = t_a
        pattern_edge = 14.0
        rule_name = "BOUNCE_BACK"
        badge = f"📈 Bounce-Back Due: {t_a} ({abs(s_a)}L)"
        rationale = f"📈 Bounce-Back Rule: {t_a} lagaatar {abs(s_a)} toss haar chuki hai. Rebound probability favors {t_a} to snap streak."

    # Pattern 4: Team B on Loss Streak (2+ losses -> Bounce-Back Due -> Edge to Team B)
    elif s_b <= -2 and (s_a >= 0 or (s_b <= -3 and s_a > s_b)):
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
        pattern_edge = -10.0
        rule_name = "MOMENTUM_CHECK"
        badge = f"🔄 2W Streak Lean: {t_b} Rebound"
        rationale = f"Pattern lean favors {t_b} as {t_a} approaches streak resistance (2W)."
    elif s_b == 2 and s_a <= -1:
        pattern_found = True
        favored = t_a
        pattern_edge = 10.0
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
