"""Integration adapter with future-toss-predict project.

Fetches the verified real match list date-by-date from d:/xampp/htdocs/future-toss-predict.
Extracts:
- Exact date-wise match schedules
- Scheduled match start time & expected toss time in IST
- Predictions and tipper reports (PLAY / SKIP / COMPLETED)
- Historical toss form (Last 5, Last 10, H2H)
"""

import os
import re
import json
import subprocess
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from core.timezone import now_ist, format_ist, parse_to_ist

FUTURE_TOSS_DIR = r"d:\xampp\htdocs\future-toss-predict"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "future_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def get_available_future_dates() -> List[str]:
    """Return all match dates available in future-toss-predict, ordered:
    1. Today
    2. Upcoming dates (ascending: tomorrow, day after...)
    3. Past dates (descending: yesterday, day before...)
    """
    cur = now_ist()
    today_str = format_ist(cur, "%Y-%m-%d")
    tomorrow_str = format_ist(cur + timedelta(days=1), "%Y-%m-%d")

    dates = set()
    base_data = os.path.join(FUTURE_TOSS_DIR, "data")
    for f_name in ["fixtures.json", "day_log.json"]:
        p = os.path.join(base_data, f_name)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    dates.update(d.keys())
            except Exception:
                pass

    dates.add(today_str)
    dates.add(tomorrow_str)

    future_dates = sorted([d for d in dates if d > today_str])
    past_dates = sorted([d for d in dates if d < today_str], reverse=True)

    return [today_str] + future_dates + past_dates


def _generate_team_short(name: str) -> str:
    """Generate concise team code e.g. India -> IND, Australia -> AUS."""
    if not name:
        return "T"
    known = {
        "afghanistan": "AFG", "bangladesh": "BAN", "australia": "AUS",
        "south africa": "SA", "india": "IND", "england": "ENG",
        "pakistan": "PAK", "new zealand": "NZ", "west indies": "WI",
        "sri lanka": "SL", "namibia": "NAM", "united arab emirates": "UAE",
        "new south wales": "NSW", "tasmania": "TAS", "victoria": "VIC",
        "south australia": "SAU", "western australia": "WA", "queensland": "QLD",
        "india champions": "IND-C", "south africa champions": "SA-C",
        "australia champions": "AUS-C", "west indies champions": "WI-C",
        "england champions": "ENG-C", "pakistan champions": "PAK-C",
        "mpumalanga rhinos": "MPU", "western province": "WP",
        "north west - eastvaal renault dragons": "DRA", "south western districts": "SWD",
        "emirates blues": "EMB", "emirates red": "EMR", "fujairah": "FUJ", "sharjah": "SHJ",
        "andhra": "AND", "karnataka": "KAR", "bengal": "BEN", "delhi": "DEL",
        "bihar": "BIH", "uttar pradesh": "UP", "goa": "GOA", "baroda": "BAR"
    }
    low = name.strip().lower()
    if low in known:
        return known[low]
    words = re.findall(r"[A-Za-z0-9]+", name)
    if len(words) == 1:
        return words[0][:3].upper()
    return "".join(w[0].upper() for w in words[:3])


def _clean_tournament_name(raw_tour: str, team_a: str, team_b: str, fmt: str) -> str:
    """Provide a clean tournament name without raw score strings."""
    low_tour = (raw_tour or "").lower()
    low_a = (team_a or "").lower()
    low_b = (team_b or "").lower()
    combo = f"{low_tour} {low_a} {low_b}"

    if "legend" in combo or "wcl" in combo or "champions" in combo:
        return "World Championship of Legends (WCL)"
    elif any(s in combo for s in ["sheffield", "new south wales", "tasmania", "victoria", "queensland", "western australia", "south australia"]):
        return "Sheffield Shield"
    elif any(s in combo for s in ["ranji", "andhra", "karnataka", "bengal", "delhi", "bihar", "uttar pradesh", "baroda", "goa"]):
        return "Ranji Trophy"
    elif any(s in combo for s in ["emirate", "d10", "d20", "fujairah", "sharjah", "dubai", "abu dhabi"]):
        return "Emirates D10"
    elif any(s in combo for s in ["rhinos", "dragons", "western province", "csa"]):
        return "CSA T20 Challenge"
    elif "cpl" in combo or "caribbean" in combo:
        return "Caribbean Premier League (CPL)"
    elif "punjab" in combo:
        return "Sher-E-Punjab T20"
    elif "odisha" in combo:
        return "Odisha Cricket League"
    elif "upl" in combo or "uttarakhand" in combo:
        return "Uttarakhand Premier League"
    elif "women" in combo:
        return f"Women's International {fmt}"
    elif any(s in low_tour for s in ["/", "&", "*"]) or (" v " in low_tour and len(low_tour) > 20):
        # Likely raw score in tournament string from live RSS
        return f"International {fmt} Series"
    elif raw_tour and raw_tour.strip() != "":
        return raw_tour.strip()
    return f"Cricket Series ({fmt})"


