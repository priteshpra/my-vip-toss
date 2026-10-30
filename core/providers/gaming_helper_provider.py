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
import hashlib
import ssl
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from core.timezone import now_ist, format_ist
from core.providers.toss_load_provider import compute_multi_source_toss_load

# Robust unverified SSL context to prevent certificate handshake errors on any cloud OS
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

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
        with urllib.request.urlopen(req, timeout=12, context=_SSL_CTX) as resp:
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
    try:
        from core.providers.crex_provider import sync_active_series_history_from_crex
        sync_active_series_history_from_crex()
    except Exception:
        pass

    creds = get_stored_credentials()
    auth_token = creds.get("token")

    def _hydrate_cached(cached_list):
        for m in cached_list:
            mid = str(m.get("id", ""))
            v = compute_multi_source_toss_load(
                team_a=m.get("team_a_name", ""),
                team_b=m.get("team_b_name", ""),
                odds_a=m.get("odds_a", 98),
                odds_b=m.get("odds_b", 80),
                tournament=m.get("tournament_name", ""),
                match_time=m.get("time_str", ""),
                match_id=mid
            )
            m["action_type"] = v["action"]  # 100% PLAY - Zero skips
            m["recommended_team"] = v["recommended_team"]
            m["confidence_pct"] = v["confidence_pct"]
            m["stake_rating"] = v["stake_rating"]
            m["consensus_badge"] = v.get("consensus_badge", "🟢 CONFIRMED LOAD")
            m["risk_level"] = v.get("risk_level", "LOW RISK")
            m["market_load_text"] = v.get("market_load_text", "")
            m["cricketclues_status"] = v.get("cricketclues_status", "")
            m["punter_rationale"] = v["punter_rationale"]
            m["total_edge"] = v.get("total_edge", 0.0)
            m["pattern_badge"] = v.get("pattern_badge", "⚖️ Neutral Toss Rotation")
            m["pattern_rule_name"] = v.get("pattern_rule_name", "NEUTRAL")
            m["pattern_rationale"] = v.get("pattern_rationale", "")
            m["a_streak_str"] = v.get("a_streak_str", "Even")
            m["b_streak_str"] = v.get("b_streak_str", "Even")
            m["a_last5_str"] = v.get("a_last5_str", "None")
            m["b_last5_str"] = v.get("b_last5_str", "None")
            if not m.get("match_date"):
                m["match_date"] = format_ist(now_ist(), "%Y-%m-%d")
        return cached_list

    # If no token or forced refresh, log in first
    if not auth_token or force_refresh:
        login_res = login_gaming_helper(creds.get("phone", DEFAULT_PHONE), creds.get("password", DEFAULT_PASS))
        if login_res.get("success"):
            auth_token = login_res["token"]
        else:
            if os.path.exists(CACHE_FILE):
                try:
                    with open(CACHE_FILE, "r", encoding="utf-8") as f:
                        return {"success": True, "matches": _hydrate_cached(json.load(f)), "cached": True}
                except Exception:
                    pass
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
        with urllib.request.urlopen(req, timeout=12, context=_SSL_CTX) as resp:
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
                    with urllib.request.urlopen(retry_req, timeout=12, context=_SSL_CTX) as retry_resp:
                        data = json.loads(retry_resp.read().decode("utf-8"))
                        matches = parse_gaming_helper_matches(data)
                        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
                        with open(CACHE_FILE, "w", encoding="utf-8") as f:
                            json.dump(matches, f, indent=2)
                        return {"success": True, "matches": matches, "raw": data}
                except Exception:
                    pass
        # Fallback to local cache if network or HTTP error occurs
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return {"success": True, "matches": _hydrate_cached(json.load(f)), "cached": True}
            except Exception:
                pass
        return {"success": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8', errors='ignore')}", "needs_login": e.code in (401, 440)}
    except Exception as e:
        # Fallback to local cache if network blip
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return {"success": True, "matches": _hydrate_cached(json.load(f)), "cached": True}
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
    """Extract clean tournament name from match_name and live provider feeds."""
    name = (match_name or "").strip()

    # 1. First, check if CREX provider has this match with official series name
    try:
        from core.providers.crex_provider import fetch_crex_fixtures, is_match_pair
        fixtures = fetch_crex_fixtures()
        for f in fixtures:
            if is_match_pair(team_a, team_b, f.get("t1_name", ""), f.get("t2_name", "")):
                ser = (f.get("series") or "").strip()
                if ser:
                    return ser
    except Exception:
        pass

    # 2. Dynamic extraction from comma-delimited match_name (e.g. "<Teams>, <Tournament>, <Venue>")
    if "," in name:
        parts = [p.strip() for p in name.split(",") if p.strip()]
        # Filter out trailing venue tags (e.g. "from Sulabiya Ground", "at Melbourne Cricket Ground")
        candidate_parts = [p for p in parts[1:] if not re.search(r"^(from\s+|at\s+|venue\s+)", p, re.IGNORECASE)]
        if candidate_parts:
            if len(candidate_parts) >= 2 and re.match(r"^\d{4}", candidate_parts[1]):
                return f"{candidate_parts[0]}, {candidate_parts[1]}"
            return candidate_parts[0]

    # 3. Known signature mappings for leagues where comma was omitted
    low = name.lower()
    if "sixes" in low:
        return "Hong Kong International Sixes"
    elif "wbbl" in low or "women's big bash" in low or "womens big bash" in low:
        return "Women's Big Bash League"
    elif "bbl" in low or "big bash" in low:
        return "Big Bash League"
    elif "super smash" in low:
        return "Super Smash"
    elif "emirates d10" in low or ("emirates" in low and "d10" in low):
        return "Emirates D10"
    elif "emirates d20" in low or ("emirates" in low and "d20" in low):
        return "Emirates D20 League"
    elif "gt20" in low or "global t20" in low:
        return "Global T20 Canada"
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

    # 4. Strip team names from match_name to extract remaining tournament token
    rem = name
    for t in [team_a, team_b, " vs ", " v ", " - "]:
        rem = re.sub(re.escape(t), " ", rem, flags=re.IGNORECASE)
    rem = re.sub(r"\s+", " ", rem).strip(" ,-")
    if len(rem) >= 3:
        return rem

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
        team_a = item.get("team_one_name") or item.get("team_a_name") or "Team 1"
        team_b = item.get("team_two_name") or item.get("team_b_name") or "Team 2"
        match_name = item.get("match_name") or item.get("match_title") or f"{team_a} vs {team_b}"
        
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

        # Gender / Category normalization: If tournament is a Women's league, guarantee team names have 'Women'
        is_women_tour = bool(re.search(r"\b(women|womens|wbbl|wpl|wt20)\b", tournament, re.IGNORECASE)) or \
                        bool(re.search(r"\b(women|womens|wbbl|wpl|wt20)\b", match_name, re.IGNORECASE))
        if is_women_tour:
            if "women" not in team_a.lower():
                team_a = f"{team_a} Women"
            if "women" not in team_b.lower():
                team_b = f"{team_b} Women"

        mid = str(item.get("id") or item.get("encrypted_id"))

        # VIP Multi-Source Toss Load & Consensus Engine Verdict (Locked per match ID)
        vip_verdict = compute_multi_source_toss_load(
            team_a=team_a,
            team_b=team_b,
            odds_a=odds_a,
            odds_b=odds_b,
            tournament=tournament,
            match_time=time_str,
            match_id=mid
        )

        # Team logos
        logo_a = item.get("team_one_logo")
        logo_b = item.get("team_two_logo")

        # Extract match date
        m_date_val = str(item.get("match_date")).split("T")[0].strip() if item.get("match_date") else format_ist(now_ist(), "%Y-%m-%d")

        normalized.append({
            "id": mid,
            "match_date": m_date_val,
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
            "recommended_team": vip_verdict["recommended_team"],
            "confidence_pct": vip_verdict["confidence_pct"],
            "stake_rating": vip_verdict["stake_rating"],
            "consensus_badge": vip_verdict.get("consensus_badge", "🟢 CONFIRMED LOAD"),
            "risk_level": vip_verdict.get("risk_level", "LOW RISK"),
            "market_load_text": vip_verdict.get("market_load_text", ""),
            "cricketclues_status": vip_verdict.get("cricketclues_status", ""),
            "punter_rationale": vip_verdict["punter_rationale"],
            "total_edge": vip_verdict.get("total_edge", 0.0),
            "clues_matched": vip_verdict.get("clues_matched", False),
            "pattern_badge": vip_verdict.get("pattern_badge", "⚖️ Neutral Toss Rotation"),
            "pattern_rule_name": vip_verdict.get("pattern_rule_name", "NEUTRAL"),
            "pattern_rationale": vip_verdict.get("pattern_rationale", ""),
            "a_streak_str": vip_verdict.get("a_streak_str", "Even"),
            "b_streak_str": vip_verdict.get("b_streak_str", "Even"),
            "a_last5_str": vip_verdict.get("a_last5_str", "None"),
            "b_last5_str": vip_verdict.get("b_last5_str", "None"),
            "is_live": item.get("isLive", 1),
            "status": "UPCOMING" if item.get("status") == 1 else "LIVE",
            "source": "GamingHelper Online"
        })

    # High-Opportunity Engine: Retain all confirmed plays so the user has daily betting options
    return normalized


def compute_vip_punter_verdict(team_a: str, team_b: str, tournament: str, match_time: str, odds_a: int = 98, odds_b: int = 80) -> Dict[str, Any]:
    """VIP High-Precision Multi-Source Toss Load & Consensus Engine."""
    res = compute_multi_source_toss_load(team_a, team_b, odds_a, odds_b, tournament, match_time)
    res["pick"] = res.get("recommended_team")
    res["rationale"] = res.get("punter_rationale")
    return res


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
