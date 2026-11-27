"""Toss Results & Audit Manager.

Persistently tracks completed tosses, outcomes, and pass/fail statistics:
- Automatically synchronizes closed/completed matches from GamingHelper.
- Strictly tracks real matches from the user's active platform.
- Evaluates recommendations as PASS (Won), FAIL (Missed), or SKIPPED (50-50).
- Honors permanent locked predictions from toss_predictions_locked.json so PLAY calls are NEVER lost.
"""

import os
import re
import json
import time
import base64
import ssl
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from core.timezone import now_ist, format_ist
from core.database.cloud_store import (
    cloud_delete_result,
    cloud_load_all_results,
    cloud_upsert_many_results,
    cloud_upsert_result,
    is_cloud_db_enabled,
)

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

RESULTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "toss_results_log.json")
DEFAULT_RESULTS_FILE = RESULTS_FILE

__all__ = [
    "load_toss_results",
    "save_toss_result",
    "delete_toss_result",
    "get_toss_accuracy_kpis",
    "sync_completed_tosses_from_gaming_helper",
    "auto_update_toss_results_from_telegram",
    "sync_live_toss_results_from_crex",
    "sync_live_toss_results_from_cricbuzz",
    "merge_backup_toss_results",
    "sync_results_file_to_github",
    "is_winner_unresolved",
    "parse_toss_datetime",
]


def _parse_toss_time_key(record: Dict[str, Any]) -> str:
    """Sort key for chronological descending order (latest match first)."""
    t_str = record.get("toss_time_ist", "")
    d_str = record.get("match_date", "1970-01-01")
    try:
        clean_t = t_str.replace(" IST", "").strip()
        dt = datetime.strptime(f"{d_str} {clean_t}", "%Y-%m-%d %I:%M %p")
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return f"{d_str} 00:00"


def parse_toss_datetime(record: Dict[str, Any]) -> Optional[datetime]:
    """Parse match_date and toss_time_ist into an IST datetime object."""
    t_str = record.get("toss_time_ist", "")
    d_str = record.get("match_date", "")
    if not t_str or not d_str:
        return None
    try:
        clean_t = t_str.replace(" IST", "").strip()
        dt = datetime.strptime(f"{d_str} {clean_t}", "%Y-%m-%d %I:%M %p")
        return dt.replace(tzinfo=now_ist().tzinfo)
    except Exception:
        return None


def is_winner_unresolved(val: Any) -> bool:
    """Check if the actual toss winner is still pending, missing, or a placeholder neutral string."""
    s = str(val or "").strip().lower()
    return (
        not s
        or "pending" in s
        or "skipped" in s
        or "neutral" in s
        or s in ["none", "tbd", "-", "null", "undefined"]
    )


def _is_valid_v8_play(rec: Optional[Dict[str, Any]]) -> bool:
    """Return True only for an explicit v8 PLAY verdict with a recommended team."""
    if not isinstance(rec, dict) or not rec.get("recommended_team"):
        return False
    action = rec.get("action_type") or rec.get("action")
    if action != "PLAY":
        return False
    if rec.get("engine_version") == 8:
        return True
    rating = str(rec.get("stake_rating", ""))
    return "VALUE LEAN" in rating or "HIGH-CONVICTION" in rating


_TOSS_RESULTS_CACHE = {"mtime": 0, "cloud_ts": 0.0, "data": []}


def _cloud_sync_active() -> bool:
    """Use Cloud PostgreSQL only for the real default results file (keep tests isolated)."""
    return RESULTS_FILE == DEFAULT_RESULTS_FILE and is_cloud_db_enabled()


def _norm_pair_sig(rec: Dict[str, Any]) -> str:
    d = str(rec.get("match_date") or "").split("T")[0].strip()
    ta = re.sub(r"[^a-z0-9]", "", str(rec.get("team_a") or "").lower())
    tb = re.sub(r"[^a-z0-9]", "", str(rec.get("team_b") or "").lower())
    if not d or not ta or not tb:
        return ""
    p1, p2 = (ta, tb) if ta <= tb else (tb, ta)
    return f"{d}:{p1}__{p2}"


def load_toss_results() -> List[Dict[str, Any]]:
    """Load all recorded completed toss results (Cloud DB + local JSON sync), sorted latest first."""
    global _TOSS_RESULTS_CACHE

    local_rows: List[Dict[str, Any]] = []
    local_mtime = 0.0
    if os.path.exists(RESULTS_FILE):
        try:
            local_mtime = os.path.getmtime(RESULTS_FILE)
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                raw_local = json.load(f)
                if isinstance(raw_local, list):
                    local_rows = [r for r in raw_local if not str(r.get("match_id", "")).startswith("seed_0")]
        except Exception:
            local_rows = []

    if _cloud_sync_active():
        now_ts = time.time()
        if (now_ts - float(_TOSS_RESULTS_CACHE.get("cloud_ts", 0.0))) < 60.0 and _TOSS_RESULTS_CACHE.get("data"):
            return list(_TOSS_RESULTS_CACHE["data"])

        cloud_rows = cloud_load_all_results()
        if cloud_rows is not None:
            cloud_filtered = [r for r in cloud_rows if not str(r.get("match_id", "")).startswith("seed_0")]
            by_id = {str(r.get("match_id", "")).strip(): i for i, r in enumerate(cloud_filtered) if r.get("match_id")}
            by_pair = {_norm_pair_sig(r): i for i, r in enumerate(cloud_filtered) if _norm_pair_sig(r)}

            to_push: List[Dict[str, Any]] = []
            for lr in local_rows:
                lid = str(lr.get("match_id", "")).strip()
                lp = _norm_pair_sig(lr)
                c_idx = by_id.get(lid) if lid in by_id else by_pair.get(lp)
                if c_idx is None:
                    cloud_filtered.append(lr)
                    if lid:
                        by_id[lid] = len(cloud_filtered) - 1
                    if lp:
                        by_pair[lp] = len(cloud_filtered) - 1
                    to_push.append(lr)
                else:
                    cr = cloud_filtered[c_idx]
                    # Only upgrade a pending Cloud row if local JSON actually has a resolved winner
                    if is_winner_unresolved(cr.get("actual_winner")) and not is_winner_unresolved(lr.get("actual_winner")):
                        cloud_filtered[c_idx] = lr
                        to_push.append(lr)

            if to_push:
                cloud_upsert_many_results(to_push)

            cloud_filtered.sort(key=_parse_toss_time_key, reverse=True)

            # Keep local JSON mirror in sync for historical_archive / Win% consumers only when content differs
            cloud_sig = [
                (str(r.get("match_id", "")), r.get("actual_winner"), r.get("outcome"), bool(r.get("is_manual", False)))
                for r in cloud_filtered
            ]
            local_sorted = sorted(local_rows, key=_parse_toss_time_key, reverse=True)
            local_sig = [
                (str(r.get("match_id", "")), r.get("actual_winner"), r.get("outcome"), bool(r.get("is_manual", False)))
                for r in local_sorted
            ]
            if cloud_sig != local_sig:
                try:
                    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
                    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
                        json.dump(cloud_filtered, f, indent=2, ensure_ascii=False)
                    local_mtime = os.path.getmtime(RESULTS_FILE)
                except Exception:
                    pass

            _TOSS_RESULTS_CACHE = {"mtime": local_mtime, "cloud_ts": time.time(), "data": cloud_filtered}
            return list(cloud_filtered)

    if local_rows:
        if _TOSS_RESULTS_CACHE["mtime"] == local_mtime and _TOSS_RESULTS_CACHE["data"]:
            return list(_TOSS_RESULTS_CACHE["data"])
        local_rows.sort(key=_parse_toss_time_key, reverse=True)
        _TOSS_RESULTS_CACHE = {"mtime": local_mtime, "cloud_ts": 0.0, "data": local_rows}
        return list(local_rows)
    return []


