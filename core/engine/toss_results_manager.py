"""Toss Results & Audit Manager.

Persistently tracks completed tosses, outcomes, and pass/fail statistics:
- Automatically synchronizes closed/completed matches from GamingHelper.
- Strictly tracks real matches from the user's active platform.
- Evaluates recommendations as PASS (Won), FAIL (Missed), or SKIPPED (50-50).
- Honors permanent locked predictions from toss_predictions_locked.json so PLAY calls are NEVER lost.
"""

import os
import json
import ssl
import urllib.request
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from core.timezone import now_ist, format_ist

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

RESULTS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "toss_results_log.json")


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


def load_toss_results() -> List[Dict[str, Any]]:
    """Load all recorded completed toss results. Strictly real matches only, sorted latest first."""
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    # Filter out any legacy dummy seed matches if they existed
                    filtered = [r for r in data if not str(r.get("match_id", "")).startswith("seed_0")]
                    filtered.sort(key=_parse_toss_time_key, reverse=True)
                    return filtered
        except Exception:
            pass
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

    # Auto-restore locked recommendation if match was originally a PLAY call
    m_id_str = str(match_id or "").strip()
    if not recommended_team and m_id_str:
        try:
            from core.providers.toss_load_provider import load_locked_predictions
            l_map = load_locked_predictions()
            if m_id_str in l_map:
                l_pred = l_map[m_id_str]
                if l_pred.get("action") == "PLAY":
                    recommended_team = l_pred.get("recommended_team")
                    if stake_rating in ["🟡 SKIP / 50-50 COIN FLIP", "NORMAL", ""]:
                        stake_rating = l_pred.get("stake_rating", "💎 VIP HIGH LOAD PLAY")
        except Exception:
            pass

    # Determine PASS / FAIL / SKIPPED / PENDING outcome
    rec_clean = (recommended_team or "").strip().lower()
    win_clean = (actual_winner or "").strip().lower()
    rating_clean = (stake_rating or "").strip().lower()

    if not actual_winner or "pending" in win_clean:
        outcome = "PENDING"
        outcome_label = "⏳ PENDING (Awaiting Result)"
    elif "skip" in rating_clean or "pass" in rating_clean or not rec_clean or rec_clean in ["none", "skip"]:
        outcome = "SKIPPED"
        outcome_label = "🟡 SKIPPED (50-50 NEUTRAL)"
        recommended_team = None
    elif rec_clean and win_clean:
        if rec_clean in win_clean or win_clean in rec_clean:
            outcome = "PASS"
            outcome_label = "✅ PASS (WON)"
        else:
            outcome = "FAIL"
            outcome_label = "❌ FAIL (MISSED)"
    else:
        outcome = "SKIPPED"
        outcome_label = "🟡 SKIPPED (50-50 NEUTRAL)"

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

    # Replace if match already recorded, else prepend
    updated = False
    for i, r in enumerate(results):
        if str(r.get("match_id")) == m_id_str or r.get("match_name") == match_name:
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
        return True
    return False


