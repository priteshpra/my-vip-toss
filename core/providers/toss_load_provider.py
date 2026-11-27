"""
Multi-Platform Toss Intelligence & Real-World Consensus Engine with Tipper Pattern Engine.

Integrates:
1. CREX Live Market Rates, Asian Exchange Bhav & Implied Win Probability (crex.com)
2. Professional Tipper Pattern Engine (Streak Exhaustion, Zig-Zag Alternation & Bounce-Back)
3. Bookmaker Rate Liability & Divergence (GamingHelper Online)
4. Verified Empirical Ground-Truth Toss History (toss_results_log.json)
5. Immutable Dual-Key Lock Vault (Zero-flicker guarantee across page refreshes and tab changes)
"""

import os
import re
import json
import time
from functools import lru_cache
from typing import Dict, Any, List, Optional, Tuple
from core.engine.pattern_engine import (
    match_teams as match_teams_strict,
    _parse_team_cached,
    STATE_MODIFIERS,
)
from core.engine.historical_archive import load_merged_toss_records

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
LOCKED_PREDICTIONS_FILE = os.path.join(DATA_DIR, "toss_predictions_locked.json")
LOG_FILE = os.path.join(DATA_DIR, "toss_results_log.json")
DEFAULT_LOG_FILE = LOG_FILE


def _normalize_name(name: str) -> str:
    """Normalize team name keeping full identity intact (no stripping of categories)."""
    return re.sub(r'[^a-z0-9]', '', (name or "").lower()).strip()


def _make_pair_key(team_a: str, team_b: str) -> str:
    """Create order-independent canonical key for a pair of teams."""
    na = _normalize_name(team_a)
    nb = _normalize_name(team_b)
    pair = sorted([na, nb])
    return f"pair:{pair[0]}__{pair[1]}"


@lru_cache(maxsize=16384)
def match_teams_fuzzy(t1: str, t2: str) -> bool:
    """Check if two team names refer to the same team using strict tier/gender/state isolation."""
    if not t1 or not t2:
        return False
    if match_teams_strict(t1, t2):
        return True

    s1, _, w1, _, u19_1, u23_1, a1 = _parse_team_cached(t1)
    s2, _, w2, _, u19_2, u23_2, a2 = _parse_team_cached(t2)
    # Never bleed across Women/Men, U19, U23, A-team, or State modifiers
    if w1 != w2 or u19_1 != u19_2 or u23_1 != u23_2 or a1 != a2:
        return False
    if s1.intersection(STATE_MODIFIERS) != s2.intersection(STATE_MODIFIERS):
        return False

    # Alphanumeric equality / containment
    c1 = re.sub(r'[^a-z0-9]', '', (t1 or "").lower())
    c2 = re.sub(r'[^a-z0-9]', '', (t2 or "").lower())
    if c1 == c2:
        return True

    # Transliteration aliases (e.g. gurkha == gorkha, royalsmen == royals)
    norm1 = c1.replace("gurkha", "gorkha").replace("royalsmen", "royals")
    norm2 = c2.replace("gurkha", "gorkha").replace("royalsmen", "royals")
    if norm1 == norm2 or (len(norm1) >= 6 and norm1 in norm2) or (len(norm2) >= 6 and norm2 in norm1):
        return True

    return False


