"""Unit tests for Telegram, GamingHelper, and Cricsheet collectors."""

from datetime import datetime, timezone, timedelta
from app.collectors.telegram_listener import TelegramListener
from app.collectors.gaming_helper import GamingHelperCollector
from app.collectors.cricsheet import CricsheetCollector


def test_telegram_message_parser_play_call():
    """Verify parsing of standard tipster toss call."""
    msg = "🔥 MATCH 45 IPL 🔥\nToss Winner: Chennai Super Kings\nStake: Normal Play"
    res = TelegramListener.parse_message_text(msg)
    assert res["action"] == "PLAY"
    assert "Chennai Super Kings" in res["predicted_team"]
    assert len(res["message_hash"]) == 64


def test_telegram_message_parser_skip_call():
    """Verify parsing of skip / pass call."""
    msg = "Match 12: Otago vs Central Stags\nVerdict: Strict PASS - 50-50 neutral rotation line."
    res = TelegramListener.parse_message_text(msg)
    assert res["action"] == "SKIP"


def test_telegram_audit_post_toss_edited():
    """Any edited message must be disqualified."""
    now_utc = datetime.now(timezone.utc)
    match_start = now_utc + timedelta(hours=1)
    audit = TelegramListener.audit_message_pre_toss(now_utc, match_start, is_edited=True)
    assert audit["is_pre_toss"] is False
    assert audit["audit_status"] == "POST_TOSS_EDITED"


def test_telegram_audit_verified_pre_toss():
    """Message sent strictly before match start must be verified pre-toss."""
    now_utc = datetime.now(timezone.utc)
    match_start = now_utc + timedelta(hours=1)
    audit = TelegramListener.audit_message_pre_toss(now_utc, match_start, is_edited=False)
    assert audit["is_pre_toss"] is True
    assert audit["audit_status"] == "VERIFIED_PRE_TOSS"


def test_gaming_helper_fallback():
    """Ensure collector gracefully loads cached matches without network."""
    collector = GamingHelperCollector()
    matches = collector.load_local_fallback()
    assert isinstance(matches, list)
    if matches:
        first = matches[0]
        assert "team_a_name" in first or "match_title" in first


def test_cricsheet_historical_load():
    """Ensure cricsheet collector loads historical sample records."""
    collector = CricsheetCollector()
    records = collector.load_local_records()
    assert isinstance(records, list)
    assert len(records) > 0
    assert "toss_winner" in records[0]