def save_toss_result(
    match_id: str,
    match_name: str,
    tournament: str,
    toss_time_ist: str,
    match_date: str,
    team_a: str,
    team_b: str,
    recommended_team: Optional[str],
    stake_rating: str,
    actual_winner: str,
    toss_decision: str = "bowl",
    is_manual: bool = False
) -> Dict[str, Any]:
    """Record a completed toss outcome from the user's actual match list and persist it."""
    results = load_toss_results()

    # Auto-restore locked recommendation if match was originally a verified PLAY call in live matches cache or locked map
    m_id_str = str(match_id or "").strip()
    if not recommended_team and not is_manual:
        live_cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "gaming_helper_matches.json")
        if os.path.exists(live_cache_file):
            try:
                from core.providers.toss_load_provider import match_teams_fuzzy
                with open(live_cache_file, "r", encoding="utf-8") as f:
                    l_cache = json.load(f)
                    for cm in l_cache:
                        cm_t1 = cm.get("team_a_name") or cm.get("team_one_name") or ""
                        cm_t2 = cm.get("team_b_name") or cm.get("team_two_name") or ""
                        cm_id = str(cm.get("id", "")).strip()
                        cm_date = str(cm.get("match_date") or "").split("T")[0].strip()
                        same_date = (not cm_date or not match_date or cm_date == str(match_date)[:10])
                        pair_match = same_date and (
                            (match_teams_fuzzy(team_a, cm_t1) and match_teams_fuzzy(team_b, cm_t2)) or
                            (match_teams_fuzzy(team_a, cm_t2) and match_teams_fuzzy(team_b, cm_t1))
                        )
                        if (m_id_str and cm_id == m_id_str) or pair_match:
                            if _is_valid_v8_play(cm):
                                recommended_team = cm.get("recommended_team")
                                stake_rating = cm.get("stake_rating", "💎 HIGH-CONVICTION GOLDEN PICK")
                                break
            except Exception:
                pass

        if not recommended_team:
            try:
                from core.providers.toss_load_provider import load_locked_predictions, _make_pair_key
                l_map = load_locked_predictions()
                if m_id_str and m_id_str in l_map:
                    l_pred = l_map[m_id_str]
                    if l_pred.get("engine_version") == 8 and l_pred.get("action") == "PLAY" and l_pred.get("recommended_team"):
                        recommended_team = l_pred.get("recommended_team")
                        stake_rating = l_pred.get("stake_rating", "💎 HIGH-CONVICTION GOLDEN PICK")
                elif team_a and team_b:
                    pkey = _make_pair_key(team_a, team_b)
                    dkey = f"{match_date}:{pkey}"
                    if dkey in l_map:
                        l_pred = l_map[dkey]
                        if l_pred.get("engine_version") == 8 and l_pred.get("action") == "PLAY" and l_pred.get("recommended_team"):
                            recommended_team = l_pred.get("recommended_team")
                            stake_rating = l_pred.get("stake_rating", "💎 HIGH-CONVICTION GOLDEN PICK")
            except Exception:
                pass

    # Determine PASS / FAIL / SKIPPED / PENDING outcome
    rec_clean = (recommended_team or "").strip().lower()
    win_clean = (actual_winner or "").strip().lower()
    rating_clean = (stake_rating or "").strip().lower()

    is_skip = bool(not rec_clean or rec_clean in ["none", "skip"] or ("strict pass" in rating_clean and not rec_clean))

    if is_skip:
        outcome = "SKIPPED"
        outcome_label = "🟡 SKIPPED (50-50 NEUTRAL)"
        recommended_team = None
        # Preserve actual toss winner if resolved, otherwise keep as Pending Confirmation (never placeholder neutral)
        if is_winner_unresolved(actual_winner):
            actual_winner = "Pending Confirmation"
    elif is_winner_unresolved(actual_winner):
        outcome = "PENDING"
        outcome_label = "⏳ PENDING (Awaiting Result)"
        actual_winner = "Pending Confirmation"
    elif rec_clean and win_clean and not is_winner_unresolved(actual_winner):
        from core.providers.toss_load_provider import match_teams_fuzzy
        is_won = False
        if rec_clean in win_clean or win_clean in rec_clean:
            is_won = True
        elif match_teams_fuzzy(recommended_team, actual_winner):
            is_won = True

        if is_won:
            outcome = "PASS"
            outcome_label = "✅ PASS (WON)"
        else:
            outcome = "FAIL"
            outcome_label = "❌ FAIL (MISSED)"
    else:
        outcome = "SKIPPED"
        outcome_label = "🟡 SKIPPED (50-50 NEUTRAL)"
        if is_winner_unresolved(actual_winner):
            actual_winner = "Pending Confirmation"

    record = {
        "match_id": m_id_str,
        "match_name": match_name,
        "tournament": tournament,
        "toss_time_ist": toss_time_ist,
        "match_date": match_date,
        "team_a": team_a,
        "team_b": team_b,
        "recommended_team": recommended_team,
        "stake_rating": stake_rating,
        "actual_winner": actual_winner,
        "toss_decision": (toss_decision or "Bowl").capitalize(),
        "outcome": outcome,
        "outcome_label": outcome_label,
        "is_manual": is_manual,
        "recorded_at": format_ist(now_ist(), "%Y-%m-%d %I:%M %p IST")
    }

    # Replace if match already recorded (by ID, exact name, or same date + fuzzy team pair), else prepend
    from core.providers.toss_load_provider import match_teams_fuzzy
    updated = False
    for i, r in enumerate(results):
        r_id = str(r.get("match_id", "")).strip()
        r_date = str(r.get("match_date") or "")[:10]
        r_ta = str(r.get("team_a") or "")
        r_tb = str(r.get("team_b") or "")
        same_pair = bool(
            r_date and match_date and r_date == str(match_date)[:10]
            and (
                (match_teams_fuzzy(team_a, r_ta) and match_teams_fuzzy(team_b, r_tb))
                or (match_teams_fuzzy(team_a, r_tb) and match_teams_fuzzy(team_b, r_ta))
            )
        )
        if r_id == m_id_str or r.get("match_name") == match_name or same_pair:
            # Preserve canonical GamingHelper match_id & pre-match recommendation when resolving an existing pending row
            if same_pair and r_id and not m_id_str == r_id and not is_manual:
                record["match_id"] = r_id
                if r.get("toss_time_ist"):
                    record["toss_time_ist"] = r["toss_time_ist"]
                if not record.get("recommended_team") and r.get("recommended_team"):
                    record["recommended_team"] = r["recommended_team"]
                    record["stake_rating"] = r.get("stake_rating", record["stake_rating"])
                    # Re-evaluate PASS/FAIL against preserved recommendation
                    if not is_winner_unresolved(record["actual_winner"]):
                        is_won = match_teams_fuzzy(record["recommended_team"], record["actual_winner"])
                        record["outcome"] = "PASS" if is_won else "FAIL"
                        record["outcome_label"] = "✅ PASS (WON)" if is_won else "❌ FAIL (MISSED)"
            results[i] = record
            updated = True
            break
    if not updated:
        results.insert(0, record)

    # Sort latest first before writing
    results.sort(key=_parse_toss_time_key, reverse=True)

    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    _TOSS_RESULTS_CACHE["mtime"] = 0
    _TOSS_RESULTS_CACHE["cloud_ts"] = 0.0

    if _cloud_sync_active():
        cloud_upsert_result(record)

    return record


