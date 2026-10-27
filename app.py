"""TossIQ VIP - Elite Cricket Toss Intelligence & Actionable Platform.

Built for serious punters: zero clutter, zero academic formulas, 100% actionable paid-style toss verdicts:
- 🟢 PLAY: [TEAM NAME] with calculated edge & stake advice (Jackpot / Confirmed).
- 🟡 SKIP / PASS: 50-50 coin flip traps to save your bankroll.
- 🪙 TOSS TIME: Exact timing in Asia/Kolkata (IST).
- 🔐 Synchronized with GamingHelper (gaminghelperonline.com/live-bets).
- 📜 Toss Done & Pass/Fail Accuracy Tracker with persistent history.
"""

import os
import sys
import json
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta

# Ensure project root is in sys.path for Streamlit Cloud / Linux runtime
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.timezone import now_ist, format_ist
from core.providers.gaming_helper_provider import (
    get_stored_credentials,
    save_credentials,
    login_gaming_helper,
    fetch_gaming_helper_live_bets,
    get_active_punter_matches
)
from core.engine.toss_results_manager import (
    load_toss_results,
    save_toss_result,
    get_toss_accuracy_kpis,
    sync_completed_tosses_from_gaming_helper,
    auto_update_toss_results_from_telegram
)

# Safe import with fallback for zero-downtime hot reloading
try:
    from core.engine.toss_results_manager import delete_toss_result
except ImportError:
    def delete_toss_result(match_id: str) -> bool:
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

