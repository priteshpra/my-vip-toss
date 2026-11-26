"""Multi-Year Historical Toss Archive & Unified Loader (2024–2026+).

Combines:
1. Live & recent verified tosses from data/toss_results_log.json (highest priority)
2. Multi-year Cricsheet archive from data/historical_toss_archive.json (2024–2026)
3. Clean non-synthetic records from local toss_analytics.db (when available)

Strictly filters out synthetic test fixtures (team_*), unresolved outcomes (Pending/Skipped/Neutral),
self-matches (team_a == team_b), and exhibition/Sixes formats, while deduplicating across sources.
"""

import io
import json
import os
import re
import sqlite3
import urllib.request
import zipfile
from typing import Any, Dict, List, Optional, Tuple

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
ROOT_DIR = os.path.dirname(DATA_DIR)
LOG_FILE = os.path.join(DATA_DIR, "toss_results_log.json")
ARCHIVE_FILE = os.path.join(DATA_DIR, "historical_toss_archive.json")
DB_FILE = os.path.join(ROOT_DIR, "toss_analytics.db")

CRICSHEET_ARCHIVE_URLS: Dict[str, str] = {
    "ipl": "https://cricsheet.org/downloads/ipl_json.zip",
    "wpl": "https://cricsheet.org/downloads/wpl_json.zip",
    "bbl": "https://cricsheet.org/downloads/bbl_json.zip",
    "wbbl": "https://cricsheet.org/downloads/wbbl_json.zip",
    "psl": "https://cricsheet.org/downloads/psl_json.zip",
    "cpl": "https://cricsheet.org/downloads/cpl_json.zip",
    "sat": "https://cricsheet.org/downloads/sat_json.zip",
    "bpl": "https://cricsheet.org/downloads/bpl_json.zip",
    "lpl": "https://cricsheet.org/downloads/lpl_json.zip",
    "ssm": "https://cricsheet.org/downloads/ssm_json.zip",
    "hnd": "https://cricsheet.org/downloads/hnd_json.zip",
    "mlc": "https://cricsheet.org/downloads/mlc_json.zip",
    "ntb": "https://cricsheet.org/downloads/ntb_json.zip",
    "csa": "https://cricsheet.org/downloads/csa_json.zip",
    "t20s": "https://cricsheet.org/downloads/t20s_json.zip",
    "odis": "https://cricsheet.org/downloads/odis_json.zip",
    "ilt": "https://cricsheet.org/downloads/ilt_json.zip",
    "sma": "https://cricsheet.org/downloads/sma_json.zip",
}

_UNRESOLVED_TOKENS = ("pending", "skipped", "neutral", "none", "tbd", "null", "undefined")
_EXHIBITION_TOKENS = ("sixes", "6s", "exhibition", "charity")
_MERGED_CACHE: Dict[str, Any] = {"key": None, "data": []}
_BACKTEST_CACHE: Dict[str, Any] = {"key": None, "data": None}


def _norm_pair_token(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower()).strip()


def _is_unresolved_winner(val: Any) -> bool:
    s = str(val or "").strip().lower()
    if not s or s in ("-", "na", "n/a"):
        return True
    return any(tok in s for tok in _UNRESOLVED_TOKENS)


def _is_valid_historical_record(rec: Dict[str, Any]) -> bool:
    """Validate that a record represents a real, resolved cricket match (no test/dummy/exhibition pollution)."""
    ta = str(rec.get("team_a") or "").strip()
    tb = str(rec.get("team_b") or "").strip()
    winner = str(rec.get("actual_winner") or "").strip()
    tour = str(rec.get("tournament") or "").strip().lower()

    if not ta or not tb or _is_unresolved_winner(winner):
        return False
    if ta.lower().startswith("team_") or tb.lower().startswith("team_"):
        return False
    if _norm_pair_token(ta) == _norm_pair_token(tb):
        return False
    if any(tok in tour for tok in _EXHIBITION_TOKENS):
        return False
    if any(tok in ta.lower() or tok in tb.lower() for tok in ("sixes", " 6s")):
        return False
    return True