def fetch_matches_for_date(date_str: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Fetch exact match list from future-toss-predict for a specific date.
    Uses local cache to ensure instant sub-second response times.
    """
    cache_file = os.path.join(CACHE_DIR, f"matches_{date_str}.json")

    cur = now_ist()
    today_str = format_ist(cur, "%Y-%m-%d")
    ttl_seconds = 20 if date_str == today_str else 600

    if not force_refresh and os.path.exists(cache_file):
        try:
            mtime = os.path.getmtime(cache_file)
            if (datetime.now().timestamp() - mtime) < ttl_seconds:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass

    # Query future-toss-predict api.php
    api_path = os.path.join(FUTURE_TOSS_DIR, "api.php").replace("\\", "/")
    cmd = [
        "php", "-r",
        f"$_GET['action']='matches'; $_GET['date']='{date_str}'; $_GET['league']='all'; require '{api_path}';"
    ]

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
        stdout_str = res.stdout.decode("utf-8", errors="ignore")
        data = json.loads(stdout_str)
        raw_matches = data.get("matches", [])
    except Exception:
        # Fallback to direct reading from future-toss-predict/data/fixtures.json and day_log.json
        raw_matches = _fallback_read_store(date_str)

    # Normalize matches
    normalized = []
    for m in raw_matches:
        team_a = m.get("teamA") or "Team 1"
        team_b = m.get("teamB") or "Team 2"
        pred = m.get("prediction", {}) or {}
        tipper = pred.get("tipperReport", {}) or {}
        form = m.get("form", {}) or {}
        time_str = m.get("time") or "10:00 AM IST"
        if "IST" not in time_str:
            time_str += " IST"

        # Calculate Toss Time (default 30 mins prior to match time)
        toss_time_str = m.get("tossTime")
        if not toss_time_str:
            try:
                clean_time = time_str.replace("IST", "").strip()
                t_obj = datetime.strptime(clean_time, "%I:%M %p")
                t_toss = t_obj - timedelta(minutes=30)
                toss_time_str = t_toss.strftime("%I:%M %p IST")
            except Exception:
                toss_time_str = "Approx 30m prior"
        elif "IST" not in toss_time_str:
            toss_time_str += " IST"

        # Action / Pick recommendation
        winner = m.get("tossWinner")
        dec = m.get("tossDecision") or "bowl"
        status = m.get("status", "UPCOMING")

        if winner or status == "COMPLETED":
            act = "COMPLETED"
            rec_team = winner or team_a
            prob = 100
            stars = "✅ COMPLETED"
            reason = f"Official toss result: {rec_team} won toss and elected to {dec}."
        elif tipper.get("action") in ("PLAY", "LEAN") and tipper.get("pick"):
            act = "PLAY"
            rec_team = tipper["pick"]
            prob = int(pred.get("favoredProbability") or pred.get("probability") or 62)
            stars = "⭐⭐⭐ HIGH EDGE" if prob >= 60 else "⭐⭐ MEDIUM EDGE"
            reason = tipper.get("headline") or f"Toss trend and indicators favor {rec_team}."
        elif pred.get("favoredWinner") or pred.get("winner"):
            act = "PLAY"
            rec_team = pred.get("favoredWinner") or pred.get("winner")
            prob = int(pred.get("favoredProbability") or pred.get("probability") or 58)
            stars = "⭐⭐ MEDIUM EDGE"
            reason = pred.get("confidence") or f"Toss edge towards {rec_team}."
        else:
            act = "SKIP"
            rec_team = None
            prob = 50
            stars = "⚖️ 50-50"
            reason = pred.get("confidence") or "50-50 neutral coin flip — Pass / No clear edge."

        # Parse match format
        raw_fmt = (m.get("format") or "T20").upper()
        if "TEST" in raw_fmt or "FIRST" in raw_fmt or "SHIELD" in raw_fmt or "RANJI" in raw_fmt:
            m_fmt = "TEST"
        elif "ODI" in raw_fmt or "ONE DAY" in raw_fmt:
            m_fmt = "ODI"
        elif "T10" in raw_fmt:
            m_fmt = "T10"
        else:
            m_fmt = "T20"

        # Clean Tournament Name
        tour_clean = _clean_tournament_name(m.get("tournament") or "", team_a, team_b, m_fmt)

        normalized.append({
            "id": m.get("id") or f"m_{team_a}_{team_b}_{date_str}",
            "team_a_name": team_a,
            "team_a_short": _generate_team_short(team_a),
            "team_b_name": team_b,
            "team_b_short": _generate_team_short(team_b),
            "tournament_name": tour_clean,
            "match_format": m_fmt,
            "scheduled_date_str": date_str,
            "time_str": time_str,
            "toss_time_str": toss_time_str,
            "status": status,
            "toss_winner_name": winner,
            "toss_decision": dec,
            "action_type": act,
            "recommended_team": rec_team,
            "win_prob": prob,
            "confidence_stars": stars,
            "reason": reason,
            "venue": m.get("venue") or "Cricket Ground",
            "a_last5": form.get("aLast5") or "0/0",
            "b_last5": form.get("bLast5") or "0/0",
            "h2h": f"{form.get('h2h', {}).get('teamAWins', 0)} - {form.get('h2h', {}).get('teamBWins', 0)}" if form.get("h2h") else "0 - 0"
        })

    # Save to cache
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(normalized, f, indent=2)
    except Exception:
        pass

    return normalized


def _fallback_read_store(date_str: str) -> List[Dict[str, Any]]:
    """Direct fallback reader for future-toss-predict data files if PHP invocation fails."""
    matches = []
    base_data = os.path.join(FUTURE_TOSS_DIR, "data")

    # Check fixtures.json
    fx_path = os.path.join(base_data, "fixtures.json")
    if os.path.exists(fx_path):
        try:
            with open(fx_path, "r", encoding="utf-8") as f:
                d = json.load(f)
                if date_str in d:
                    matches.extend(d[date_str])
        except Exception:
            pass

    # Check day_log.json
    dl_path = os.path.join(base_data, "day_log.json")
    if os.path.exists(dl_path):
        try:
            with open(dl_path, "r", encoding="utf-8") as f:
                d = json.load(f)
                if date_str in d:
                    for row in d[date_str]:
                        if not any(m.get("id") == row.get("id") for m in matches):
                            matches.append(row)
        except Exception:
            pass

    return matches
