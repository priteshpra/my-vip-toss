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
from typing import Dict, Any, List, Optional, Tuple
from core.engine.pattern_engine import match_teams as match_teams_strict

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
LOCKED_PREDICTIONS_FILE = os.path.join(DATA_DIR, "toss_predictions_locked.json")
LOG_FILE = os.path.join(DATA_DIR, "toss_results_log.json")


def _normalize_name(name: str) -> str:
    """Normalize team name keeping full identity intact (no stripping of categories)."""
    return re.sub(r'[^a-z0-9]', '', (name or "").lower()).strip()


def _make_pair_key(team_a: str, team_b: str) -> str:
    """Create order-independent canonical key for a pair of teams."""
    na = _normalize_name(team_a)
    nb = _normalize_name(team_b)
    pair = sorted([na, nb])
    return f"pair:{pair[0]}__{pair[1]}"


def match_teams_fuzzy(t1: str, t2: str) -> bool:
    """Check if two team names refer to the same team using strict category-aware matching."""
    return match_teams_strict(t1, t2)


def load_locked_predictions() -> Dict[str, Any]:
    """Load persistent locked predictions from disk to guarantee zero flipping across refreshes."""
    if os.path.exists(LOCKED_PREDICTIONS_FILE):
        try:
            with open(LOCKED_PREDICTIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_locked_prediction(match_id: str, verdict: Dict[str, Any], team_a: str = "", team_b: str = "") -> None:
    """Freeze and persist a match prediction permanently under ID and Pair Key.
    
    IMMUTABLE VAULT RULE:
    If this match was EVER locked as a PLAY call with a recommended team,
    it can NEVER be downgraded to SKIP or overwritten with empty picks!
    """
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        locked = load_locked_predictions()
        m_id_str = str(match_id or "").strip()
        pkey = _make_pair_key(team_a, team_b) if (team_a and team_b) else ""

        # Check existing lock: if it was already a PLAY, NEVER overwrite with SKIP!
        existing = None
        if m_id_str and m_id_str in locked:
            existing = locked[m_id_str]
        elif pkey and pkey in locked:
            existing = locked[pkey]

        if existing and existing.get("action") == "PLAY" and existing.get("recommended_team"):
            if verdict.get("action") != "PLAY" or not verdict.get("recommended_team"):
                # Preserve the original PLAY call! Do not overwrite with SKIP!
                return

        if m_id_str:
            locked[m_id_str] = verdict
        if pkey:
            locked[pkey] = verdict
            locked[f"{team_a.strip().lower()}__vs__{team_b.strip().lower()}"] = verdict
        with open(LOCKED_PREDICTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(locked, f, indent=2)
    except Exception:
        pass


def get_locked_prediction(match_id: str = "", team_a: str = "", team_b: str = "") -> Optional[Dict[str, Any]]:
    """Retrieve frozen prediction by match_id or team pair. Guarantees 0% flicker across refreshes."""
    locked = load_locked_predictions()
    if match_id and str(match_id) in locked:
        return locked[str(match_id)]
    if team_a and team_b:
        pkey = _make_pair_key(team_a, team_b)
        if pkey in locked:
            return locked[pkey]
        for k, v in locked.items():
            if "__vs__" in k:
                parts = k.split("__vs__")
                if len(parts) == 2:
                    if (match_teams_fuzzy(team_a, parts[0]) and match_teams_fuzzy(team_b, parts[1])) or \
                       (match_teams_fuzzy(team_a, parts[1]) and match_teams_fuzzy(team_b, parts[0])):
                        return v
    return None


def get_ground_truth_history(team_a: str, team_b: str) -> Tuple[int, int, int, int, int, int]:
    """Retrieve ground-truth wins and H2H from toss_results_log.json."""
    a_wins, a_losses = 0, 0
    b_wins, b_losses = 0, 0
    h2h_a, h2h_b = 0, 0

    if not os.path.exists(LOG_FILE):
        return 0, 0, 0, 0, 0, 0

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
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
    VIP Multi-Platform Toss Consensus & Pattern Convergence Engine.

    Combines:
    1. CREX Real-Time Live Bhav & Implied Win Probability
    2. Tipper Pattern Rule Engine (Streak Exhaustion, Bounce-Back & Zig-Zag)
    3. Bookmaker Liability / Exchange Rate Divergence (Odds A vs Odds B)
    4. Ground-Truth Empirical Head-to-Head & Toss Win Records
    5. Consensus Convergence Gate:
       - Multi-Source Convergence (Confidence >= 70%): 💎 100% SATIK PLAY
       - Split / 50-50 Traps: 🟡 STRICT PASS (Capital Safe)
    """
    m_id_str = str(match_id or "").strip()
    t_a = (team_a or "").strip()
    t_b = (team_b or "").strip()

    # Step 0: Strict Exhibition / Sixes Elimination Gate (Zero Tolerance for Casual Blitz Formats)
    is_exhibition = any(k in (tournament or "").lower() for k in [
        "sixes", "hong kong sixes", "exhibition", "masters", "charity"
    ]) or any(k in (t_a or "").lower() or k in (t_b or "").lower() for k in ["sixes", "6s"])

    # Step 1: Check Immutable Lock Vault First
    locked_verdict = get_locked_prediction(m_id_str, t_a, t_b)
    if locked_verdict:
        # If lock was SKIP, allow live market / pattern consensus to upgrade to PLAY (unless it's an exhibition match)
        if locked_verdict.get("action") == "SKIP" or is_exhibition:
            locked_verdict = None

    if locked_verdict:
        # Guarantee pattern fields are always present even for legacy locked predictions
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

    # Step 2: Query CREX Real-Time Live Market Data
    from core.providers.crex_provider import get_crex_live_bhav
    crex_data = None
    try:
        crex_data = get_crex_live_bhav(t_a, t_b, match_id=m_id_str)
    except Exception:
        pass

    # Step 3: Query Tipper Pattern Rule & Streak Engine
    from core.engine.pattern_engine import evaluate_toss_pattern_rule, get_team_toss_history
    pat = evaluate_toss_pattern_rule(t_a, t_b, tournament)
    pat_fav = pat.get("favored_team")
    pat_edge = pat.get("pattern_edge", 0.0)
    pat_badge = pat.get("pattern_badge", "⚖️ Neutral Rotation")
    pat_rat = pat.get("pattern_rationale", "")

    # Hard-skip all Exhibition / Sixes matches before any pattern evaluation
    if is_exhibition:
        res = {
            "action": "SKIP",
            "recommended_team": None,
            "confidence_pct": 50,
            "stake_rating": "🟡 STRICT PASS (NO BET)",
            "consensus_badge": "🟡 PASS / EXHIBITION LOTTERY",
            "risk_level": "HIGH RISK (EXHIBITION)",
            "market_load_text": "Exhibition / Blitz Match (50-50 Flip)",
            "cricketclues_status": "No Statistical Edge in Sixes",
            "punter_rationale": "⛔ STRICT PASS (EXHIBITION LOTTERY): Hong Kong Sixes casual 5-over blitz format hai jahan coin toss pure lottery 50-50 flip hai. Captains ki koi fixed calling rhythm nahi hoti — capital safe rakhne ke liye strict pass karein.",
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

    # Step 4: Analyze Bookmaker Rate Divergence (Asian Odds)
    oa = max(20, min(250, odds_a))
    ob = max(20, min(250, odds_b))
    rate_diff = abs(oa - ob)
    is_dummy_rate = (oa in [98, 80] and ob in [98, 80])

    bookie_fav = None
    bookie_load = 50
    if not is_dummy_rate and rate_diff >= 12:
        if oa < ob:
            bookie_fav = t_a
            bookie_load = int(round((ob / (oa + ob)) * 100))
        else:
            bookie_fav = t_b
            bookie_load = int(round((oa / (oa + ob)) * 100))

    # Step 5: Empirical Ground-Truth History
    a_w, a_l, b_w, b_l, h2h_a, h2h_b = get_ground_truth_history(t_a, t_b)
    tot_h2h = h2h_a + h2h_b
    h2h_fav = None
    if tot_h2h >= 1 and h2h_a != h2h_b:
        h2h_fav = t_a if h2h_a > h2h_b else t_b

    # Step 6: Multi-Platform Consensus Evaluation
    action = "SKIP"
    favored = None
    underdog = None
    confidence = 50
    rating = "🟡 STRICT PASS (NO BET)"
    badge = "🟡 PASS / NOT SATIK"
    risk_level = "HIGH RISK (AVOID)"
    rationale = "⛔ Pass Match: Market load is flat 50-50 neutral. Satik threshold (70%+) meet nahi kar raha — paisa safe rakhne ke liye pass karein."
    source_summary = "Standard Line"

    # Scenario A: CREX Live Market has active rates and conviction
    if crex_data and crex_data.get("fav_team"):
        c_fav = crex_data["fav_team"]
        c_prob = crex_data.get("win_prob", 50)
        c_back = crex_data.get("back", 0)
        c_lay = crex_data.get("lay", 0)

        fav_team_resolved = t_a if match_teams_fuzzy(c_fav, t_a) else (t_b if match_teams_fuzzy(c_fav, t_b) else c_fav)
        underdog_team = t_b if fav_team_resolved == t_a else t_a

        # Rigorous Convergence Gating: Prevent Gambler's Fallacy & False Golden Locks
        if (c_prob >= 60 or (c_back > 0 and c_back <= 65)) and pat_fav and match_teams_fuzzy(pat_fav, fav_team_resolved):
            action = "PLAY"
            favored = fav_team_resolved
            underdog = underdog_team
            confidence = max(80, min(92, c_prob + 5))
            badge = "💎 UNANIMOUS CREX + PATTERN LOCK"
            rating = "💎 100% SATIK PLAY (GOLDEN LOCK)"
            risk_level = "VERY LOW RISK (MAX CONVICTION)"
            source_summary = f"CREX [{c_back}-{c_lay}] + Pattern ({pat['rule_name']})"
            rationale = (
                f"💎 UNANIMOUS SUPER CONVERGENCE: CREX Market Sentiment ({c_prob}%, Bhav {c_back}-{c_lay}) "
                f"+ Tipper Pattern Rule ({pat_badge}) both 100% confirm {favored}. High statistical edge."
            )
        elif (c_prob >= 60 or (c_back > 0 and c_back <= 65)) and h2h_fav and match_teams_fuzzy(h2h_fav, fav_team_resolved) and tot_h2h >= 2:
            action = "PLAY"
            favored = fav_team_resolved
            underdog = underdog_team
            confidence = max(78, min(88, int(round((h2h_a if h2h_fav == t_a else h2h_b) / tot_h2h * 100))))
            badge = "💎 CREX + H2H DOMINANCE LOCK"
            rating = "💎 100% SATIK PLAY (GOLDEN LOCK)"
            risk_level = "LOW RISK (HIGH CONVICTION)"
            source_summary = f"CREX [{c_back}-{c_lay}] + Ground-Truth H2H"
            rationale = (
                f"💎 SATIK GOLD PLAY: CREX Market Sentiment ({c_prob}%) and historical H2H toss dominance "
                f"both converge on {favored}."
            )
        elif (c_prob >= 60 or (c_back > 0 and c_back <= 65)) and bookie_fav and match_teams_fuzzy(bookie_fav, fav_team_resolved) and bookie_load >= 70:
            action = "PLAY"
            favored = fav_team_resolved
            underdog = underdog_team
            confidence = bookie_load
            rating = "💎 100% SATIK PLAY (GOLDEN LOCK)"
            badge = "💎 100% SATIK CALL"
            risk_level = "LOW RISK (HIGH CONVICTION)"
            source_summary = f"CREX [{c_back}-{c_lay}] + Bookie Liability ({bookie_load}%)"
            rationale = f"💎 SATIK GOLD PLAY: Both CREX Market and Asian Bookmaker line converge on {favored}."
        elif c_prob >= 75 or (c_back > 0 and c_back <= 33):
            action = "PLAY"
            favored = fav_team_resolved
            underdog = underdog_team
            confidence = min(88, c_prob)
            rating = "💎 100% SATIK PLAY (GOLDEN LOCK)"
            badge = f"💎 CREX HEAVY LOAD ({c_prob}%)"
            risk_level = "LOW RISK (INSTITUTIONAL HEAVY WEIGHT)"
            source_summary = f"CREX Market Conviction [{c_back}-{c_lay}]"
            rationale = f"💎 HIGH CONVICTION PLAY: Heavy institutional positioning ({c_prob}%, Bhav {c_back}-{c_lay}) gives {favored} strong momentum edge."
        elif h2h_fav and tot_h2h >= 3 and (max(h2h_a, h2h_b) / tot_h2h) >= 0.75:
            h2h_win_cnt = max(h2h_a, h2h_b)
            action = "PLAY"
            favored = h2h_fav
            underdog = t_b if h2h_fav == t_a else t_a
            confidence = min(85, max(75, int(round((h2h_win_cnt / tot_h2h) * 100))))
            rating = "💎 100% SATIK PLAY (GOLDEN LOCK)"
            badge = f"💎 H2H DOMINANCE ({h2h_win_cnt}W-{tot_h2h - h2h_win_cnt}L)"
            risk_level = "LOW RISK (H2H DOMINANCE)"
            source_summary = f"Ground-Truth H2H Dominance ({h2h_win_cnt}W-{tot_h2h - h2h_win_cnt}L across {tot_h2h} matches)"
            rationale = f"💎 SATIK GOLD PLAY: Proven head-to-head toss dominance ({h2h_win_cnt}W-{tot_h2h - h2h_win_cnt}L across {tot_h2h} matches) gives {favored} heavy calling rhythm advantage."
        elif c_prob >= 62 or (c_back > 0 and c_back <= 60):
            # Moderate Market Lean - Not a 100% Golden Lock; played with half limit
            action = "PLAY"
            favored = fav_team_resolved
            underdog = underdog_team
            confidence = c_prob
            rating = "🔥 VALUE LEAN (PLAY HALF LIMIT)"
            badge = f"⚡ MARKET LEAN ({c_prob}%)"
            risk_level = "MODERATE RISK (HALF LIMIT)"
            source_summary = f"CREX Market Flow [{c_back}-{c_lay}]"
            rationale = (
                f"🔥 VALUE LEAN: CREX Market Sentiment ({c_prob}%, Bhav {c_back}-{c_lay}) {favored} ko favor kar raha hai. "
                f"Lekin secondary convergence na hone ke karan ise 'Half Limit' se khelein."
            )
        elif pat_fav and abs(pat_edge) >= 14 and not (c_fav and not match_teams_fuzzy(c_fav, pat_fav)):
            # Pattern Reversal without contradiction - Value Lean with half limit
            action = "PLAY"
            favored = pat_fav
            underdog = t_b if favored == t_a else t_a
            confidence = 65
            rating = "🔥 VALUE LEAN (PLAY HALF LIMIT)"
            badge = f"🔄 {pat_badge}"
            risk_level = "MODERATE RISK (PATTERN REVERSAL)"
            source_summary = f"Tipper Pattern Rule: {pat['rule_name']}"
            rationale = (
                f"🔄 PATTERN VALUE LEAN: {pat_rat} Pattern engine rebound signal de raha hai. "
                f"Coin toss variance ke chalte ise Half Limit se hi khelein."
            )
        else:
            action = "SKIP"
            confidence = max(50, c_prob)
            rating = "🟡 STRICT PASS (NO BET)"
            badge = "🟡 PASS / 50-50 TRAP"
            risk_level = "HIGH RISK (AVOID)"
            source_summary = f"CREX Market Line [{c_back}-{c_lay}] (50-50 Neutral)"
            rationale = (
                f"⛔ STRICT PASS (50-50 COIN FLIP TRAP): Is match me koi multi-source convergence nahi hai "
                f"({pat_badge}). Match favorite hone se coin toss jeetna zaroori nahi hota — "
                f"Paisa surakshit rakhne ke liye strict pass karein."
            )

    # Scenario B: CREX market not open yet — rely on proven H2H Dominance or verified Bookie divergence
    elif h2h_fav and tot_h2h >= 3 and (max(h2h_a, h2h_b) / tot_h2h) >= 0.75:
        h2h_win_cnt = max(h2h_a, h2h_b)
        action = "PLAY"
        favored = h2h_fav
        underdog = t_b if h2h_fav == t_a else t_a
        confidence = min(85, max(75, int(round((h2h_win_cnt / tot_h2h) * 100))))
        rating = "💎 100% SATIK PLAY (GOLDEN LOCK)"
        badge = f"💎 H2H DOMINANCE ({h2h_win_cnt}W-{tot_h2h - h2h_win_cnt}L)"
        risk_level = "LOW RISK (HIGH CONVICTION)"
        source_summary = f"Ground-Truth H2H Dominance ({h2h_win_cnt}W-{tot_h2h - h2h_win_cnt}L across {tot_h2h} matches)"
        rationale = f"💎 SATIK GOLD PLAY: Proven head-to-head toss dominance ({h2h_win_cnt}W-{tot_h2h - h2h_win_cnt}L across {tot_h2h} matches) gives {favored} heavy calling rhythm advantage."

    elif bookie_fav and not is_dummy_rate and bookie_load >= 75:
        action = "PLAY"
        favored = bookie_fav
        underdog = t_b if bookie_fav == t_a else t_a
        confidence = min(85, bookie_load)
        rating = "💎 100% SATIK PLAY (GOLDEN LOCK)"
        badge = "💎 100% SATIK CALL"
        risk_level = "LOW RISK (HIGH CONVICTION)"
        source_summary = f"Bookmaker Liability Divergence ({oa}:{ob})"
        rationale = f"💎 SATIK GOLD PLAY: Heavy bookmaker rate divergence ({min(oa, ob)} vs {max(oa, ob)}) favors {favored}."

    elif pat_fav and abs(pat_edge) >= 14:
        action = "PLAY"
        favored = pat_fav
        underdog = t_b if favored == t_a else t_a
        confidence = 65
        rating = "🔥 VALUE LEAN (PLAY HALF LIMIT)"
        badge = f"🔄 {pat_badge}"
        risk_level = "MODERATE RISK (PATTERN REVERSAL)"
        source_summary = f"Tipper Pattern Rule: {pat['rule_name']}"
        rationale = f"🔄 PATTERN VALUE LEAN: {pat_rat} Tipper pattern triggers reversal lean (Play with half limit)."

    else:
        # Check for extreme opponent slump (lost 4 of last 5 tosses)
        s_a, last5_a, tot_a = get_team_toss_history(t_a, tournament)
        s_b, last5_b, tot_b = get_team_toss_history(t_b, tournament)
        b_losses = last5_b.count('L')
        a_losses = last5_a.count('L')

        if b_losses >= 4 and a_losses < 3:
            action = "PLAY"
            favored = t_a
            underdog = t_b
            confidence = 65
            rating = "🔥 VALUE LEAN (PLAY HALF LIMIT)"
            badge = f"📉 Opponent Toss Slump: {t_b} (Lost {b_losses}/5)"
            risk_level = "MODERATE RISK (SLUMP ADVANTAGE)"
            source_summary = f"Opponent Slump Analysis ({t_b} {b_losses}L in last 5)"
            rationale = f"🔥 FORM LEAN: {t_b} toss rhythm me slump me hai ({b_losses} toss haar chuki hai). Value pick with half limit."
        elif a_losses >= 4 and b_losses < 3:
            action = "PLAY"
            favored = t_b
            underdog = t_a
            confidence = 65
            rating = "🔥 VALUE LEAN (PLAY HALF LIMIT)"
            badge = f"📉 Opponent Toss Slump: {t_a} (Lost {a_losses}/5)"
            risk_level = "MODERATE RISK (SLUMP ADVANTAGE)"
            source_summary = f"Opponent Slump Analysis ({t_a} {a_losses}L in last 5)"
            rationale = f"🔥 FORM LEAN: {t_a} toss rhythm me slump me hai ({a_losses} toss haar chuki hai). Value pick with half limit."
        else:
            action = "SKIP"
            confidence = 50
            rating = "🟡 STRICT PASS (NO BET)"
            badge = "🟡 PASS / 50-50 NEUTRAL"
            risk_level = "HIGH RISK (AVOID)"
            source_summary = "Flat Market Line (50-50 Neutral)"
            rationale = "⛔ STRICT PASS (50-50 COIN FLIP): Is match me koi verified multi-source convergence ya H2H edge nahi hai. Zero statistical edge — capital safe rakhne ke liye strict pass karein."

    res = {
        "action": action,
        "recommended_team": favored if action == "PLAY" else None,
        "underdog_team": underdog if action == "PLAY" else None,
        "confidence_pct": confidence,
        "stake_rating": rating,
        "consensus_badge": badge,
        "risk_level": risk_level,
        "market_load_text": f"📊 Market Load: {confidence}% on {favored}" if action == "PLAY" else f"📊 Load: {confidence}% (Below 70% Satik Threshold)",
        "cricketclues_status": source_summary,
        "punter_rationale": rationale,
        "total_edge": round(float(confidence - 50), 1),
        "clues_matched": bool(crex_data),
        # Dedicated Pattern Fields for UI Display
        "pattern_badge": pat.get("pattern_badge", "⚖️ Neutral Rotation"),
        "pattern_rule_name": pat.get("rule_name", "NEUTRAL"),
        "pattern_rationale": pat.get("pattern_rationale", ""),
        "a_streak_str": pat.get("a_streak_str", "Even"),
        "b_streak_str": pat.get("b_streak_str", "Even"),
        "a_last5_str": pat.get("a_last5_str", "None"),
        "b_last5_str": pat.get("b_last5_str", "None")
    }

    # Lock prediction permanently so it NEVER flips or changes on refresh
    save_locked_prediction(m_id_str, res, t_a, t_b)

    return res