def _record_dedup_key(rec: Dict[str, Any]) -> Tuple[str, Tuple[str, str]]:
    d = str(rec.get("match_date") or "")[:10].strip()
    na = _norm_pair_token(str(rec.get("team_a") or ""))
    nb = _norm_pair_token(str(rec.get("team_b") or ""))
    pair = (na, nb) if na <= nb else (nb, na)
    return (d, pair)


def _parse_sort_key(rec: Dict[str, Any]) -> str:
    d = str(rec.get("match_date") or "1970-01-01")[:10]
    t = str(rec.get("toss_time_ist") or "").replace(" IST", "").strip()
    if t and ":" in t:
        try:
            from datetime import datetime
            dt = datetime.strptime(f"{d} {t}", "%Y-%m-%d %I:%M %p")
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    return f"{d} 00:00"


def _load_json_list(path: str) -> List[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _load_clean_db_records(db_path: str) -> List[Dict[str, Any]]:
    if not db_path or not os.path.exists(db_path):
        return []
    out: List[Dict[str, Any]] = []
    try:
        conn = sqlite3.connect(db_path, timeout=3.0)
        try:
            rows = conn.execute(
                "SELECT id, match_date, tournament_name, team_a_name, team_b_name, toss_winner_name, toss_decision, match_format "
                "FROM historical_match_statistics"
            ).fetchall()
        finally:
            conn.close()
        for r_id, m_date, tour, ta, tb, winner, dec, fmt in rows:
            rec = {
                "match_id": str(r_id or ""),
                "match_date": str(m_date or "")[:10],
                "tournament": str(tour or ""),
                "team_a": str(ta or ""),
                "team_b": str(tb or ""),
                "actual_winner": str(winner or ""),
                "toss_decision": str(dec or "Bowl"),
                "match_format": str(fmt or "T20"),
                "source": "historical_db",
            }
            if _is_valid_historical_record(rec):
                out.append(rec)
    except Exception:
        return []
    return out


def load_merged_toss_records(
    log_file: Optional[str] = None,
    archive_file: Optional[str] = None,
    db_file: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load deduplicated resolved toss records (newest first) across live log, 2024-2026 archive, and DB."""
    use_default_cache = (log_file is None and archive_file is None and db_file is None)
    lf = log_file if log_file is not None else LOG_FILE
    af = archive_file if archive_file is not None else ARCHIVE_FILE
    df = db_file if db_file is not None else DB_FILE

    if use_default_cache:
        cache_key = tuple(
            os.path.getmtime(p) if (p and os.path.exists(p)) else 0
            for p in (lf, af, df)
        )
        if _MERGED_CACHE["key"] == cache_key and _MERGED_CACHE["data"]:
            return list(_MERGED_CACHE["data"])

    merged: List[Dict[str, Any]] = []
    seen_keys = set()

    def _add_records(recs: List[Dict[str, Any]]) -> None:
        for r in recs:
            if not isinstance(r, dict) or not _is_valid_historical_record(r):
                continue
            key = _record_dedup_key(r)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged.append(r)

    # Priority 1: Live/recent verified log
    _add_records(_load_json_list(lf))
    # Priority 2: Multi-year Cricsheet archive (2024-2026+)
    _add_records(_load_json_list(af))
    # Priority 3: Local SQLite historical_match_statistics (excluding synthetic/corrupt rows)
    _add_records(_load_clean_db_records(df))

    merged.sort(key=_parse_sort_key, reverse=True)

    if use_default_cache:
        _MERGED_CACHE["key"] = cache_key
        _MERGED_CACHE["data"] = list(merged)

    return merged


def seed_archive_from_local_sources(
    archive_file: Optional[str] = None,
    db_file: Optional[str] = None,
) -> int:
    """Export any clean non-synthetic historical records from local DB/cache into historical_toss_archive.json."""
    af = archive_file or ARCHIVE_FILE
    df = db_file or DB_FILE

    existing = _load_json_list(af)
    merged_map: Dict[Tuple[str, Tuple[str, str]], Dict[str, Any]] = {}
    for rec in existing:
        if isinstance(rec, dict) and _is_valid_historical_record(rec):
            merged_map[_record_dedup_key(rec)] = rec

    for rec in _load_clean_db_records(df):
        k = _record_dedup_key(rec)
        if k not in merged_map:
            merged_map[k] = rec

    # Also read any locally cached Cricsheet JSON bundles
    cache_root = os.path.join(DATA_DIR, "cricsheet_cache")
    if os.path.exists(cache_root):
        for root, _, files in os.walk(cache_root):
            for fname in files:
                if not fname.endswith(".json") or fname == "README.txt":
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    parsed = _parse_cricsheet_json(raw, os.path.splitext(fname)[0])
                    if parsed and _is_valid_historical_record(parsed):
                        k = _record_dedup_key(parsed)
                        if k not in merged_map:
                            merged_map[k] = parsed
                except Exception:
                    continue

    out = sorted(merged_map.values(), key=_parse_sort_key, reverse=True)
    os.makedirs(os.path.dirname(af), exist_ok=True)
    with open(af, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    _MERGED_CACHE["key"] = None
    return len(out)


def _parse_cricsheet_json(raw: Dict[str, Any], match_id: str, min_date: str = "2024-01-01") -> Optional[Dict[str, Any]]:
    info = raw.get("info") if isinstance(raw, dict) else None
    if not info:
        return None
    teams = info.get("teams") or []
    if len(teams) < 2:
        return None
    dates = info.get("dates") or []
    d_str = str(dates[0])[:10] if dates else ""
    if not d_str or d_str < min_date:
        return None
    toss = info.get("toss") or {}
    winner = str(toss.get("winner") or "").strip()
    if not winner:
        return None
    event = info.get("event") or {}
    tour = event.get("name") if isinstance(event, dict) else str(event or "")
    gender = str(info.get("gender") or "").lower()
    ta = str(teams[0]).strip()
    tb = str(teams[1]).strip()
    if gender == "female":
        if "women" not in ta.lower():
            ta = f"{ta} Women"
        if "women" not in tb.lower():
            tb = f"{tb} Women"
        if "women" not in winner.lower():
            winner = f"{winner} Women"

    return {
        "match_id": f"cricsheet_{match_id}",
        "match_date": d_str,
        "tournament": str(tour or info.get("match_type") or "Cricket"),
        "team_a": ta,
        "team_b": tb,
        "actual_winner": winner,
        "toss_decision": str(toss.get("decision") or "bat").capitalize(),
        "match_format": str(info.get("match_type") or "T20").upper(),
        "source": "CRICSHEET",
    }


def sync_historical_archive_from_cricsheet(
    min_date: str = "2024-01-01",
    leagues: Optional[List[str]] = None,
    archive_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Download public Cricsheet bundles and merge 2024-2026+ toss outcomes into historical_toss_archive.json."""
    af = archive_file or ARCHIVE_FILE
    target_keys = leagues or list(CRICSHEET_ARCHIVE_URLS.keys())

    existing = _load_json_list(af)
    merged_map: Dict[Tuple[str, Tuple[str, str]], Dict[str, Any]] = {}
    for rec in existing:
        if isinstance(rec, dict) and _is_valid_historical_record(rec):
            merged_map[_record_dedup_key(rec)] = rec

    initial_count = len(merged_map)
    synced_leagues = 0

    for lk in target_keys:
        url = CRICSHEET_ARCHIVE_URLS.get(lk)
        if not url:
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = resp.read()
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                for name in zf.namelist():
                    if not name.endswith(".json"):
                        continue
                    try:
                        raw = json.loads(zf.read(name).decode("utf-8"))
                        parsed = _parse_cricsheet_json(raw, os.path.splitext(os.path.basename(name))[0], min_date=min_date)
                        if parsed and _is_valid_historical_record(parsed):
                            k = _record_dedup_key(parsed)
                            if k not in merged_map:
                                merged_map[k] = parsed
                    except Exception:
                        continue
            synced_leagues += 1
        except Exception:
            continue

    out = sorted(merged_map.values(), key=_parse_sort_key, reverse=True)
    os.makedirs(os.path.dirname(af), exist_ok=True)
    with open(af, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    _MERGED_CACHE["key"] = None

    return {
        "total_records": len(out),
        "added_records": max(0, len(out) - initial_count),
        "synced_leagues": synced_leagues,
    }


def run_empirical_rule_backtest(
    log_file: Optional[str] = None,
    archive_file: Optional[str] = None,
    db_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Chronologically backtest each pattern rule across all merged real matches (zero lookahead)."""
    from collections import defaultdict
    from core.engine.pattern_engine import match_teams

    use_default = (log_file is None and archive_file is None and db_file is None)
    if use_default:
        bt_key = tuple(
            os.path.getmtime(p) if (p and os.path.exists(p)) else 0
            for p in (LOG_FILE, ARCHIVE_FILE, DB_FILE)
        )
        if _BACKTEST_CACHE["key"] == bt_key and _BACKTEST_CACHE["data"] is not None:
            return _BACKTEST_CACHE["data"]

    recs = list(reversed(load_merged_toss_records(log_file=log_file, archive_file=archive_file, db_file=db_file)))
    stats = defaultdict(lambda: [0, 0])
    team_hist: Dict[str, List[int]] = defaultdict(list)
    h2h_hist: Dict[Tuple[str, str], List[str]] = defaultdict(list)

    def _streak(h: List[int]) -> int:
        if len(h) < 3:
            return 0
        last = h[-1]
        c = 0
        for x in reversed(h):
            if x == last:
                c += 1
            else:
                break
        return c if last == 1 else -c

    evaluated = 0
    for r in recs:
        ta = str(r.get("team_a") or "").strip()
        tb = str(r.get("team_b") or "").strip()
        w = str(r.get("actual_winner") or "").strip()
        if not (match_teams(ta, w) or match_teams(tb, w)):
            continue
        evaluated += 1
        w_is_a = match_teams(ta, w)
        ka, kb = _norm_pair_token(ta), _norm_pair_token(tb)
        ha, hb = team_hist[ka], team_hist[kb]
        pk = (ka, kb) if ka <= kb else (kb, ka)
        hh = [1 if x == ka else 0 for x in h2h_hist[pk]]
        sa, sb = _streak(ha), _streak(hb)

        if len(hh) >= 2 and hh[-1] == hh[-2]:
            pred_a = (hh[-1] == 1)
            stats["H2H_DOMINANCE"][0 if (pred_a == w_is_a) else 1] += 1
        elif len(hh) >= 3 and hh[-1] != hh[-2] and hh[-2] != hh[-3]:
            pred_a = (hh[-1] == 0)
            stats["ZIG_ZAG_ALTERNATION"][0 if (pred_a == w_is_a) else 1] += 1
        elif len(ha) >= 3 and len(hb) >= 3:
            if sa <= -3 and sb > -3:
                stats["STREAK_EXHAUSTION (3L)"][0 if w_is_a else 1] += 1
            elif sb <= -3 and sa > -3:
                stats["STREAK_EXHAUSTION (3L)"][0 if not w_is_a else 1] += 1
            elif sa >= 4 and sb >= -1:
                stats["WIN_STREAK_SNAP (4W+)"][0 if not w_is_a else 1] += 1
            elif sb >= 4 and sa >= -1:
                stats["WIN_STREAK_SNAP (4W+)"][0 if w_is_a else 1] += 1
            elif 2 <= sa <= 3 and -2 <= sb <= 0:
                stats["WINNING_MOMENTUM (2-3W)"][0 if w_is_a else 1] += 1
            elif 2 <= sb <= 3 and -2 <= sa <= 0:
                stats["WINNING_MOMENTUM (2-3W)"][0 if not w_is_a else 1] += 1

        team_hist[ka].append(1 if w_is_a else 0)
        team_hist[kb].append(0 if w_is_a else 1)
        h2h_hist[pk].append(ka if w_is_a else kb)

    rows = []
    for rule_name, (wins, losses) in sorted(stats.items()):
        tot = wins + losses
        pct = round((wins / tot) * 100.0, 1) if tot > 0 else 0.0
        rows.append({
            "Rule": rule_name,
            "Wins": wins,
            "Losses": losses,
            "Total": tot,
            "Win %": pct,
        })
    res = {"matches_evaluated": evaluated, "rules": rows}
    if use_default:
        _BACKTEST_CACHE["key"] = bt_key
        _BACKTEST_CACHE["data"] = res
    return res

