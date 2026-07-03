import datetime as dt

import pytest

from src.timeutil import (IST, NaiveDatetimeError, auth_day, ensure_aware,
                          from_epoch, in_intraday_entry_window, ist_iso)


def ist(y, mo, d, h, mi=0):
    return IST.localize(dt.datetime(y, mo, d, h, mi))


def test_naive_datetime_is_a_bug():
    with pytest.raises(NaiveDatetimeError):
        ensure_aware(dt.datetime(2026, 7, 3, 10, 0))
    with pytest.raises(NaiveDatetimeError):
        ist_iso(dt.datetime(2026, 7, 3, 10, 0))


def test_ist_iso_carries_offset():
    assert ist_iso(ist(2026, 7, 3, 10, 30)) == "2026-07-03T10:30:00+05:30"


def test_auth_day_6am_cutover_boundaries():
    # token issued 07:00 on the 3rd belongs to auth day 3rd…
    assert auth_day(ist(2026, 7, 3, 7)) == dt.date(2026, 7, 3)
    # …still the same auth day at 05:59 the next morning…
    assert auth_day(ist(2026, 7, 4, 5, 59)) == dt.date(2026, 7, 3)
    # …and expired from 06:00.
    assert auth_day(ist(2026, 7, 4, 6, 0)) == dt.date(2026, 7, 4)


def test_from_epoch_returns_ist():
    d = from_epoch(1_751_500_000)
    assert d.tzinfo is not None
    assert d.utcoffset() == dt.timedelta(hours=5, minutes=30)


def test_intraday_entry_window():
    assert not in_intraday_entry_window(ist(2026, 7, 3, 9, 29))
    assert in_intraday_entry_window(ist(2026, 7, 3, 9, 30))
    assert in_intraday_entry_window(ist(2026, 7, 3, 14, 30))
    assert not in_intraday_entry_window(ist(2026, 7, 3, 14, 31))