def delete_toss_result(match_id: str) -> bool:
    """Delete a recorded toss result (e.g. if accidentally marked or wrong match)."""
    results = load_toss_results()
    initial_len = len(results)
    results = [r for r in results if str(r.get("match_id")) != str(match_id)]
    if len(results) < initial_len:
        os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        _TOSS_RESULTS_CACHE["mtime"] = 0
        _TOSS_RESULTS_CACHE["cloud_ts"] = 0.0
        if _cloud_sync_active():
            cloud_delete_result(str(match_id))
        return True
    return False


def sync_completed_tosses_from_gaming_helper(run_resolvers: bool = True) -> List[Dict[str, Any]]:
    """
    Automatically pull completed/closed matches from GamingHelper.
    Preserves locked pre-match PLAY recommendations so verified calls are never downgraded to SKIP.
    """
    from core.providers.gaming_helper_provider import (
        get_stored_credentials,
        login_gaming_helper,
        DEFAULT_PHONE,
        DEFAULT_PASS,
        _format_time_24_to_ist,
        _extract_tournament_from_match_name,
        API_BASE
    )
    from core.providers.toss_load_provider import (
        load_locked_predictions,
        get_locked_prediction,
        compute_multi_source_toss_load,
    )

    creds = get_stored_credentials()
    token = creds.get("token")
    if not token:
        login_res = login_gaming_helper(DEFAULT_PHONE, DEFAULT_PASS)
        token = login_res.get("token")
        if not token:
            return load_toss_results()

    # Paginate through recent closed matches; stop early once an entire page is already confirmed
    raw_closed = []
    existing_results = load_toss_results()
    existing_map = {str(r.get("match_id")): r for r in existing_results}
    page = 1
    while page <= 5:
        url = f"{API_BASE}/close-bet?page={page}&perPage=100"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                batch = data.get("data") or []
                if not batch:
                    break
                raw_closed.extend(batch)
                # Stop paginating once every match on this page is already resolved in our log
                all_resolved_on_page = all(
                    str(item.get("id")) in existing_map
                    and not is_winner_unresolved(existing_map[str(item.get("id"))].get("actual_winner"))
                    for item in batch
                )
                total_pages = data.get("totalPages", 1)
                if all_resolved_on_page or page >= total_pages:
                    break
                page += 1
        except urllib.error.HTTPError as he:
            if he.code == 440:
                login_res = login_gaming_helper(DEFAULT_PHONE, DEFAULT_PASS)
                token = login_res.get("token")
                if not token:
                    break
                continue
            break
        except Exception:
            break

    today_str = format_ist(now_ist(), "%Y-%m-%d")
    locked_map = load_locked_predictions()

    # Process all closed matches from API
    for m in (raw_closed or []):
        m_id = str(m.get("id"))
        m_name = m.get("match_name") or ""
        t1 = m.get("team_one_name") or ""
        t2 = m.get("team_two_name") or ""
        if not t1 or not t2:
            continue

        # If match is already recorded in log (resolved OR already initialized as Pending), don't rewrite on every sync
        if m_id in existing_map:
            continue

        raw_time = m.get("match_time") or '["10:00","10:30"]'
        toss_raw = "10:00"
        match_raw = "10:30"
        try:
            if isinstance(raw_time, str) and raw_time.startswith("["):
                p_times = json.loads(raw_time)
                toss_raw = p_times[0]
                match_raw = p_times[1] if len(p_times) > 1 else p_times[0]
        except Exception:
            pass

        toss_ist = _format_time_24_to_ist(toss_raw)
        tour = _extract_tournament_from_match_name(m_name, t1, t2)

        # Gender / Category normalization: If tournament is a Women's league, guarantee team names have 'Women'
        is_women_tour = bool(re.search(r"\b(women|womens|wbbl|wpl|wt20)\b", tour, re.IGNORECASE)) or \
                        bool(re.search(r"\b(women|womens|wbbl|wpl|wt20)\b", m_name, re.IGNORECASE))
        if is_women_tour:
            if "women" not in t1.lower():
                t1 = f"{t1} Women"
            if "women" not in t2.lower():
                t2 = f"{t2} Women"

        # Parse odds
        raw_ratio = m.get("win_ratio")
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

        # Extract actual match date from API record
        m_date_raw = m.get("match_date")
        if m_date_raw:
            m_date = str(m_date_raw).split("T")[0].strip()
        else:
            m_date = today_str

        # Recommendation & Verdict Resolution:
        # Guarantee 100% strict consistency with what the user saw in Live Toss!
        rec_team = None
        rating = "🟡 SKIP / 50-50 COIN FLIP"
        found_verdict = False

        # Priority 1: Check Live Matches Cache (The EXACT verdict shown to the user in Tab 1 Live Toss)
        live_cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "gaming_helper_matches.json")
        if os.path.exists(live_cache_file):
            try:
                from core.providers.toss_load_provider import match_teams_fuzzy
                with open(live_cache_file, "r", encoding="utf-8") as f:
                    live_cache = json.load(f)
                    for cm in live_cache:
                        cm_id = str(cm.get("id", "")).strip()
                        cm_date = str(cm.get("match_date") or "").split("T")[0].strip()
                        id_matches = bool(cm_id and cm_id == m_id)
                        cm_t1 = cm.get("team_a_name") or cm.get("team_one_name") or ""
                        cm_t2 = cm.get("team_b_name") or cm.get("team_two_name") or ""
                        pair_matches = (
                            (match_teams_fuzzy(t1, cm_t1) and match_teams_fuzzy(t2, cm_t2)) or
                            (match_teams_fuzzy(t1, cm_t2) and match_teams_fuzzy(t2, cm_t1))
                        ) and (not cm_date or not m_date or cm_date == m_date)

                        if id_matches or pair_matches:
                            if _is_valid_v8_play(cm):
                                rec_team = cm.get("recommended_team")
                                rating = cm.get("stake_rating", "💎 VIP HIGH LOAD PLAY")
                            else:
                                rec_team = None
                                rating = "🟡 SKIP / 50-50 COIN FLIP"
                            found_verdict = True
                            break
            except Exception:
                pass

        # Priority 2: Check frozen locked prediction strictly for this match_id or this date
        if not found_verdict:
            l_pred = get_locked_prediction(m_id, t1, t2, match_date=m_date)
            if l_pred:
                if _is_valid_v8_play(l_pred):
                    rec_team = l_pred.get("recommended_team")
                    rating = l_pred.get("stake_rating", "💎 VIP HIGH LOAD PLAY")
                else:
                    rec_team = None
                    rating = "🟡 SKIP / 50-50 COIN FLIP"
                found_verdict = True

        # Never fabricate a post-match PLAY call after betting closes:
        # if not pre-locked as PLAY in Tab 1 / locked vault, record strictly as SKIPPED.
        if not found_verdict:
            rec_team = None
            rating = "🟡 SKIP / 50-50 COIN FLIP"

        # STRICT TIME GATE: Do NOT record a match from close-bet if its toss time is in the future!
        # GamingHelper closes betting 10-15 min before toss, but the toss hasn't happened yet!
        toss_dt = None
        try:
            clean_t = toss_ist.replace(" IST", "").strip()
            toss_dt = datetime.strptime(f"{m_date} {clean_t}", "%Y-%m-%d %I:%M %p").replace(tzinfo=now_ist().tzinfo)
        except Exception:
            pass

        if toss_dt and toss_dt > now_ist():
            # Scheduled toss is in the future; cannot be recorded as completed yet
            continue

        # Initialize as Pending Confirmation (bulk auto-resolvers Cricbuzz/Telegram run right after the loop)
        win_init = "Pending Confirmation"
        dec_init = "Pending"

        save_toss_result(
            match_id=m_id,
            match_name=f"{t1} vs {t2}",
            tournament=tour,
            toss_time_ist=toss_ist,
            match_date=m_date,
            team_a=t1,
            team_b=t2,
            recommended_team=rec_team,
            stake_rating=rating,
            actual_winner=win_init,
            toss_decision=dec_init,
            is_manual=False
        )

    if run_resolvers:
        # Automatically resolve freshly closed matches using Telegram, Cricbuzz & CREX
        try:
            auto_update_toss_results_from_telegram()
        except Exception as e:
            print(f"Error in auto_update_toss_results_from_telegram: {e}")

        try:
            sync_live_toss_results_from_cricbuzz()
        except Exception as e:
            print(f"Error in sync_live_toss_results_from_cricbuzz: {e}")

        try:
            sync_live_toss_results_from_crex()
        except Exception as e:
            print(f"Error in sync_live_toss_results_from_crex: {e}")

    return load_toss_results()


