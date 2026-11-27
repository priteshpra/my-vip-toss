"""TossIQ VIP - Elite Cricket Toss Intelligence & Actionable Platform.

Built for serious punters: zero clutter, zero academic formulas, realistic probability-calibrated toss verdicts:
- 🟢 PLAY: [TEAM NAME] with calculated edge & stake advice (Jackpot / Confirmed).
- 🟡 SKIP / PASS: 50-50 coin flip traps to save your bankroll.
- 🪙 TOSS TIME: Exact timing in Asia/Kolkata (IST).
- 🔐 Synchronized with GamingHelper (gaminghelperonline.com/live-bets).
- 📜 Toss Done & Pass/Fail Accuracy Tracker with persistent history.
"""

import os
import sys
import json
import time
import importlib
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# Ensure project root is in sys.path for Streamlit Cloud / Linux runtime
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.timezone import now_ist, format_ist
from core.providers.gaming_helper_provider import (
    get_stored_credentials,
    save_credentials,
    login_gaming_helper,
    verify_gaming_helper_otp,
    resend_gaming_helper_otp,
    fetch_gaming_helper_live_bets,
    get_active_punter_matches
)

import core.engine.toss_results_manager as _trm

load_toss_results = _trm.load_toss_results
save_toss_result = _trm.save_toss_result
delete_toss_result = getattr(_trm, "delete_toss_result", lambda mid: False)
get_toss_accuracy_kpis = _trm.get_toss_accuracy_kpis
sync_completed_tosses_from_gaming_helper = _trm.sync_completed_tosses_from_gaming_helper
auto_update_toss_results_from_telegram = getattr(_trm, "auto_update_toss_results_from_telegram", lambda: 0)
sync_live_toss_results_from_crex = getattr(_trm, "sync_live_toss_results_from_crex", lambda: 0)
sync_live_toss_results_from_cricbuzz = getattr(_trm, "sync_live_toss_results_from_cricbuzz", lambda: 0)
merge_backup_toss_results = getattr(_trm, "merge_backup_toss_results", lambda recs: 0)
sync_results_file_to_github = getattr(_trm, "sync_results_file_to_github", lambda **kwargs: {"success": False, "error": "Unavailable"})

def _fallback_is_winner_unresolved(val: Any) -> bool:
    s = str(val or "").strip().lower()
    return (
        not s
        or "pending" in s
        or "skipped" in s
        or "neutral" in s
        or s in ["none", "tbd", "-", "null", "undefined"]
    )

is_winner_unresolved = getattr(_trm, "is_winner_unresolved", _fallback_is_winner_unresolved)


from core.providers.crex_provider import (
    get_crex_live_bhav
)
from core.providers.toss_load_provider import (
    match_teams_fuzzy,
    compute_multi_source_toss_load,
    get_locked_prediction
)

# --- CREX Market Bhav Overrides Storage ---
CREX_OVERRIDES_FILE = os.path.join(os.path.dirname(__file__), "data", "crex_overrides.json")
_CREX_OVERRIDES_CACHE = {"mtime": 0, "data": {}}

