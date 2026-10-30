"""
CREX Live Automated Bhav & Win Probability Provider
Fetches live fixtures and real-time market odds from crex.com SSR state.
No external API keys required.
"""

import os
import re
import html
import json
import time
import urllib.request
from typing import List, Dict, Optional, Any

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "crex_live_cache.json")
CACHE_TTL_SECONDS = 60  # Cache for 1 minute to avoid hammering

_MEM_CACHE: Dict[str, Any] = {
    "timestamp": 0,
    "fixtures": []
}

def _word_match(w1: str, w2: str) -> bool:
    if w1 == w2:
        return True
    if len(w1) >= 4 and len(w2) >= 4:
        if w1 in w2 or w2 in w1 or w1[:4] == w2[:4]:
            return True
    return False

def _team_match(t_src: str, t_target: str) -> bool:
    try:
        from core.engine.pattern_engine import match_teams
        if match_teams(t_src, t_target):
            return True
    except Exception:
        pass
    stop_words = {"women", "womens", "wmn", "mens", "the", "and", "team", "club", "cricket", "sixes", "6s", "masters", "legends", "champions"}
    w1 = [w for w in re.findall(r'[a-zA-Z]{3,}', t_src.lower()) if w not in stop_words]
    w2 = [w for w in re.findall(r'[a-zA-Z]{3,}', t_target.lower()) if w not in stop_words]
    for a in w1:
        for b in w2:
            if _word_match(a, b):
                return True
    return False

def is_match_pair(ta: str, tb: str, c1: str, c2: str) -> bool:
    dir_match = _team_match(ta, c1) and _team_match(tb, c2)
    rev_match = _team_match(ta, c2) and _team_match(tb, c1)
    return dir_match or rev_match


def load_crex_cache_from_disk() -> List[Dict]:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data.get("fixtures", [])
                elif isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def save_crex_cache_to_disk(fixtures: List[Dict]) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": time.time(),
                "fixtures": fixtures
            }, f, indent=2)
    except Exception:
        pass