def sync_live_toss_results_from_cricbuzz() -> int:
    """
    Automatically resolve live & completed match tosses directly from Cricbuzz.
    Matches team names against pending or today's matches and resolves actual toss winners.
    """
    try:
        from core.providers.cricbuzz_toss_provider import fetch_cricbuzz_toss_outcomes
        from core.providers.toss_load_provider import match_teams_fuzzy, load_locked_predictions

        c_tosses = fetch_cricbuzz_toss_outcomes(force_refresh=True)
        if not c_tosses:
            return 0

        results = load_toss_results()
        locked_map = load_locked_predictions()
        resolved_count = 0

        # Auto-resolve unresolved matches in results log (both played and skipped)
        for r in results:
            if not is_winner_unresolved(r.get("actual_winner")):
                continue

            m_id = str(r.get("match_id", ""))
            t1 = r.get("team_a") or ""
            t2 = r.get("team_b") or ""
            rec = r.get("recommended_team")
            rating = r.get("stake_rating", "NORMAL")

            for ct in c_tosses:
                ca = ct.get("team_a", "")
                cb = ct.get("team_b", "")
                winner_raw = ct.get("winner", "")
                decision = ct.get("decision", "Bowl")

                if ca and cb:
                    matched = (
                        (match_teams_fuzzy(t1, ca) and match_teams_fuzzy(t2, cb)) or
                        (match_teams_fuzzy(t1, cb) and match_teams_fuzzy(t2, ca))
                    )
                else:
                    matched = bool(winner_raw and (match_teams_fuzzy(t1, winner_raw) or match_teams_fuzzy(t2, winner_raw)))

                if matched:
                    actual_win = t1 if match_teams_fuzzy(winner_raw, t1) else (t2 if match_teams_fuzzy(winner_raw, t2) else winner_raw)
                    save_toss_result(
                        match_id=m_id,
                        match_name=r.get("match_name"),
                        tournament=r.get("tournament", "General"),
                        toss_time_ist=r.get("toss_time_ist", ""),
                        match_date=r.get("match_date", ""),
                        team_a=t1,
                        team_b=t2,
                        recommended_team=rec,
                        stake_rating=rating,
                        actual_winner=actual_win,
                        toss_decision=decision,
                        is_manual=False
                    )
                    resolved_count += 1
                    break

        # Also resolve today's active matches whose toss just completed on Cricbuzz
        today_str = format_ist(now_ist(), "%Y-%m-%d")
        existing_resolved_ids = {str(r.get("match_id")) for r in load_toss_results() if not is_winner_unresolved(r.get("actual_winner"))}

        # 1. Check live active matches cache
        live_cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "gaming_helper_matches.json")
        active_live_matches = []
        if os.path.exists(live_cache_file):
            try:
                with open(live_cache_file, "r", encoding="utf-8") as f:
                    active_live_matches = json.load(f)
            except Exception:
                pass

        for m in active_live_matches:
            m_id = str(m.get("id") or "").strip()
            if not m_id or m_id in existing_resolved_ids:
                continue
            m_date = str(m.get("match_date", "")).split("T")[0]
            if m_date and m_date != today_str:
                continue

            l_ta = str(m.get("team_a_name") or m.get("team_a") or "").strip()
            l_tb = str(m.get("team_b_name") or m.get("team_b") or "").strip()
            if not l_ta or not l_tb:
                continue

            for ct in c_tosses:
                ca = ct.get("team_a", "")
                cb = ct.get("team_b", "")
                winner_raw = ct.get("winner", "")
                decision = ct.get("decision", "Bowl")

                matched = (
                    (match_teams_fuzzy(l_ta, ca) and match_teams_fuzzy(l_tb, cb)) or
                    (match_teams_fuzzy(l_ta, cb) and match_teams_fuzzy(l_tb, ca))
                )
                if matched:
                    actual_win = l_ta if match_teams_fuzzy(winner_raw, l_ta) else (l_tb if match_teams_fuzzy(winner_raw, l_tb) else winner_raw)
                    rec = m.get("recommended_team") if _is_valid_v8_play(m) else None
                    rating = m.get("stake_rating", "🟡 SKIP / 50-50 COIN FLIP") if rec else "🟡 SKIP / 50-50 COIN FLIP"
                    save_toss_result(
                        match_id=m_id,
                        match_name=f"{l_ta} vs {l_tb}",
                        tournament=m.get("tournament_name") or m.get("tournament") or "General",
                        toss_time_ist=m.get("toss_time_str") or m.get("time_str") or format_ist(now_ist(), "%I:%M %p IST"),
                        match_date=today_str,
                        team_a=l_ta,
                        team_b=l_tb,
                        recommended_team=rec,
                        stake_rating=rating,
                        actual_winner=actual_win,
                        toss_decision=decision,
                        is_manual=False
                    )
                    existing_resolved_ids.add(m_id)
                    resolved_count += 1
                    break

        # 2. Check locked_map for any matches with team_a and team_b defined
        for lid, lp in locked_map.items():
            m_id = str(lid).strip()
            if not m_id or m_id in existing_resolved_ids:
                continue
            if m_id.startswith("pair:") or "__vs__" in m_id:
                continue
            l_date = str(lp.get("match_date", "")).split("T")[0]
            if l_date and l_date != today_str:
                continue

            l_ta = str(lp.get("team_a_name") or lp.get("team_a") or "").strip()
            l_tb = str(lp.get("team_b_name") or lp.get("team_b") or "").strip()
            if not l_ta or not l_tb:
                continue

            for ct in c_tosses:
                ca = ct.get("team_a", "")
                cb = ct.get("team_b", "")
                winner_raw = ct.get("winner", "")
                decision = ct.get("decision", "Bowl")

                matched = (
                    (match_teams_fuzzy(l_ta, ca) and match_teams_fuzzy(l_tb, cb)) or
                    (match_teams_fuzzy(l_ta, cb) and match_teams_fuzzy(l_tb, ca))
                )
                if matched:
                    actual_win = l_ta if match_teams_fuzzy(winner_raw, l_ta) else (l_tb if match_teams_fuzzy(winner_raw, l_tb) else winner_raw)
                    rec = lp.get("recommended_team") if (lp.get("engine_version") == 8 and lp.get("action") == "PLAY") else None
                    rating = lp.get("stake_rating", "🟡 SKIP / 50-50 COIN FLIP") if rec else "🟡 SKIP / 50-50 COIN FLIP"
                    save_toss_result(
                        match_id=str(lid),
                        match_name=f"{l_ta} vs {l_tb}",
                        tournament=lp.get("tournament", "General"),
                        toss_time_ist=lp.get("time_str", format_ist(now_ist(), "%I:%M %p IST")),
                        match_date=today_str,
                        team_a=l_ta,
                        team_b=l_tb,
                        recommended_team=rec,
                        stake_rating=rating,
                        actual_winner=actual_win,
                        toss_decision=decision,
                        is_manual=False
                    )
                    existing_resolved_ids.add(m_id)
                    resolved_count += 1
                    break

        return resolved_count
    except Exception as e:
        print(f"[CricbuzzSyncError] {e}")
        return 0