# --- Elite VIP Sportsbook Theme CSS ---
st.markdown("""
<style>
    /* Dark VIP terminal theme with Crisp High-Contrast Pure White Typography */
    .stApp {
        background-color: #070a13;
        color: #ffffff !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }

    /* Universal Pure White Text Overrides */
    .stMarkdown p, .stMarkdown span, .stMarkdown div, .stMarkdown li {
        color: #ffffff !important;
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
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

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
                st.error("❌ Galat PIN! Access Denied.")
    st.stop()

# --- Top Header ---
st.markdown(f"""
<div class="vip-header">
    <div>
        <div style="display: flex; align-items: center;">
            <h1 class="vip-title">🪙 Toss<span>IQ</span> VIP</h1>
            <span class="vip-tag">Punter Edition</span>
        </div>
        <p class="vip-sub">Live Bets Synchronized with GamingHelper (gaminghelperonline.com) · Asia/Kolkata (IST)</p>
    </div>
    <div class="clock-badge">
        <div style="font-size: 16px; font-weight: 900; color: #10b981;">⏰ {ist_time_display}</div>
        <div style="color: #ffffff; font-size: 12px; margin-top: 2px; font-weight: 700;">{ist_date_display}</div>
    </div>
</div>
""", unsafe_allow_html=True)

# Fetch Live Matches from GamingHelper Online
punter_data = get_active_punter_matches(force_refresh=False)
all_matches = punter_data.get("matches", [])
is_gh_connected = punter_data.get("connected", False)
creds = get_stored_credentials()

# Automatically sync completed/closed matches from GamingHelper so Tab 2 is always up-to-date
if is_gh_connected:
    sync_completed_tosses_from_gaming_helper()

# Account Status Bar
col_st1, col_st2 = st.columns([3, 1])
with col_st1:
    if is_gh_connected:
        st.success(f"🟢 **GamingHelper Online Connected:** Demo Account `{creds.get('phone', '7028163937')}` active · **{len(all_matches)} Live Bets Active**")
    else:
        st.warning("🟡 **GamingHelper Online:** Demo Account connection in progress...")

with col_st2:
    if st.button("🔄 Sync Live Bets & Results", type="primary", use_container_width=True, key="btn_sync_gh_now"):
        with st.spinner("Syncing latest live matches & completed tosses from GamingHelper..."):
            punter_data = get_active_punter_matches(force_refresh=True)
            all_matches = punter_data.get("matches", [])
            sync_completed_tosses_from_gaming_helper()
            st.success("Synced live bets & completed tosses successfully!")
            st.rerun()

# --- 2 Clear Simple Tabs ---
vip_tabs = st.tabs([
    "🎯 लाइव टॉस बेट्स (Live Toss Bets)",
    "📜 टॉस रिज़ल्ट्स (Toss Done)"
])


# ==============================================================================
# TAB 1: LIVE BETS & ACTIONABLE VERDICTS
# ==============================================================================
with vip_tabs[0]:
    # Extract unique tournaments from live matches
    all_live_tournaments = []
    for m in all_matches:
        t = m.get("tournament_name")
        if t and t not in all_live_tournaments:
            all_live_tournaments.append(t)
    all_live_tournaments.sort()

    tour_live_opts = ["🌟 Sabhi Tournaments (All)"] + [
        f"{t} ({sum(1 for m in all_matches if m.get('tournament_name') == t)})"
        for t in all_live_tournaments
    ]

    # --- Search & Filter Bar ---
    col_t_live, col_filter, col_search = st.columns([1.5, 1.3, 1.2])
    with col_t_live:
        selected_live_tour_raw = st.selectbox(
            "🏏 Filter by Tournament",
            tour_live_opts,
            index=0,
            key="select_live_tournament"
        )
        selected_live_tour = selected_live_tour_raw.rsplit(" (", 1)[0] if selected_live_tour_raw != "🌟 Sabhi Tournaments (All)" else "🌟 Sabhi Tournaments (All)"

    with col_filter:
        filter_type = st.selectbox(
            "🎯 Filter by Verdict",
            ["🌟 Sabhi Matches (All)", "🟢 Sirf Play Wale (Actionable)", "💎 Sirf Jackpot Plays", "🟡 Sirf Skips (Pass)"],
            index=0,
            key="select_punter_filter"
        )

    with col_search:
        search_q = st.text_input(
            "🔍 Search Team / Match",
            placeholder="e.g. Abu Dhabi, Emirates Blues...",
            key="input_search_punter"
        )

    # Filter by Tournament
    filtered_matches = all_matches
    if selected_live_tour != "🌟 Sabhi Tournaments (All)":
        filtered_matches = [m for m in filtered_matches if m.get("tournament_name") == selected_live_tour]

    # Filter by Search Query
    if search_q.strip():
        q = search_q.strip().lower()
        filtered_matches = [
            m for m in filtered_matches
            if q in m["team_a_name"].lower() or q in m["team_b_name"].lower() or q in m["tournament_name"].lower()
        ]

    # Calculate Counts
    total_matches = len(filtered_matches)
    play_matches = [m for m in filtered_matches if m["action_type"] == "PLAY"]
    skip_matches = [m for m in filtered_matches if m["action_type"] == "SKIP"]
    jackpot_matches = [m for m in play_matches if "JACKPOT" in m.get("stake_rating", "")]

    # Apply Verdict Filter
    if filter_type == "🟢 Sirf Play Wale (Actionable)":
        filtered_list = play_matches
    elif filter_type == "💎 Sirf Jackpot Plays":
        filtered_list = jackpot_matches
    elif filter_type == "🟡 Sirf Skips (Pass)":
        filtered_list = skip_matches
    else:
        filtered_list = filtered_matches

    # --- KPI Ticker Strip ---
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-val" style="color: #38bdf8;">{total_matches}</div>
            <div class="kpi-lbl">Total Live Matches</div>
        </div>
        """, unsafe_allow_html=True)
    with k2:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-val" style="color: #f59e0b;">{len(jackpot_matches)}</div>
            <div class="kpi-lbl">💎 Jackpot High-Stakes Plays</div>
        </div>
        """, unsafe_allow_html=True)
    with k3:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-val" style="color: #10b981;">{len(play_matches)}</div>
            <div class="kpi-lbl">🟢 Confirmed Actionable Plays</div>
        </div>
        """, unsafe_allow_html=True)
    with k4:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-val" style="color: #ef4444;">{len(skip_matches)}</div>
            <div class="kpi-lbl">🟡 Skips / 50-50 Traps (Pass)</div>
        </div>
        """, unsafe_allow_html=True)

    st.write("")

    # --- Quick Scan Summary Drawer ---
    if filtered_list:
        summary_rows = []
        for m in filtered_list:
            t_str = m.get("toss_time_str") or "30m prior"
            act = m.get("action_type")
            pick = m.get("recommended_team")
            conf = m.get("confidence_pct", 50)

            if act == "PLAY" and pick:
                if "JACKPOT" in m.get("stake_rating", ""):
                    verdict_str = f"💎 JACKPOT: {pick.upper()} ({conf}%)"
                else:
                    verdict_str = f"🟢 WINNER: {pick.upper()} ({conf}%)"
            elif act == "COMPLETED":
                verdict_str = f"🏆 TOSS WON: {pick.upper()}"
            else:
                verdict_str = "🟡 SKIP (50-50 PASS)"

            summary_rows.append({
                "🪙 Toss Time (IST)": t_str,
                "Match": f"{m['team_a_name']} vs {m['team_b_name']}",
                "Tournament": m.get("tournament_name"),
                "🎯 Predicted Toss Winner": verdict_str
            })

        with st.expander("📋 Quick Summary Table", expanded=True):
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    # --- Match Cards Listing ---
    st.markdown(f"### 🪙 Live Fixtures ({len(filtered_list)} Matches)")

    if not filtered_list:
        st.info("Selected filters ke hisab se koi match nahi mila. 'Sabhi Matches' select karein.")
    else:
        for m in filtered_list:
            mid = m["id"]
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
            rat = m.get("punter_rationale", "")

            with st.container():
                st.markdown(f"""
                <div class="vip-match-card">
                    <div class="card-top-row">
                        <span class="tour-pill">🏏 {tour}</span>
                        <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                            <span class="toss-time-pill">
                                🪙 TOSS TIME: {toss_str}
                            </span>
                            <span class="match-time-pill">⏰ Match Start: {time_str}</span>
                        </div>
                    </div>
                    <div class="teams-arena">
                        <div class="team-title">{t_a} <span style="font-size:14px; color:#38bdf8;">({short_a})</span></div>
                        <div class="vs-emblem">VS</div>
                        <div class="team-title" style="text-align:right;"><span style="font-size:14px; color:#38bdf8;">({short_b})</span> {t_b}</div>
                    </div>
                """, unsafe_allow_html=True)

                # --- COMPACT ACTION VERDICT BANNER ---
                if act == "PLAY" and pick:
                    is_jackpot = "JACKPOT" in rating
                    badge_bg = "#f59e0b" if is_jackpot else "#065f46"
                    badge_col = "#000000" if is_jackpot else "#a7f3d0"
                    st.markdown(f"""
                    <div class="verdict-play">
                        <div class="verdict-header">
                            <span class="verdict-team-name">🟢 TOSS WINNER: {pick.upper()}</span>
                            <span class="verdict-badge" style="background: {badge_bg}; color: {badge_col}; font-weight: 800;">
                                {rating} · {conf}% PROBABILITY
                            </span>
                        </div>
                        <div class="verdict-reason">
                            💡 <b>Prediction Signal:</b> {rat}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div class="verdict-skip">
                        <div class="verdict-header">
                            <span class="verdict-team-name">🟡 SKIP (50-50 PASS)</span>
                            <span class="verdict-badge" style="background: #1c1303; color: #fbbf24; border: 1px solid #d97706;">
                                ⚖️ PASS
                            </span>
                        </div>
                        <div class="verdict-reason">
                            💡 {rat}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                # Check if this match was already marked done
                all_done_list = load_toss_results()
                done_map = {str(r.get("match_id")): r for r in all_done_list}
                is_already_done = str(mid) in done_map
                prev_r = done_map.get(str(mid), {})

                if is_already_done:
                    prev_win = prev_r.get("actual_winner", t_a)
                    prev_dec = prev_r.get("toss_decision", "Bowl")
                    exp_title = f"✏️ रिजल्ट बदलें / सुधारें: {t_a} vs {t_b} (वर्तमान: 🏆 {prev_win})"
                    btn_text = "🔄 रिजल्ट अपडेट करें (Update Result)"
                else:
                    prev_win = t_a
                    prev_dec = "Bowl"
                    exp_title = f"🪙 Mark Toss Done: {t_a} vs {t_b}"
                    btn_text = "💾 Save Toss Done"

                # --- INTERACTIVE 1-CLICK TOSS DONE ACTION EXPANDER ---
                with st.expander(exp_title, expanded=False):
                    if is_already_done:
                        st.info(f"ℹ️ **वर्तमान रिज़ल्ट:** 🏆 विनर: **{prev_win}** ({prev_dec}) · नतीजा: **{prev_r.get('outcome_label')}**")

                    if is_already_done:
                        col_m1, col_m2, col_m3, col_m4 = st.columns([2, 2, 1.4, 1.4])
                    else:
                        col_m1, col_m2, col_m3 = st.columns([2, 2, 1.5])
                        col_m4 = None

                    with col_m1:
                        actual_winner = st.selectbox(
                            "🏆 Toss Winner (Kaun Jeeta?)",
                            [t_a, t_b],
                            index=0 if prev_win == t_a else 1,
                            key=f"win_select_{mid}"
                        )
                    with col_m2:
                        toss_dec = st.selectbox(
                            "Toss Decision (Pehle kya liya?)",
                            ["Bowl (Bowling Pehle)", "Bat (Batting Pehle)"],
                            index=0 if "bowl" in prev_dec.lower() else 1,
                            key=f"dec_select_{mid}"
                        )
                    with col_m3:
                        st.write("")
                        st.write("")
                        if st.button(btn_text, key=f"btn_save_done_{mid}", type="primary", use_container_width=True):
                            dec_str = "Bowl" if "Bowl" in toss_dec else "Bat"
                            saved_rec = save_toss_result(
                                match_id=mid,
                                match_name=f"{t_a} vs {t_b}",
                                tournament=tour,
                                toss_time_ist=toss_str,
                                match_date=today_iso,
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
                            if st.button("🗑️ रिज़ल्ट हटाएँ (Delete)", key=f"btn_tab1_del_{mid}", use_container_width=True):
                                delete_toss_result(mid)
                                st.warning("⚠️ रिज़ल्ट हटा दिया गया!")
                                st.rerun()

                st.markdown("</div>", unsafe_allow_html=True)


# ==============================================================================
# TAB 2: TOSS DONE & PASS/FAIL ACCURACY AUDIT TRACKER
# ==============================================================================
with vip_tabs[1]:
    col_head_t2, col_btn_t2 = st.columns([2.5, 1.2])
    with col_head_t2:
        st.header("📜 टॉस रिज़ल्ट्स (Toss Done)")
    with col_btn_t2:
        if st.button("⚡ Sync Latest Results", key="btn_tg_sync_tab2", use_container_width=True):
            with st.spinner("Fetching latest toss outcomes..."):
                sync_completed_tosses_from_gaming_helper()
                res_count = auto_update_toss_results_from_telegram()
                if res_count > 0:
                    st.success(f"✅ {res_count} Toss results updated!")
                else:
                    st.info("ℹ️ All matches up-to-date.")
                st.rerun()

    completed_results = load_toss_results()

    if not completed_results:
        st.markdown("""
        <div style="background: #0f172a; border: 1px dashed #334155; border-radius: 14px; padding: 24px; text-align: center; margin: 16px 0;">
            <div style="font-size: 32px; margin-bottom: 8px;">🪙</div>
            <h3 style="color: #f59e0b; margin: 0 0 6px 0; font-size: 18px;">अभी तक कोई टॉस Done मार्क नहीं हुआ है</h3>
            <p style="color: #94a3b8; font-size: 14px; margin: 0 auto;">
                जैसे ही किसी मैच का टॉस पूरा हो, 'Mark Toss Done' पर क्लिक करके विनर सेव करें।
            </p>
        </div>
        """, unsafe_allow_html=True)
    else:
        # Clean Date Filter: Today only + Date Picker + All Dates
        today_done_count = sum(1 for r in completed_results if r.get("match_date") == today_iso)
        total_done_count = len(completed_results)

        col_df1, col_df2 = st.columns([2.2, 1.2])
        with col_df1:
            date_filter_mode = st.pills(
                "📅 तारीख फ़िल्टर",
                options=[
                    f"📅 आज (Today) · {today_done_count}",
                    "🗓️ तारीख चुनें (Pick Date)",
                    f"🌟 सभी तारीखें ({total_done_count})"
                ],
                default=f"📅 आज (Today) · {today_done_count}",
                key="pills_done_date_mode"
            )
        with col_df2:
            custom_picked_date = st.date_input(
                "🗓️ Date Picker",
                value=cur_ist.date(),
                key="input_custom_done_date",
                label_visibility="collapsed"
            )

        custom_picked_iso = custom_picked_date.strftime("%Y-%m-%d") if custom_picked_date else today_iso

        if date_filter_mode and "आज (Today)" in date_filter_mode:
            selected_date_val = today_iso
            kpi_title_ctx = f"आज ({today_iso})"
        elif date_filter_mode and "Pick Date" in date_filter_mode:
            selected_date_val = custom_picked_iso
            kpi_title_ctx = f"तारीख: {custom_picked_iso}"
        else:
            selected_date_val = "ALL"
            kpi_title_ctx = "सभी तारीखें"

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
        st.progress(prog_val, text=f"🎯 {kpi_title_ctx} Pass Accuracy Rate: {kpis['pass_rate_pct']:.1f}%")

        st.divider()

        # Secondary Filters: Tournament & Search
        all_done_tournaments = []
        for r in active_date_results:
            t = r.get("tournament")
            if t and t not in all_done_tournaments:
                all_done_tournaments.append(t)
        all_done_tournaments.sort()

        tour_done_opts = ["🌟 Sabhi Tournaments (All)"] + [
            f"{t} ({sum(1 for r in active_date_results if r.get('tournament') == t)})"
            for t in all_done_tournaments
        ]

        c_f1, c_f2 = st.columns([1.6, 1.4])
        with c_f1:
            selected_done_tour_raw = st.selectbox(
                "🏏 Filter by Tournament (टूर्नामेंट फ़िल्टर करें)",
                options=tour_done_opts,
                index=0,
                key="select_done_tournament"
            )
            selected_done_tour = selected_done_tour_raw.rsplit(" (", 1)[0] if selected_done_tour_raw != "🌟 Sabhi Tournaments (All)" else "🌟 Sabhi Tournaments (All)"

        with c_f2:
            search_done = st.text_input(
                "🔍 Search Completed Fixture",
                placeholder="Search by team or venue...",
                key="input_search_done"
            )

        done_filter = st.pills(
            "Filter by Toss Outcome",
            options=["🌟 Sabhi Completed", "✅ Sirf Pass (Wins)", "❌ Sirf Fail (Losses)", "⏳ Pending Result", "🟡 Sirf Skipped"],
            default="🌟 Sabhi Completed",
            key="pills_done_filter"
        )

        # Filter Rows
        filtered_done = active_date_results

        # 1. Tournament Filter
        if selected_done_tour != "🌟 Sabhi Tournaments (All)":
            filtered_done = [r for r in filtered_done if r.get("tournament") == selected_done_tour]

        # 2. Outcome Filter
        if done_filter == "✅ Sirf Pass (Wins)":
            filtered_done = [r for r in filtered_done if r.get("outcome") == "PASS"]
        elif done_filter == "❌ Sirf Fail (Losses)":
            filtered_done = [r for r in filtered_done if r.get("outcome") == "FAIL"]
        elif done_filter == "⏳ Pending Result":
            filtered_done = [r for r in filtered_done if r.get("outcome") == "PENDING"]
        elif done_filter == "🟡 Sirf Skipped":
            filtered_done = [r for r in filtered_done if r.get("outcome") == "SKIPPED"]

        # 3. Search Filter
        if search_done.strip():
            sq = search_done.strip().lower()
            filtered_done = [
                r for r in filtered_done
                if sq in r["match_name"].lower() or sq in r["tournament"].lower()
            ]


        # Tournament Mini-Summary Banner when a specific tournament is selected
        if selected_done_tour != "🌟 Sabhi Tournaments (All)":
            t_total = len(filtered_done)
            t_pass = sum(1 for r in filtered_done if r.get("outcome") == "PASS")
            t_fail = sum(1 for r in filtered_done if r.get("outcome") == "FAIL")
            t_skip = sum(1 for r in filtered_done if r.get("outcome") == "SKIPPED")
            t_played = t_pass + t_fail
            t_pct = (t_pass / t_played * 100) if t_played > 0 else 0
            st.info(f"🏏 **टूर्नामेंट: {selected_done_tour}** — कुल मैचेस: **{t_total}** | ✅ Pass: **{t_pass}** | ❌ Fail: **{t_fail}** | 🟡 Skipped: **{t_skip}** | Pass Accuracy: **{t_pct:.1f}%**")

        # Quick Global Result Editor & Fixer Expander
        with st.expander("✏️ किसी भी टॉस का रिज़ल्ट बदलें या सुधारें (Quick Result Fixer / Editor)", expanded=False):
            st.markdown("💡 **अगर टॉस मार्क करते वक्त कोई ग़लती हो गई हो**, तो यहाँ से मैच चुनकर तुरंत असली विनर अपडेट करें या रिज़ल्ट हटाएँ:")
            match_titles = [
                f"{r.get('match_name')} ({r.get('match_date')} {r.get('toss_time_ist')}) · Current: 🏆 {r.get('actual_winner')} [{r.get('outcome_label')}]"
                for r in completed_results
            ]
            if match_titles:
                selected_fix_str = st.selectbox("मैच चुनें जिसका रिज़ल्ट बदलना है:", match_titles, key="select_global_fix_match")
                sel_fix_idx = match_titles.index(selected_fix_str)
                target_r = completed_results[sel_fix_idx]

                col_u1, col_u2, col_u3, col_u4 = st.columns([2, 2, 1.4, 1.4])
                with col_u1:
                    new_fix_winner = st.selectbox(
                        "🏆 असली टॉस विनर चुनें",
                        [target_r.get("team_a"), target_r.get("team_b")],
                        index=0 if target_r.get("actual_winner") == target_r.get("team_a") else 1,
                        key="global_fix_winner"
                    )
                with col_u2:
                    new_fix_dec = st.selectbox(
                        "पहले क्या चुना?",
                        ["Bowl (Bowling Pehle)", "Bat (Batting Pehle)"],
                        index=0 if "bowl" in str(target_r.get("toss_decision", "")).lower() else 1,
                        key="global_fix_dec"
                    )
                with col_u3:
                    st.write("")
                    st.write("")
                    if st.button("💾 अपडेट करें (Save)", key="btn_global_save_fix", type="primary", use_container_width=True):
                        dec_str = "Bowl" if "Bowl" in new_fix_dec else "Bat"
                        saved_fixed = save_toss_result(
                            match_id=target_r.get("match_id"),
                            match_name=target_r.get("match_name"),
                            tournament=target_r.get("tournament"),
                            toss_time_ist=target_r.get("toss_time_ist"),
                            match_date=target_r.get("match_date"),
                            team_a=target_r.get("team_a"),
                            team_b=target_r.get("team_b"),
                            recommended_team=target_r.get("recommended_team"),
                            stake_rating=target_r.get("stake_rating", "NORMAL"),
                            actual_winner=new_fix_winner,
                            toss_decision=dec_str,
                            is_manual=True
                        )
                        st.success(f"✅ रिज़ल्ट सफलतापूर्वक अपडेट हो गया! विनर: {new_fix_winner} -> {saved_fixed['outcome_label']}")
                        st.rerun()
                with col_u4:
                    st.write("")
                    st.write("")
                    if st.button("🗑️ रिज़ल्ट हटाएँ (Delete)", key="btn_global_del_fix", use_container_width=True):
                        delete_toss_result(target_r.get("match_id"))
                        st.warning("⚠️ रिज़ल्ट हटा दिया गया!")
                        st.rerun()

        # Completed Summary Table & Cards Grouped by Date
        if filtered_done:
            # Group filtered results by match_date in descending order
            date_groups = {}
            for r in filtered_done:
                d = r.get("match_date", "Other")
                if d not in date_groups:
                    date_groups[d] = []
                date_groups[d].append(r)

            for d_val, day_matches in date_groups.items():
                if d_val == today_iso:
                    d_title = f"📅 आज के मैच (Today) — {d_val}"
                    d_color = "#38bdf8"
                elif d_val == yesterday_iso:
                    d_title = f"📅 कल के मैच (Yesterday) — {d_val}"
                    d_color = "#f59e0b"
                else:
                    d_title = f"📅 तारीख: {d_val}"
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
                    day_table_rows.append({
                        "⏰ Toss Time": r.get('toss_time_ist'),
                        "Match": r.get("match_name"),
                        "Tournament": r.get("tournament"),
                        "🎯 Recommended Play": f"🟢 {r.get('recommended_team')}" if r.get('recommended_team') else "🟡 Skip (50-50)",
                        "🏆 Actual Toss Winner": f"🏆 {r.get('actual_winner')} ({r.get('toss_decision', 'Bowl')})",
                        "📊 Result Outcome": r.get("outcome_label", "PASS")
                    })
                st.dataframe(pd.DataFrame(day_table_rows), use_container_width=True, hide_index=True)

                # Day's audit cards
                for r in day_matches:
                    out = r.get("outcome")
                    is_pass = (out == "PASS")
                    is_fail = (out == "FAIL")
                    is_pending = (out == "PENDING")

                    if is_pass:
                        card_border = "#10b981"
                        card_badge = "✅ PASS (PROFIT / WON)"
                        badge_bg = "#064e3b"
                        badge_color = "#34d399"
                    elif is_fail:
                        card_border = "#ef4444"
                        card_badge = "❌ FAIL (MISSED)"
                        badge_bg = "#450a0a"
                        badge_color = "#f87171"
                    elif is_pending:
                        card_border = "#f59e0b"
                        card_badge = "⏳ PENDING (Awaiting Result)"
                        badge_bg = "#451a03"
                        badge_color = "#fbbf24"
                    else:
                        card_border = "#f59e0b"
                        card_badge = "🟡 SKIPPED (50-50 PASS)"
                        badge_bg = "#451a03"
                        badge_color = "#fbbf24"

                    rec_display = f"🟢 PLAY: {r.get('recommended_team')}" if r.get('recommended_team') else "🟡 SKIP (50-50 NEUTRAL)"

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
                                <div style="font-size: 16px; font-weight: 900; color: #38bdf8; margin-top: 2px;">🏆 {r.get('actual_winner')}</div>
                            </div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    if is_pending:
                        st.caption("⚡ **टॉस हो गया है — असली विनर कौन बना? बस 1 क्लिक करें:**")
                        q_c1, q_c2 = st.columns(2)
                        with q_c1:
                            if st.button(f"🏆 {r.get('team_a')} Jeeta", key=f"btn_p_a_{r.get('match_id')}", type="primary", use_container_width=True):
                                save_toss_result(
                                    r.get("match_id"), r.get("match_name"), r.get("tournament"),
                                    r.get("toss_time_ist"), r.get("match_date"), r.get("team_a"),
                                    r.get("team_b"), r.get("recommended_team"), r.get("stake_rating", "NORMAL"),
                                    r.get("team_a"), "Bowl", is_manual=True
                                )
                                st.rerun()
                        with q_c2:
                            if st.button(f"🏆 {r.get('team_b')} Jeeta", key=f"btn_p_b_{r.get('match_id')}", type="primary", use_container_width=True):
                                save_toss_result(
                                    r.get("match_id"), r.get("match_name"), r.get("tournament"),
                                    r.get("toss_time_ist"), r.get("match_date"), r.get("team_a"),
                                    r.get("team_b"), r.get("recommended_team"), r.get("stake_rating", "NORMAL"),
                                    r.get("team_b"), "Bowl", is_manual=True
                                )
                                st.rerun()

                    # 1-Click interactive editor to change/correct actual toss winner
                    with st.expander(f"✏️ रिजल्ट बदलें / सुधारें: {r.get('team_a')} vs {r.get('team_b')}", expanded=False):
                        c_edit1, c_edit2, c_edit3, c_edit4 = st.columns([2, 2, 1.4, 1.4])
                        with c_edit1:
                            new_winner = st.selectbox(
                                "🏆 Real Toss Winner (Asli Winner)",
                                [r.get("team_a"), r.get("team_b")],
                                index=0 if r.get("actual_winner") == r.get("team_a") else 1,
                                key=f"edit_win_{r.get('match_id')}"
                            )
                        with c_edit2:
                            new_dec = st.selectbox(
                                "Elected to",
                                ["Bowl (Bowling Pehle)", "Bat (Batting Pehle)"],
                                index=0 if "bowl" in str(r.get("toss_decision", "")).lower() else 1,
                                key=f"edit_dec_{r.get('match_id')}"
                            )
                        with c_edit3:
                            st.write("")
                            st.write("")
                            if st.button("💾 अपडेट करें (Save)", key=f"btn_update_{r.get('match_id')}", type="primary", use_container_width=True):
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
                            if st.button("🗑️ हटाएँ (Delete)", key=f"btn_card_del_{r.get('match_id')}", use_container_width=True):
                                delete_toss_result(r.get("match_id"))
                                st.warning("⚠️ रिज़ल्ट हटा दिया गया!")
                                st.rerun()
        else:
            st.info("No completed tosses match the selected filter. As matches finish, all toss results and pass/fail audit will appear here.")