def load_locked_predictions() -> Dict[str, Any]:
    """Load persistent locked predictions from disk to guarantee zero flipping across refreshes."""
    if os.path.exists(LOCKED_PREDICTIONS_FILE):
        try:
            with open(LOCKED_PREDICTIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_locked_prediction(
    match_id: str,
    verdict: Dict[str, Any],
    team_a: str = "",
    team_b: str = "",
    match_date: str = ""
) -> None:
    """Freeze and persist a match prediction strictly under its specific match ID (or date-scoped pair).
    
    IMMUTABLE VAULT RULE:
    If this specific match ID was EVER locked as a PLAY call with a recommended team,
    it can NEVER be downgraded to SKIP or overwritten with empty picks!
    """
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        locked = load_locked_predictions()
        m_id_str = str(match_id or "").strip()
        d_clean = str(match_date or verdict.get("match_date") or "").split("T")[0].strip()
        pkey = _make_pair_key(team_a, team_b) if (team_a and team_b) else ""

        # Check existing lock: ONLY check for THIS SPECIFIC MATCH ID (or date-scoped key)
        existing = None
        if m_id_str and m_id_str in locked:
            existing = locked[m_id_str]
        elif d_clean and pkey and f"{d_clean}:{pkey}" in locked:
            existing = locked[f"{d_clean}:{pkey}"]

        if existing and existing.get("action") == "PLAY" and existing.get("recommended_team"):
            if existing.get("engine_version", 0) >= 8 and (verdict.get("action") != "PLAY" or not verdict.get("recommended_team")):
                # Preserve the original PLAY call only if locked under current v8 engine
                return

        if m_id_str:
            locked[m_id_str] = verdict
        if d_clean and pkey:
            locked[f"{d_clean}:{pkey}"] = verdict
        with open(LOCKED_PREDICTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(locked, f, indent=2)
    except Exception:
        pass


def get_locked_prediction(
    match_id: str = "",
    team_a: str = "",
    team_b: str = "",
    match_date: str = ""
) -> Optional[Dict[str, Any]]:
    """Retrieve frozen prediction strictly by match_id or date-scoped team pair.
    
    Guarantees 0% flicker for a match across refreshes WITHOUT leaking predictions across different dates.
    """
    locked = load_locked_predictions()
    m_id_str = str(match_id or "").strip()
    if m_id_str and m_id_str in locked:
        cand = locked[m_id_str]
        if isinstance(cand, dict) and (cand.get("engine_version") == 8 or cand.get("action") == "SKIP"):
            return cand
        return None

    d_clean = str(match_date or "").split("T")[0].strip()
    if d_clean and team_a and team_b:
        pkey = _make_pair_key(team_a, team_b)
        dkey = f"{d_clean}:{pkey}"
        if dkey in locked:
            cand = locked[dkey]
            if isinstance(cand, dict) and (cand.get("engine_version") == 8 or cand.get("action") == "SKIP"):
                return cand
            return None

    return None


def get_ground_truth_history(team_a: str, team_b: str) -> Tuple[int, int, int, int, int, int]:
    """Retrieve ground-truth wins and H2H across multi-year archive + toss_results_log.json."""
    a_wins, a_losses = 0, 0
    b_wins, b_losses = 0, 0
    h2h_a, h2h_b = 0, 0

    try:
        if LOG_FILE != DEFAULT_LOG_FILE:
            if not os.path.exists(LOG_FILE):
                return 0, 0, 0, 0, 0, 0
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                records = json.load(f)
        else:
            records = load_merged_toss_records()

        for r in records:
            actual = r.get("actual_winner")
            if not actual or actual.lower() in ["pending", "pending confirmation", "none", ""]:
                continue
            r_ta = r.get("team_a") or ""
            r_tb = r.get("team_b") or ""

            is_ta = match_teams_fuzzy(team_a, r_ta) or match_teams_fuzzy(team_a, r_tb)
            is_tb = match_teams_fuzzy(team_b, r_ta) or match_teams_fuzzy(team_b, r_tb)

            if is_ta and is_tb:
                if match_teams_fuzzy(team_a, actual):
                    h2h_a += 1
                elif match_teams_fuzzy(team_b, actual):
                    h2h_b += 1

            if is_ta:
                if match_teams_fuzzy(team_a, actual):
                    a_wins += 1
                else:
                    a_losses += 1

            if is_tb:
                if match_teams_fuzzy(team_b, actual):
                    b_wins += 1
                else:
                    b_losses += 1
    except Exception:
        pass

    return a_wins, a_losses, b_wins, b_losses, h2h_a, h2h_b


_cached_tg_feed = None
_cached_tg_time = 0

def get_memoized_tg_feed():
    global _cached_tg_feed, _cached_tg_time
    import time
    now = time.time()
    if _cached_tg_feed is not None and (now - _cached_tg_time) < 300:
        return _cached_tg_feed
    try:
        from core.providers.telegram_toss_feed import fetch_telegram_toss_feed
        _cached_tg_feed = fetch_telegram_toss_feed(pages=1)
        _cached_tg_time = now
    except Exception:
        _cached_tg_feed = []
    return _cached_tg_feed or []


def compute_multi_source_toss_load(
    team_a: str,
    team_b: str,
    odds_a: int = 98,
    odds_b: int = 80,
    tournament: str = "",
    match_time: str = "",
    match_id: str = ""
) -> Dict[str, Any]:
    """
    VIP Multi-Platform Toss Consensus & Pattern Convergence Engine (v7).

    Independent 5-Pillar Architecture (eliminates single-signal triple-counting):
    1. Empirical Form & Season Win Rate (requires >= 3 verified matches on BOTH teams)
    2. Sharp Bookmaker & Telegram Syndicate Flow (external market / syndicate anchor)
    3. Head-to-Head Direct Dominance & Sequence Rhythm (verified H2H history)
    4. Venue Conditions & MCC 1.3 Coin Calling Law (requires D/N dew or H2H home venue edge)
    5. Heuristic Pattern Engine Indicator (Streak Exhaustion / Zig-Zag / Momentum)

    Honesty Rule: Tosses are 50/50 random events; confidence is an empirical estimate, never 100%.
    """
    m_id_str = str(match_id or "").strip()
    t_a = (team_a or "").strip()
    t_b = (team_b or "").strip()

    # Step 0: Strict Exhibition / Sixes Elimination Gate (Zero Tolerance for Casual Blitz Formats)
    is_exhibition = any(k in (tournament or "").lower() for k in [
        "sixes", "hong kong sixes", "exhibition", "charity"
    ]) or any(k in (t_a or "").lower() or k in (t_b or "").lower() for k in ["sixes", "6s"])

    # Step 1: Check Immutable Lock Vault First (Re-evaluate if previously locked as SKIP, or with legacy engine < v8)
    locked_verdict = get_locked_prediction(m_id_str, t_a, t_b)
    if locked_verdict:
        if locked_verdict.get("action") == "SKIP" or not locked_verdict.get("recommended_team") or is_exhibition:
            locked_verdict = None
        elif locked_verdict.get("engine_version") != 8:
            locked_verdict = None
        elif locked_verdict.get("confidence_pct", 50) > 80 or "crex" in str(locked_verdict.get("consensus_badge", "")).lower() or locked_verdict.get("pillars_total") != 5:
            locked_verdict = None

    if locked_verdict:
        # Strict 63%+ Playable Gating & Minimum 2 of 5 Independent Pillars
        pat_name = locked_verdict.get("pattern_rule_name", "NEUTRAL")
        is_pat_valid = pat_name not in ["NEUTRAL", "BALANCED_ROTATION", "EXHIBITION", ""]
        if (locked_verdict.get("confidence_pct", 50) < 63 or locked_verdict.get("pillars_aligned", 0) < 2 or not is_pat_valid) and locked_verdict.get("action") == "PLAY":
            locked_verdict["action"] = "SKIP"
            locked_verdict["recommended_team"] = None
            locked_verdict["verdict_tier"] = "PASS"
            locked_verdict["stake_rating"] = "🟡 STRICT PASS (50-50 COIN FLIP TRAP)"
            locked_verdict["consensus_badge"] = f"🟡 50-50 PASS ({locked_verdict.get('confidence_pct', 50)}%)"
            locked_verdict["risk_level"] = "HIGH RISK (50-50 COIN FLIP)"
            locked_verdict["punter_rationale"] = "⛔ STRICT PASS: Independent 5-Pillar & External Anchor criteria meet nahi hue. Capital safe rakhne ke liye strict pass karein."
            save_locked_prediction(m_id_str, locked_verdict, t_a, t_b)

        if not locked_verdict.get("pattern_badge") or not locked_verdict.get("a_streak_str"):
            try:
                from core.engine.pattern_engine import evaluate_toss_pattern_rule
                pat = evaluate_toss_pattern_rule(t_a, t_b, tournament)
                locked_verdict["pattern_badge"] = pat.get("pattern_badge", "⚖️ Neutral Toss Rotation")
                locked_verdict["pattern_rule_name"] = pat.get("rule_name", "NEUTRAL")
                locked_verdict["pattern_rationale"] = pat.get("pattern_rationale", "")
                locked_verdict["a_streak_str"] = pat.get("a_streak_str", "Even")
                locked_verdict["b_streak_str"] = pat.get("b_streak_str", "Even")
                locked_verdict["a_last5_str"] = pat.get("a_last5_str", "None")
                locked_verdict["b_last5_str"] = pat.get("b_last5_str", "None")
                save_locked_prediction(m_id_str, locked_verdict, t_a, t_b)
            except Exception:
                pass
        return locked_verdict



    # Step 3: Query Tipper Pattern Rule & Streak Engine
    from core.engine.pattern_engine import evaluate_toss_pattern_rule, get_team_toss_history
    pat = evaluate_toss_pattern_rule(t_a, t_b, tournament)
    pat_fav = pat.get("favored_team")
    pat_edge = pat.get("pattern_edge", 0.0)  # Positive favors Team A, Negative favors Team B
    pat_badge = pat.get("pattern_badge", "⚖️ Neutral Rotation")
    pat_rat = pat.get("pattern_rationale", "")

    # Hard-skip all Exhibition / Sixes matches
    if is_exhibition:
        res = {
            "action": "SKIP",
            "recommended_team": None,
            "underdog_team": None,
            "confidence_pct": 50,
            "verdict_tier": "PASS",
            "stake_rating": "🟡 STRICT PASS (NO BET)",
            "consensus_badge": "🟡 PASS / EXHIBITION LOTTERY",
            "risk_level": "HIGH RISK (EXHIBITION)",
            "market_load_text": "Exhibition / Blitz Match (50-50 Flip)",
            "cricketclues_status": "No Statistical Edge in Sixes",
            "punter_rationale": "⛔ STRICT PASS (EXHIBITION LOTTERY): Hong Kong Sixes casual 5-over blitz format hai jahan coin toss pure lottery 50-50 flip hai. Capital safe rakhne ke liye strict pass karein.",
            "total_edge": 0.0,
            "pattern_badge": pat.get("pattern_badge", "⚖️ Neutral Toss Rotation"),
            "pattern_rule_name": pat.get("rule_name", "EXHIBITION"),
            "pattern_rationale": pat.get("pattern_rationale", ""),
            "a_streak_str": pat.get("a_streak_str", "Even"),
            "b_streak_str": pat.get("b_streak_str", "Even"),
            "a_last5_str": pat.get("a_last5_str", "None"),
            "b_last5_str": pat.get("b_last5_str", "None")
        }
        save_locked_prediction(m_id_str, res, t_a, t_b)
        return res

    # Step 4: Empirical Ground-Truth History & Recent Form
    a_w, a_l, b_w, b_l, h2h_a, h2h_b = get_ground_truth_history(t_a, t_b)
    tot_h2h = h2h_a + h2h_b
    s_a, last5_a, tot_a = get_team_toss_history(t_a, tournament)
    s_b, last5_b, tot_b = get_team_toss_history(t_b, tournament)
    b_losses = last5_b.count("L") if last5_b else 0
    a_losses = last5_a.count("L") if last5_a else 0
    a_wins = last5_a.count("W") if last5_a else a_w
    b_wins = last5_b.count("W") if last5_b else b_w

    h2h_form_edge = 0.0
    if tot_h2h >= 1 and h2h_a != h2h_b:
        h2h_ratio = (h2h_a - h2h_b) / float(tot_h2h)
        h2h_form_edge += (h2h_ratio * 16.0)

    tot_matches_a = a_w + a_l
    tot_matches_b = b_w + b_l
    if tot_matches_a >= 2 and tot_matches_b >= 2:
        wr_a = a_w / float(tot_matches_a)
        wr_b = b_w / float(tot_matches_b)
        h2h_form_edge += ((wr_a - wr_b) * 12.0)

    # Opponent slump edge
    if b_losses >= 3 and a_losses < b_losses:
        h2h_form_edge += (b_losses - a_losses) * 4.0
    elif a_losses >= 3 and b_losses < a_losses:
        h2h_form_edge -= (a_losses - b_losses) * 4.0

    # Step 5: Market Edge (Asian Bookmaker Toss Odds + Manual CREX Toss Override)
    oa = max(20, min(250, int(odds_a or 98)))
    ob = max(20, min(250, int(odds_b or 80)))
    rate_diff = abs(oa - ob)
    is_dummy_rate = (oa in [98, 80] and ob in [98, 80])

    market_edge = 0.0
    has_active_market = False
    if not is_dummy_rate and rate_diff >= 10:
        # Real bookmaker toss market divergence
        has_active_market = True
        if oa < ob:
            bookie_prob = int(round((ob / (oa + ob)) * 100))
            market_edge = (bookie_prob - 50) * 0.7
        else:
            bookie_prob = int(round((oa / (oa + ob)) * 100))
            market_edge = -(bookie_prob - 50) * 0.7
    else:
        # Check manual toss bhav overrides (data/crex_overrides.json) if GamingHelper odds are flat
        ov_file = os.path.join(DATA_DIR, "crex_overrides.json")
        if os.path.exists(ov_file):
            try:
                with open(ov_file, "r", encoding="utf-8") as f_ov:
                    ov_map = json.load(f_ov)
                ov = ov_map.get(m_id_str)
                if not ov and isinstance(ov_map, dict):
                    for k_ov, v_ov in ov_map.items():
                        if k_ov.lower() in t_a.lower() or k_ov.lower() in t_b.lower():
                            ov = v_ov
                            break
                if isinstance(ov, dict) and ov.get("back") and ov.get("fav_team"):
                    ov_back = int(ov["back"])
                    ov_fav = str(ov["fav_team"])
                    if 10 <= ov_back <= 85:
                        if match_teams_fuzzy(ov_fav, t_a):
                            oa, ob = ov_back, 100
                            has_active_market = True
                            market_edge = ((100 / (oa + 100)) * 100 - 50) * 0.7
                        elif match_teams_fuzzy(ov_fav, t_b):
                            oa, ob = 100, ov_back
                            has_active_market = True
                            market_edge = -((100 / (ob + 100)) * 100 - 50) * 0.7
            except Exception:
                pass

    # Step 6: Independent 5-Pillar Confluence Engine (v7 Architecture)
    # Each pillar measures a distinct signal so a single 3-match streak cannot trigger 3 pillars at once.
    both_teams_sufficient = (tot_a >= 3 and tot_b >= 3 and tot_matches_a >= 3 and tot_matches_b >= 3)
    wr_a_full = (a_w / float(tot_matches_a)) if tot_matches_a >= 3 else 0.50
    wr_b_full = (b_w / float(tot_matches_b)) if tot_matches_b >= 3 else 0.50

    confluence_factors_a = []
    confluence_factors_b = []

    # SUPER PILLAR 1: Form Momentum & Season Win Rate (requires >= 3 verified matches on BOTH teams)
    is_knockout = any(k in (tournament or "").lower() for k in [
        "final", "semi", "playoff", "eliminator", "qualifier", "decider", "trophy", "cup"
    ])
    has_conversion_edge_a = both_teams_sufficient and (
        (a_wins >= 4 and b_wins <= 2 and wr_a_full > wr_b_full)
        or (tot_matches_a >= 5 and tot_matches_b >= 5 and (wr_a_full - wr_b_full) >= 0.15 and a_wins > b_wins)
        or (is_knockout and (a_wins - b_wins) >= 2 and wr_a_full >= wr_b_full)
    )
    has_conversion_edge_b = both_teams_sufficient and (
        (b_wins >= 4 and a_wins <= 2 and wr_b_full > wr_a_full)
        or (tot_matches_a >= 5 and tot_matches_b >= 5 and (wr_b_full - wr_a_full) >= 0.15 and b_wins > a_wins)
        or (is_knockout and (b_wins - a_wins) >= 2 and wr_b_full >= wr_a_full)
    )

    if has_conversion_edge_a and not has_conversion_edge_b:
        confluence_factors_a.append(
            f"Form & Season Edge: {t_a} ({a_wins}/5 recent, {wr_a_full*100:.0f}% overall) leads {t_b} ({b_wins}/5 recent, {wr_b_full*100:.0f}% overall)"
        )
    elif has_conversion_edge_b and not has_conversion_edge_a:
        confluence_factors_b.append(
            f"Form & Season Edge: {t_b} ({b_wins}/5 recent, {wr_b_full*100:.0f}% overall) leads {t_a} ({a_wins}/5 recent, {wr_a_full*100:.0f}% overall)"
        )

    # SUPER PILLAR 2: Sharp Bookmaker Toss Line Divergence
    # Note: telegram_toss_feed scrapes post-toss results ("WON THE TOSS") for Tab 2 resolution,
    # NOT pre-toss predictions, so it is intentionally excluded from pre-toss Pillar 2 voting.
    tg_fav_a = False
    tg_fav_b = False

    if has_active_market and oa < ob:
        confluence_factors_a.append(f"Market & Syndicate Flow: Asian bookie rate divergence ({oa} vs {ob}) favors {t_a}")
    elif has_active_market and ob < oa:
        confluence_factors_b.append(f"Market & Syndicate Flow: Asian bookie rate divergence ({ob} vs {oa}) favors {t_b}")

    # SUPER PILLAR 3: Head-to-Head Direct Dominance & Sequence Rhythm
    from core.engine.pattern_engine import get_h2h_toss_sequence
    h2h_seq = get_h2h_toss_sequence(t_a, t_b)
    has_zigzag_a = (len(h2h_seq) >= 3 and h2h_seq[-1] != h2h_seq[-2] and h2h_seq[-2] != h2h_seq[-3] and h2h_seq[-1] == 'B')
    has_zigzag_b = (len(h2h_seq) >= 3 and h2h_seq[-1] != h2h_seq[-2] and h2h_seq[-2] != h2h_seq[-3] and h2h_seq[-1] == 'A')

    has_h2h_dom_a = (tot_h2h >= 2 and h2h_a > h2h_b and (h2h_a / tot_h2h) >= 0.60)
    has_h2h_dom_b = (tot_h2h >= 2 and h2h_b > h2h_a and (h2h_b / tot_h2h) >= 0.60)

    if (has_h2h_dom_a or has_zigzag_a) and not (has_h2h_dom_b or has_zigzag_b):
        details = []
        if has_h2h_dom_a:
            details.append(f"{h2h_a}/{tot_h2h} past H2H wins")
        if has_zigzag_a:
            details.append("alternating zig-zag rotation rhythm")
        confluence_factors_a.append(f"H2H Mastery & Rotation: {t_a} holds direct advantage ({', '.join(details)})")
    elif (has_h2h_dom_b or has_zigzag_b) and not (has_h2h_dom_a or has_zigzag_a):
        details = []
        if has_h2h_dom_b:
            details.append(f"{h2h_b}/{tot_h2h} past H2H wins")
        if has_zigzag_b:
            details.append("alternating zig-zag rotation rhythm")
        confluence_factors_b.append(f"H2H Mastery & Rotation: {t_b} holds direct advantage ({', '.join(details)})")

    # SUPER PILLAR 4: Venue Conditions & MCC 1.3 Coin Calling Law (requires verified history on BOTH teams)
    is_neutral_venue = any(k in (tournament or "").lower() for k in ["world cup", "champions", "asia cup", "tri-series", "t10"])
    t_low = (match_time or "").lower()
    is_d_n = (
        "night" in t_low
        or any(h in t_low for h in ["17:", "18:", "19:", "20:", "21:", "22:", "23:"])
        or bool(re.search(r'\b0?[5-9]:\d{2}\s*pm\b|\b1[01]:\d{2}\s*pm\b', t_low))
    )

    # Only award Venue/Calling Law when there is an actual D/N dew + clear edge OR verified H2H home advantage (no free Team B default)
    caller_edge_b = (not is_neutral_venue and both_teams_sufficient and is_d_n and b_wins >= 4 and a_wins <= 2)
    home_edge_a = (not is_neutral_venue and both_teams_sufficient and tot_h2h >= 2 and h2h_a > h2h_b and a_wins >= 3)

    if caller_edge_b and not home_edge_a:
        confluence_factors_b.append(f"Venue & Calling Law: Day/Night calling edge & strong recent form favor {t_b}")
    elif home_edge_a and not caller_edge_b:
        confluence_factors_a.append(f"Venue & Calling Law: Home venue H2H defense ({h2h_a}/{tot_h2h}) favors {t_a}")

    # SUPER PILLAR 5: Mathematical Tipper Pattern Engine Lock
    if pat.get("pattern_found"):
        fav_pat = pat.get("favored_team")
        rule_label = pat.get("rule_name", "PATTERN").replace("_", " ").title()
        if fav_pat == t_a:
            confluence_factors_a.append(f"Pattern Engine Lock: Mathematical {rule_label} rule locked on {t_a}")
        elif fav_pat == t_b:
            confluence_factors_b.append(f"Pattern Engine Lock: Mathematical {rule_label} rule locked on {t_b}")

    pillars_a = len(confluence_factors_a)
    pillars_b = len(confluence_factors_b)

    if pillars_a > pillars_b:
        favored = t_a
        underdog = t_b
        active_pillars = confluence_factors_a
        conflicting_pillars = confluence_factors_b
        net_pillars = pillars_a - pillars_b
    elif pillars_b > pillars_a:
        favored = t_b
        underdog = t_a
        active_pillars = confluence_factors_b
        conflicting_pillars = confluence_factors_a
        net_pillars = pillars_b - pillars_a
    else:
        favored = t_a
        underdog = t_b
        active_pillars = []
        conflicting_pillars = []
        net_pillars = 0

    # Heuristic Streak Exhaustion Red Flag Check (Mean Reversion Warning)
    if both_teams_sufficient and ((favored == t_a and s_b <= -3) or (favored == t_b and s_a <= -3)):
        exhausted_team = t_b if favored == t_a else t_a
        exhausted_streak = abs(s_b if favored == t_a else s_a)
        conflicting_pillars.append(
            f"Opponent Losing-Streak Red Flag: {exhausted_team} has lost {exhausted_streak} consecutive tosses (snap-back risk)"
        )

    # If the favored team is on an extended winning streak (4+ consecutive toss wins), avoid chasing the run
    if both_teams_sufficient and ((favored == t_a and s_a >= 4) or (favored == t_b and s_b >= 4)):
        win_run = s_a if favored == t_a else s_b
        conflicting_pillars.append(
            f"Winning Streak Over-Extension Flag: {favored} has won {win_run} consecutive tosses (regression risk)"
        )

    # Composite Heuristic Score (Independent 5-Pillar & Pattern Engine)
    calc_conf = 50 + int(round(net_pillars * 6.5))
    if has_active_market:
        calc_conf += 3

    # Hard realistic cap at 75% (coin tosses are never 100% certain)
    confidence = max(52, min(75, calc_conf))

    # Step 7: Strict Independent Gate (Eliminates single-streak false PLAY locks)
    # Requires:
    # 1. >= 3 genuinely independent aligned pillars & net_pillars >= 3
    # 2. 0 conflicting opposing pillars / red flags
    # 3. Real External or H2H Anchor (active bookie rate divergence OR Telegram syndicate OR verified H2H dominance/zig-zag)
    # 4. Pattern Engine confirmation matching favored team
    # 5. Composite score >= 70%
    is_pattern_confirmed = bool(pat.get("pattern_found") and pat.get("favored_team") == favored)

    has_external_anchor = (
        has_active_market
        or (favored == t_a and tg_fav_a)
        or (favored == t_b and tg_fav_b)
        or (favored == t_a and (has_h2h_dom_a or has_zigzag_a))
        or (favored == t_b and (has_h2h_dom_b or has_zigzag_b))
    )

    is_iron_clad_lock = (
        len(active_pillars) >= 3
        and net_pillars >= 3
        and len(conflicting_pillars) == 0
        and has_external_anchor
        and is_pattern_confirmed
        and confidence >= 70
    )

    is_value_lean = (
        not is_iron_clad_lock
        and len(active_pillars) >= 2
        and net_pillars >= 2
        and len(conflicting_pillars) == 0
        and (both_teams_sufficient or tot_h2h >= 2)
        and is_pattern_confirmed
        and confidence >= 63
    )

    # Real empirical win% metrics for transparent decision-making
    pct_a_real = round((a_w / float(tot_matches_a)) * 100.0, 1) if tot_matches_a > 0 else 50.0
    pct_b_real = round((b_w / float(tot_matches_b)) * 100.0, 1) if tot_matches_b > 0 else 50.0
    rec_a = f"{a_w}/{tot_matches_a} ({pct_a_real:.0f}%)" if tot_matches_a > 0 else "0/0 (N/A)"
    rec_b = f"{b_w}/{tot_matches_b} ({pct_b_real:.0f}%)" if tot_matches_b > 0 else "0/0 (N/A)"

    rule_win_pct: Optional[float] = None
    if LOG_FILE == DEFAULT_LOG_FILE:
        try:
            from core.engine.historical_archive import run_empirical_rule_backtest
            bt_rows = run_empirical_rule_backtest().get("rules", [])
            r_raw = pat.get("rule_name", "NEUTRAL")
            bt_key = None
            if r_raw == "H2H_DOMINANCE":
                bt_key = "H2H_DOMINANCE"
            elif r_raw == "ZIG_ZAG_ALTERNATION":
                bt_key = "ZIG_ZAG_ALTERNATION"
            elif r_raw == "WINNING_MOMENTUM":
                bt_key = "WINNING_MOMENTUM (2-3W)"
            elif r_raw == "STREAK_EXHAUSTION":
                bt_key = "WIN_STREAK_SNAP (4W+)" if (s_a >= 4 or s_b >= 4) else "STREAK_EXHAUSTION (3L)"
            if bt_key:
                match_row = next((r for r in bt_rows if r.get("Rule") == bt_key), None)
                if match_row:
                    rule_win_pct = float(match_row.get("Win %", 50.0))
        except Exception:
            rule_win_pct = None

    fav_pct_val = pct_a_real if favored == t_a else pct_b_real
    und_pct_val = pct_b_real if favored == t_a else pct_a_real
    fav_rec_str = rec_a if favored == t_a else rec_b
    und_rec_str = rec_b if favored == t_a else rec_a
    h2h_str = f"H2H {h2h_a}-{h2h_b}" if tot_h2h > 0 else "H2H 0-0"
    rule_bt_txt = f" · Rule Hist: {rule_win_pct:.1f}%" if rule_win_pct is not None else ""
    win_pct_summary = f"{favored}: {fav_rec_str} vs {underdog}: {und_rec_str} ({h2h_str}{rule_bt_txt})"

    if is_iron_clad_lock:
        action = "PLAY"
        verdict_tier = "SUPER_LOCK"
        rating = "💎 HIGH-CONVICTION PICK (3+ PILLARS + ANCHOR)"
        badge = f"💎 TOP GOLDEN PICK ({confidence}%)"
        risk_level = f"FLAT STAKE ({len(active_pillars)}/5 PILLARS + ANCHOR · COIN TOSS)"
        pillar_bullets = " · ".join(active_pillars)
        pat_name = pat.get("rule_name", "PATTERN").replace("_", " ").title()
        rationale = (
            f"💎 TOP PICK ({confidence}% Score): {pillar_bullets} · Pattern: {pat_name}. "
            f"📈 Real Win%: {win_pct_summary}. Toss random event hai (100% guarantee nahi); disciplined flat stake (2.5% max) se {favored} play karein."
        )
        source_summary = f"Multi-Source Confluence ({len(active_pillars)}/5 · {confidence}%)"
        rec_team = favored
        und_team = underdog
        market_load_str = f"📊 {len(active_pillars)}/5 Pillars + Anchor | Win%: {fav_pct_val:.0f}% vs {und_pct_val:.0f}%"
    elif is_value_lean:
        action = "PLAY"
        verdict_tier = "VALUE_LEAN"
        confidence = min(69, confidence)
        rating = "⚡ VALUE LEAN (HALF STAKE · 2+ PILLARS)"
        badge = f"⚡ VALUE LEAN ({confidence}%)"
        risk_level = f"HALF STAKE ({len(active_pillars)}/5 PILLARS · ~50-53% HIST WIN)"
        pillar_bullets = " · ".join(active_pillars)
        pat_name = pat.get("rule_name", "PATTERN").replace("_", " ").title()
        rationale = (
            f"⚡ VALUE LEAN ({confidence}% Score): {pillar_bullets} · Pattern: {pat_name}. "
            f"📈 Real Win%: {win_pct_summary}. 2-Pillar Value spot hai (3+ Pillar se kam conviction) — Half Stake (1–1.25% max) ke saath {favored} play karein."
        )
        source_summary = f"Value Lean ({len(active_pillars)}/5 Aligned · {confidence}%)"
        rec_team = favored
        und_team = underdog
        market_load_str = f"📊 {len(active_pillars)}/5 Pillars (Value) | Win%: {fav_pct_val:.0f}% vs {und_pct_val:.0f}%"
    else:
        action = "SKIP"
        verdict_tier = "PASS"
        rating = "🟡 STRICT PASS (50-50 COIN FLIP TRAP)"
        badge = f"🟡 50-50 PASS ({confidence}%)"
        risk_level = "HIGH RISK (COIN FLIP TRAP)"

        # Build precise diagnostic reason for rejection
        reasons_list = []
        if not both_teams_sufficient and tot_h2h < 2:
            reasons_list.append(f"Insufficient team toss history ({t_a}: {tot_a}, {t_b}: {tot_b} matches; min 3 each required)")
        if len(active_pillars) < 2:
            reasons_list.append(f"Only {len(active_pillars)}/5 independent pillars aligned (min 2 for Value Lean, 3 for Top Pick)")
        if not is_pattern_confirmed:
            reasons_list.append("Pattern Engine not confirmed (Neutral/Balanced rotation)")
        if len(conflicting_pillars) > 0:
            reasons_list.append(f"{len(conflicting_pillars)} conflicting opponent pillars")
        if confidence < 63:
            reasons_list.append(f"Confidence score {confidence}% (min 63% required)")

        diag_txt = " · ".join(reasons_list) if reasons_list else "Both teams in 50-50 neutral coin-flip zone"
        rationale = f"⛔ STRICT PASS (50-50 COIN FLIP): {diag_txt}. Win%: {rec_a} vs {rec_b} ({h2h_str}). Capital safe rakhne ke liye pass karein."
        source_summary = f"Flat / Coin-Flip Zone ({confidence}%)"
        rec_team = None
        und_team = None
        market_load_str = f"📊 Neutral Zone ({confidence}%)"

    res = {
        "action": action,
        "recommended_team": rec_team,
        "underdog_team": und_team,
        "confidence_pct": confidence,
        "verdict_tier": verdict_tier,
        "stake_rating": rating,
        "consensus_badge": badge,
        "risk_level": risk_level,
        "market_load_text": market_load_str,
        "cricketclues_status": source_summary,
        "punter_rationale": rationale,
        "total_edge": round(float(confidence - 50), 1),
        "clues_matched": bool(has_active_market),
        "pillars_total": 5,
        "pillars_aligned": len(active_pillars),
        "active_pillars_list": active_pillars,
        "team_a_win_pct": pct_a_real,
        "team_b_win_pct": pct_b_real,
        "team_a_record": rec_a,
        "team_b_record": rec_b,
        "h2h_summary": h2h_str,
        "rule_win_pct": rule_win_pct,
        "fav_win_pct_summary": win_pct_summary,
        "engine_version": 8,
        # Dedicated Pattern Fields for UI Display
        "pattern_badge": pat.get("pattern_badge", "⚖️ Neutral Rotation"),
        "pattern_rule_name": pat.get("rule_name", "NEUTRAL"),
        "pattern_rationale": pat.get("pattern_rationale", ""),
        "a_streak_str": pat.get("a_streak_str", "Even"),
        "b_streak_str": pat.get("b_streak_str", "Even"),
        "a_last5_str": pat.get("a_last5_str", "None"),
        "b_last5_str": pat.get("b_last5_str", "None")
    }

    save_locked_prediction(m_id_str, res, t_a, t_b)
    return res