def auto_update_toss_results_from_telegram() -> int:
    """
    Automatically resolve Pending Confirmation matches using live toss announcements
    from public Telegram channel @KINGTOSSLINE111.
    """
    from core.providers.telegram_toss_feed import fetch_telegram_toss_feed, match_team_with_tg_winner
    from core.providers.toss_load_provider import load_locked_predictions

    feed = fetch_telegram_toss_feed(pages=5)
    if not feed:
        return 0

    results = load_toss_results()
    locked_map = load_locked_predictions()
    resolved_count = 0
    cur_time = now_ist()

    for r in results:
        # Resolve all unresolved matches (both played and skipped)
        if not is_winner_unresolved(r.get("actual_winner")):
            continue

        # STRICT TIME GATE: Cannot resolve a match whose toss time is still in the future!
        toss_dt = parse_toss_datetime(r)
        if toss_dt and toss_dt > cur_time:
            continue

        m_id = str(r.get("match_id", ""))
        t1 = r.get("team_a") or ""
        t2 = r.get("team_b") or ""
        rec = r.get("recommended_team")
        rating = r.get("stake_rating", "NORMAL")

        # Restore recommendation ONLY if locked under current v8 engine as PLAY
        if not rec and m_id in locked_map:
            l_pred = locked_map[m_id]
            if l_pred.get("engine_version") == 8 and l_pred.get("action") == "PLAY" and l_pred.get("recommended_team"):
                rec = l_pred.get("recommended_team")
                rating = l_pred.get("stake_rating", "💎 HIGH-CONVICTION GOLDEN PICK (TOP 3-4)")

        for tg in feed:
            w_raw = tg.get("winner_raw", "")
            decision = tg.get("decision", "Bowl")
            post_iso = tg.get("post_iso", "")

            # If message timestamp is available, verify it was posted in the valid window of this match
            if post_iso and toss_dt:
                try:
                    p_dt = datetime.fromisoformat(post_iso.replace("Z", "+00:00"))
                    p_ist = p_dt.astimezone(cur_time.tzinfo)
                    # Window: within [-30 min, +8 hours] of match toss time
                    min_allowed = toss_dt - timedelta(minutes=30)
                    max_allowed = toss_dt + timedelta(hours=8)
                    if not (min_allowed <= p_ist <= max_allowed):
                        continue
                except Exception:
                    pass

            winner_name = None
            if match_team_with_tg_winner(t1, w_raw):
                winner_name = t1
            elif match_team_with_tg_winner(t2, w_raw):
                winner_name = t2

            if winner_name:
                save_toss_result(
                    match_id=m_id,
                    match_name=r.get("match_name"),
                    tournament=r.get("tournament", "General"),
                    toss_time_ist=r.get("toss_time_ist", ""),
                    match_date=r.get("match_date", ""),
                    team_a=t1,
                    team_b=t2,
                    recommended_team=rec,
                    stake_rating=rating,
                    actual_winner=winner_name,
                    toss_decision=decision,
                    is_manual=False
                )
                resolved_count += 1
                break

    return resolved_count


