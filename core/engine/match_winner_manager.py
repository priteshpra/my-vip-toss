"""Match Winner Results & Audit Tracker Manager.

Persistently tracks completed Match Winner predictions, outcomes, and pass/fail audit logs:
- Stores results in data/mw_results_log.json
- Automatically synchronizes finished matches from CREX live feed
- Evaluates predictions as PASS (Won), FAIL (Missed), SKIPPED (50-50), or PENDING
- Calculates transparent KPIs: Win Rate %, Super Lock accuracy %, Value Play accuracy %
"""

import os
import re
import json
import ssl
import urllib.request
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from core.timezone import now_ist, format_ist

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

MW_RESULTS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "mw_results_log.json"
)


def _parse_mw_time_key(record: Dict[str, Any]) -> str:
    """Sort key for chronological descending order (latest match first)."""
    t_str = record.get("match_time_ist", "")
    d_str = record.get("match_date", "1970-01-01")
    try:
        clean_t = t_str.replace(" IST", "").strip()
        dt = datetime.strptime(f"{d_str} {clean_t}", "%Y-%m-%d %I:%M %p")
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return f"{d_str} 00:00"


def load_mw_results() -> List[Dict[str, Any]]:
    """Load all recorded match winner results, sorted latest first."""
    if os.path.exists(MW_RESULTS_FILE):
        try:
            with open(MW_RESULTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    data.sort(key=_parse_mw_time_key, reverse=True)
                    return data
        except Exception:
            pass
    return []


def save_mw_result(
    match_id: str,
    match_name: str,
    tournament: str,
    match_date: str,
    match_time_ist: str,
    team_a: str,
    team_b: str,
    predicted_winner: str,
    verdict_code: str = "VALUE_PLAY",
    verdict_badge: str = "🔥 VALUE PLAY",
    bhav_entry: str = "",
    win_prob: int = 65,
    actual_winner: str = "Pending Confirmation",
    result_summary: str = "",
    note: str = "",
    is_manual: bool = False
) -> Dict[str, Any]:
    """Create or update a Match Winner record and persist to disk."""
    results = load_mw_results()
    m_id = str(match_id or "").strip()
    if not m_id:
        m_id = f"mw_{match_date.replace('-', '')}_{team_a[:3].lower()}_{team_b[:3].lower()}"

    # Determine outcome
    pred_clean = (predicted_winner or "").strip()
    actual_clean = (actual_winner or "").strip()

    if "pending" in actual_clean.lower() or not actual_clean:
        outcome = "PENDING"
        outcome_label = "⏳ LIVE / IN-PLAY"
    elif "skip" in verdict_code.lower() or not pred_clean:
        outcome = "SKIPPED"
        outcome_label = "🟡 SKIPPED (50-50)"
    else:
        from core.providers.toss_load_provider import match_teams_fuzzy
        if match_teams_fuzzy(pred_clean, actual_clean):
            outcome = "PASS"
            outcome_label = "✅ PASS (WON)"
        else:
            outcome = "FAIL"
            outcome_label = "❌ FAIL (MISSED)"

    record = {
        "match_id": m_id,
        "match_name": match_name or f"{team_a} vs {team_b}",
        "tournament": tournament or "Cricket Match",
        "match_time_ist": match_time_ist,
        "match_date": match_date,
        "team_a": team_a,
        "team_b": team_b,
        "predicted_winner": pred_clean,
        "verdict_code": verdict_code,
        "verdict_badge": verdict_badge,
        "bhav_entry": bhav_entry,
        "win_prob": win_prob,
        "actual_winner": actual_clean,
        "result_summary": result_summary or f"{actual_clean} Won" if actual_clean and outcome != "PENDING" else "Match In-Play",
        "outcome": outcome,
        "outcome_label": outcome_label,
        "note": note,
        "is_manual": is_manual,
        "recorded_at": format_ist(now_ist(), "%Y-%m-%d %I:%M %p IST")
    }

    # Replace existing or append
    updated = False
    for i, r in enumerate(results):
        if r.get("match_id") == m_id:
            results[i] = record
            updated = True
            break

    if not updated:
        results.insert(0, record)

    results.sort(key=_parse_mw_time_key, reverse=True)

    try:
        os.makedirs(os.path.dirname(MW_RESULTS_FILE), exist_ok=True)
        with open(MW_RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error persisting MW results: {e}")

    return record


def delete_mw_result(match_id: str) -> bool:
    """Delete a match winner result by match_id."""
    results = load_mw_results()
    new_results = [r for r in results if r.get("match_id") != match_id]
    if len(new_results) != len(results):
        try:
            with open(MW_RESULTS_FILE, "w", encoding="utf-8") as f:
                json.dump(new_results, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False
    return False


def get_mw_accuracy_kpis() -> Dict[str, Any]:
    """Calculate accuracy stats and win rates across all completed Match Winner predictions."""
    results = load_mw_results()
    
    # Only evaluate settled action picks (exclude pure 50-50 Skips and Pending)
    settled = [r for r in results if r.get("outcome") in ["PASS", "FAIL"]]
    won_cnt = sum(1 for r in settled if r.get("outcome") == "PASS")
    missed_cnt = sum(1 for r in settled if r.get("outcome") == "FAIL")
    pending_cnt = sum(1 for r in results if r.get("outcome") == "PENDING")
    
    total_settled = len(settled)
    accuracy_pct = round((won_cnt / total_settled) * 100, 1) if total_settled > 0 else 0.0

    # Sub-breakdown: Super Locks vs Value Plays
    sl_settled = [r for r in settled if r.get("verdict_code") == "SUPER_LOCK"]
    sl_won = sum(1 for r in sl_settled if r.get("outcome") == "PASS")
    sl_acc = round((sl_won / len(sl_settled)) * 100, 1) if sl_settled else 0.0

    vp_settled = [r for r in settled if r.get("verdict_code") == "VALUE_PLAY"]
    vp_won = sum(1 for r in vp_settled if r.get("outcome") == "PASS")
    vp_acc = round((vp_won / len(vp_settled)) * 100, 1) if vp_settled else 0.0

    return {
        "total_settled": total_settled,
        "won_cnt": won_cnt,
        "missed_cnt": missed_cnt,
        "pending_cnt": pending_cnt,
        "accuracy_pct": accuracy_pct,
        "super_locks_total": len(sl_settled),
        "super_locks_won": sl_won,
        "super_locks_acc": sl_acc,
        "value_plays_total": len(vp_settled),
        "value_plays_won": vp_won,
        "value_plays_acc": vp_acc
    }


def sync_mw_results_from_crex() -> int:
    """Synchronize finished matches and outcomes directly from CREX live feed."""
    import html
    from core.providers.toss_load_provider import match_teams_fuzzy
    from core.providers.crex_provider import is_match_pair, fetch_crex_fixtures

    fixtures = fetch_crex_fixtures(force_refresh=True)
    if not fixtures:
        return 0

    results = load_mw_results()
    resolved_count = 0

    for f in fixtures:
        status = f.get("status", 0)
        status_text = f.get("status_text", "")
        # status 2 = Completed
        if status != 2 and not ("won by" in status_text.lower() or "beat" in status_text.lower()):
            continue

        t1 = f.get("t1_name", "")
        t2 = f.get("t2_name", "")
        if not t1 or not t2:
            continue

        # Extract actual match winner from status_text
        actual_winner = ""
        result_summary = status_text
        if "won by" in status_text.lower():
            winner_part = status_text.split("won by")[0].strip()
            if match_teams_fuzzy(t1, winner_part):
                actual_winner = t1
            elif match_teams_fuzzy(t2, winner_part):
                actual_winner = t2
            else:
                actual_winner = winner_part
        elif "beat" in status_text.lower():
            winner_part = status_text.split("beat")[0].strip()
            if match_teams_fuzzy(t1, winner_part):
                actual_winner = t1
            elif match_teams_fuzzy(t2, winner_part):
                actual_winner = t2

        if not actual_winner:
            continue

        # Check if already recorded in results
        matched_record = None
        for r in results:
            if is_match_pair(t1, t2, r.get("team_a", ""), r.get("team_b", "")):
                matched_record = r
                break

        if matched_record:
            if matched_record.get("outcome") == "PENDING" or "pending" in str(matched_record.get("actual_winner", "")).lower():
                save_mw_result(
                    match_id=matched_record.get("match_id"),
                    match_name=matched_record.get("match_name"),
                    tournament=matched_record.get("tournament"),
                    match_date=matched_record.get("match_date"),
                    match_time_ist=matched_record.get("match_time_ist"),
                    team_a=matched_record.get("team_a"),
                    team_b=matched_record.get("team_b"),
                    predicted_winner=matched_record.get("predicted_winner"),
                    verdict_code=matched_record.get("verdict_code"),
                    verdict_badge=matched_record.get("verdict_badge"),
                    bhav_entry=matched_record.get("bhav_entry"),
                    win_prob=matched_record.get("win_prob", 65),
                    actual_winner=actual_winner,
                    result_summary=result_summary,
                    note=matched_record.get("note", ""),
                    is_manual=False
                )
                resolved_count += 1
        else:
            # If CREX had a strong favorite, create a completed audit entry
            fav = f.get("fav_team") or ""
            prob = f.get("win_prob") or 50
            back = f.get("back")
            if fav and (back is not None and back <= 85):
                v_code = "SUPER_LOCK" if back <= 50 else "VALUE_PLAY"
                v_badge = f"💎 {prob}% SUPER LOCK" if back <= 50 else f"🔥 {prob}% VALUE PLAY"
                save_mw_result(
                    match_id=f"mw_crex_{f.get('date', '').replace('-', '')}_{t1[:3].lower()}_{t2[:3].lower()}",
                    match_name=f"{t1} vs {t2}",
                    tournament=f.get("series", "Cricket Match"),
                    match_date=f.get("date", format_ist(now_ist(), "%Y-%m-%d")),
                    match_time_ist=f.get("time", format_ist(now_ist(), "%I:%M %p IST")),
                    team_a=t1,
                    team_b=t2,
                    predicted_winner=fav,
                    verdict_code=v_code,
                    verdict_badge=v_badge,
                    bhav_entry=f"[{fav[:3].upper()}] {back}-{f.get('lay') or ''}",
                    win_prob=prob,
                    actual_winner=actual_winner,
                    result_summary=result_summary,
                    note="Auto-synced from CREX final scorecard.",
                    is_manual=False
                )
                resolved_count += 1

    return resolved_count
