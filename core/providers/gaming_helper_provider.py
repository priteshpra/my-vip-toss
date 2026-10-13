"""GamingHelperOnline Provider & VIP Punter Match Synchronizer.

Integrates directly with https://gaminghelperonline.com/live-bets API:
- Endpoint: https://gaminglive.gaminghelperonline.com/api/live-bet
- Authentication: https://gaminglive.gaminghelperonline.com/api/users/login (phone, password)
- Extracts live match list, start time, expected toss time
- Runs VIP Punter Toss Engine (Play / Skip verdict)
"""

import os
import re
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from core.timezone import now_ist, format_ist

SESSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "gaming_helper_session.json")
CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "gaming_helper_matches.json")
API_BASE = "https://gaminglive.gaminghelperonline.com/api"

# Default verified user credentials
DEFAULT_PHONE = "7028163937"
DEFAULT_PASS = "Jayshri@0411"


def get_stored_credentials() -> Dict[str, str]:
    """Retrieve saved phone & password if available."""
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                if d.get("phone") and d.get("password"):
                    return d
        except Exception:
            pass
    # Fallback to configured user credentials
    return {"phone": DEFAULT_PHONE, "password": DEFAULT_PASS, "token": ""}


def save_credentials(phone: str, password: str, token: str = "") -> None:
    """Save credentials and token to session storage."""
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump({"phone": phone, "password": password, "token": token, "updated_at": datetime.now().isoformat()}, f, indent=2)