def reconcile_pending_from_resolved() -> int:
    """Resolve any Pending Confirmation rows using already-resolved duplicate rows on the same date."""
    from core.providers.toss_load_provider import match_teams_fuzzy
    results = load_toss_results()
    if not results:
        return 0

    resolved_rows = [r for r in results if not is_winner_unresolved(r.get("actual_winner"))]
    fixed = 0
    drop_indices = set()

    for idx, p in enumerate(results):
        if not is_winner_unresolved(p.get("actual_winner")):
            continue
        p_date = str(p.get("match_date") or "")[:10]
        p_ta = str(p.get("team_a") or "")
        p_tb = str(p.get("team_b") or "")
        for r_idx, r in enumerate(resolved_rows):
            r_date = str(r.get("match_date") or "")[:10]
            if not p_date or p_date != r_date:
                continue
            r_ta = str(r.get("team_a") or "")
            r_tb = str(r.get("team_b") or "")
            if (
                (match_teams_fuzzy(p_ta, r_ta) and match_teams_fuzzy(p_tb, r_tb))
                or (match_teams_fuzzy(p_ta, r_tb) and match_teams_fuzzy(p_tb, r_ta))
            ):
                winner = str(r.get("actual_winner") or "")
                mapped_winner = p_ta if match_teams_fuzzy(winner, p_ta) else (p_tb if match_teams_fuzzy(winner, p_tb) else winner)
                p["actual_winner"] = mapped_winner
                p["toss_decision"] = r.get("toss_decision") or p.get("toss_decision") or "Bowl"
                rec = p.get("recommended_team")
                if rec:
                    is_won = match_teams_fuzzy(rec, mapped_winner)
                    p["outcome"] = "PASS" if is_won else "FAIL"
                    p["outcome_label"] = "✅ PASS (WON)" if is_won else "❌ FAIL (MISSED)"
                else:
                    p["outcome"] = "SKIPPED"
                    p["outcome_label"] = "🟡 SKIPPED (50-50 NEUTRAL)"
                # Mark synthetic crex_*/dup row for removal when GamingHelper row is updated
                for orig_i, orig_r in enumerate(results):
                    if orig_i != idx and orig_r is r and str(orig_r.get("match_id", "")).startswith("crex_"):
                        drop_indices.add(orig_i)
                fixed += 1
                break

    if fixed > 0:
        cleaned = [r for i, r in enumerate(results) if i not in drop_indices]
        cleaned.sort(key=_parse_toss_time_key, reverse=True)
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2)
        _TOSS_RESULTS_CACHE["mtime"] = 0

    return fixed


