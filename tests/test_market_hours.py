from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trader.market_hours import ET, is_regular_hours, session_note

UTC = ZoneInfo("UTC")


def et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# A normal Wednesday: 2026-06-03.
@pytest.mark.parametrize(
    "dt,expected",
    [
        (et(2026, 6, 3, 9, 30), True),    # open bell
        (et(2026, 6, 3, 12, 0), True),    # midday
        (et(2026, 6, 3, 15, 59), True),   # last minute
        (et(2026, 6, 3, 16, 0), False),   # close bell (exclusive)
        (et(2026, 6, 3, 9, 29), False),   # pre-market
        (et(2026, 6, 3, 18, 0), False),   # after-hours
    ],
)
def test_regular_wednesday(dt, expected):
    assert is_regular_hours(dt) is expected


def test_weekend_closed():
    assert not is_regular_hours(et(2026, 6, 6, 12, 0))   # Saturday
    assert not is_regular_hours(et(2026, 6, 7, 12, 0))   # Sunday
    assert session_note(et(2026, 6, 6, 12, 0)) == "weekend"


def test_holiday_closed():
    # Juneteenth 2026-06-19 (Friday).
    assert not is_regular_hours(et(2026, 6, 19, 12, 0))
    assert session_note(et(2026, 6, 19, 12, 0)) == "NYSE holiday"


def test_early_close():
    # Day after Thanksgiving 2026-11-27: closes 13:00 ET.
    assert is_regular_hours(et(2026, 11, 27, 12, 59))
    assert not is_regular_hours(et(2026, 11, 27, 13, 0))


def test_tz_conversion_from_utc():
    # 14:30 UTC = 09:30 ET (EDT) on a summer weekday → open.
    assert is_regular_hours(datetime(2026, 6, 3, 13, 30, tzinfo=UTC))  # 09:30 EDT
    assert not is_regular_hours(datetime(2026, 6, 3, 13, 29, tzinfo=UTC))


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        is_regular_hours(datetime(2026, 6, 3, 12, 0))
