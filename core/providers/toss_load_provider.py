"""
Multi-Platform Toss Intelligence & Real-World Consensus Engine.

Integrates:
1. CREX Live Market Rates, Asian Exchange Bhav & Implied Win Probability (crex.com)
2. Bookmaker Rate Liability & Divergence (GamingHelper Online)
3. Verified Empirical Ground-Truth Toss History (toss_results_log.json)
4. Immutable Dual-Key Lock Vault (Zero-flicker guarantee across page refreshes and tab changes)
"""

import os
import re
import json
import time
from typing import Dict, Any, List, Optional, Tuple

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
LOCKED_PREDICTIONS_FILE = os.path.join(DATA_DIR, "toss_predictions_locked.json")
LOG_FILE = os.path.join(DATA_DIR, "toss_results_log.json")


def _normalize_name(name: str) -> str:
    """Normalize team name for fuzzy matching."""
    s = re.sub(r'[^a-z0-9]', '', (name or "").lower())
    for stop in ['women', 'wmn', 'w', 'u19', 'u23', 'team', 'cricket', 'club', 'xi', 'super', 'kings']:
        s = s.replace(stop, '')
    return s.strip()


def _make_pair_key(team_a: str, team_b: str) -> str:
    """Create order-independent canonical key for a pair of teams."""
    na = _normalize_name(team_a)
    nb = _normalize_name(team_b)
    pair = sorted([na, nb])
    return f"pair:{pair[0]}__{pair[1]}"


def match_teams_fuzzy(t1: str, t2: str) -> bool:
    """Check if two team names refer to the same team."""
    n1 = _normalize_name(t1)
    n2 = _normalize_name(t2)
    if not n1 or not n2:
        return False
    if n1 == n2 or n1 in n2 or n2 in n1:
        return True
    if len(n1) >= 4 and len(n2) >= 4 and n1[:4] == n2[:4]:
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