def sync_live_toss_results_from_crex() -> int:
    """
    Automatically resolve live & completed matches directly from CREX live feed & series pages.
    Uses date-aware deduplication and concurrent match-page fetching so pending matches resolve fast.
    """
    import html
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from core.providers.toss_load_provider import load_locked_predictions, match_teams_fuzzy
    from core.providers.crex_provider import is_match_pair, sync_active_series_history_from_crex

    resolved_count = reconcile_pending_from_resolved()

    url = "https://crex.com/fixtures/match-list"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    fixtures = []
    try:
        with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r'<script id="app-root-state" type="application/json">(.*?)</script>', content, re.DOTALL)
        if m:
            raw_json = html.unescape(m.group(1).strip().replace("&q;", '"'))
            data = json.loads(raw_json)
            fixtures = data.get("https://stats.crickapi.com/fixture/getFixture", [])
    except Exception:
        fixtures = []

    existing_results = load_toss_results()
    locked_map = load_locked_predictions()
    ist_tz = now_ist().tzinfo
    today_str = format_ist(now_ist(), "%Y-%m-%d")
    yesterday_str = format_ist(now_ist() - timedelta(days=1), "%Y-%m-%d")

    def _fix_date(fix_item: Dict[str, Any]) -> str:
        t_ms = fix_item.get("t")
        if t_ms:
            try:
                from datetime import timezone
                dt = datetime.fromtimestamp(float(t_ms) / 1000.0, tz=timezone.utc).astimezone(ist_tz)
                return dt.strftime("%Y-%m-%d")
            except Exception:
                pass
        return today_str

    # Collect candidates that are Live/Completed and NOT already resolved for that date
    candidates = []
    for fix in fixtures:
        if fix.get("status") not in [1, 2]:
            continue
        t1 = fix.get("team1")
        t2 = fix.get("team2")
        link = fix.get("link")
        if not link or not t1 or not t2:
            continue
        f_date = _fix_date(fix)

        already_resolved = False
        for ex in existing_results:
            ex_date = str(ex.get("match_date") or "")[:10]
            if ex_date and ex_date != f_date:
                continue
            if not is_winner_unresolved(ex.get("actual_winner")):
                if is_match_pair(t1, t2, ex.get("team_a", ""), ex.get("team_b", "")):
                    already_resolved = True
                    break
        if not already_resolved:
            candidates.append((fix, f_date))

    toss_comment_re = re.compile(
        r'([A-Za-z0-9\s\.\-&]+?)\s+(?:won\s+the\s+toss\s+and\s+)?(?:opt|opted|elected|chose|decided)\s+to\s+(bat|bowl|field)',
        re.IGNORECASE,
    )

    def _scrape_crex_match(item_tuple):
        fix, f_date = item_tuple
        t1 = fix.get("team1")
        t2 = fix.get("team2")
        link = fix.get("link")
        match_url = "https://crex.com" + link
        try:
            m_req = urllib.request.Request(match_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(m_req, timeout=6, context=_SSL_CTX) as m_resp:
                m_content = m_resp.read().decode("utf-8", errors="ignore")
            mm = re.search(r'<script id="app-root-state" type="application/json">(.*?)</script>', m_content, re.DOTALL)
            if not mm:
                return None
            m_data = json.loads(html.unescape(mm.group(1).strip().replace("&q;", '"')))

            sv3 = m_data.get("https://api.goscorer.com/api/v3/getSV3", {}) or {}
            comment = str(sv3.get("comment1") or "")
            b_txt = str(sv3.get("B") or "")
            f_team = str(sv3.get("F") or "").replace("^", "").strip()
            toss_winner = None
            toss_decision = "Bowl"

            for text_cand in (comment, b_txt):
                m_c = toss_comment_re.search(text_cand)
                if m_c:
                    team_code = m_c.group(1).strip()
                    raw_dec = m_c.group(2).strip().lower()
                    toss_decision = "Bat" if "bat" in raw_dec else "Bowl"
                    t1_sn = str(fix.get("t1SName") or sv3.get("team1") or "")
                    t2_sn = str(fix.get("t2SName") or sv3.get("team2") or "")
                    if team_code.lower() in [t1_sn.lower(), t1.lower()] or match_teams_fuzzy(team_code, t1):
                        toss_winner = t1
                        break
                    elif team_code.lower() in [t2_sn.lower(), t2.lower()] or match_teams_fuzzy(team_code, t2):
                        toss_winner = t2
                        break

            if not toss_winner and f_team and any(w in b_txt.lower() for w in ["opt to", "opted to", "won toss", "elected to"]):
                toss_decision = "Bat" if "bat" in b_txt.lower() else "Bowl"
                t1_sn = str(fix.get("t1SName") or sv3.get("team1") or "")
                t2_sn = str(fix.get("t2SName") or sv3.get("team2") or "")
                if f_team.lower() in [t1_sn.lower(), t1.lower()] or match_teams_fuzzy(f_team, t1):
                    toss_winner = t1
                elif f_team.lower() in [t2_sn.lower(), t2.lower()] or match_teams_fuzzy(f_team, t2):
                    toss_winner = t2

            if not toss_winner:
                post_key = next((k for k in m_data.keys() if "getPostMatchData" in k), None)
                post = m_data.get(post_key, {}) if post_key else {}
                if isinstance(post, dict) and post.get("toss_team"):
                    t_team_key = post.get("toss_team")
                    chose_to = post.get("chose_to")
                    toss_decision = "Bat" if chose_to == 0 else "Bowl"
                    for k in ("team1fkey", "t1f"):
                        if fix.get(k) == t_team_key:
                            toss_winner = t1
                            break
                    if not toss_winner:
                        for k in ("team2fkey", "t2f"):
                            if fix.get(k) == t_team_key:
                                toss_winner = t2
                                break

            if not toss_winner:
                return None
            return (fix, f_date, toss_winner, toss_decision)
        except Exception:
            return None

    live_cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "gaming_helper_matches.json")
    live_cache = []
    if os.path.exists(live_cache_file):
        try:
            with open(live_cache_file, "r", encoding="utf-8") as f:
                live_cache = json.load(f)
        except Exception:
            live_cache = []

    if candidates:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_scrape_crex_match, cand) for cand in candidates[:25]]
            for fut in as_completed(futures):
                parsed = fut.result()
                if not parsed:
                    continue
                fix, f_date, toss_winner, toss_decision = parsed
                t1 = fix.get("team1")
                t2 = fix.get("team2")

                rec_team = None
                rating = "🟡 SKIP / 50-50 COIN FLIP"
                matched_id = None

                for ex in existing_results:
                    ex_date = str(ex.get("match_date") or "")[:10]
                    if ex_date and ex_date != f_date:
                        continue
                    if is_match_pair(t1, t2, ex.get("team_a", ""), ex.get("team_b", "")):
                        matched_id = ex.get("match_id")
                        rec_team = ex.get("recommended_team")
                        rating = ex.get("stake_rating", "NORMAL")
                        break

                if not rec_team and live_cache:
                    for cm in live_cache:
                        cm_date = str(cm.get("match_date") or "").split("T")[0].strip()
                        if cm_date and cm_date != f_date:
                            continue
                        cm_t1 = cm.get("team_a_name") or cm.get("team_one_name") or ""
                        cm_t2 = cm.get("team_b_name") or cm.get("team_two_name") or ""
                        if is_match_pair(t1, t2, cm_t1, cm_t2):
                            matched_id = cm.get("id") or matched_id
                            if _is_valid_v8_play(cm):
                                rec_team = cm.get("recommended_team")
                                rating = cm.get("stake_rating", "💎 HIGH-CONVICTION GOLDEN PICK (TOP 3-4)")
                            else:
                                rec_team = None
                                rating = "🟡 SKIP / 50-50 COIN FLIP"
                            break

                if not matched_id:
                    for lid, lp in locked_map.items():
                        if str(lid).startswith("pair:") or "__vs__" in str(lid):
                            continue
                        lp_date = str(lp.get("match_date", "")).split("T")[0]
                        if lp_date and lp_date != f_date:
                            continue
                        if is_match_pair(t1, t2, lp.get("team_a", ""), lp.get("team_b", "")):
                            matched_id = lid
                            if lp.get("engine_version") == 8 and lp.get("action") == "PLAY" and lp.get("recommended_team"):
                                rec_team = lp.get("recommended_team")
                                rating = lp.get("stake_rating", "💎 HIGH-CONVICTION GOLDEN PICK (TOP 3-4)")
                            break

                if not matched_id:
                    matched_id = f"crex_{fix.get('matchFkey') or fix.get('id')}"

                series_name = fix.get("seriesShortName") or fix.get("n") or "Cricket Match"
                time_ist = fix.get("startTimeText") or format_ist(now_ist(), "%I:%M %p IST")
                if "IST" not in time_ist:
                    time_ist = f"{time_ist} IST"

                save_toss_result(
                    match_id=str(matched_id),
                    match_name=f"{t1} vs {t2}",
                    tournament=series_name,
                    toss_time_ist=time_ist,
                    match_date=f_date,
                    team_a=t1,
                    team_b=t2,
                    recommended_team=rec_team,
                    stake_rating=rating,
                    actual_winner=toss_winner,
                    toss_decision=toss_decision,
                    is_manual=False
                )
                resolved_count += 1

    # Fallback: harvest CREX series pages if recent matches (today/yesterday) are still unresolved
    still_pending_recent = any(
        is_winner_unresolved(r.get("actual_winner"))
        and str(r.get("match_date") or "")[:10] in (today_str, yesterday_str)
        for r in load_toss_results()
    )
    if still_pending_recent:
        try:
            sync_active_series_history_from_crex(max_series=10)
            resolved_count += reconcile_pending_from_resolved()
        except Exception:
            pass

    return resolved_count