def load_crex_overrides() -> dict:
    global _CREX_OVERRIDES_CACHE
    if os.path.exists(CREX_OVERRIDES_FILE):
        try:
            mtime = os.path.getmtime(CREX_OVERRIDES_FILE)
            if _CREX_OVERRIDES_CACHE["mtime"] == mtime and _CREX_OVERRIDES_CACHE["data"]:
                return dict(_CREX_OVERRIDES_CACHE["data"])
            with open(CREX_OVERRIDES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                _CREX_OVERRIDES_CACHE = {"mtime": mtime, "data": data}
                return dict(data)
        except Exception:
            pass
    return {}

def save_crex_override(match_id: str, back_val: int, lay_val: int, team_name: str = "") -> None:
    try:
        os.makedirs(os.path.dirname(CREX_OVERRIDES_FILE), exist_ok=True)
        data = load_crex_overrides()
        data[str(match_id)] = {
            "back": int(back_val),
            "lay": int(lay_val),
            "fav_team": team_name
        }
        if team_name:
            data[team_name] = {
                "back": int(back_val),
                "lay": int(lay_val),
                "fav_team": team_name
            }
        with open(CREX_OVERRIDES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        _CREX_OVERRIDES_CACHE["mtime"] = 0
    except Exception:
        pass

def delete_crex_override(match_id: str, team_name: str = "") -> bool:
    try:
        data = load_crex_overrides()
        changed = False
        if str(match_id) in data:
            del data[str(match_id)]
            changed = True
        if team_name:
            for k in list(data.keys()):
                if team_name.lower() in k.lower():
                    del data[k]
                    changed = True
        if changed:
            with open(CREX_OVERRIDES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            _CREX_OVERRIDES_CACHE["mtime"] = 0
            return True
    except Exception:
        pass
    return False

# --- Page Configuration ---
st.set_page_config(
    page_title="TossIQ VIP — Paid Punter Toss Intelligence",
    page_icon="🪙",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Current IST Time
cur_ist = now_ist()
ist_time_display = format_ist(cur_ist, "%I:%M:%S %p IST")
ist_date_display = format_ist(cur_ist, "%A, %d %B %Y")
today_iso = format_ist(cur_ist, "%Y-%m-%d")
yesterday_ist = cur_ist - timedelta(days=1)
yesterday_iso = format_ist(yesterday_ist, "%Y-%m-%d")

# --- Elite VIP Sportsbook Theme CSS ---
st.markdown("""
<style>
    /* Dark VIP terminal theme with Crisp High-Contrast Pure White Typography */
    .stApp {
        background-color: #070a13;
        color: #ffffff !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }

    /* Universal Pure White Text Overrides (Excluding CREX black-text number boxes) */
    .stMarkdown p:not(.crex-odd-box), 
    .stMarkdown span:not(.crex-odd-box), 
    .stMarkdown div:not(.crex-odd-box), 
    .stMarkdown li {
        color: #ffffff !important;
    }

    /* Force Pitch Black Bold Numbers in CREX Boxes */
    .stMarkdown span.crex-odd-box,
    .stMarkdown .crex-odd-box,
    span.crex-odd-box,
    .crex-odd-box {
        background-color: #ffffff !important;
        background: #ffffff !important;
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
        font-weight: 900 !important;
        font-size: 15px !important;
        padding: 2px 10px !important;
        border-radius: 6px !important;
        border: 1.5px solid #94a3b8 !important;
        min-width: 36px !important;
        text-align: center !important;
        display: inline-block !important;
        line-height: 1.4 !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3) !important;
    }

    .stCaption, .stCaption p, [data-testid="stCaptionContainer"] p {
        color: #f1f5f9 !important;
        font-size: 13.5px !important;
        font-weight: 600 !important;
    }

    label[data-testid="stWidgetLabel"] p, label[data-testid="stWidgetLabel"] span {
        color: #ffffff !important;
        font-weight: 800 !important;
        font-size: 14.5px !important;
    }

    [data-testid="stExpander"] details summary span {
        color: #ffffff !important;
        font-weight: 800 !important;
        font-size: 15px !important;
    }

    button[data-baseweb="tab"] p, button[data-baseweb="tab"] span {
        color: #ffffff !important;
        font-weight: 800 !important;
        font-size: 15px !important;
    }

    /* Streamlit Pills Component Styling - High-Contrast Dark Theme */
    [data-testid="stPills"] button,
    div[data-testid="stPills"] button,
    button[data-testid="stPill"],
    div[role="radiogroup"] button {
        background-color: #1e293b !important;
        color: #ffffff !important;
        border: 1.5px solid #475569 !important;
        border-radius: 20px !important;
        padding: 5px 14px !important;
        font-weight: 700 !important;
    }
    
    [data-testid="stPills"] button *,
    div[data-testid="stPills"] button *,
    button[data-testid="stPill"] *,
    div[role="radiogroup"] button * {
        color: #ffffff !important;
        font-weight: 700 !important;
    }

    /* Selected state for pills */
    [data-testid="stPills"] button[aria-checked="true"],
    [data-testid="stPills"] button[aria-selected="true"],
    [data-testid="stPills"] button[data-selected="true"],
    div[role="radiogroup"] button[aria-checked="true"] {
        background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%) !important;
        color: #ffffff !important;
        border: 2px solid #38bdf8 !important;
        box-shadow: 0 0 12px rgba(56, 189, 248, 0.45) !important;
    }

    [data-testid="stPills"] button[aria-checked="true"] *,
    [data-testid="stPills"] button[aria-selected="true"] *,
    div[role="radiogroup"] button[aria-checked="true"] * {
        color: #ffffff !important;
        font-weight: 800 !important;
    }

    /* Hover state for pills */
    [data-testid="stPills"] button:hover,
    div[data-testid="stPills"] button:hover {
        background-color: #334155 !important;
        border-color: #64748b !important;
        color: #ffffff !important;
    }

    /* Selectbox styling in dark theme */
    div[data-baseweb="select"] > div {
        background-color: #0f172a !important;
        border-color: #334155 !important;
        color: #ffffff !important;
    }

    div[data-baseweb="select"] * {
        color: #ffffff !important;
    }

    /* Text input styling in dark theme */
    div[data-baseweb="input"] > div {
        background-color: #0f172a !important;
        border-color: #334155 !important;
        color: #ffffff !important;
    }

    div[data-baseweb="input"] input {
        color: #ffffff !important;
    }
    
    /* VIP Top Header */
    .vip-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
        border: 1px solid #312e81;
        padding: 16px 24px;
        border-radius: 16px;
        margin-bottom: 20px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
    }
    .vip-title {
        font-size: 28px;
        font-weight: 900;
        color: #ffffff !important;
        margin: 0;
        letter-spacing: -0.5px;
    }
    .vip-title span {
        color: #f59e0b !important;
    }
    .vip-tag {
        background: #f59e0b;
        color: #000000;
        font-size: 11px;
        font-weight: 900;
        padding: 3px 8px;
        border-radius: 6px;
        margin-left: 8px;
        text-transform: uppercase;
    }
    .vip-sub {
        color: #ffffff !important;
        font-size: 13.5px;
        font-weight: 600;
        margin: 4px 0 0 0;
    }
    .clock-badge {
        background: #090d16;
        border: 1px solid #334155;
        padding: 8px 18px;
        border-radius: 30px;
        text-align: right;
    }

    /* KPI Ticker Row */
    .kpi-card {
        background: #0f172a;
        border: 1px solid #1e293b;
        border-radius: 12px;
        padding: 14px 18px;
        text-align: center;
    }
    .kpi-val {
        font-size: 26px;
        font-weight: 900;
        margin-bottom: 2px;
    }
    .kpi-lbl {
        font-size: 12px;
        color: #ffffff !important;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }

    /* Match Card */
    .vip-match-card {
        background: #0d1322;
        border: 1px solid #1e293b;
        border-radius: 12px;
        padding: 12px 18px;
        margin-bottom: 12px;
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.4);
        transition: transform 0.15s ease, border-color 0.15s ease;
    }
    .vip-match-card:hover {
        border-color: #3b82f6;
    }
    .card-top-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 8px;
        margin-bottom: 8px;
    }
    .tour-pill {
        background: #1e293b;
        color: #38bdf8;
        border: 1px solid #334155;
        padding: 3px 12px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 800;
    }
    .toss-time-pill {
        background: #f59e0b;
        color: #000000;
        font-weight: 900;
        font-size: 13px;
        padding: 4px 12px;
        border-radius: 20px;
        box-shadow: 0 0 12px rgba(245, 158, 11, 0.4);
        display: inline-flex;
        align-items: center;
        gap: 5px;
    }
    .match-time-pill {
        background: #1e1b4b;
        color: #c7d2fe;
        border: 1px solid #4338ca;
        padding: 3px 10px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 700;
    }

    /* Teams Head-to-Head Banner */
    .teams-arena {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 6px 14px;
        background: #090d16;
        border-radius: 8px;
        border: 1px solid #1e293b;
        margin-bottom: 8px;
    }
    .team-title {
        font-size: 17px;
        font-weight: 900;
        color: #ffffff;
    }
    .vs-emblem {
        background: #1e293b;
        color: #f59e0b;
        border: 1px solid #d97706;
        font-size: 11px;
        font-weight: 900;
        padding: 2px 10px;
        border-radius: 20px;
    }

    /* CREX-style Win Probability & Market Bhav Bar */
    .crex-prob-bar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: #060a14;
        border: 1px solid #1e293b;
        border-radius: 8px;
        padding: 8px 12px;
        margin-bottom: 8px;
        flex-wrap: wrap;
        gap: 8px;
    }
    .crex-prob-label {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 13px;
        font-weight: 800;
        color: #94a3b8;
    }
    .crex-prob-source {
        font-size: 10.5px;
        color: #64748b;
        font-weight: 600;
        background: #0f172a;
        padding: 2px 6px;
        border-radius: 4px;
        border: 1px solid #1e293b;
    }
    .crex-prob-badges {
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .crex-fav-pill {
        background: #0284c7;
        color: #ffffff;
        font-weight: 900;
        font-size: 13px;
        padding: 2px 10px;
        border-radius: 5px;
        letter-spacing: 0.5px;
    }
    .crex-odd-box {
        background: #ffffff !important;
        color: #000000 !important;
        font-weight: 900 !important;
        font-size: 14.5px !important;
        padding: 2px 10px !important;
        border-radius: 5px !important;
        border: 1.5px solid #94a3b8 !important;
        min-width: 36px !important;
        text-align: center !important;
        display: inline-block !important;
        line-height: 1.4 !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.25) !important;
    }
    .crex-prob-pct {
        font-size: 12px;
        font-weight: 800;
        color: #38bdf8;
        background: #082f49;
        border: 1px solid #0284c7;
        padding: 2px 8px;
        border-radius: 12px;
        margin-left: 2px;
    }

    /* Punter Action Verdict Boxes - Compact & Short Height */
    .verdict-play {
        background: linear-gradient(135deg, #064e3b 0%, #065f46 100%);
        border: 1.5px solid #10b981;
        border-radius: 10px;
        padding: 8px 14px;
        box-shadow: 0 2px 10px rgba(16, 185, 129, 0.25);
    }
    .verdict-skip {
        background: linear-gradient(135deg, #451a03 0%, #78350f 100%);
        border: 1.5px solid #f59e0b;
        border-radius: 10px;
        padding: 8px 14px;
    }
    .verdict-pass {
        background: linear-gradient(135deg, #064e3b 0%, #047857 100%);
        border: 1.5px solid #10b981;
        border-radius: 10px;
        padding: 8px 14px;
    }
    .verdict-fail {
        background: linear-gradient(135deg, #450a0a 0%, #7f1d1d 100%);
        border: 1.5px solid #ef4444;
        border-radius: 10px;
        padding: 8px 14px;
    }
    
    .verdict-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 6px;
        margin-bottom: 3px;
    }
    .verdict-team-name {
        font-size: 18px;
        font-weight: 900;
        color: #ffffff;
        letter-spacing: -0.3px;
    }
    .verdict-badge {
        padding: 3px 10px;
        border-radius: 16px;
        font-weight: 800;
        font-size: 11.5px;
        letter-spacing: 0.3px;
    }
    .verdict-reason {
        color: #ffffff !important;
        font-size: 13px;
        line-height: 1.5;
        margin: 3px 0 0 0;
    }

    /* Punter Indicators Strip */
    .indicator-strip {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 6px;
        margin-top: 8px;
    }
    .indicator-item {
        background: #090d16;
        border: 1px solid #1e293b;
        border-radius: 8px;
        padding: 6px 10px;
    }
    .indicator-label {
        font-size: 11px;
        color: #ffffff !important;
        font-weight: 800;
        text-transform: uppercase;
    }
    .indicator-val {
        font-size: 13px;
        font-weight: 800;
        color: #ffffff;
        margin-top: 2px;
    }
</style>
""", unsafe_allow_html=True)

# --- MASTER SECURITY PIN GATE (PRIVATE ACCESS) ---
MASTER_PIN = os.environ.get("VIP_PIN", "256531")
require_pin = os.environ.get("REQUIRE_PIN", "false").lower() in ("true", "1")
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = not require_pin

if not st.session_state["authenticated"]:
    st.markdown("""
    <div style="max-width: 450px; margin: 40px auto 20px auto; background: #0f172a; border: 1px solid #1e293b; border-radius: 16px; padding: 32px 28px; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.6);">
        <div style="font-size: 42px; margin-bottom: 8px;">🔐</div>
        <h2 style="color: #ffffff; margin: 0 0 6px 0; font-size: 24px; font-weight: 900;">TossIQ VIP Portal</h2>
        <p style="color: #ffffff; font-size: 14px; margin: 0 0 20px 0; font-weight: 600;">Private & Restricted Access. Please enter your secret PIN to unlock.</p>
    </div>
    """, unsafe_allow_html=True)

    col_l1, col_l2, col_l3 = st.columns([1, 1.4, 1])
    with col_l2:
        pin_input = st.text_input("Enter Secret PIN", type="password", key="vip_pin_entry", placeholder="Enter PIN...", label_visibility="collapsed")
        if st.button("🔓 Unlock VIP Dashboard", type="primary", use_container_width=True, key="btn_unlock_vip"):
            if pin_input.strip() == MASTER_PIN:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("❌ Incorrect PIN! Access Denied.")
    st.stop()

# --- Top Header ---
st.markdown(f"""
<div class="vip-header">
    <div>
        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
            <h1 class="vip-title" style="margin: 0;">🪙 Toss<span>IQ</span> VIP</h1>
            <span class="vip-tag">Punter Edition</span>
            <span style="background: rgba(16, 185, 129, 0.2); border: 1px solid #10b981; border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 700; color: #10b981;">⚡ BUILD: 2026-11-23-V7-LIVE</span>
        </div>
        <p class="vip-sub">Live Bets Synchronized with GamingHelper (gaminghelperonline.com) · Asia/Kolkata (IST)</p>
    </div>
    <div class="clock-badge">
        <div style="font-size: 16px; font-weight: 900; color: #10b981;">⏰ {ist_time_display}</div>
        <div style="color: #ffffff; font-size: 12px; margin-top: 2px; font-weight: 700;">{ist_date_display}</div>
    </div>
</div>
""", unsafe_allow_html=True)

# Cached live matches pipeline (90s TTL in session_state so UI clicks/filters stay instant)
_now_ts = time.time()
if (
    "_punter_data_cache" not in st.session_state
    or (_now_ts - float(st.session_state.get("_punter_data_ts", 0))) > 90
):
    punter_data = get_active_punter_matches(force_refresh=False)
    st.session_state["_punter_data_cache"] = punter_data
    st.session_state["_punter_data_ts"] = _now_ts
else:
    punter_data = st.session_state["_punter_data_cache"]
all_matches = punter_data.get("matches", [])
is_gh_connected = punter_data.get("connected", False)

# Direct Immutable VIP Lock Enforcement (respecting Daily 3-4 Golden Quota cap):
for m in all_matches:
    if "DAILY 3-4 GOLDEN QUOTA" in str(m.get("stake_rating", "")):
        continue
    mid_s = str(m.get("id", ""))
    t_a_s = m.get("team_a_name", "")
    t_b_s = m.get("team_b_name", "")
    l_pred = get_locked_prediction(mid_s, t_a_s, t_b_s)
    if l_pred and l_pred.get("action") == "PLAY":
        m["action_type"] = "PLAY"
        m["action"] = "PLAY"
        m["recommended_team"] = l_pred.get("recommended_team")
        m["vip_pick"] = l_pred.get("recommended_team")
        m["confidence_pct"] = l_pred.get("confidence_pct", 75)
        m["stake_rating"] = l_pred.get("stake_rating", "💎 HIGH-CONVICTION GOLDEN PICK (TOP 3-4)")
        m["consensus_badge"] = l_pred.get("consensus_badge", "💎 TOP GOLDEN PICK (75%)")
        m["pillars_aligned"] = l_pred.get("pillars_aligned", 4)
        m["pillars_total"] = l_pred.get("pillars_total", 5)
        m["active_pillars_list"] = l_pred.get("active_pillars_list", [])
        m["punter_rationale"] = l_pred.get("punter_rationale", "")
        m["risk_level"] = l_pred.get("risk_level", "LOW RISK")
        m["verdict_tier"] = l_pred.get("verdict_tier", "SUPER_LOCK")
        m["fav_win_pct_summary"] = l_pred.get("fav_win_pct_summary", "")
        m["rule_win_pct"] = l_pred.get("rule_win_pct")
        m["team_a_record"] = l_pred.get("team_a_record", "")
        m["team_b_record"] = l_pred.get("team_b_record", "")
        m["h2h_summary"] = l_pred.get("h2h_summary", "")
is_gh_connected = st.session_state.get("live_gh_connected", punter_data.get("connected", False))
creds = get_stored_credentials()

# Detect if OTP verification is required
if is_gh_connected or len(all_matches) > 0:
    needs_otp = False
    st.session_state["gh_needs_otp"] = False
else:
    needs_otp = punter_data.get("needs_otp") or st.session_state.get("gh_needs_otp", False)
otp_email = punter_data.get("email") or st.session_state.get("gh_otp_email") or creds.get("email", "registered email")
otp_cid = punter_data.get("challengeId") or st.session_state.get("gh_challenge_id") or creds.get("challengeId")

# Account Status Bar
col_st1, col_st2, col_st3 = st.columns([2.2, 1, 1])
with col_st1:
    if is_gh_connected:
        st.success(f"🟢 **GamingHelper Connected:** Demo `{creds.get('phone', '7028163937')}` · **{len(all_matches)} Live Bets** · 📡 **CREX: Active**")
    elif needs_otp:
        st.error(f"🔴 **Session Expired:** OTP Required below · **{len(all_matches)} Live Bets**")
    else:
        st.warning(f"🟡 **GamingHelper:** Reconnecting · **{len(all_matches)} Live Bets** · 📡 **CREX: Active**")

with col_st2:
    if st.button("🔄 Sync Live Bets & CREX", type="primary", use_container_width=True, key="btn_sync_gh_now"):
        with st.spinner("Syncing latest live matches from GamingHelper..."):
            punter_data = get_active_punter_matches(force_refresh=True)
            if punter_data.get("needs_otp"):
                st.session_state["gh_needs_otp"] = True
                st.session_state["gh_otp_email"] = punter_data.get("email", "registered email")
                st.session_state["gh_challenge_id"] = punter_data.get("challengeId")
                st.session_state["live_gh_connected"] = False
                st.warning("⚠️ Session expired! Please enter OTP below.")
                st.rerun()
            st.session_state["live_punter_matches"] = punter_data.get("matches", [])
            st.session_state["_punter_data_cache"] = punter_data
            st.session_state["_punter_data_ts"] = time.time()
            st.session_state["live_gh_connected"] = punter_data.get("connected", False)
            try:
                sync_completed_tosses_from_gaming_helper(run_resolvers=False)
            except Exception as e_gh:
                print(f"[SyncNotice] GH sync: {e_gh}")
            try:
                sync_live_toss_results_from_cricbuzz()
            except Exception as e_cb:
                print(f"[SyncNotice] Cricbuzz sync: {e_cb}")
            try:
                sync_live_toss_results_from_crex()
            except Exception as e_cr:
                print(f"[SyncNotice] CREX sync: {e_cr}")
            st.success("Synced live bets successfully!")
            st.rerun()

with col_st3:
    if st.button("⚡ Force Reset", use_container_width=True, key="btn_force_clean_reload", help="Clear all UI session state and perform fresh execution"):
        st.session_state.clear()
        st.rerun()

# --- DEDICATED IN-PORTAL OTP VERIFICATION MODAL / BANNER ---
if needs_otp or not is_gh_connected:
    with st.expander("🔐 **GamingHelper Session / OTP Login Panel**", expanded=bool(needs_otp)):
        if needs_otp:
            st.markdown(f"""
            <div style="background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 10px; padding: 14px; margin-bottom: 12px;">
                <div style="font-size: 14px; font-weight: 800; color: #f87171;">
                    🔒 Session Out: OTP Verification Required
                </div>
                <div style="font-size: 12px; color: #cbd5e1; margin-top: 4px;">
                    GamingHelper ne aapke email (<b>{otp_email}</b>) par 6-digit OTP bheja hai. Yahan enter karke 1-click verify karein:
                </div>
            </div>
            """, unsafe_allow_html=True)
            col_otp_in, col_otp_btn, col_otp_res = st.columns([2, 1.4, 1.2])
            with col_otp_in:
                portal_otp = st.text_input("Enter 6-Digit OTP", max_chars=6, placeholder="e.g. 247563", key="portal_gh_otp_field")
            with col_otp_btn:
                if st.button("🔑 Verify & Sync Bets", type="primary", use_container_width=True, key="btn_portal_verify_gh_otp"):
                    if portal_otp and len(portal_otp.strip()) == 6:
                        with st.spinner("Verifying OTP with GamingHelper..."):
                            v_res = verify_gaming_helper_otp(portal_otp.strip(), challenge_id=otp_cid)
                            if v_res.get("success"):
                                st.success("✅ Logged in successfully! Refreshing matches...")
                                st.session_state["gh_needs_otp"] = False
                                st.session_state["live_gh_connected"] = True
                                p_fresh = get_active_punter_matches(force_refresh=True)
                                st.session_state["live_punter_matches"] = p_fresh.get("matches", [])
                                st.rerun()
                            else:
                                st.error(f"❌ Verification failed: {v_res.get('error')}")
                    else:
                        st.warning("Kripya 6-digit OTP code enter karein.")
            with col_otp_res:
                if st.button("📩 Resend OTP", use_container_width=True, key="btn_portal_resend_gh_otp"):
                    with st.spinner("Resending OTP..."):
                        r_res = resend_gaming_helper_otp(challenge_id=otp_cid)
                        if r_res.get("success"):
                            st.info("Naya OTP email par bhej diya gaya hai!")
                        else:
                            st.warning(f"⚠️ {r_res.get('error')}")
        else:
            st.info("Agar session out ho gaya ho ya naye matches sync nahi ho rahe hon, toh niche click karke OTP mangwayein:")
            if st.button("📲 Re-authenticate / Send Login OTP", key="btn_trigger_fresh_login", use_container_width=True):
                with st.spinner("Sending login request to GamingHelper..."):
                    l_res = login_gaming_helper(creds.get("phone", "7028163937"), creds.get("password", "Jayshri@0411"))
                    if l_res.get("otpRequired"):
                        st.session_state["gh_needs_otp"] = True
                        st.session_state["gh_otp_email"] = l_res.get("email", "registered email")
                        st.session_state["gh_challenge_id"] = l_res.get("challengeId")
                        st.session_state["live_gh_connected"] = False
                        st.rerun()
                    elif l_res.get("success"):
                        st.session_state["live_gh_connected"] = True
                        st.success("Session refreshed successfully!")
                        st.rerun()
                    else:
                        st.error(f"Login failed: {l_res.get('error')}")

# --- 2 Dedicated Pure Toss Tabs ---
vip_tabs = st.tabs([
    "🔴 Live Toss",
    "✅ Toss Results"
])


# ==============================================================================
# TAB 1: LIVE BETS & ACTIONABLE VERDICTS
# ==============================================================================
with vip_tabs[0]:
    # Smart Date-Aware & Live Status Filtering (Only pending, actionable coin tosses in Live Toss)
    cur_ist = now_ist()
    today_iso = format_ist(cur_ist, "%Y-%m-%d")
    tomorrow_iso = format_ist(cur_ist + timedelta(days=1), "%Y-%m-%d")

    all_done_list = load_toss_results()
    def _is_toss_completed(m):
        mid = str(m.get("id"))
        ta = m.get("team_a_name", "")
        tb = m.get("team_b_name", "")
        m_date = str(m.get("match_date") or "").split("T")[0].strip() or today_iso
        for r in all_done_list:
            if str(r.get("match_id")) == mid:
                act_w = str(r.get("actual_winner") or "")
                if act_w and "pending" not in act_w.lower():
                    return True
            if str(r.get("match_date") or "").strip() == m_date and ((match_teams_fuzzy(ta, str(r.get("team_a", ""))) and match_teams_fuzzy(tb, str(r.get("team_b", "")))) or (match_teams_fuzzy(tb, str(r.get("team_a", ""))) and match_teams_fuzzy(ta, str(r.get("team_b", ""))))):
                act_w = str(r.get("actual_winner") or "")
                if act_w and "pending" not in act_w.lower():
                    return True
        t_clean = (m.get("toss_time_str") or m.get("time_str") or "").replace(" IST", "").strip()
        try:
            t_dt = datetime.strptime(f"{m_date} {t_clean}", "%Y-%m-%d %I:%M %p").replace(tzinfo=cur_ist.tzinfo)
            if (cur_ist - t_dt).total_seconds() > 900:
                return True
        except Exception:
            pass
        return False

    c_today_pending = sum(1 for m in all_matches if str(m.get("match_date") or "").split("T")[0].strip() == today_iso and not _is_toss_completed(m))
    c_tomorrow_pending = sum(1 for m in all_matches if str(m.get("match_date") or "").split("T")[0].strip() == tomorrow_iso and not _is_toss_completed(m))
    c_all_pending = sum(1 for m in all_matches if not _is_toss_completed(m))

    date_options = []
    date_iso_map = {}

    lbl_today = f"📅 Today ({format_ist(cur_ist, '%d %b')}) [{c_today_pending}]"
    date_options.append(lbl_today)
    date_iso_map[lbl_today] = today_iso

    lbl_tomorrow = f"📅 Tomorrow ({format_ist(cur_ist + timedelta(days=1), '%d %b')}) [{c_tomorrow_pending}]"
    date_options.append(lbl_tomorrow)
    date_iso_map[lbl_tomorrow] = tomorrow_iso

    lbl_all = f"🌟 All Dates [{c_all_pending}]"
    date_options.append(lbl_all)
    date_iso_map[lbl_all] = "ALL"

    # Smart default: Auto-switch to 'Kal' (Tomorrow) if today's matches are completed (0 pending), otherwise 'Aaj'
    if c_today_pending > 0:
        default_date_idx = 0
    elif c_tomorrow_pending > 0:
        default_date_idx = 1
    else:
        default_date_idx = 0

    cur_punter_pill = st.session_state.get("punter_date_pill_v2")
    matched_pill_opt = None
    if cur_punter_pill:
        for opt in date_options:
            if opt.split(" [")[0] == str(cur_punter_pill).split(" [")[0]:
                matched_pill_opt = opt
                break
        if not matched_pill_opt:
            if any(k in str(cur_punter_pill) for k in ["Today", "Aaj"]):
                matched_pill_opt = date_options[0]
            elif any(k in str(cur_punter_pill) for k in ["Tomorrow", "Kal"]):
                matched_pill_opt = date_options[1]
            elif any(k in str(cur_punter_pill) for k in ["All", "Sabhi"]):
                matched_pill_opt = date_options[2]
            else:
                matched_pill_opt = date_options[default_date_idx]
    if matched_pill_opt:
        st.session_state["punter_date_pill_v2"] = matched_pill_opt
    else:
        st.session_state["punter_date_pill_v2"] = date_options[default_date_idx]

    col_dpill, col_spc = st.columns([2.5, 1])
    with col_dpill:
        selected_date_lbl = st.pills(
            "Fixture Date",
            date_options,
            default=date_options[default_date_idx],
            key="punter_date_pill_v2",
            label_visibility="collapsed"
        )
    if not selected_date_lbl:
        selected_date_lbl = date_options[default_date_idx]

    selected_iso = date_iso_map.get(selected_date_lbl, today_iso)

    # STRICT LIVE TOSS: Never show completed coin flips in Live Toss (completed tosses live in 'Toss Done' tab)
    if selected_iso == "ALL":
        active_matches = [m for m in all_matches if not _is_toss_completed(m)]
    else:
        active_matches = [
            m for m in all_matches
            if str(m.get("match_date") or "").split("T")[0].strip() == selected_iso
            and not _is_toss_completed(m)
        ]

    # Sort matches chronologically by toss time in IST
    def _toss_sort_minutes(m):
        t_str = m.get("toss_time_str") or m.get("time_str") or ""
        clean = t_str.replace("IST", "").strip()
        parts = clean.split()
        if len(parts) >= 2:
            time_part = parts[0]
            meridiem = parts[1].upper()
            try:
                h, mi = [int(x) for x in time_part.split(":")[:2]]
                if meridiem == "PM" and h != 12:
                    h += 12
                elif meridiem == "AM" and h == 12:
                    h = 0
                return h * 60 + mi
            except Exception:
                pass
        elif ":" in clean:
            try:
                h, mi = [int(x) for x in clean.split(":")[:2]]
                return h * 60 + mi
            except Exception:
                pass
        return 9999

    active_matches.sort(key=_toss_sort_minutes)

    if selected_iso == today_iso:
        date_caption_text = f"📅 **Active Live Fixtures (Today):** {format_ist(cur_ist, '%d %b %Y (%A)')}"
    elif selected_iso == tomorrow_iso:
        date_caption_text = f"📅 **Upcoming Fixtures (Tomorrow):** {format_ist(cur_ist + timedelta(days=1), '%d %b %Y (%A)')}"
    else:
        date_caption_text = "📅 **Active Upcoming Fixtures (All Dates):**"
    st.caption(f"{date_caption_text} · Timings in **IST (Indian Standard Time)**")

    # Extract unique tournaments from active matches
    all_live_tournaments = []
    for m in active_matches:
        t = m.get("tournament_name")
        if t and t not in all_live_tournaments:
            all_live_tournaments.append(t)
    all_live_tournaments.sort()

    tour_live_opts = ["🌟 All Tournaments"] + [
        f"{t} ({sum(1 for m in active_matches if m.get('tournament_name') == t)})"
        for t in all_live_tournaments
    ]

    st.markdown("""
    <div style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 10px 16px; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">
        <span style="font-size: 13.5px; font-weight: 700; color: #10b981;">🛡️ <b>Dual-Tier Engine (v8):</b> 💎 Top Pick (3+ Pillars · 70%+) & ⚡ Value Lean (2+ Pillars · 63–69% · Half Stake) <span style="color:#94a3b8; font-weight:600;">(With Real Historical Win %)</span></span>
        <span style="font-size: 12px; color: #cbd5e1; background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.4); padding: 4px 10px; border-radius: 6px;">🎯 Daily Quota: Top 3-4 Picks Max</span>
    </div>
    """, unsafe_allow_html=True)

    # --- Search & Filter Bar ---
    col_t_live, col_filter, col_search = st.columns([1.4, 1.4, 1.2])
    with col_t_live:
        selected_live_tour_raw = st.selectbox(
            "🏏 Filter by Tournament",
            tour_live_opts,
            index=0,
            key="select_live_tournament"
        )
        selected_live_tour = selected_live_tour_raw.rsplit(" (", 1)[0] if selected_live_tour_raw != "🌟 All Tournaments" else "🌟 All Tournaments"

    with col_search:
        search_q = st.text_input(
            "🔍 Search Match",
            placeholder="Search teams or league...",
            key="input_search_punter"
        )

    # Filter by Tournament
    filtered_matches = active_matches
    if selected_live_tour != "🌟 All Tournaments":
        filtered_matches = [
            m for m in filtered_matches
            if (m.get("tournament_name") or "").strip().lower() == selected_live_tour.strip().lower()
            or selected_live_tour.strip().lower() in (m.get("tournament_name") or "").strip().lower()
            or (m.get("tournament_name") or "").strip().lower() in selected_live_tour.strip().lower()
        ]

    # Filter by Search Query
    if search_q.strip():
        q = search_q.strip().lower()
        filtered_matches = [
            m for m in filtered_matches
            if q in m["team_a_name"].lower() or q in m["team_b_name"].lower() or q in m["tournament_name"].lower()
        ]

    # Calculate Counts
    total_matches = len(filtered_matches)
    super_satik_matches = [
        m for m in filtered_matches
        if (m.get("action_type") == "PLAY" or m.get("action") == "PLAY")
        and m.get("recommended_team")
    ]
    pass_matches = [
        m for m in filtered_matches
        if m not in super_satik_matches
    ]

    opt_satik = f"🎯 Playable Picks ({len(super_satik_matches)})"
    opt_all = f"🌟 All Matches ({total_matches})"
    opt_skip = f"🟡 Skipped (50-50) ({len(pass_matches)})"
    verdict_options = [opt_satik, opt_all, opt_skip]

    cur_v_pref = st.session_state.get("select_punter_filter_v2")
    if cur_v_pref:
        if any(k in str(cur_v_pref) for k in ["Playable", "Value", "Golden", "Top", "VIP", "Lock", "100%", "सटीक", "70%", "Satik", "74%"]):
            matched_v_opt = opt_satik if len(super_satik_matches) > 0 else opt_all
        elif any(k in str(cur_v_pref) for k in ["Skip", "50-50", "Pass", "पास"]):
            matched_v_opt = opt_skip
        else:
            matched_v_opt = opt_all
        st.session_state["select_punter_filter_v2"] = matched_v_opt
    else:
        st.session_state["select_punter_filter_v2"] = opt_satik if len(super_satik_matches) > 0 else opt_all

    with col_filter:
        filter_type = st.selectbox(
            "🎯 Filter by Verdict",
            verdict_options,
            key="select_punter_filter_v2"
        )

    # Apply Verdict Filter
    if any(k in filter_type for k in ["Playable", "Value", "Golden", "Top", "VIP", "Lock", "100%", "सटीक", "70%", "Satik", "74%"]):
        filtered_list = super_satik_matches
    elif any(k in filter_type for k in ["Skip", "50-50", "Pass", "पास"]):
        filtered_list = pass_matches
    else:
        filtered_list = filtered_matches

    # --- KPI Ticker Strip ---
    k1, k2, k3 = st.columns(3)
    with k1:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-val" style="color: #38bdf8;">{total_matches}</div>
            <div class="kpi-lbl">Total Matches</div>
        </div>
        """, unsafe_allow_html=True)
    with k2:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-val" style="color: #10b981;">{len(super_satik_matches)}</div>
            <div class="kpi-lbl">🎯 Playable Picks (Top + Value)</div>
        </div>
        """, unsafe_allow_html=True)
    with k3:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-val" style="color: #eab308;">{len(pass_matches)}</div>
            <div class="kpi-lbl">🟡 Skipped (50-50)</div>
        </div>
        """, unsafe_allow_html=True)

    st.write("")

    # --- Quick Scan Summary Drawer ---
    if filtered_list:
        summary_rows = []
        crex_s_map = load_crex_overrides()
        for m in filtered_list:
            t_str = m.get("toss_time_str") or "30m prior"
            if "IST" not in t_str and t_str and ":" in t_str:
                t_str = f"{t_str} IST"
            act = m.get("action_type")
            pick = m.get("recommended_team")
            conf = m.get("confidence_pct", 50)

            # CREX bhav calculation for summary table
            oa_s = max(10, min(250, int(m.get("odds_a") or 98)))
            ob_s = max(10, min(250, int(m.get("odds_b") or 80)))
            if pick and pick.lower() in m['team_a_name'].lower():
                fav_s = m.get("team_a_short") or m['team_a_name'][:3].upper()
                fav_o_s = min(oa_s, ob_s)
            elif pick and pick.lower() in m['team_b_name'].lower():
                fav_s = m.get("team_b_short") or m['team_b_name'][:3].upper()
                fav_o_s = min(oa_s, ob_s)
            elif oa_s < ob_s:
                fav_s = m.get("team_a_short") or m['team_a_name'][:3].upper()
                fav_o_s = oa_s
            else:
                fav_s = m.get("team_b_short") or m['team_b_name'][:3].upper()
                fav_o_s = ob_s

            ov_s = crex_s_map.get(str(m.get("id")))
            if not ov_s:
                for k_name, ov in crex_s_map.items():
                    if k_name.lower() in m['team_a_name'].lower() or k_name.lower() in m['team_b_name'].lower() or m['team_a_name'].lower() in k_name.lower() or m['team_b_name'].lower() in k_name.lower():
                        ov_s = ov
                        break
            if ov_s and ov_s.get("back") and ov_s.get("lay"):
                crex_bhav_str = f"[{fav_s}] {ov_s['back']}-{ov_s['lay']}"
            else:
                t_prob_s = max(51, min(95, conf or 50))
                c_bhav_s = int(round((100.0 / t_prob_s - 1.0) * 100))
                b_s = fav_o_s if fav_o_s not in [80, 98] else c_bhav_s
                crex_bhav_str = f"[{fav_s}] {b_s}-{b_s + (1 if b_s < 40 else 2)}"

            if act == "PLAY" and pick:
                if conf >= 70:
                    verdict_str = f"💎 VIP: {pick.upper()} ({conf}% Lock)"
                elif conf >= 60:
                    verdict_str = f"⚡ LEAN: {pick.upper()} ({conf}% Value)"
                else:
                    verdict_str = f"🟡 EDGE: {pick.upper()} ({conf}% Close)"
            elif act == "COMPLETED":
                verdict_str = f"🏆 TOSS WON: {pick.upper()}"
            else:
                verdict_str = "⛔ SKIP (50-50 PASS)"

            summary_rows.append({
                "🪙 Toss Time (IST)": t_str,
                "Match": f"{m['team_a_name']} vs {m['team_b_name']}",
                "Tournament": m.get("tournament_name"),
                "📊 CREX Bhav": crex_bhav_str,
                "📈 Real Win %": m.get("fav_win_pct_summary") or m.get("market_load_text", f"{conf}% Load"),
                "🎯 Predicted Toss Winner": verdict_str
            })

        with st.expander("📋 Quick Summary Table", expanded=True):
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    # --- Match Cards Listing ---
    st.markdown(f"### 🪙 Live Fixtures ({len(filtered_list)} Matches)")

    if not filtered_list:
        st.markdown(f"""
        <div style="background: #0b1120; border: 1px dashed #334155; border-radius: 12px; padding: 36px 20px; text-align: center; margin: 18px 0;">
            <div style="font-size: 32px; margin-bottom: 8px;">🪙</div>
            <div style="font-size: 16px; font-weight: 700; color: #f8fafc;">No Matches Pending in this View</div>
            <div style="font-size: 13px; color: #94a3b8; margin-top: 6px; max-width: 500px; margin-left: auto; margin-right: auto;">
                All scheduled tosses for this selection are either completed or under 50-50 capital protection. Switch to <b>All Matches</b> or check <b>Toss Results</b> for verified outcomes.
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        crex_map = load_crex_overrides()
        all_done_list = load_toss_results()
        done_by_id = {str(r.get("match_id", "")).strip(): r for r in all_done_list if str(r.get("match_id", "")).strip()}

        for idx, m in enumerate(filtered_list):
            mid = m["id"]
            widget_mid = f"{mid}_{idx}"
            t_a = m["team_a_name"]
            t_b = m["team_b_name"]
            short_a = m.get("team_a_short") or t_a[:3].upper()
            short_b = m.get("team_b_short") or t_b[:3].upper()
            tour = m["tournament_name"]
            time_str = m["time_str"]
            toss_str = m["toss_time_str"]
            odds_a = m.get("odds_a", 98)
            odds_b = m.get("odds_b", 80)
            venue = m.get("venue", "Cricket Ground")
            act = m["action_type"]
            pick = m.get("recommended_team")
            conf = m.get("confidence_pct", 50)
            rating = m.get("stake_rating", "NORMAL")
            consensus_badge = m.get("consensus_badge", rating)
            load_text = m.get("market_load_text", f"{conf}% Market Load")
            clues_text = m.get("cricketclues_status", "Standard Market Line")
            rat = m.get("punter_rationale", "")
            pil_aligned = m.get("pillars_aligned") or (4 if conf >= 70 else 2)
            pil_total = m.get("pillars_total") or 5
            pil_list = m.get("active_pillars_list", [])

            # Calculate CREX-style Win Probability & [Fav] [Back] [Lay] boxes
            oa = max(10, min(250, int(odds_a or 98)))
            ob = max(10, min(250, int(odds_b or 80)))
            if pick and pick.lower() in t_a.lower():
                fav_name = t_a
                fav_short = short_a
                fav_odds = min(oa, ob)
            elif pick and pick.lower() in t_b.lower():
                fav_name = t_b
                fav_short = short_b
                fav_odds = min(oa, ob)
            elif oa < ob:
                fav_name = t_a
                fav_short = short_a
                fav_odds = oa
            else:
                fav_name = t_b
                fav_short = short_b
                fav_odds = ob

            # Bhav Resolution Hierarchy:
            # 1. Manual User Override (if saved by user)
            override = crex_map.get(str(mid))
            if not override:
                for k_name, ov in crex_map.items():
                    if k_name.lower() in t_a.lower() or k_name.lower() in t_b.lower() or t_a.lower() in k_name.lower() or t_b.lower() in k_name.lower():
                        override = ov
                        break

            # 2. Real-time Automated CREX Live Scraper
            crex_live = get_crex_live_bhav(t_a, t_b, match_id=str(mid))

            if override and override.get("back") and override.get("lay"):
                back_num = override["back"]
                lay_num = override["lay"]
                if override.get("fav_team"):
                    fav_name = override["fav_team"]
                    fav_short = short_a if fav_name == t_a else (short_b if fav_name == t_b else fav_name[:3].upper())
                implied_win_pct = int(round(100.0 / (1.0 + (back_num / 100.0)))) if back_num > 0 else 50
                display_win_prob = conf if (conf and conf > 50) else implied_win_pct
                bhav_badge = "CREX (Manual Override)"
                bhav_badge_color = "#f59e0b"
                bhav_badge_bg = "#451a03"
                bhav_badge_border = "#b45309"
            elif crex_live and crex_live.get("back") and crex_live.get("lay"):
                back_num = crex_live["back"]
                lay_num = crex_live["lay"]
                if crex_live.get("fav_team"):
                    fav_name = crex_live["fav_team"]
                    fav_short = crex_live.get("fav_short") or (short_a if fav_name == t_a else (short_b if fav_name == t_b else fav_name[:3].upper()))
                display_win_prob = crex_live.get("win_prob") or (conf if (conf and conf > 50) else 50)
                bhav_badge = "🟢 CREX Live (Auto)"
                bhav_badge_color = "#34d399"
                bhav_badge_bg = "#064e3b"
                bhav_badge_border = "#059669"
            else:
                target_prob = max(51, min(95, conf or 50))
                calc_bhav = int(round((100.0 / target_prob - 1.0) * 100))
                if fav_odds not in [80, 98]:
                    back_num = fav_odds
                else:
                    back_num = calc_bhav

                if back_num < 40:
                    lay_num = back_num + 1
                elif back_num < 70:
                    lay_num = back_num + 2
                else:
                    lay_num = back_num + (3 if back_num < 80 else 2)

                display_win_prob = target_prob
                bhav_badge = "⚡ CREX Market Model"
                bhav_badge_color = "#38bdf8"
                bhav_badge_bg = "#082f49"
                bhav_badge_border = "#0284c7"

            # Check if this match was already marked done (Strictly for THIS match ID or THIS DATE)
            is_already_done = False
            prev_r = {}
            m_target_date = str(m.get("match_date")).split("T")[0].strip() if m.get("match_date") else today_iso

            # Check if scheduled toss time is still in the future
            is_toss_in_future = False
            try:
                t_clean = toss_str.replace(" IST", "").strip()
                m_toss_dt = datetime.strptime(f"{m_target_date} {t_clean}", "%Y-%m-%d %I:%M %p").replace(tzinfo=cur_ist.tzinfo)
                if m_toss_dt > cur_ist:
                    is_toss_in_future = True
            except Exception:
                pass

            # Fast O(1) ID lookup first
            mid_str = str(mid).strip()
            if mid_str in done_by_id:
                cand_r = done_by_id[mid_str]
                if not (is_toss_in_future and not cand_r.get("is_manual")):
                    act_win = str(cand_r.get("actual_winner", "")).strip()
                    if act_win and "pending" not in act_win.lower():
                        is_already_done = True
                        prev_r = cand_r
                    else:
                        prev_r = cand_r

            if not is_already_done:
                for r in all_done_list:
                    r_mid = str(r.get("match_id", "")).strip()
                    r_date = str(r.get("match_date", "")).strip()

                    # Must match exact ID OR (must be same date AND matching teams)
                    id_matches = bool(r_mid and (r_mid == mid_str))
                    same_date = bool(r_date and (r_date == m_target_date))

                    r_ta = r.get("team_a", "")
                    r_tb = r.get("team_b", "")
                    names_match = (
                        (match_teams_fuzzy(t_a, r_ta) and match_teams_fuzzy(t_b, r_tb)) or
                        (match_teams_fuzzy(t_a, r_tb) and match_teams_fuzzy(t_b, r_ta))
                    )

                    if id_matches or (same_date and names_match):
                        # Guard against premature resolutions: If scheduled toss is in future and not manually confirmed, ignore
                        if is_toss_in_future and not r.get("is_manual"):
                            continue
                        act_win = str(r.get("actual_winner", "")).strip()
                        if act_win and "pending" not in act_win.lower():
                            is_already_done = True
                            prev_r = r
                            break
                        elif not prev_r:
                            prev_r = r

            if is_already_done:
                prev_win = prev_r.get("actual_winner", t_a)
                prev_dec = prev_r.get("toss_decision", "Bowl")
                prev_out = prev_r.get("outcome_label", "PASS")
                exp_title = f"✏️ Update Result: {t_a} vs {t_b} (Winner: {prev_win})"
                btn_text = "🔄 Update Result"
                toss_status_pill = f"""<span style="background: #064e3b; color: #34d399; border: 1px solid #10b981; padding: 4px 12px; border-radius: 12px; font-weight: 900; font-size: 12px;">🏆 TOSS DONE: {prev_win}</span>"""
            else:
                prev_win = t_a
                prev_dec = "Bowl"
                prev_out = "PENDING"
                exp_title = f"🪙 Mark Toss Outcome: {t_a} vs {t_b}"
                btn_text = "💾 Save Toss Result"
                toss_status_pill = f"""<span class="toss-time-pill">🪙 TOSS: {toss_str}</span>"""

            # Tipper Pattern Engine stats & streaks
            pattern_badge = m.get("pattern_badge")
            a_strk = m.get("a_streak_str")
            b_strk = m.get("b_streak_str")
            a_last5 = m.get("a_last5_str")
            b_last5 = m.get("b_last5_str")
            if not pattern_badge or not a_strk:
                try:
                    from core.engine.pattern_engine import evaluate_toss_pattern_rule
                    pat_e = evaluate_toss_pattern_rule(t_a, t_b, tour)
                    pattern_badge = pat_e.get("pattern_badge", "⚖️ Neutral Toss Rotation")
                    a_strk = pat_e.get("a_streak_str", "Even")
                    b_strk = pat_e.get("b_streak_str", "Even")
                    a_last5 = pat_e.get("a_last5_str", "None")
                    b_last5 = pat_e.get("b_last5_str", "None")
                except Exception:
                    pattern_badge = "⚖️ Neutral Toss Rotation"
                    a_strk = "Even"
                    b_strk = "Even"
                    a_last5 = "None"
                    b_last5 = "None"

            with st.container():
                st.markdown(f"""
                <div class="vip-match-card">
                    <div class="card-top-row">
                        <span class="tour-pill">🏏 {tour}</span>
                        <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                            {toss_status_pill}
                            <span class="match-time-pill">⏰ Match Start: {time_str}</span>
                        </div>
                    </div>
                    <div class="teams-arena">
                        <div class="team-title">{t_a} <span style="font-size:14px; color:#38bdf8;">({short_a})</span></div>
                        <div class="vs-emblem">VS</div>
                        <div class="team-title" style="text-align:right;"><span style="font-size:14px; color:#38bdf8;">({short_b})</span> {t_b}</div>
                    </div>
                    <div style="background: rgba(15, 23, 42, 0.85); border: 1px solid rgba(56, 189, 248, 0.25); border-radius: 8px; padding: 10px 14px; margin: 10px 0; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 13px; font-weight: 800; color: #38bdf8;">🪙 Asian Toss Line:</span>
                            <span style="background: #0f172a; border: 1px solid #334155; color: #f8fafc; padding: 3px 10px; border-radius: 6px; font-size: 12px; font-weight: 800;">[{short_a}: {oa}] · [{short_b}: {ob}]</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 13px; font-weight: 800; color: #34d399;">🎯 Model Edge:</span>
                            <span style="background: {'rgba(16, 185, 129, 0.2)' if conf >= 70 else 'rgba(234, 179, 8, 0.2)'}; border: 1px solid {'#10b981' if conf >= 70 else '#eab308'}; color: {'#34d399' if conf >= 70 else '#facc15'}; padding: 3px 10px; border-radius: 6px; font-size: 12px; font-weight: 900;">{conf}% Probability</span>
                        </div>
                    </div>
                    <div style="background: #090e1a; border: 1px solid #1e293b; border-radius: 8px; padding: 10px 14px; margin: 10px 0;">
                        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px;">
                            <span style="font-size: 13px; font-weight: 800; color: #38bdf8; display: flex; align-items: center; gap: 6px;">
                                <span>🔄</span> <b>Pattern Rule:</b> <span style="background: #1e293b; color: #f8fafc; padding: 2px 8px; border-radius: 6px; font-size: 12px; border: 1px solid #334155;">{pattern_badge}</span>
                            </span>
                            <span style="font-size: 11px; color: #94a3b8; font-weight: 700;">Ground-Truth & Trend Math</span>
                        </div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
                            <div style="background: #0d1527; border: 1px solid #1e2d4a; padding: 6px 10px; border-radius: 6px; display: flex; justify-content: space-between; align-items: center;">
                                <span style="font-size: 12px; color: #cbd5e1; font-weight: 700;">{short_a} Streak:</span>
                                <span style="font-size: 12px; font-weight: 900; color: #f59e0b;">{a_strk} <span style="font-size: 11px; color: #94a3b8; font-weight: 600;">({a_last5})</span></span>
                            </div>
                            <div style="background: #0d1527; border: 1px solid #1e2d4a; padding: 6px 10px; border-radius: 6px; display: flex; justify-content: space-between; align-items: center;">
                                <span style="font-size: 12px; color: #cbd5e1; font-weight: 700;">{short_b} Streak:</span>
                                <span style="font-size: 12px; font-weight: 900; color: #f59e0b;">{b_strk} <span style="font-size: 11px; color: #94a3b8; font-weight: 600;">({b_last5})</span></span>
                            </div>
                        </div>
                    </div>
                """, unsafe_allow_html=True)

                # --- COMPACT ACTION VERDICT BANNER ---
                if is_already_done:
                    outcome_c = "#34d399" if prev_r.get("outcome") == "PASS" else ("#f87171" if prev_r.get("outcome") == "FAIL" else "#fbbf24")
                    outcome_bg = "#064e3b" if prev_r.get("outcome") == "PASS" else ("#450a0a" if prev_r.get("outcome") == "FAIL" else "#451a03")
                    outcome_bd = "#10b981" if prev_r.get("outcome") == "PASS" else ("#ef4444" if prev_r.get("outcome") == "FAIL" else "#d97706")
                    st.markdown(f"""
                    <div style="background: {outcome_bg}; border: 2px solid {outcome_bd}; border-radius: 10px; padding: 14px 18px; margin: 10px 0;">
                        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                            <span style="font-size: 18px; font-weight: 900; color: {outcome_c};">🏆 TOSS COMPLETED: {prev_win.upper()} ({prev_dec.upper()})</span>
                            <span style="background: {outcome_bg}; color: {outcome_c}; border: 1px solid {outcome_bd}; padding: 4px 14px; border-radius: 12px; font-weight: 900; font-size: 13px;">
                                {prev_r.get('outcome_label')}
                            </span>
                        </div>
                        <div style="font-size: 13px; color: #ffffff; margin-top: 8px; font-weight: 600;">
                            🎯 <b>Pre-Match Prediction:</b> <b>{('🟢 PLAY: ' + prev_r['recommended_team']) if prev_r.get('recommended_team') else '🟡 STRICT SKIP (50-50 NEUTRAL)'}</b> · 📜 Verified in Tab 2 (Toss Done Audit Log)
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                elif act == "PLAY" and pick:
                    if conf >= 70:
                        st.markdown(f"""
                        <div class="verdict-play" style="border: 2.5px solid #10b981; background: linear-gradient(135deg, #022c22 0%, #064e3b 100%); border-radius: 12px; padding: 14px 18px; box-shadow: 0 4px 20px rgba(16, 185, 129, 0.45); margin: 10px 0;">
                            <div class="verdict-header" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                                <span class="verdict-team-name" style="font-size: 22px; font-weight: 900; color: #34d399; letter-spacing: -0.3px;">
                                    🟢 TOP PICK: <span style="color: #ffffff; text-decoration: underline;">{pick.upper()}</span>
                                </span>
                                <span class="verdict-badge" style="background: #f59e0b; color: #000000; font-weight: 900; font-size: 13px; padding: 4px 12px; border-radius: 20px;">
                                    💎 {consensus_badge} · {conf}% LOAD
                                </span>
                            </div>
                            <div style="display: flex; gap: 8px; margin: 8px 0 6px 0; flex-wrap: wrap;">
                                <span style="background: #091e17; border: 1px solid #10b981; color: #a7f3d0; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 800;">
                                    📊 {load_text}
                                </span>
                                <span style="background: #091e2b; border: 1px solid #38bdf8; color: #bae6fd; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 700;">
                                    📈 {m.get('fav_win_pct_summary') or clues_text}
                                </span>
                            </div>
                            <div class="verdict-reason" style="color: #ffffff; font-size: 14.5px; font-weight: 600; margin-top: 4px;">
                                💡 <b>Top Pick Analysis (Flat Stake 2.5%):</b> {rat}
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                    elif conf >= 60:
                        st.markdown(f"""
                        <div class="verdict-play" style="border: 2px solid #0284c7; background: linear-gradient(135deg, #082f49 0%, #0c4a6e 100%); border-radius: 12px; padding: 14px 18px; box-shadow: 0 4px 15px rgba(2, 132, 199, 0.35); margin: 10px 0;">
                            <div class="verdict-header" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                                <span class="verdict-team-name" style="font-size: 20px; font-weight: 900; color: #38bdf8; letter-spacing: -0.3px;">
                                    ⚡ VALUE LEAN: <span style="color: #ffffff; text-decoration: underline;">{pick.upper()}</span>
                                </span>
                                <span class="verdict-badge" style="background: #0284c7; color: #ffffff; font-weight: 900; font-size: 13px; padding: 4px 12px; border-radius: 20px;">
                                    ⚡ {consensus_badge} · HALF STAKE
                                </span>
                            </div>
                            <div style="display: flex; gap: 8px; margin: 8px 0 6px 0; flex-wrap: wrap;">
                                <span style="background: #082f49; border: 1px solid #0284c7; color: #bae6fd; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 800;">
                                    📊 {load_text}
                                </span>
                                <span style="background: #0f172a; border: 1px solid #38bdf8; color: #e0f2fe; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 700;">
                                    📈 {m.get('fav_win_pct_summary') or clues_text}
                                </span>
                            </div>
                            <div class="verdict-reason" style="color: #e0f2fe; font-size: 14px; font-weight: 600; margin-top: 4px;">
                                💡 <b>Value Lean (Half Stake 1–1.25%):</b> {rat}
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.markdown(f"""
                        <div class="verdict-play" style="border: 2px solid #d97706; background: linear-gradient(135deg, #2d1803 0%, #452203 100%); border-radius: 12px; padding: 14px 18px; box-shadow: 0 4px 15px rgba(217, 119, 6, 0.25); margin: 10px 0;">
                            <div class="verdict-header" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                                <span class="verdict-team-name" style="font-size: 19px; font-weight: 900; color: #fbbf24; letter-spacing: -0.3px;">
                                    🟡 SLIGHT EDGE: <span style="color: #ffffff; text-decoration: underline;">{pick.upper()}</span>
                                </span>
                                <span class="verdict-badge" style="background: #d97706; color: #ffffff; font-weight: 900; font-size: 13px; padding: 4px 12px; border-radius: 20px;">
                                    ⚠️ 50-50 CLOSE CALL · {conf}% LEAN
                                </span>
                            </div>
                            <div style="display: flex; gap: 8px; margin: 8px 0 6px 0; flex-wrap: wrap;">
                                <span style="background: #2d1803; border: 1px solid #d97706; color: #fde68a; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 800;">
                                    📊 {load_text}
                                </span>
                                <span style="background: #0f172a; border: 1px solid #334155; color: #cbd5e1; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 700;">
                                    🌐 {clues_text}
                                </span>
                            </div>
                            <div class="verdict-reason" style="color: #fef3c7; font-size: 13.5px; font-weight: 500; margin-top: 4px;">
                                💡 <b>Edge Analysis:</b> {rat}
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div class="verdict-skip" style="border: 2px solid #ef4444; background: linear-gradient(135deg, #2b0707 0%, #450a0a 100%); border-radius: 12px; padding: 14px 18px; margin: 10px 0;">
                        <div class="verdict-header" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                            <span class="verdict-team-name" style="font-size: 18px; font-weight: 900; color: #f87171;">
                                🟡 50-50 COIN FLIP (STRICT PASS)
                            </span>
                            <span class="verdict-badge" style="background: #1c0303; color: #f87171; border: 1.5px solid #ef4444; padding: 4px 12px; border-radius: 20px; font-weight: 900; font-size: 12px;">
                                ⛔ NO BET (50-50 PASS)
                            </span>
                        </div>
                        <div class="verdict-reason" style="color: #fecaca; font-size: 13.5px; font-weight: 500; margin-top: 6px;">
                            💡 {rat}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                # --- INTERACTIVE 1-CLICK TOSS DONE ACTION EXPANDER ---
                with st.expander(exp_title, expanded=False):
                    if is_already_done:
                        st.info(f"ℹ️ **Current Result:** 🏆 Winner: **{prev_win}** ({prev_dec}) · Outcome: **{prev_r.get('outcome_label')}**")

                    if is_already_done:
                        col_m1, col_m2, col_m3, col_m4 = st.columns([2, 2, 1.4, 1.4])
                    else:
                        col_m1, col_m2, col_m3 = st.columns([2, 2, 1.5])
                        col_m4 = None

                    with col_m1:
                        actual_winner = st.selectbox(
                            "🏆 Toss Winner",
                            [t_a, t_b],
                            index=0 if prev_win == t_a else 1,
                            key=f"win_select_{widget_mid}"
                        )
                    with col_m2:
                        toss_dec = st.selectbox(
                            "Toss Decision",
                            ["Bowl (Bowl First)", "Bat (Bat First)"],
                            index=0 if "bowl" in prev_dec.lower() else 1,
                            key=f"dec_select_{widget_mid}"
                        )
                    with col_m3:
                        st.write("")
                        st.write("")
                        if st.button(btn_text, key=f"btn_save_done_{widget_mid}", type="primary", use_container_width=True):
                            dec_str = "Bowl" if "Bowl" in toss_dec else "Bat"
                            m_target_date = str(m.get("match_date")).split("T")[0].strip() if m.get("match_date") else today_iso
                            saved_rec = save_toss_result(
                                match_id=mid,
                                match_name=f"{t_a} vs {t_b}",
                                tournament=tour,
                                toss_time_ist=toss_str,
                                match_date=m_target_date,
                                team_a=t_a,
                                team_b=t_b,
                                recommended_team=pick,
                                stake_rating=rating,
                                actual_winner=actual_winner,
                                toss_decision=dec_str,
                                is_manual=True
                            )
                            st.success(f"✅ Toss Result Updated! Outcome: {saved_rec['outcome_label']}")
                            st.rerun()
                    if col_m4 is not None:
                        with col_m4:
                            st.write("")
                            st.write("")
                            if st.button("🗑️ Delete Result", key=f"btn_tab1_del_{widget_mid}", use_container_width=True):
                                delete_toss_result(mid)
                                st.warning("⚠️ Result deleted!")
                                st.rerun()

                with st.expander(f"📊 5 Super Pillars Syndicate Breakdown: {t_a} vs {t_b}", expanded=(conf >= 70)):
                    st.markdown(f"""
                    <div style="background: #090e1a; border: 1px solid #1e293b; border-radius: 8px; padding: 14px; margin-bottom: 10px;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; border-bottom: 1px solid #1e293b; padding-bottom: 8px;">
                            <span style="font-size: 13px; font-weight: 900; color: #38bdf8;">🏛️ 5 Super Pillars Confluence:</span>
                            <span style="background: {'rgba(16, 185, 129, 0.2)' if conf >= 70 and pil_aligned >= 3 else 'rgba(234, 179, 8, 0.2)'}; color: {'#34d399' if conf >= 70 and pil_aligned >= 3 else '#facc15'}; border: 1px solid {'#10b981' if conf >= 70 and pil_aligned >= 3 else '#eab308'}; padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: 800;">
                                {'💎 ' + str(pil_aligned) if pil_aligned >= 3 else str(pil_aligned)}/{pil_total} Pillars Aligned ({conf}%)
                            </span>
                        </div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px;">
                            <div style="background: #0d1527; border: 1px solid #1e2d4a; padding: 10px; border-radius: 6px;">
                                <div style="font-size: 12px; color: #94a3b8; font-weight: 700; margin-bottom: 4px;">Form & Streaks</div>
                                <div style="font-size: 13px; color: #f8fafc; font-weight: 800;">{short_a}: <span style="color:#f59e0b;">{a_strk}</span> ({a_last5})</div>
                                <div style="font-size: 13px; color: #f8fafc; font-weight: 800;">{short_b}: <span style="color:#f59e0b;">{b_strk}</span> ({b_last5})</div>
                            </div>
                            <div style="background: #0d1527; border: 1px solid #1e2d4a; padding: 10px; border-radius: 6px;">
                                <div style="font-size: 12px; color: #94a3b8; font-weight: 700; margin-bottom: 4px;">Pattern & Asian Line</div>
                                <div style="font-size: 13px; color: #38bdf8; font-weight: 800;">{pattern_badge}</div>
                                <div style="font-size: 11px; color: #94a3b8; margin-top: 2px;">Asian Line: [{short_a}: {oa}] · [{short_b}: {ob}]</div>
                            </div>
                        </div>
                        <div style="background: #0f172a; border-left: 3px solid {'#10b981' if conf >= 70 else '#eab308'}; padding: 10px 12px; border-radius: 4px;">
                            <div style="font-size: 12px; font-weight: 800; color: {'#34d399' if conf >= 70 else '#facc15'}; margin-bottom: 4px;">
                                {'💎 High-Conviction Multi-Source Pick' if conf >= 70 else '⚠️ 50-50 Capital Protection (Pass)'}
                            </div>
                            <div style="font-size: 13px; color: #e2e8f0; line-height: 1.5;">
                                {rat}
                            </div>
                            <div style="font-size: 11px; color: #94a3b8; margin-top: 6px;">
                                💰 <b>Staking Discipline:</b> {'Disciplined Flat Stake (2.5% max bankroll limit).' if conf >= 70 else 'Strict Pass — Avoid 50-50 coin flip gambles.'}
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                st.markdown("</div>", unsafe_allow_html=True)


# ==============================================================================
# TAB 2: TOSS DONE & PASS/FAIL ACCURACY AUDIT TRACKER
# ==============================================================================
with vip_tabs[1]:
    col_head_t2, col_btn_t2 = st.columns([2.5, 1.2])
    with col_head_t2:
        st.header("📜 Toss Results & Audit History")
    with col_btn_t2:
        if st.button("⚡ Sync Latest Results", key="btn_tg_sync_tab2", use_container_width=True):
            with st.spinner("Fetching latest toss outcomes from GamingHelper, Telegram, Cricbuzz & CREX..."):
                tg_count = 0
                cb_count = 0
                crex_count = 0
                try:
                    sync_completed_tosses_from_gaming_helper(run_resolvers=False)
                except Exception as e_gh:
                    print(f"[SyncNotice] GH sync: {e_gh}")
                try:
                    tg_count = auto_update_toss_results_from_telegram()
                except Exception as e_tg:
                    print(f"[SyncNotice] Telegram sync: {e_tg}")
                try:
                    cb_count = sync_live_toss_results_from_cricbuzz()
                except Exception as e_cb:
                    print(f"[SyncNotice] Cricbuzz sync: {e_cb}")
                try:
                    crex_count = sync_live_toss_results_from_crex()
                except Exception as e_cr:
                    print(f"[SyncNotice] CREX sync: {e_cr}")
                st.session_state.pop("_punter_data_ts", None)
                total_updated = tg_count + cb_count + crex_count
                if total_updated > 0:
                    st.success(f"✅ {total_updated} Toss results updated! (Telegram: {tg_count}, Cricbuzz: {cb_count}, CREX: {crex_count})")
                else:
                    st.info("ℹ️ Live sources checked (Telegram/Cricbuzz/CREX). Local club/regional matches not listed on public feeds can be marked 1-click below.")
                st.rerun()

    with st.expander("📊 Empirical Rule Backtest (2024–2026 Real Archive)", expanded=False):
        try:
            from core.engine.historical_archive import run_empirical_rule_backtest
            bt = run_empirical_rule_backtest()
            st.caption(f"Chronological out-of-sample check across **{bt['matches_evaluated']}** verified matches (no future lookahead):")
            if bt["rules"]:
                st.dataframe(pd.DataFrame(bt["rules"]), use_container_width=True, hide_index=True)
            else:
                st.info("No rule samples available yet. Click 'Sync 2024–26 Archive' above.")
        except Exception as e_bt:
            st.warning(f"Backtest unavailable: {e_bt}")

    with st.expander("💾 Backup & Restore / Cloud Save (Server Restart Safety)", expanded=False):
        st.caption("Streamlit Cloud par server reboot hone par temporary files reset ho jati hain. Apne manual Toss Done updates ko JSON backup me save/restore karein ya `GITHUB_TOKEN` se GitHub par permanent save karein.")
        col_bk1, col_bk2, col_bk3 = st.columns(3)
        with col_bk1:
            _cur_results_json = json.dumps(load_toss_results(), indent=2, ensure_ascii=False)
            st.download_button(
                label="📥 Download Results Backup",
                data=_cur_results_json,
                file_name=f"toss_results_backup_{today_iso}.json",
                mime="application/json",
                use_container_width=True,
                key="btn_download_toss_backup"
            )
        with col_bk2:
            _up_bk = st.file_uploader(
                "Upload Backup JSON",
                type=["json"],
                label_visibility="collapsed",
                key="uploader_toss_backup"
            )
            if _up_bk is not None:
                if st.button("📤 Restore Uploaded Backup", key="btn_restore_toss_backup", use_container_width=True):
                    try:
                        _bk_data = json.loads(_up_bk.getvalue().decode("utf-8"))
                        _restored_n = merge_backup_toss_results(_bk_data)
                        st.session_state.pop("_punter_data_ts", None)
                        st.success(f"✅ {_restored_n} matches backup se restore/merge ho gaye!")
                        st.rerun()
                    except Exception as _e_bk:
                        st.error(f"Backup restore failed: {_e_bk}")
        with col_bk3:
            _gh_tok = ""
            try:
                _gh_tok = st.secrets.get("GITHUB_TOKEN", "")
            except Exception:
                _gh_tok = ""
            if not _gh_tok:
                _gh_tok = os.environ.get("GITHUB_TOKEN", "")
            if _gh_tok:
                if st.button("☁️ Save to GitHub (Permanent)", key="btn_save_github_backup", use_container_width=True):
                    with st.spinner("Saving toss results to GitHub..."):
                        _gh_res = sync_results_file_to_github(github_token=_gh_tok)
                        if _gh_res.get("success"):
                            st.success(_gh_res.get("message", "✅ Saved to GitHub!"))
                        else:
                            st.error(_gh_res.get("error", "GitHub save failed."))
            else:
                st.info("☁️ Permanent Cloud Save ke liye Streamlit Secrets me `GITHUB_TOKEN` add karein; tab tak Download/Restore Backup use karein.")

    completed_results = load_toss_results()

    if not completed_results:
        st.markdown("""
        <div style="background: #0f172a; border: 1px dashed #334155; border-radius: 14px; padding: 24px; text-align: center; margin: 16px 0;">
            <div style="font-size: 32px; margin-bottom: 8px;">🪙</div>
            <h3 style="color: #f59e0b; margin: 0 0 6px 0; font-size: 18px;">No Completed Tosses Recorded Yet</h3>
            <p style="color: #94a3b8; font-size: 14px; margin: 0 auto;">
                Toss outcomes will be recorded here automatically via live sync or manual confirmation.
            </p>
        </div>
        """, unsafe_allow_html=True)
    else:
        # Clean Date Filter: Strictly count confirmed completed tosses
        today_done_count = sum(1 for r in completed_results if r.get("match_date") == today_iso and r.get("outcome") != "PENDING" and not is_winner_unresolved(r.get("actual_winner")))
        yesterday_done_count = sum(1 for r in completed_results if r.get("match_date") == yesterday_iso and r.get("outcome") != "PENDING" and not is_winner_unresolved(r.get("actual_winner")))
        total_done_count = sum(1 for r in completed_results if r.get("outcome") != "PENDING" and not is_winner_unresolved(r.get("actual_winner")))

        # Smart default: Auto-select 'Today' if today has completed tosses, otherwise 'Yesterday' or 'All Dates'
        today_pill_label = f"📅 Today ({today_done_count})"
        yesterday_pill_label = f"📅 Yesterday ({yesterday_done_count})"
        all_done_pill_label = f"🌟 All Dates ({total_done_count})"
        tab3_date_options = [
            today_pill_label,
            yesterday_pill_label,
            all_done_pill_label,
            "🗓️ Custom Date"
        ]

        # Collect distinct historical dates from actual results (excluding today & yesterday)
        available_past_dates = sorted(list(set(
            str(r.get("match_date") or "").split("T")[0].strip()
            for r in completed_results
            if r.get("match_date") and str(r.get("match_date") or "").split("T")[0].strip() not in [today_iso, yesterday_iso]
        )), reverse=True)

        if not available_past_dates:
            available_past_dates = [today_iso]

        default_done_pill = today_pill_label
        cur_done_mode = st.session_state.get("pills_done_date_mode_v4")
        matched_done_opt = None
        if cur_done_mode:
            for opt in tab3_date_options:
                if opt.split(" (")[0] == str(cur_done_mode).split(" (")[0] or opt.split(" · ")[0] == str(cur_done_mode).split(" · ")[0] or ("Custom" in str(cur_done_mode) and "Custom" in opt) or ("अन्य" in str(cur_done_mode) and "Custom" in opt):
                    matched_done_opt = opt
                    break
        if matched_done_opt:
            st.session_state["pills_done_date_mode_v4"] = matched_done_opt
        else:
            st.session_state["pills_done_date_mode_v4"] = default_done_pill

        col_df1, col_df2 = st.columns([2.2, 1.4])
        with col_df1:
            date_filter_mode = st.pills(
                "📅 Filter by Date",
                options=tab3_date_options,
                default=default_done_pill,
                key="pills_done_date_mode_v4"
            )
        with col_df2:
            if date_filter_mode and ("Custom" in date_filter_mode or "Past" in date_filter_mode or "अन्य" in date_filter_mode):
                custom_picked_iso = st.selectbox(
                    "🗓️ Select Date",
                    options=available_past_dates,
                    key="select_custom_done_date",
                    label_visibility="collapsed"
                )
            else:
                custom_picked_iso = today_iso

        # Priority Resolution:
        # 1. If user clicked Today
        if date_filter_mode and ("Today" in date_filter_mode or "आज" in date_filter_mode):
            selected_date_val = today_iso
            kpi_title_ctx = f"Today ({today_iso})"
        # 2. If user clicked Yesterday
        elif date_filter_mode and ("Yesterday" in date_filter_mode or "कल" in date_filter_mode):
            selected_date_val = yesterday_iso
            kpi_title_ctx = f"Yesterday ({yesterday_iso})"
        # 3. If user clicked Other Past Date
        elif date_filter_mode and ("Custom" in date_filter_mode or "Past" in date_filter_mode or "अन्य" in date_filter_mode):
            selected_date_val = custom_picked_iso
            kpi_title_ctx = f"Date: {custom_picked_iso}"
        # 4. If user clicked All dates
        elif date_filter_mode and ("All" in date_filter_mode or "सभी" in date_filter_mode):
            selected_date_val = "ALL"
            kpi_title_ctx = "All Dates"
        # 5. Default fallback: Always Today!
        else:
            selected_date_val = today_iso
            kpi_title_ctx = f"Today ({today_iso})"

        # Dynamically compute KPIs strictly for the SELECTED DATE!
        if selected_date_val != "ALL":
            active_date_results = [r for r in completed_results if r.get("match_date") == selected_date_val]
        else:
            active_date_results = completed_results

        kpis = get_toss_accuracy_kpis(active_date_results)

        # Top 5 KPI Metrics Cards (Updates Dynamically With Selected Date)
        k1, k2, k3, k4, k5 = st.columns(5)
        with k1:
            st.markdown(f"""
            <div class="kpi-card">
                <div class="kpi-val" style="color: #38bdf8;">{kpis['total_completed']}</div>
                <div class="kpi-lbl">Total Tosses Done</div>
            </div>
            """, unsafe_allow_html=True)
        with k2:
            st.markdown(f"""
            <div class="kpi-card">
                <div class="kpi-val" style="color: #f59e0b;">{kpis['played_count']}</div>
                <div class="kpi-lbl">Tosses Played (Bets)</div>
            </div>
            """, unsafe_allow_html=True)
        with k3:
            st.markdown(f"""
            <div class="kpi-card">
                <div class="kpi-val" style="color: #10b981;">{kpis['passed_count']}</div>
                <div class="kpi-lbl">✅ Tosses Passed (Won)</div>
            </div>
            """, unsafe_allow_html=True)
        with k4:
            st.markdown(f"""
            <div class="kpi-card">
                <div class="kpi-val" style="color: #ef4444;">{kpis['failed_count']}</div>
                <div class="kpi-lbl">❌ Tosses Failed (Missed)</div>
            </div>
            """, unsafe_allow_html=True)
        with k5:
            st.markdown(f"""
            <div class="kpi-card">
                <div class="kpi-val" style="color: #a78bfa;">{kpis['pass_rate_pct']:.1f}%</div>
                <div class="kpi-lbl">🏆 Win Rate ({kpis['pass_rate_pct']:.1f}%)</div>
            </div>
            """, unsafe_allow_html=True)

        st.write("")
        prog_val = min(1.0, max(0.0, kpis['pass_rate_pct'] / 100.0))
        st.progress(prog_val, text=f"🎯 {kpi_title_ctx} Win Accuracy: {kpis['pass_rate_pct']:.1f}%")

        st.divider()

        # Secondary Filters: Tournament & Search
        all_done_tournaments = []
        for r in active_date_results:
            t = r.get("tournament")
            if t and t not in all_done_tournaments:
                all_done_tournaments.append(t)
        all_done_tournaments.sort()

        tour_done_opts = ["🌟 All Tournaments"] + [
            f"{t} ({sum(1 for r in active_date_results if r.get('tournament') == t)})"
            for t in all_done_tournaments
        ]

        c_f1, c_f2 = st.columns([1.6, 1.4])
        with c_f1:
            selected_done_tour_raw = st.selectbox(
                "🏏 Filter by Tournament",
                options=tour_done_opts,
                index=0,
                key="select_done_tournament"
            )
            selected_done_tour = selected_done_tour_raw.rsplit(" (", 1)[0] if selected_done_tour_raw != "🌟 All Tournaments" else "🌟 All Tournaments"

        with c_f2:
            search_done = st.text_input(
                "🔍 Search Match",
                placeholder="Search teams or league...",
                key="input_search_done"
            )

        # Count outcomes for the selected date
        pending_in_active = sum(1 for r in active_date_results if is_winner_unresolved(r.get("actual_winner")))
        pass_in_active = sum(1 for r in active_date_results if r.get("outcome") == "PASS")
        fail_in_active = sum(1 for r in active_date_results if r.get("outcome") == "FAIL")
        skip_in_active = sum(1 for r in active_date_results if r.get("outcome") == "SKIPPED")
        played_in_active = pass_in_active + fail_in_active

        done_filter_options = [
            f"💎 Played Bets ({played_in_active})",
            f"✅ Won ({pass_in_active})",
            f"❌ Missed ({fail_in_active})",
            f"🟡 Skipped ({skip_in_active})",
            f"🌟 All ({len(active_date_results)})"
        ]
        if pending_in_active > 0:
            done_filter_options.append(f"⏳ Pending ({pending_in_active})")

        done_filter = st.pills(
            "Filter by Outcome",
            options=done_filter_options,
            default=done_filter_options[0],
            key="pills_done_filter_v2"
        )

        # Filter Rows
        filtered_done = active_date_results

        # 1. Tournament Filter
        if selected_done_tour != "🌟 All Tournaments":
            filtered_done = [r for r in filtered_done if r.get("tournament") == selected_done_tour]

        # 2. Outcome Filter
        if done_filter and "Won" in done_filter:
            filtered_done = [r for r in filtered_done if r.get("outcome") == "PASS"]
        elif done_filter and "Missed" in done_filter:
            filtered_done = [r for r in filtered_done if r.get("outcome") == "FAIL"]
        elif done_filter and "Pending" in done_filter:
            filtered_done = [r for r in filtered_done if is_winner_unresolved(r.get("actual_winner"))]
        elif done_filter and ("Skipped" in done_filter or "स्किप" in done_filter):
            filtered_done = [r for r in filtered_done if r.get("outcome") == "SKIPPED"]
        elif done_filter and ("All" in done_filter or "सभी" in done_filter):
            filtered_done = filtered_done
        else:
            # Default "💎 Played Bets": show only Played bets (Pass + Fail), unless 0 played bets, then show all
            if played_in_active > 0:
                filtered_done = [r for r in filtered_done if r.get("outcome") in ["PASS", "FAIL"]]
            else:
                filtered_done = filtered_done

        # 3. Search Filter
        if search_done.strip():
            sq = search_done.strip().lower()
            filtered_done = [
                r for r in filtered_done
                if sq in r["match_name"].lower() or sq in r["tournament"].lower()
            ]

        # Tournament Mini-Summary Banner when a specific tournament is selected
        if selected_done_tour != "🌟 All Tournaments":
            t_total = len(filtered_done)
            t_pass = sum(1 for r in filtered_done if r.get("outcome") == "PASS")
            t_fail = sum(1 for r in filtered_done if r.get("outcome") == "FAIL")
            t_skip = sum(1 for r in filtered_done if r.get("outcome") == "SKIPPED")
            t_played = t_pass + t_fail
            t_pct = (t_pass / t_played * 100) if t_played > 0 else 0
            st.info(f"🏏 **Tournament: {selected_done_tour}** — Total Matches: **{t_total}** | ✅ Won: **{t_pass}** | ❌ Missed: **{t_fail}** | 🟡 Skipped: **{t_skip}** | Win Accuracy: **{t_pct:.1f}%**")

        # Quick Global Result Editor & Fixer Expander
        with st.expander("✏️ Quick Result Editor / Fixer", expanded=False):
            st.markdown("💡 **If any toss outcome was recorded incorrectly**, select the match below to update the true winner or delete record:")
            match_titles = [
                f"{r.get('match_name')} ({r.get('match_date')} {r.get('toss_time_ist')}) · Current: 🏆 {r.get('actual_winner')} [{r.get('outcome_label')}]"
                for r in completed_results
            ]
            if match_titles:
                selected_fix_str = st.selectbox("Select match to edit:", match_titles, key="select_global_fix_match")
                sel_fix_idx = match_titles.index(selected_fix_str)
                target_r = completed_results[sel_fix_idx]

                col_u1, col_u2, col_u2b, col_u3, col_u4 = st.columns([1.8, 1.6, 1.6, 1.1, 1.1])
                with col_u1:
                    new_fix_winner = st.selectbox(
                        "🏆 True Toss Winner",
                        [target_r.get("team_a"), target_r.get("team_b")],
                        index=0 if target_r.get("actual_winner") == target_r.get("team_a") else 1,
                        key="global_fix_winner"
                    )
                with col_u2:
                    new_fix_dec = st.selectbox(
                        "Toss Decision",
                        ["Bowl (Bowl First)", "Bat (Bat First)"],
                        index=0 if "bowl" in str(target_r.get("toss_decision", "")).lower() else 1,
                        key="global_fix_dec"
                    )
                with col_u2b:
                    cur_rec_lbl = f"Keep ({target_r.get('recommended_team') or 'SKIP'})"
                    new_fix_rec = st.selectbox(
                        "Pre-Match Call",
                        [cur_rec_lbl, "Force SKIP (50-50)", target_r.get("team_a"), target_r.get("team_b")],
                        index=0,
                        key="global_fix_rec"
                    )
                with col_u3:
                    st.write("")
                    st.write("")
                    if st.button("💾 Save Result", key="btn_global_save_fix", type="primary", use_container_width=True):
                        dec_str = "Bowl" if "Bowl" in new_fix_dec else "Bat"
                        if new_fix_rec == "Force SKIP (50-50)":
                            upd_rec = None
                            upd_rating = "🟡 SKIP / 50-50 COIN FLIP"
                        elif new_fix_rec == cur_rec_lbl:
                            upd_rec = target_r.get("recommended_team")
                            upd_rating = target_r.get("stake_rating", "NORMAL")
                        else:
                            upd_rec = new_fix_rec
                            upd_rating = target_r.get("stake_rating") or "⚡ VALUE LEAN"
                        saved_fixed = save_toss_result(
                            match_id=target_r.get("match_id"),
                            match_name=target_r.get("match_name"),
                            tournament=target_r.get("tournament"),
                            toss_time_ist=target_r.get("toss_time_ist"),
                            match_date=target_r.get("match_date"),
                            team_a=target_r.get("team_a"),
                            team_b=target_r.get("team_b"),
                            recommended_team=upd_rec,
                            stake_rating=upd_rating,
                            actual_winner=new_fix_winner,
                            toss_decision=dec_str,
                            is_manual=True
                        )
                        st.success(f"✅ Result successfully updated! Winner: {new_fix_winner} -> {saved_fixed['outcome_label']}")
                        st.rerun()
                with col_u4:
                    st.write("")
                    st.write("")
                    if st.button("🗑️ Delete Record", key="btn_global_del_fix", use_container_width=True):
                        delete_toss_result(target_r.get("match_id"))
                        st.warning("⚠️ Record deleted!")
                        st.rerun()

        # Completed Summary Table & Cards Grouped by Date
        if filtered_done:
            # If user explicitly selected ALL dates and list is huge, show latest 50 to maintain 60fps on mobile
            if selected_date_val == "ALL" and len(filtered_done) > 50:
                st.caption(f"⚡ *Showing latest 50 completed matches for optimal mobile performance (Total: {len(filtered_done)}). Use date filter above for specific dates.*")
                display_done_list = filtered_done[:50]
            else:
                display_done_list = filtered_done

            # Group filtered results by match_date in descending order
            date_groups = {}
            for r in display_done_list:
                d = r.get("match_date", "Other")
                if d not in date_groups:
                    date_groups[d] = []
                date_groups[d].append(r)

            for d_val, day_matches in date_groups.items():
                if d_val == today_iso:
                    d_title = f"📅 Today's Matches — {d_val}"
                    d_color = "#38bdf8"
                elif d_val == yesterday_iso:
                    d_title = f"📅 Yesterday's Matches — {d_val}"
                    d_color = "#f59e0b"
                else:
                    d_title = f"📅 Fixtures — {d_val}"
                    d_color = "#a78bfa"

                day_pass = sum(1 for r in day_matches if r.get("outcome") == "PASS")
                day_fail = sum(1 for r in day_matches if r.get("outcome") == "FAIL")
                day_skip = sum(1 for r in day_matches if r.get("outcome") == "SKIPPED")
                day_pend = sum(1 for r in day_matches if r.get("outcome") == "PENDING")
                day_played = day_pass + day_fail
                day_wr = (day_pass / day_played * 100) if day_played > 0 else 0.0

                st.markdown(f"""
                <div style="background: linear-gradient(90deg, #0f172a, #1e293b); border-left: 6px solid {d_color}; border-radius: 10px; padding: 12px 18px; margin: 26px 0 14px 0; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                    <div>
                        <div style="font-size: 18px; font-weight: 800; color: #ffffff;">{d_title}</div>
                        <div style="font-size: 13px; color: #ffffff; margin-top: 2px; font-weight: 600;">Total: <b>{len(day_matches)} Matches</b> · Played: <b>{day_played}</b> · Skipped: <b>{day_skip}</b> · Pending: <b>{day_pend}</b></div>
                    </div>
                    <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                        <span style="background: #064e3b; color: #34d399; border: 1px solid #10b981; padding: 4px 12px; border-radius: 20px; font-weight: 800; font-size: 13px;">✅ {day_pass} Won</span>
                        <span style="background: #450a0a; color: #f87171; border: 1px solid #ef4444; padding: 4px 12px; border-radius: 20px; font-weight: 800; font-size: 13px;">❌ {day_fail} Missed</span>
                        <span style="background: #1e1b4b; color: #c084fc; border: 1px solid #8b5cf6; padding: 4px 12px; border-radius: 20px; font-weight: 800; font-size: 13px;">🏆 {day_wr:.1f}% Win Rate</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)

                # Day's summary table
                day_table_rows = []
                for r in day_matches:
                    act_win = str(r.get("actual_winner") or "").strip()
                    is_unres = is_winner_unresolved(act_win)
                    dec = r.get("toss_decision", "Bowl")
                    win_display = "⏳ Pending Confirmation" if is_unres else f"🏆 {act_win} ({dec})"

                    day_table_rows.append({
                        "⏰ Toss Time (IST)": r.get('toss_time_ist'),
                        "Match": r.get("match_name"),
                        "Tournament": r.get("tournament"),
                        "🎯 Recommended Play": f"🟢 {r.get('recommended_team')}" if r.get('recommended_team') else "🟡 Skipped (50-50)",
                        "🏆 Actual Toss Winner": win_display,
                        "📊 Outcome": r.get("outcome_label", "PASS")
                    })
                st.dataframe(pd.DataFrame(day_table_rows), use_container_width=True, hide_index=True)

                # Day's audit cards
                for r_idx, r in enumerate(day_matches):
                    r_mid = str(r.get("match_id", ""))
                    r_wid = f"{r_mid}_{r_idx}"
                    out = r.get("outcome")
                    is_pass = (out == "PASS")
                    is_fail = (out == "FAIL")
                    act_win = str(r.get("actual_winner") or "").strip()
                    is_unres = is_winner_unresolved(act_win)
                    is_pending = (out == "PENDING" or is_unres)

                    if is_pass:
                        card_border = "#10b981"
                        card_badge = "✅ WON"
                        badge_bg = "#064e3b"
                        badge_color = "#34d399"
                    elif is_fail:
                        card_border = "#ef4444"
                        card_badge = "❌ MISSED"
                        badge_bg = "#450a0a"
                        badge_color = "#f87171"
                    elif is_pending:
                        card_border = "#f59e0b"
                        card_badge = "⏳ PENDING"
                        badge_bg = "#451a03"
                        badge_color = "#fbbf24"
                    else:
                        card_border = "#f59e0b"
                        card_badge = "🟡 SKIPPED (50-50 PASS)"
                        badge_bg = "#451a03"
                        badge_color = "#fbbf24"

                    rec_display = f"🟢 PLAY: {r.get('recommended_team')}" if r.get('recommended_team') else "🟡 SKIP (50-50 PASS)"
                    dec = r.get("toss_decision", "Bowl")
                    win_card_label = "⏳ Pending Confirmation" if is_unres else f"🏆 {act_win} ({dec})"
                    win_color = "#f59e0b" if is_unres else "#38bdf8"

                    st.markdown(f"""
                    <div class="vip-match-card" style="border-left: 6px solid {card_border};">
                        <div class="card-top-row">
                            <span class="tour-pill">🏏 {r.get('tournament')} · 📅 {r.get('match_date')} · ⏰ {r.get('toss_time_ist')}</span>
                            <span style="background: {badge_bg}; color: {badge_color}; border: 1px solid {card_border}; padding: 6px 16px; border-radius: 20px; font-weight: 900; font-size: 13px;">
                                {card_badge}
                            </span>
                        </div>
                        <div class="teams-arena">
                            <div class="team-title">{r.get('team_a')}</div>
                            <div class="vs-emblem">VS</div>
                            <div class="team-title" style="text-align:right;">{r.get('team_b')}</div>
                        </div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 6px;">
                            <div style="background: #090d16; border: 1px solid #1e293b; padding: 10px 14px; border-radius: 8px;">
                                <div style="font-size: 11px; color: #94a3b8; font-weight: 700; text-transform: uppercase;">Prediction</div>
                                <div style="font-size: 16px; font-weight: 900; color: #ffffff; margin-top: 2px;">{rec_display}</div>
                            </div>
                            <div style="background: #090d16; border: 1px solid #1e293b; padding: 10px 14px; border-radius: 8px;">
                                <div style="font-size: 11px; color: #94a3b8; font-weight: 700; text-transform: uppercase;">Toss Winner</div>
                                <div style="font-size: 15px; font-weight: 900; color: {win_color}; margin-top: 2px;">{win_card_label}</div>
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    if is_pending or is_unres:
                        st.caption("⚡ **Toss is completed — select true winner with 1 click:**")
                        q_c1, q_c2 = st.columns(2)
                        with q_c1:
                            if st.button(f"🏆 {r.get('team_a')} Won", key=f"btn_p_a_{r_wid}", type="primary", use_container_width=True):
                                save_toss_result(
                                    r.get("match_id"), r.get("match_name"), r.get("tournament"),
                                    r.get("toss_time_ist"), r.get("match_date"), r.get("team_a"),
                                    r.get("team_b"), r.get("recommended_team"), r.get("stake_rating", "NORMAL"),
                                    r.get("team_a"), "Bowl", is_manual=True
                                )
                                st.rerun()
                        with q_c2:
                            if st.button(f"🏆 {r.get('team_b')} Won", key=f"btn_p_b_{r_wid}", type="primary", use_container_width=True):
                                save_toss_result(
                                    r.get("match_id"), r.get("match_name"), r.get("tournament"),
                                    r.get("toss_time_ist"), r.get("match_date"), r.get("team_a"),
                                    r.get("team_b"), r.get("recommended_team"), r.get("stake_rating", "NORMAL"),
                                    r.get("team_b"), "Bowl", is_manual=True
                                )
                                st.rerun()

                    # 1-Click interactive editor to change/correct actual toss winner
                    with st.expander(f"✏️ Update Result: {r.get('team_a')} vs {r.get('team_b')}", expanded=False):
                        c_edit1, c_edit2, c_edit3, c_edit4 = st.columns([2, 2, 1.4, 1.4])
                        with c_edit1:
                            new_winner = st.selectbox(
                                "🏆 True Toss Winner",
                                [r.get("team_a"), r.get("team_b")],
                                index=0 if r.get("actual_winner") == r.get("team_a") else 1,
                                key=f"edit_win_{r_wid}"
                            )
                        with c_edit2:
                            new_dec = st.selectbox(
                                "Toss Decision",
                                ["Bowl (Bowl First)", "Bat (Bat First)"],
                                index=0 if "bowl" in str(r.get("toss_decision", "")).lower() else 1,
                                key=f"edit_dec_{r_wid}"
                            )
                        with c_edit3:
                            st.write("")
                            st.write("")
                            if st.button("💾 Save Result", key=f"btn_update_{r_wid}", type="primary", use_container_width=True):
                                dec_str = "Bowl" if "Bowl" in new_dec else "Bat"
                                save_toss_result(
                                    match_id=r.get("match_id"),
                                    match_name=r.get("match_name"),
                                    tournament=r.get("tournament"),
                                    toss_time_ist=r.get("toss_time_ist"),
                                    match_date=r.get("match_date"),
                                    team_a=r.get("team_a"),
                                    team_b=r.get("team_b"),
                                    recommended_team=r.get("recommended_team"),
                                    stake_rating=r.get("stake_rating", "NORMAL"),
                                    actual_winner=new_winner,
                                    toss_decision=dec_str,
                                    is_manual=True
                                )
                                st.success(f"✅ Updated! Winner: {new_winner}")
                                st.rerun()
                        with c_edit4:
                            st.write("")
                            st.write("")
                            if st.button("🗑️ Delete Record", key=f"btn_card_del_{r_wid}", use_container_width=True):
                                delete_toss_result(r.get("match_id"))
                                st.warning("⚠️ Record deleted!")
                                st.rerun()
        else:
            if selected_date_val == today_iso:
                st.markdown(f"""
                <div style="background: #0f172a; border: 1px dashed #38bdf8; border-radius: 14px; padding: 24px; text-align: center; margin: 16px 0;">
                    <div style="font-size: 32px; margin-bottom: 8px;">🪙</div>
                    <h3 style="color: #38bdf8; margin: 0 0 6px 0; font-size: 18px;">Upcoming Matches Today ({today_iso})</h3>
                    <p style="color: #94a3b8; font-size: 14px; margin: 0 auto; max-width: 600px;">
                        Matches scheduled for today will appear here as soon as their toss is officially completed.
                    </p>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.info("No toss results found for the selected filters.")