def sync_completed_tosses_from_gaming_helper() -> List[Dict[str, Any]]:
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

    # Paginate through all closed matches so nothing gets truncated
    raw_closed = []
    page = 1
    while True:
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
            with urllib.request.urlopen(req, timeout=12, context=_SSL_CTX) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                batch = data.get("data") or []
                if not batch:
                    break
                raw_closed.extend(batch)
                total_pages = data.get("totalPages", 1)
                if page >= total_pages:
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
    existing_results = load_toss_results()
    existing_map = {str(r.get("match_id")): r for r in existing_results}
    locked_map = load_locked_predictions()

    # Process all closed matches from API
    for m in (raw_closed or []):
        m_id = str(m.get("id"))
        m_name = m.get("match_name") or ""
        t1 = m.get("team_one_name") or ""
        t2 = m.get("team_two_name") or ""
        if not t1 or not t2:
            continue

        # If match is already confirmed in the log (with non-pending winner), don't overwrite
        if m_id in existing_map:
            ex = existing_map[m_id]
            if ex.get("actual_winner") and "pending" not in str(ex.get("actual_winner", "")).lower():
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

        # 1. First priority: Check frozen locked prediction map (Guarantees PLAY verdict is preserved)
        rec_team = None
        rating = "🟡 STRICT PASS (NO BET)"
        l_pred = get_locked_prediction(m_id, t1, t2)
        if l_pred:
            if l_pred.get("action") == "PLAY":
                rec_team = l_pred.get("recommended_team")
                rating = l_pred.get("stake_rating", "💎 VIP HIGH LOAD PLAY")
            else:
                rec_team = None
                rating = "🟡 SKIP / 50-50 COIN FLIP"
        else:
            # 2. Second priority: Live matches cache
            live_cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "gaming_helper_matches.json")
            found_in_cache = False
            if os.path.exists(live_cache_file):
                try:
                    with open(live_cache_file, "r", encoding="utf-8") as f:
                        live_cache = json.load(f)
                        for cm in live_cache:
                            if str(cm.get("id")) == m_id or (cm.get("team_a_name") == t1 and cm.get("team_b_name") == t2):
                                if cm.get("action_type") == "PLAY":
                                    rec_team = cm.get("recommended_team")
                                    rating = cm.get("stake_rating", "💎 VIP HIGH LOAD PLAY")
                                else:
                                    rec_team = None
                                    rating = "🟡 SKIP / 50-50 COIN FLIP"
                                found_in_cache = True
                                break
                except Exception:
                    pass

            if not found_in_cache:
                vip = compute_multi_source_toss_load(t1, t2, odds_a, odds_b, tour, _format_time_24_to_ist(match_raw), match_id=m_id)
                if vip.get("action") == "PLAY":
                    rec_team = vip.get("recommended_team")
                    rating = vip.get("stake_rating", "💎 VIP HIGH LOAD PLAY")
                else:
                    rec_team = None
                    rating = "🟡 SKIP / 50-50 COIN FLIP"

        # Extract actual match date from API record
        m_date_raw = m.get("match_date")
        if m_date_raw:
            m_date = str(m_date_raw).split("T")[0].strip()
        else:
            m_date = today_str

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

        # Mark as Pending Confirmation for genuine user or telegram confirmation
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
            actual_winner="Pending Confirmation",
            toss_decision="Pending",
            is_manual=False
        )

    # Automatically resolve freshly closed matches using Telegram live feed @KINGTOSSLINE111
    try:
        auto_update_toss_results_from_telegram()
    except Exception as e:
        print(f"Error in auto_update_toss_results_from_telegram: {e}")

    return load_toss_results()


def auto_update_toss_results_from_telegram() -> int:
    """
    Automatically resolve Pending Confirmation matches using live toss announcements
    from public Telegram channel @KINGTOSSLINE111.
    """
    from core.providers.telegram_toss_feed import fetch_telegram_toss_feed, match_team_with_tg_winner
    from core.providers.toss_load_provider import load_locked_predictions

    feed = fetch_telegram_toss_feed(pages=3)
    if not feed:
        return 0

    results = load_toss_results()
    locked_map = load_locked_predictions()
    resolved_count = 0
    cur_time = now_ist()

    for r in results:
        # Only resolve pending matches
        if r.get("outcome") != "PENDING" and "pending" not in str(r.get("actual_winner", "")).lower():
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

        # Restore recommendation if locked map has PLAY
        if not rec and m_id in locked_map:
            l_pred = locked_map[m_id]
            if l_pred.get("action") == "PLAY":
                rec = l_pred.get("recommended_team")
                rating = l_pred.get("stake_rating", "💎 VIP HIGH LOAD PLAY")

        for tg in feed:
            w_raw = tg.get("winner_raw", "")
            decision = tg.get("decision", "Bowl")
            post_iso = tg.get("post_iso", "")

            # If message timestamp is available, verify it was posted in the valid window of this match
            if post_iso and toss_dt:
                try:
                    p_dt = datetime.fromisoformat(post_iso.replace("Z", "+00:00"))
                    p_ist = p_dt.astimezone(cur_time.tzinfo)
                    # Window: within [-15 min, +3.5 hours] of match toss time
                    min_allowed = toss_dt - timedelta(minutes=15)
                    max_allowed = toss_dt + timedelta(hours=3, minutes=30)
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
