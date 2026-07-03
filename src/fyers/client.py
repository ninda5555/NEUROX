"""Fyers REST client + the shared rate limiter.

CLAUDE.md §17.6: EVERY Fyers REST call goes through the shared rate limiter
(10/second, 200/minute, 100,000/day) — no exceptions, no direct calls. No
module outside src/fyers/ may import fyers_apiv3 (enforced by a test).

The 10/s and 200/min windows are in-memory token buckets; the 100k/day count
is persisted in app_state so restarts don't reset it.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import threading
import time
from collections import deque
from typing import Any, Callable

import httpx

from src import db as dbm
from src.timeutil import IST, ist_date, from_epoch, now_ist

RATE_PER_SECOND = 10
RATE_PER_MINUTE = 200
RATE_PER_DAY = 100_000

# History API chunk ceilings (CLAUDE.md §3.3)
MAX_DAYS_PER_REQUEST = {"1d": 366, "5min": 100}
RESOLUTION = {"1d": "D", "5min": "5"}


class DailyLimitExceeded(RuntimeError):
    pass


class FyersApiError(RuntimeError):
    def __init__(self, endpoint: str, response: Any):
        self.endpoint = endpoint
        self.response = response
        super().__init__(f"Fyers API error on {endpoint}: {response!r}")


class SlidingWindowLimiter:
    """Allows at most `limit` events per `window_s` seconds (sliding window).
    acquire() blocks until a slot frees up. Injectable clock for tests."""

    def __init__(self, limit: int, window_s: float,
                 time_fn: Callable[[], float] = time.monotonic,
                 sleep_fn: Callable[[float], None] = time.sleep):
        self.limit = limit
        self.window_s = window_s
        self._events: deque[float] = deque()
        self._lock = threading.Lock()
        self._time = time_fn
        self._sleep = sleep_fn

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._time()
                while self._events and self._events[0] <= now - self.window_s:
                    self._events.popleft()
                if len(self._events) < self.limit:
                    self._events.append(now)
                    return
                wait = self._events[0] + self.window_s - now
            self._sleep(max(wait, 0.001))


class RateLimiter:
    """The shared limiter: 10/s + 200/min sliding windows, 100k/day persisted
    per IST calendar day in app_state (key fyers_calls_YYYY-MM-DD)."""

    def __init__(self, conn: sqlite3.Connection,
                 time_fn: Callable[[], float] = time.monotonic,
                 sleep_fn: Callable[[float], None] = time.sleep):
        self._per_second = SlidingWindowLimiter(RATE_PER_SECOND, 1.0, time_fn, sleep_fn)
        self._per_minute = SlidingWindowLimiter(RATE_PER_MINUTE, 60.0, time_fn, sleep_fn)
        self._conn = conn
        self._lock = threading.Lock()

    def _day_key(self) -> str:
        return f"fyers_calls_{ist_date().isoformat()}"

    def calls_today(self) -> int:
        return int(dbm.get_state(self._conn, self._day_key(), "0") or 0)

    def acquire(self) -> None:
        with self._lock:
            used = self.calls_today()
            if used >= RATE_PER_DAY:
                raise DailyLimitExceeded(
                    f"daily Fyers call budget exhausted ({used}/{RATE_PER_DAY}); "
                    "resumes after IST midnight"
                )
            dbm.set_state(self._conn, self._day_key(), str(used + 1))
        self._per_minute.acquire()
        self._per_second.acquire()


def chunk_date_ranges(start: dt.date, end: dt.date, max_days: int) -> list[tuple[dt.date, dt.date]]:
    """Inclusive [start, end] split into chunks of at most max_days days."""
    if start > end:
        return []
    out = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=max_days - 1), end)
        out.append((cur, chunk_end))
        cur = chunk_end + dt.timedelta(days=1)
    return out


class FyersClient:
    """Thin wrapper over fyers-apiv3. Every method acquires the shared rate
    limiter before touching the network."""

    def __init__(self, config, conn: sqlite3.Connection, access_token: str | None = None):
        self._cfg = config
        self._conn = conn
        self.limiter = RateLimiter(conn)
        self._token = access_token
        self._model = None

    # -- session ------------------------------------------------------------
    def set_access_token(self, token: str) -> None:
        self._token = token
        self._model = None

    def _sdk(self):
        if self._model is None:
            if not self._token:
                raise RuntimeError("no access token — run src/scripts/daily_auth.py first")
            from fyers_apiv3 import fyersModel  # only src/fyers/ may import this
            log_dir = str(self._cfg.path("fyers.log_dir"))
            self._model = fyersModel.FyersModel(
                client_id=self._cfg["fyers.app_id"], token=self._token,
                is_async=False, log_path=log_dir,
            )
        return self._model

    @staticmethod
    def _check(endpoint: str, resp: Any) -> Any:
        if not isinstance(resp, dict) or resp.get("s") != "ok":
            raise FyersApiError(endpoint, resp)
        return resp

    # -- REST endpoints -----------------------------------------------------
    def profile(self) -> dict:
        self.limiter.acquire()
        return self._check("profile", self._sdk().get_profile())

    def quotes(self, symbols: list[str]) -> dict:
        """Batched quotes, ~50 symbols/request (§3.3)."""
        if len(symbols) > 50:
            raise ValueError("quotes() takes at most 50 symbols per request")
        self.limiter.acquire()
        return self._check("quotes", self._sdk().quotes({"symbols": ",".join(symbols)}))

    def history(self, symbol: str, tf: str, range_from: dt.date, range_to: dt.date) -> list[list]:
        """One history request (caller chunks; see chunk_date_ranges).
        Returns raw candle rows [epoch, o, h, l, c, v]."""
        span = (range_to - range_from).days + 1
        if span > MAX_DAYS_PER_REQUEST[tf]:
            raise ValueError(
                f"history span {span}d exceeds {MAX_DAYS_PER_REQUEST[tf]}d "
                f"per-request ceiling for {tf} (§3.3) — chunk the range"
            )
        self.limiter.acquire()
        resp = self._check("history", self._sdk().history({
            "symbol": symbol,
            "resolution": RESOLUTION[tf],
            "date_format": "1",
            "range_from": range_from.isoformat(),
            "range_to": range_to.isoformat(),
            "cont_flag": "1",
        }))
        return resp.get("candles") or []

    def get_public_file(self, url: str) -> bytes:
        """Fyers-hosted public files (symbol master). Auth-free, but it is
        still a Fyers REST call, so it goes through the limiter too."""
        self.limiter.acquire()
        r = httpx.get(url, timeout=60, follow_redirects=True)
        r.raise_for_status()
        return r.content


def candles_to_records(raw: list[list]) -> list[dict]:
    """Fyers candle rows -> dicts with IST-aware timestamps."""
    out = []
    for row in raw:
        if len(row) < 6:
            raise ValueError(f"malformed candle row: {row!r}")
        ts = from_epoch(row[0])
        out.append({"ts": ts, "open": float(row[1]), "high": float(row[2]),
                    "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])})
    return out