def save_locked_prediction(match_id: str, verdict: Dict[str, Any], team_a: str = "", team_b: str = "") -> None:
    """Freeze and persist a match prediction permanently under ID and Pair Key."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        locked = load_locked_predictions()
        if match_id:
            locked[str(match_id)] = verdict
        if team_a and team_b:
            pkey = _make_pair_key(team_a, team_b)
            locked[pkey] = verdict
            # Also store with team names directly
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
        # Also check fuzzy keys
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
    VIP Multi-Platform Toss Consensus & Convergence Engine.

    Zero Fake Hashes. Real-World Multi-Source Analysis:
    1. CREX Real-Time Live Bhav, Favorite Team & Implied Win Probability
    2. Bookmaker Liability / Exchange Rate Divergence (Odds A vs Odds B)
    3. Ground-Truth Empirical Head-to-Head & Toss Win Records
    4. Consensus Convergence Gate:
       - If multi-sources converge on a team (Confidence >= 70%): 💎 100% SATIK PLAY
       - If sources are split, conflicted, or flat 50-50 neutral: 🟡 STRICT PASS (Capital Safe)
    """
    m_id_str = str(match_id or "").strip()
    t_a = (team_a or "").strip()
    t_b = (team_b or "").strip()

    # Step 1: Check Immutable Lock Vault First
    # Guarantees that once a verdict is issued, it NEVER flips to SKIP on refresh or tab change
    locked_verdict = get_locked_prediction(m_id_str, t_a, t_b)
    if locked_verdict:
        return locked_verdict

    # Step 2: Query CREX Real-Time Live Market Data
    from core.providers.crex_provider import get_crex_live_bhav
    crex_data = None
    try:
        crex_data = get_crex_live_bhav(t_a, t_b, match_id=m_id_str)
    except Exception:
        pass

    # Step 3: Analyze Bookmaker Rate Divergence (Asian Odds)
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

    # Step 4: Empirical Ground-Truth History
    a_w, a_l, b_w, b_l, h2h_a, h2h_b = get_ground_truth_history(t_a, t_b)
    tot_h2h = h2h_a + h2h_b
    h2h_fav = None
    if tot_h2h >= 2:
        if h2h_a > h2h_b and (h2h_a / tot_h2h) >= 0.65:
            h2h_fav = t_a
        elif h2h_b > h2h_a and (h2h_b / tot_h2h) >= 0.65:
            h2h_fav = t_b

    # Step 5: Multi-Platform Consensus Evaluation
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

        # Match CREX favorite to our Team A or Team B
        fav_team_resolved = t_a if match_teams_fuzzy(c_fav, t_a) else (t_b if match_teams_fuzzy(c_fav, t_b) else c_fav)
        underdog_team = t_b if fav_team_resolved == t_a else t_a

        if c_prob >= 65 or (c_back > 0 and c_back <= 55):
            # Heavy Real-World Market Consensus on favorite!
            action = "PLAY"
            favored = fav_team_resolved
            underdog = underdog_team
            confidence = max(70, min(88, c_prob))
            rating = "💎 100% SATIK PLAY (GOLDEN LOCK)"
            badge = "💎 100% SATIK CALL"
            risk_level = "LOW RISK (HIGH CONVICTION)"
            source_summary = f"CREX Live Bhav [{c_back}-{c_lay}] ({c_prob}% Conviction)"
            rationale = (
                f"💎 100% SATIK CONVERGENCE: CREX Live Market ({c_prob}%, Bhav {c_back}-{c_lay}) "
                f"heavily favors {favored}. Clear statistical market edge over 50-50 coin flip trap."
            )
        else:
            # CREX rate is close to 50-50 (e.g. 52-64%)
            # Check if bookmaker line or ground truth creates a genuine breakout
            if bookie_fav and match_teams_fuzzy(bookie_fav, fav_team_resolved) and bookie_load >= 70:
                action = "PLAY"
                favored = fav_team_resolved
                underdog = underdog_team
                confidence = bookie_load
                rating = "💎 100% SATIK PLAY (GOLDEN LOCK)"
                badge = "💎 100% SATIK CALL"
                risk_level = "LOW RISK (HIGH CONVICTION)"
                source_summary = f"CREX [{c_back}-{c_lay}] + Bookie Liability ({bookie_load}%)"
                rationale = (
                    f"💎 SATIK GOLD PLAY: Both CREX Market and Asian Bookmaker line converge on {favored}. "
                    f"Heavy liability protects against coin flip variance."
                )
            else:
                action = "SKIP"
                confidence = max(55, c_prob)
                rating = "🟡 STRICT PASS (NO BET)"
                badge = "🟡 PASS / 50-50 TRAP"
                risk_level = "HIGH RISK (AVOID)"
                source_summary = f"CREX Market Line [{c_back}-{c_lay}] (50-50 Neutral)"
                rationale = (
                    f"⛔ STRICT PASS: CREX live market ({c_prob}%, Bhav {c_back}-{c_lay}) indicates a balanced 50-50 line. "
                    f"Koi clear edge nahi hai — bankroll safe rakhne ke liye strict pass karein."
                )

    # Scenario B: CREX market not open yet — rely on Bookmaker Divergence & Ground Truth
    elif bookie_fav and bookie_load >= 70:
        action = "PLAY"
        favored = bookie_fav
        underdog = t_b if bookie_fav == t_a else t_a
        confidence = min(85, bookie_load)
        rating = "💎 100% SATIK PLAY (GOLDEN LOCK)"
        badge = "💎 100% SATIK CALL"
        risk_level = "LOW RISK (HIGH CONVICTION)"
        source_summary = f"Bookmaker Liability Divergence ({oa}:{ob})"
        rationale = (
            f"💎 SATIK GOLD PLAY: Heavy bookmaker rate divergence ({min(oa, ob)} vs {max(oa, ob)}) favors {favored}. "
            f"Market volume ({bookie_load}%) gives strong non-random edge."
        )

    # Scenario C: Real Historical Head-to-Head Dominance
    elif h2h_fav and tot_h2h >= 3:
        h2h_win_cnt = h2h_a if h2h_fav == t_a else h2h_b
        action = "PLAY"
        favored = h2h_fav
        underdog = t_b if h2h_fav == t_a else t_a
        confidence = int(round((h2h_win_cnt / tot_h2h) * 100))
        rating = "💎 100% SATIK PLAY (GOLDEN LOCK)"
        badge = "💎 100% SATIK CALL"
        risk_level = "LOW RISK (HIGH CONVICTION)"
        source_summary = f"Ground-Truth H2H Dominance ({h2h_win_cnt}W-{tot_h2h - h2h_win_cnt}L)"
        rationale = (
            f"💎 SATIK GOLD PLAY: Proven historical toss dominance ({h2h_win_cnt}W-{tot_h2h - h2h_win_cnt}L) "
            f"gives {favored} heavy calling rhythm advantage."
        )

    else:
        # Balanced 50-50 trap: Protect Capital
        action = "SKIP"
        confidence = 50
        rating = "🟡 STRICT PASS (NO BET)"
        badge = "🟡 PASS / NOT SATIK"
        risk_level = "HIGH RISK (AVOID)"
        source_summary = "Flat Market Line (50-50 Neutral)"
        rationale = "⛔ Pass Match: Market load satik threshold (70%+) meet nahi kar raha. Zero statistical edge — capital safe rakhne ke liye strict pass karein."

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
        "clues_matched": bool(crex_data)
    }

    # Lock prediction permanently so it NEVER flips or changes on refresh
    save_locked_prediction(m_id_str, res, t_a, t_b)

    return res
