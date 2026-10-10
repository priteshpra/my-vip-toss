"""Unit tests for Asia/Kolkata timezone handling."""

import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from core.timezone import now_ist, to_ist, parse_to_ist, format_ist, format_ist_date, IST


def test_now_ist_is_localized():
    current = now_ist()
    assert current.tzinfo == IST
    assert current.utcoffset().total_seconds() == 19800  # +05:30 = 5.5 hours = 19800 seconds


def test_utc_to_ist_conversion():
    # 14:00 UTC = 19:30 IST (+5:30)
    utc_dt = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    ist_dt = to_ist(utc_dt)
    assert ist_dt.hour == 19
    assert ist_dt.minute == 30
    assert ist_dt.tzinfo == IST


def test_parse_to_ist_from_iso_strings():
    # ISO string with UTC 'Z'
    parsed_utc = parse_to_ist("2026-10-10T14:00:00Z")
    assert parsed_utc.hour == 19
    assert parsed_utc.minute == 30

    # ISO string with explicit +05:30 offset
    parsed_ist = parse_to_ist("2026-10-10T19:30:00+05:30")
    assert parsed_ist.hour == 19
    assert parsed_ist.minute == 30

    # Date string only
    parsed_date = parse_to_ist("2026-10-10")
    assert format_ist_date(parsed_date) == "2026-10-10"


def test_format_ist():
    dt = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    formatted = format_ist(dt)
    assert "07:30 PM IST" in formatted