def merge_backup_toss_results(backup_records: List[Dict[str, Any]]) -> int:
    """Merge uploaded backup toss result records into RESULTS_FILE cleanly.

    Prefers resolved / manual entries over Pending Confirmation without duplicating matches.
    Returns count of records added or updated.
    """
    global _TOSS_RESULTS_CACHE
    if not isinstance(backup_records, list) or not backup_records:
        return 0

    from core.providers.toss_load_provider import match_teams_fuzzy
    existing = load_toss_results()
    changed = 0

    for raw in backup_records:
        if not isinstance(raw, dict):
            continue
        m_id = str(raw.get("match_id", "")).strip()
        ta = str(raw.get("team_a", "")).strip()
        tb = str(raw.get("team_b", "")).strip()
        m_date = str(raw.get("match_date") or "").split("T")[0].strip()
        if not m_id and not (ta and tb):
            continue

        b_win = raw.get("actual_winner")
        b_resolved = not is_winner_unresolved(b_win)
        b_manual = bool(raw.get("is_manual", False))

        # Find matching existing row by match_id, match_name, or same-date fuzzy team pair
        match_idx = None
        for idx, cur in enumerate(existing):
            c_id = str(cur.get("match_id", "")).strip()
            c_date = str(cur.get("match_date") or "").split("T")[0].strip()
            c_ta = str(cur.get("team_a", "")).strip()
            c_tb = str(cur.get("team_b", "")).strip()
            same_pair = bool(
                c_date and m_date and c_date == m_date and ta and tb and c_ta and c_tb and (
                    (match_teams_fuzzy(ta, c_ta) and match_teams_fuzzy(tb, c_tb))
                    or (match_teams_fuzzy(ta, c_tb) and match_teams_fuzzy(tb, c_ta))
                )
            )
            if (m_id and c_id == m_id) or (raw.get("match_name") and cur.get("match_name") == raw.get("match_name")) or same_pair:
                match_idx = idx
                break

        if match_idx is None:
            existing.append(dict(raw))
            changed += 1
        else:
            cur = existing[match_idx]
            c_resolved = not is_winner_unresolved(cur.get("actual_winner"))
            c_manual = bool(cur.get("is_manual", False))
            # Upgrade pending row to resolved backup, or manual backup over non-manual row
            if (b_resolved and not c_resolved) or (b_resolved and b_manual and not c_manual):
                merged = dict(cur)
                merged["actual_winner"] = b_win
                if raw.get("toss_decision"):
                    merged["toss_decision"] = raw.get("toss_decision")
                if raw.get("outcome"):
                    merged["outcome"] = raw.get("outcome")
                if raw.get("outcome_label"):
                    merged["outcome_label"] = raw.get("outcome_label")
                merged["is_manual"] = b_manual
                if raw.get("recorded_at"):
                    merged["recorded_at"] = raw.get("recorded_at")
                # Keep PASS/FAIL/SKIPPED coherent; honor explicit manual SKIP from backup, never upgrade SKIP -> PLAY from stale backup
                if b_manual and (not raw.get("recommended_team") or raw.get("outcome") == "SKIPPED"):
                    merged["recommended_team"] = None
                    merged["stake_rating"] = raw.get("stake_rating", "🟡 FORCE SKIP / 50-50 MANUAL FIX")
                rec_t = merged.get("recommended_team")
                if not rec_t or str(rec_t).strip().lower() in ("none", "skip"):
                    merged["recommended_team"] = None
                    merged["outcome"] = "SKIPPED"
                    merged["outcome_label"] = "🟡 SKIPPED (50-50 NEUTRAL)"
                elif not is_winner_unresolved(merged.get("actual_winner")):
                    w_str = str(merged["actual_winner"])
                    rec_low = str(rec_t).strip().lower()
                    won = rec_low in w_str.lower() or w_str.lower() in rec_low or match_teams_fuzzy(str(rec_t), w_str)
                    merged["outcome"] = "PASS" if won else "FAIL"
                    merged["outcome_label"] = "✅ PASS (WON)" if won else "❌ FAIL (MISSED)"
                existing[match_idx] = merged
                changed += 1

    if changed > 0:
        existing.sort(key=_parse_toss_time_key, reverse=True)
        os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        _TOSS_RESULTS_CACHE["mtime"] = 0
        _TOSS_RESULTS_CACHE["cloud_ts"] = 0.0
        if _cloud_sync_active():
            cloud_upsert_many_results(existing)

    return changed


def sync_results_file_to_github(
    github_token: Optional[str] = None,
    repo: str = "priteshpra/my-vip-toss",
    branch: str = "main",
) -> Dict[str, Any]:
    """Commit data/toss_results_log.json to GitHub via Contents API so manual edits survive Streamlit Cloud reboots."""
    token = (github_token or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        return {"success": False, "error": "GITHUB_TOKEN not configured."}
    if not os.path.exists(RESULTS_FILE):
        return {"success": False, "error": "toss_results_log.json not found locally."}

    api_url = f"https://api.github.com/repos/{repo}/contents/data/toss_results_log.json"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "TossIQ-VIP-App",
        "Content-Type": "application/json",
    }

    try:
        req_get = urllib.request.Request(f"{api_url}?ref={branch}", headers=headers, method="GET")
        with urllib.request.urlopen(req_get, timeout=15, context=_SSL_CTX) as resp:
            cur_meta = json.loads(resp.read().decode("utf-8"))
        sha = cur_meta.get("sha")
    except Exception as e_get:
        return {"success": False, "error": f"GitHub read failed: {e_get}"}

    try:
        with open(RESULTS_FILE, "rb") as f:
            raw_bytes = f.read()
        payload = json.dumps({
            "message": f"chore: save toss results log ({format_ist(now_ist(), '%Y-%m-%d %H:%M IST')})",
            "content": base64.b64encode(raw_bytes).decode("ascii"),
            "sha": sha,
            "branch": branch,
        }).encode("utf-8")
        req_put = urllib.request.Request(api_url, data=payload, headers=headers, method="PUT")
        with urllib.request.urlopen(req_put, timeout=20, context=_SSL_CTX) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
        commit_sha = (res_json.get("commit") or {}).get("sha", "")[:7]
        return {"success": True, "message": f"✅ GitHub par permanent save ho gaya! (Commit: {commit_sha})"}
    except Exception as e_put:
        return {"success": False, "error": f"GitHub save failed: {e_put}"}


def get_toss_accuracy_kpis(target_results: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Calculate aggregate pass/fail KPIs strictly across confirmed completed tosses."""
    results = target_results if target_results is not None else load_toss_results()
    played = 0
    passed = 0
    failed = 0
    skipped = 0
    pending = 0

    for r in results:
        out = r.get("outcome")
        act_win = str(r.get("actual_winner", "")).lower()
        if out == "PASS":
            played += 1
            passed += 1
        elif out == "FAIL":
            played += 1
            failed += 1
        elif out == "PENDING" or "pending" in act_win:
            pending += 1
        else:
            skipped += 1

    pass_rate = (passed / played * 100) if played > 0 else 0.0
    confirmed_completed = played + skipped

    return {
        "total_completed": confirmed_completed,
        "played_count": played,
        "passed_count": passed,
        "failed_count": failed,
        "skipped_count": skipped,
        "pending_count": pending,
        "pass_rate_pct": pass_rate,
        "results": results
    }