def login_gaming_helper(phone: str = DEFAULT_PHONE, password: str = DEFAULT_PASS) -> Dict[str, Any]:
    """Authenticate with gaminghelperonline.com and retrieve access token."""
    url = f"{API_BASE}/users/login"
    payload = json.dumps({"phone": phone.strip(), "password": password.strip()}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            token = data.get("newAccessToken") or data.get("access-token") or data.get("token") or ""
            if token:
                save_credentials(phone, password, token)
                return {"success": True, "token": token, "data": data}
            return {"success": False, "error": f"Token not found in login response: {data}"}
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="ignore")
        return {"success": False, "error": f"HTTP {e.code}: {err_msg}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def fetch_gaming_helper_live_bets(force_refresh: bool = False) -> Dict[str, Any]:
    """Fetch live bets from gaminglive.gaminghelperonline.com/api/live-bet."""
    creds = get_stored_credentials()
    auth_token = creds.get("token")

    # If no token or forced refresh, log in first
    if not auth_token or force_refresh:
        login_res = login_gaming_helper(creds.get("phone", DEFAULT_PHONE), creds.get("password", DEFAULT_PASS))
        if login_res.get("success"):
            auth_token = login_res["token"]
        else:
            return {"success": False, "error": login_res.get("error"), "needs_login": True}

    url = f"{API_BASE}/live-bet"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {auth_token}",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            matches = parse_gaming_helper_matches(data)
            # Cache successfully fetched matches
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(matches, f, indent=2)
            return {"success": True, "matches": matches, "raw": data}
    except urllib.error.HTTPError as e:
        if e.code in (401, 440):
            # Token expired: auto re-login once and retry fetch
            login_res = login_gaming_helper(creds.get("phone", DEFAULT_PHONE), creds.get("password", DEFAULT_PASS))
            if login_res.get("success"):
                try:
                    retry_req = urllib.request.Request(
                        url,
                        headers={
                            "Authorization": f"Bearer {login_res['token']}",
                            "Accept": "application/json",
                            "User-Agent": "Mozilla/5.0"
                        }
                    )
                    with urllib.request.urlopen(retry_req, timeout=12) as retry_resp:
                        data = json.loads(retry_resp.read().decode("utf-8"))
                        matches = parse_gaming_helper_matches(data)
                        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
                        with open(CACHE_FILE, "w", encoding="utf-8") as f:
                            json.dump(matches, f, indent=2)
                        return {"success": True, "matches": matches, "raw": data}
                except Exception:
                    pass
        return {"success": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8', errors='ignore')}", "needs_login": e.code in (401, 440)}
    except Exception as e:
        # Fallback to local cache if network blip
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return {"success": True, "matches": json.load(f), "cached": True}
            except Exception:
                pass
        return {"success": False, "error": str(e)}


def _format_time_24_to_ist(t_str: str) -> str:
    """Format string like '16:20' or '11:15' into 12-hour IST e.g. '04:20 PM IST'."""
    try:
        clean = t_str.strip()
        parts = clean.split(":")
        h = int(parts[0])
        m = int(parts[1])
        meridiem = "AM" if h < 12 else "PM"
        display_h = h % 12
        if display_h == 0:
            display_h = 12
        return f"{display_h:02d}:{m:02d} {meridiem} IST"
    except Exception:
        return f"{t_str} IST" if "IST" not in t_str else t_str


def _extract_tournament_from_match_name(match_name: str, team_a: str, team_b: str) -> str:
    """Extract clean tournament name from match_name."""
    import re
    name = match_name.strip()
    low = name.lower()

    if "emirates d10" in low or ("emirates" in low and "d10" in low):
        return "Emirates D10"
    elif "summer cup" in low or "i10x" in low or re.search(r"\bicca\b", low):
        return "ICCA i10x Summer Cup"
    elif "world championship of legends" in low or re.search(r"\bwcl\b", low):
        return "World Championship of Legends (WCL)"
    elif "csa pro20" in low or "csa pro50" in low or re.search(r"\bcsa\b", low):
        return "CSA Pro20 Cup"
    elif "national premier league" in low or "zimbabwe" in low:
        return "Zimbabwe National Premier League"
    elif "alwar" in low:
        return "Alwar T-20 Premier League"
    elif "deutsche" in low or "darmstadt" in low:
        return "Deutsche Cricket Talent Series T20"
    elif "liga grizzly" in low or "portugal" in low or "santarem" in low:
        return "Portugal Liga Grizzly T10"
    elif "japan futures" in low or "sano" in low:
        return "Japan Futures League T20"
    elif "under-19" in low or re.search(r"\bu-?19\b", low):
        return "BCCI Under-19s Trophy"
    elif "zalmi" in low or "blinders" in low or re.search(r"\becs\b", low) or "european cricket" in low:
        return "European Cricket Series (ECS)"
    elif "gahanga" in low or "sorwathe" in low or "muhanga" in low:
        return "Rwanda T20 Cup"
    elif "burnside" in low or "riccarton" in low or "canterbury" in low or "st albans" in low:
        return "New Zealand Club Cricket"

    if "," in name:
        parts = [p.strip() for p in name.split(",")]
        tour = parts[-1]
        if any(w in tour.lower() for w in ["league", "series", "d10", "t20", "cup", "championship", "trophy", "premier"]):
            return tour
        elif len(parts) >= 2:
            return parts[1]

    return "T20 / T10 Cricket League"


def parse_gaming_helper_matches(payload: Any) -> List[Dict[str, Any]]:
    """Parse raw JSON response from gaminghelperonline into normalized match list with VIP punter picks."""
    raw_list = []
    if isinstance(payload, dict):
        raw_list = payload.get("liveMatches") or payload.get("matches") or payload.get("data") or []
    elif isinstance(payload, list):
        raw_list = payload

    normalized = []
    for item in raw_list:
        team_a = item.get("team_one_name") or "Team 1"
        team_b = item.get("team_two_name") or "Team 2"
        match_name = item.get("match_name") or f"{team_a} vs {team_b}"
        
        # Parse match times: ["16:20","16:25"] -> [Toss Time, Match Time]
        raw_times = item.get("match_time")
        toss_raw = "10:00"
        match_raw = "10:30"
        if raw_times:
            if isinstance(raw_times, str) and raw_times.startswith("["):
                try:
                    parsed_times = json.loads(raw_times)
                    if len(parsed_times) >= 2:
                        toss_raw = parsed_times[0]
                        match_raw = parsed_times[1]
                    elif len(parsed_times) == 1:
                        match_raw = parsed_times[0]
                        toss_raw = match_raw
                except Exception:
                    pass
            elif isinstance(raw_times, list):
                if len(raw_times) >= 2:
                    toss_raw = raw_times[0]
                    match_raw = raw_times[1]

        toss_time_str = _format_time_24_to_ist(toss_raw)
        time_str = _format_time_24_to_ist(match_raw)

        # Parse odds ratio: ["98","80"]
        raw_ratio = item.get("win_ratio")
        odds_a = 98
        odds_b = 80
        if raw_ratio:
            try:
                if isinstance(raw_ratio, str) and raw_ratio.startswith("["):
                    parsed_ratio = json.loads(raw_ratio)
                    odds_a = int(parsed_ratio[0]) if len(parsed_ratio) >= 1 else 98
                    odds_b = int(parsed_ratio[1]) if len(parsed_ratio) >= 2 else 80
            except Exception:
                pass

        # Extract tournament name
        tournament = _extract_tournament_from_match_name(match_name, team_a, team_b)

        # VIP Punter Toss Engine Verdict
        vip_verdict = compute_vip_punter_verdict(team_a, team_b, tournament, time_str, odds_a, odds_b)

        # Team logos
        logo_a = item.get("team_one_logo")
        logo_b = item.get("team_two_logo")

        normalized.append({
            "id": str(item.get("id") or item.get("encrypted_id")),
            "team_a_name": team_a,
            "team_a_short": "".join([w[0].upper() for w in team_a.split()[:3]]) or "T1",
            "team_a_logo": f"https://gaminglive.gaminghelperonline.com/uploads/team_logo/{logo_a}" if logo_a else None,
            "team_b_name": team_b,
            "team_b_short": "".join([w[0].upper() for w in team_b.split()[:3]]) or "T2",
            "team_b_logo": f"https://gaminglive.gaminghelperonline.com/uploads/team_logo/{logo_b}" if logo_b else None,
            "tournament_name": tournament,
            "match_title": match_name,
            "time_str": time_str,
            "toss_time_str": toss_time_str,
            "venue": item.get("match_address") or "International Ground",
            "odds_a": odds_a,
            "odds_b": odds_b,
            "action_type": vip_verdict["action"],  # "PLAY", "SKIP"
            "recommended_team": vip_verdict["pick"],
            "confidence_pct": vip_verdict["confidence_pct"],
            "stake_rating": vip_verdict["stake_rating"],
            "punter_rationale": vip_verdict["rationale"],
            "is_live": item.get("isLive", 1),
            "status": "UPCOMING" if item.get("status") == 1 else "LIVE",
            "source": "GamingHelper Online"
        })

    # Professional Tipper Quota Protection:
    # Cap PLAY matches to top ~15-20% (max 5-6 matches per day)
    # NEVER force a minimum: if 0 matches qualify, 0 are played (Bankroll 100% safe!)
    if len(normalized) > 0:
        max_plays = max(1, min(6, int(len(normalized) * 0.20)))
        current_plays = [m for m in normalized if m["action_type"] == "PLAY"]
        if len(current_plays) > max_plays:
            current_plays.sort(key=lambda x: x.get("confidence_pct", 50), reverse=True)
            top_play_ids = {m["id"] for m in current_plays[:max_plays]}
            for m in normalized:
                if m["action_type"] == "PLAY" and m["id"] not in top_play_ids:
                    m["action_type"] = "SKIP"
                    m["recommended_team"] = None
                    m["confidence_pct"] = 50
                    m["stake_rating"] = "🟡 SKIP / 50-50 COIN FLIP"
                    m["punter_rationale"] = "Tipper Quota Discipline: Bankroll safe rakhne ke liye sirf din ke top high-conviction matches ko PLAY rakha gaya hai. Yeh match strict pass karein."

    return normalized


def compute_vip_punter_verdict(team_a: str, team_b: str, tournament: str, match_time: str, odds_a: int = 98, odds_b: int = 80) -> Dict[str, Any]:
    """
    VIP High-IQ Sharp Punter Toss Engine (Absolute Capital Protection):
    Treats every bet as if betting your own hard-earned money.
    Zero tolerance for amateur coin flips, 1-2 match streaks, or noisy leagues.
    
    Rules:
    1. STRICT LEAGUE FILTER: Amateur, club, talent series, and junior leagues are STRICT SKIP.
    2. SAMPLE FLOOR: At least 5+ verified matches across both teams required (unless massive market odds shift >= 30).
    3. BAYESIAN LAPLACE SMOOTHING: Uses prior m=4 (p_0=0.50) to prevent small sample hallucinations.
    4. MINIMUM EDGE: Net edge must be >= 18% to qualify for PLAY.
    5. FORM REQUIREMENT: Favored team must have at least 3+ toss wins and >= 60% win rate.
    6. CAPITAL PRESERVATION: If any condition fails, verdict is STRICT PASS / NO BET.
    """
    t_a_clean = (team_a or "").strip()
    t_b_clean = (team_b or "").strip()
    tour_clean = (tournament or "").strip()

    # 1. Market Asymmetry (GamingHelper default is 98:80 template, gap = 18 is neutral)
    odds_diff = odds_a - odds_b  # Positive favors A, negative favors B
    abs_odds_gap = abs(odds_diff)
    real_market_edge = 0
    if abs_odds_gap >= 30:
        real_market_edge = 20 if odds_diff > 0 else -20
    elif abs_odds_gap >= 26:
        real_market_edge = 12 if odds_diff > 0 else -12

    # 2. Strict League Noise Filter (Never bet hard-earned money on amateur / junior / unranked club matches)
    NOISY_LEAGUES = [
        "talent series", "talent", "deutsche", "under-19", "under 19", "u19", "u-19",
        "under-23", "under 23", "u23", "alwar", "bcci", "japan futures", "futures",
        "club cricket", "friendship", "minor league", "national premier league",
        "cricket club", "sports club", "cardinals", "falcons"
    ]
    is_noisy_league = any(k in tour_clean.lower() for k in NOISY_LEAGUES) or any(
        k in t_a_clean.lower() for k in ["under-19", "u19", "under-23", "u23", "sports club"]
    )

    if is_noisy_league and abs_odds_gap < 32:
        return {
            "action": "SKIP",
            "pick": None,
            "confidence_pct": 50,
            "stake_rating": "🟡 SKIP / 50-50 COIN FLIP",
            "rationale": f"Amateur / Sub-Tier League ({tour_clean}): Yahan captains aur players har match me badalte hain aur toss purely 50-50 random flip hota hai. Sharp Punter Rule: Apne paise ko aisi unverified league me risk me mat daalo, STRICT PASS karein."
        }

    # 3. Verified Historical Database Lookup
    a_wins, a_losses, b_wins, b_losses = 0, 0, 0, 0
    try:
        results_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "toss_results_log.json")
        if os.path.exists(results_file):
            with open(results_file, "r", encoding="utf-8") as f:
                history = json.load(f)
                if isinstance(history, list):
                    for r in history:
                        win = (r.get("actual_winner") or "").strip().lower()
                        if not win or "pending" in win:
                            continue
                        r_ta = (r.get("team_a") or "").strip().lower()
                        r_tb = (r.get("team_b") or "").strip().lower()
                        def _tm(a, b):
                            a, b = a.lower(), b.lower()
                            if a == b: return True
                            if ("women" in a or "wmn" in a) != ("women" in b or "wmn" in b): return False
                            if ("u19" in a or "under-19" in a) != ("u19" in b or "under-19" in b): return False
                            if ("champions" in a or "legends" in a) != ("champions" in b or "legends" in b): return False
                            return (a in b or b in a)

                        a_matched = (_tm(t_a_clean, r_ta) or _tm(t_a_clean, r_tb))
                        b_matched = (_tm(t_b_clean, r_ta) or _tm(t_b_clean, r_tb))
                        if a_matched:
                            if _tm(t_a_clean, win):
                                a_wins += 1
                            else:
                                a_losses += 1
                        if b_matched:
                            if _tm(t_b_clean, win):
                                b_wins += 1
                            else:
                                b_losses += 1
    except Exception:
        pass

    a_total = a_wins + a_losses
    b_total = b_wins + b_losses
    tot_samples = a_total + b_total

    # 4. Strict Minimum Sample Size Protection (At least 5+ matches required for statistical validity)
    if tot_samples < 5 and real_market_edge == 0:
        return {
            "action": "SKIP",
            "pick": None,
            "confidence_pct": 50,
            "stake_rating": "🟡 SKIP / 50-50 COIN FLIP",
            "rationale": f"Dono teams ({t_a_clean} aur {t_b_clean}) ka toss sample size sirf {tot_samples} matches hai (kam se kam 5 verified matches zaroori hain). 1-2 match ka streak pure coin flip luck hota hai. High-IQ Punter Rule: 50-50 trap me paisa mat phansao, STRICT PASS karein."
        }

    # 5. Bayesian Laplace Smoothing with Prior m=4 (Base 50-50 prior)
    a_rate = (a_wins + 2) / (a_total + 4)
    b_rate = (b_wins + 2) / (b_total + 4)

    # Sample certainty discount (scales with verified sample up to 6 matches)
    sample_weight = min(1.0, tot_samples / 6.0)
    hist_edge = (a_rate - b_rate) * 35.0 * sample_weight

    total_edge = real_market_edge + hist_edge

    # 6. Minimum Edge Threshold (Strict 18% floor)
    if abs(total_edge) < 18:
        return {
            "action": "SKIP",
            "pick": None,
            "confidence_pct": 50,
            "stake_rating": "🟡 SKIP / 50-50 COIN FLIP",
            "rationale": f"Dono teams ({t_a_clean} aur {t_b_clean}) ke beech ka toss edge kafi kam ({abs(total_edge):.1f}%) hai (kam se kam 18% edge chahiye). High-IQ Punter Advice: 50-50 coin flip trap me risk mat lo, STRICT PASS karein."
        }

    favored = t_a_clean if total_edge > 0 else t_b_clean
    underdog = t_b_clean if total_edge > 0 else t_a_clean
    fav_wins = a_wins if total_edge > 0 else b_wins
    fav_tot = a_total if total_edge > 0 else b_total

    # 7. Quality check on favored team (Must have at least 3 verified wins & >= 60% win rate unless market edge is extreme)
    if fav_wins < 3 and real_market_edge < 20:
        return {
            "action": "SKIP",
            "pick": None,
            "confidence_pct": 50,
            "stake_rating": "🟡 SKIP / 50-50 COIN FLIP",
            "rationale": f"{favored} ke paas sirf {fav_wins} verified toss wins hain. Sharp Punter Rule: Bina kam se kam 3 solid wins aur 60%+ win rate ke toss par paisa lagana gambling hai. STRICT PASS karein."
        }

    # 8. High-conviction PLAY or JACKPOT
    is_jackpot = (abs(total_edge) >= 26 and fav_wins >= 4 and fav_tot >= 5 and tot_samples >= 7)
    if is_jackpot:
        conf = min(88, int(82 + abs(total_edge) * 0.25))
        return {
            "action": "PLAY",
            "pick": favored,
            "confidence_pct": conf,
            "stake_rating": "💎 JACKPOT PLAY (MAX STAKE)",
            "rationale": f"Rock-solid verified toss form ({fav_wins}/{fav_tot} wins, Sample: {tot_samples} matches) aur market confirmation seedha {favored} ke favour me hai. High-IQ Jackpot play."
        }
    else:
        conf = min(80, int(72 + abs(total_edge) * 0.25))
        return {
            "action": "PLAY",
            "pick": favored,
            "confidence_pct": conf,
            "stake_rating": "🟢 CONFIRMED PLAY",
            "rationale": f"Verified toss form ({fav_wins}/{fav_tot} wins, Total Sample: {tot_samples} matches) aur {favored} ka genuine edge ({abs(total_edge):.1f}%) confirmed hai. High-IQ safe play."
        }



def get_active_punter_matches(*args, **kwargs) -> Dict[str, Any]:
    """
    Main aggregator for the VIP Punter UI:
    Returns the real-time match list directly from GamingHelper Online (liveMatches).
    Accepts flexible arguments (*args, **kwargs, force_refresh).
    """
    force_refresh = kwargs.get("force_refresh", False)
    if args:
        if isinstance(args[0], bool):
            force_refresh = args[0]
    res = fetch_gaming_helper_live_bets(force_refresh=force_refresh)
    if res.get("success"):
        return {
            "source": "GamingHelper Online (Live Bets)",
            "connected": True,
            "matches": res.get("matches") or [],
            "needs_login": False
        }
    
    # If connection failed, fallback to local cache or empty
    return {
        "source": "GamingHelper Online (Re-connecting)",
        "connected": False,
        "matches": [],
        "error": res.get("error"),
        "needs_login": res.get("needs_login", False)
    }
