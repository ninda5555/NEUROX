import datetime as dt

import pytest

from src.fyers.client import (DailyLimitExceeded, RateLimiter,
                              SlidingWindowLimiter, chunk_date_ranges,
                              RATE_PER_DAY)


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept = 0.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.slept += s
        self.t += s


def test_sliding_window_blocks_at_limit():
    clock = FakeClock()
    lim = SlidingWindowLimiter(3, 1.0, time_fn=clock.time, sleep_fn=clock.sleep)
    for _ in range(3):
        lim.acquire()
    assert clock.slept == 0.0      # first 3 pass immediately
    lim.acquire()                  # 4th must wait for the window to slide
    assert clock.slept >= 1.0


def test_sliding_window_refills_after_window():
    clock = FakeClock()
    lim = SlidingWindowLimiter(2, 1.0, time_fn=clock.time, sleep_fn=clock.sleep)
    lim.acquire()
    lim.acquire()
    clock.t += 1.01                # window slides past both events
    before = clock.slept
    lim.acquire()
    assert clock.slept == before   # no wait needed


def test_daily_budget_persisted_and_enforced(conn):
    rl = RateLimiter(conn)
    rl.acquire()
    rl.acquire()
    assert rl.calls_today() == 2
    # a fresh limiter over the same DB sees the same count (restart survival)
    rl2 = RateLimiter(conn)
    assert rl2.calls_today() == 2
    # exhaust the budget -> hard stop, not a silent overrun
    key = rl._day_key()
    conn.execute("UPDATE app_state SET value=? WHERE key=?", (str(RATE_PER_DAY), key))
    conn.commit()
    with pytest.raises(DailyLimitExceeded):
        rl2.acquire()


def test_chunk_date_ranges_daily_366():
    start, end = dt.date(2024, 7, 1), dt.date(2026, 7, 1)
    chunks = chunk_date_ranges(start, end, 366)
    assert chunks[0][0] == start and chunks[-1][1] == end
    assert all((b - a).days + 1 <= 366 for a, b in chunks)
    # contiguous, no gaps or overlaps
    for (_, b1), (a2, _) in zip(chunks, chunks[1:]):
        assert (a2 - b1).days == 1


def test_chunk_date_ranges_5min_100():
    chunks = chunk_date_ranges(dt.date(2026, 3, 1), dt.date(2026, 7, 1), 100)
    assert all((b - a).days + 1 <= 100 for a, b in chunks)
    assert chunks[0][0] == dt.date(2026, 3, 1)
    assert chunks[-1][1] == dt.date(2026, 7, 1)


def test_chunk_single_day_and_empty():
    d = dt.date(2026, 7, 1)
    assert chunk_date_ranges(d, d, 100) == [(d, d)]
    assert chunk_date_ranges(d, d - dt.timedelta(days=1), 100) == []
