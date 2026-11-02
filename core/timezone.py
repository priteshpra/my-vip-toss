"""Strict Asia/Kolkata (IST) Timezone Engine."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Union

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def now_ist() -> datetime:
    """Return the current time in Asia/Kolkata timezone (timezone-aware)."""
    return datetime.now(IST)


def to_ist(dt: Optional[Union[str, datetime]]) -> Optional[datetime]:
    """Convert any naive or aware datetime or timestamp string to Asia/Kolkata timezone."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return parse_to_ist(dt)
    if dt.tzinfo is None:
        # Naive datetime is assumed UTC per API standards
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def parse_to_ist(val: Optional[Union[str, datetime]]) -> Optional[datetime]:
    """Parse string or datetime and ensure it is localized to Asia/Kolkata."""
    if not val:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=UTC).astimezone(IST)
        return val.astimezone(IST)

    val_str = str(val).strip()
    if not val_str:
        return None

    # Try standard ISO parsing
    try:
        dt = datetime.fromisoformat(val_str)
        return to_ist(dt)
    except ValueError:
        pass

    # Try common formats
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(val_str, fmt)
            return to_ist(dt)
        except ValueError:
            continue

    raise ValueError(f"Unable to parse timestamp to IST: {val_str}")


def format_ist(dt: Optional[datetime], fmt: str = "%Y-%m-%d %I:%M %p IST") -> str:
    """Format a datetime in IST."""
    if dt is None:
        return "N/A"
    localized = to_ist(dt)
    return localized.strftime(fmt)


def format_ist_date(dt: Optional[datetime]) -> str:
    """Format as YYYY-MM-DD in IST."""
    if dt is None:
        return "N/A"
    return to_ist(dt).strftime("%Y-%m-%d")


def format_ist_time_only(dt: Optional[datetime]) -> str:
    """Format as 12-hour time in IST."""
    if dt is None:
        return "TBD"
    return to_ist(dt).strftime("%I:%M %p IST")


def to_iso_ist(dt: Optional[datetime]) -> Optional[str]:
    """Return ISO 8601 string localized to Asia/Kolkata (+05:30)."""
    if dt is None:
        return None
    return to_ist(dt).isoformat()