def fetch_crex_fixtures(force_refresh: bool = False) -> List[Dict]:
    """
    Scrapes live fixtures from crex.com/fixtures/match-list
    Returns list of fixture dicts with live odds & team names.
    """
    global _MEM_CACHE
    now = time.time()
    
    if not force_refresh and (now - _MEM_CACHE["timestamp"]) < CACHE_TTL_SECONDS and _MEM_CACHE["fixtures"]:
        return _MEM_CACHE["fixtures"]

    url = "https://crex.com/fixtures/match-list"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })

    try:
        resp = urllib.request.urlopen(req, timeout=10)
        content = resp.read().decode("utf-8", errors="ignore")
        
        m = re.search(r'<script id="app-root-state" type="application/json">(.*?)</script>', content, re.DOTALL)
        if not m:
            cached = load_crex_cache_from_disk()
            if cached:
                _MEM_CACHE["fixtures"] = cached
                _MEM_CACHE["timestamp"] = now
                return cached
            return []

        raw_json = html.unescape(m.group(1).strip().replace("&q;", '"'))
        data = json.loads(raw_json)
        
        fixtures_keys = [k for k in data.keys() if "getFixture" in k]
        if not fixtures_keys:
            return load_crex_cache_from_disk()
            
        raw_fixtures = data[fixtures_keys[0]]
        
        mapping_keys = [k for k in data.keys() if "getHomeMapDatadatewise" in k]
        mapping = data[mapping_keys[0]] if mapping_keys else {}
        teams_dict = {t["f_key"]: t for t in mapping.get("t", []) if "f_key" in t}
        series_dict = {s["f_key"]: s for s in mapping.get("s", []) if "f_key" in s}

        fixtures = []
        for f in raw_fixtures:
            t1_obj = teams_dict.get(f.get("t1f"), {})
            t2_obj = teams_dict.get(f.get("t2f"), {})
            s_obj = series_dict.get(f.get("sf"), {})
            
            t1_name = t1_obj.get("n", f.get("team1") or f.get("t1f") or "")
            t2_name = t2_obj.get("n", f.get("team2") or f.get("t2f") or "")
            t1_sn = t1_obj.get("sn", f.get("t1SName") or "")
            t2_sn = t2_obj.get("sn", f.get("t2SName") or "")
            series_name = s_obj.get("n", f.get("seriesShortName") or f.get("n") or "")
            
            # Parse odds string if present
            odds_raw = f.get("odds")
            odds_dict = {}
            if isinstance(odds_raw, str) and odds_raw.startswith("{"):
                try:
                    odds_dict = json.loads(odds_raw)
                except Exception:
                    pass
            
            g_num = f.get("global_num") or {}
            
            rate_b = odds_dict.get("rate")
            rate_l = odds_dict.get("rate2")
            fav_team = g_num.get("fr") or ""
            
            back = None
            lay = None
            
            if rate_b is not None and rate_b > 0:
                back = int(rate_b)
            elif g_num.get("b"):
                b_dec = float(g_num["b"])
                if b_dec >= 1.0:
                    back = int(round((b_dec - 1.0) * 100))
                    
            if rate_l is not None and rate_l > 0:
                lay = int(rate_l)
            elif g_num.get("l"):
                l_dec = float(g_num["l"])
                if l_dec >= 1.0:
                    lay = int(round((l_dec - 1.0) * 100))

            # Calculate implied win prob
            win_prob = 50
            if back and back > 0:
                win_prob = int(round(100.0 / (1.0 + (back / 100.0))))
            elif g_num.get("b") and float(g_num["b"]) > 1.0:
                win_prob = int(round(100.0 / float(g_num["b"])))

            fixtures.append({
                "t1_name": t1_name,
                "t2_name": t2_name,
                "t1_sn": t1_sn,
                "t2_sn": t2_sn,
                "series": series_name,
                "fav_team": fav_team,
                "back": back,
                "lay": lay,
                "win_prob": win_prob,
                "status_text": f.get("statusText", ""),
                "date": f.get("date", ""),
                "time": f.get("startTimeText", "")
            })

        _MEM_CACHE["fixtures"] = fixtures
        _MEM_CACHE["timestamp"] = now
        save_crex_cache_to_disk(fixtures)
        return fixtures

    except Exception:
        cached = load_crex_cache_from_disk()
        if cached:
            _MEM_CACHE["fixtures"] = cached
            _MEM_CACHE["timestamp"] = now
            return cached
        return []


def get_crex_live_bhav(team_a: str, team_b: str, match_id: Optional[str] = None, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """
    Finds matching live fixture in CREX and returns Bhav rates and Favorite team.
    Returns None if no matching live odds are found.
    """
    fixtures = fetch_crex_fixtures(force_refresh=force_refresh)
    if not fixtures:
        return None

    for cf in fixtures:
        c1 = cf["t1_name"]
        c2 = cf["t2_name"]
        if is_match_pair(team_a, team_b, c1, c2):
            back = cf.get("back")
            lay = cf.get("lay")
            fav = cf.get("fav_team") or ""
            
            # If rate is found on CREX
            if back is not None and lay is not None and back > 0 and lay > 0:
                # Determine short name of favorite team
                fav_short = ""
                if _team_match(fav, c1):
                    fav_short = cf.get("t1_sn") or fav[:3].upper()
                elif _team_match(fav, c2):
                    fav_short = cf.get("t2_sn") or fav[:3].upper()
                else:
                    fav_short = fav[:3].upper()

                return {
                    "matched": True,
                    "back": back,
                    "lay": lay,
                    "fav_team": fav,
                    "fav_short": fav_short,
                    "win_prob": cf.get("win_prob", 50),
                    "crex_t1": c1,
                    "crex_t2": c2,
                    "series": cf.get("series", ""),
                    "source": "CREX Live (Automated)"
                }
    return None

