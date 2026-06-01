"""US equity regular-trading-hours guard (spec §7.5.6).

Pure: every function takes an explicit timezone-aware `now` so tests are
deterministic (freezegun / fixed datetimes). RTH = 09:30–16:00 America/New_York,
Mon–Fri, excluding NYSE full holidays. On early-close days the session ends 13:00 ET.

NYSE holiday / early-close tables must be maintained yearly. Years not in the table
fall back to weekday+time only (fail-OPEN on unknown holidays is acceptable here:
the worst case is the veto window + execute-once still gate a stray order, and a
genuinely closed market rejects the order broker-side).
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# NYSE full-day closures.
_HOLIDAYS: set[date] = {
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}

# Early closes (1:00 PM ET).
_EARLY_CLOSES: set[date] = {
    date(2026, 11, 27), date(2026, 12, 24),
    date(2027, 11, 26),
}

_KNOWN_YEARS = {2026, 2027}


def _close_time(d: date) -> time:
    return EARLY_CLOSE if d in _EARLY_CLOSES else RTH_CLOSE


def is_regular_hours(now: datetime) -> bool:
    """True if `now` falls within US equity RTH. `now` must be tz-aware."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    et = now.astimezone(ET)
    d = et.date()
    if et.weekday() >= 5:  # Sat/Sun
        return False
    if d in _HOLIDAYS:
        return False
    return RTH_OPEN <= et.timetz().replace(tzinfo=None) < _close_time(d)


def session_note(now: datetime) -> str:
    """Short reason string for logs/Telegram when out of RTH."""
    et = now.astimezone(ET)
    if et.weekday() >= 5:
        return "weekend"
    if et.date() in _HOLIDAYS:
        return "NYSE holiday"
    if et.date().year not in _KNOWN_YEARS:
        return "holiday table missing for year (weekday/time only)"
    t = et.timetz().replace(tzinfo=None)
    if t < RTH_OPEN:
        return "pre-market"
    return "after-hours"
