"""Moscow calendar helpers for daily quota."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_MSK = ZoneInfo("Europe/Moscow")


def msk_now() -> datetime:
    """Current time in Europe/Moscow (timezone-aware)."""
    return datetime.now(_MSK)


def msk_today() -> str:
    """Return ``YYYY-MM-DD`` for the current Moscow calendar day."""
    return msk_now().date().isoformat()
