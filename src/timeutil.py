"""IST time handling. CLAUDE.md §17.9: time is IST everywhere; a naive
datetime without timezone info is a bug — helpers here refuse them."""

from __future__ import annotations

import datetime as dt

import pytz

IST = pytz.timezone("Asia/Kolkata")

# NSE session boundaries (CLAUDE.md §2, §5). Times are IST wall-clock.
MARKET_OPEN = dt.time(9, 15)
MARKET_CLOSE = dt.time(15, 30)
INTRADAY_ENTRY_START = dt.time(9, 30)   # skip open-auction noise
INTRADAY_ENTRY_END = dt.time(14, 30)    # no fresh entries near close
INTRADAY_SQUAREOFF = dt.time(15, 15)

# Fyers access tokens expire daily around the ~06:00 IST cutover (§3.1).
TOKEN_CUTOVER = dt.time(6, 0)


class NaiveDatetimeError(TypeError):
    """Raised whenever a naive datetime reaches an IST helper."""


def ensure_aware(d: dt.datetime) -> dt.datetime:
    if d.tzinfo is None or d.tzinfo.utcoffset(d) is None:
        raise NaiveDatetimeError(
            f"naive datetime {d!r} — CLAUDE.md §17.9: every datetime must "
            "carry timezone info"
        )
    return d


def now_ist() -> dt.datetime:
    # UTC-anchored then converted, rather than datetime.now(IST) directly.
    # The two are mathematically identical for a fixed-offset zone like IST
    # (no DST, ever) — but this form doesn't depend on datetime.now(tz)
    # correctly delegating to tz.fromutc() under the hood, which is one
    # fewer thing to have to trust on a box this session can't inspect
    # directly when a timestamp bug is reported (2026-07-28 incident).
    return dt.datetime.now(dt.timezone.utc).astimezone(IST)


def to_ist(d: dt.datetime) -> dt.datetime:
    return ensure_aware(d).astimezone(IST)


def ist_iso(d: dt.datetime) -> str:
    """ISO-8601 with offset, in IST (§2: store ISO-8601 with offset)."""
    return to_ist(d).isoformat(timespec="seconds")


def parse_iso(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s)
    return to_ist(d)


def from_epoch(epoch_s: int | float) -> dt.datetime:
    return dt.datetime.fromtimestamp(float(epoch_s), tz=dt.timezone.utc).astimezone(IST)


def ist_date(d: dt.datetime | None = None) -> dt.date:
    return to_ist(d).date() if d is not None else now_ist().date()


def auth_day(d: dt.datetime | None = None) -> dt.date:
    """The Fyers 'auth day' a moment belongs to, with the ~06:00 IST cutover:
    a token issued at 07:00 on the 3rd is still the same auth day at 05:59 on
    the 4th, and expired at 06:00 on the 4th."""
    d = to_ist(d) if d is not None else now_ist()
    cut = dt.timedelta(hours=TOKEN_CUTOVER.hour, minutes=TOKEN_CUTOVER.minute)
    return (d - cut).date()


def in_intraday_entry_window(d: dt.datetime | None = None) -> bool:
    t = (to_ist(d) if d is not None else now_ist()).time()
    return INTRADAY_ENTRY_START <= t <= INTRADAY_ENTRY_END
