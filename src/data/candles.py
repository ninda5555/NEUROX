"""Tick -> 1-min -> 5-min candle aggregation (CLAUDE.md §10 data/candles.py).

NOTE (§15): the prototype's src/candles.py was meant to be ported; the
prototype was not attached, so this implements the documented behavior.
Reconcile when the prototype is provided.

Ticks carry cumulative session volume (Fyers SymbolUpdate), so per-bar volume
is the cumulative delta. Bars are stamped by their IST open time.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from src.timeutil import ensure_aware, to_ist


@dataclass
class Tick:
    ts: dt.datetime           # aware
    ltp: float
    cum_volume: float         # cumulative session volume


@dataclass
class Candle:
    ts: dt.datetime           # bar open (IST)
    open: float
    high: float
    low: float
    close: float
    volume: float

    def as_dict(self) -> dict:
        return {"ts": self.ts, "open": self.open, "high": self.high,
                "low": self.low, "close": self.close, "volume": self.volume}


def _bucket(ts: dt.datetime, minutes: int) -> dt.datetime:
    t = to_ist(ts)
    return t.replace(minute=(t.minute // minutes) * minutes, second=0, microsecond=0)


@dataclass
class CandleAggregator:
    """Streaming aggregator for one symbol. on_tick() returns a completed
    Candle whenever a bucket closes (else None); flush() closes the last bar
    (e.g. at session end / 15:30)."""

    minutes: int = 5
    _cur: Candle | None = None
    _last_cum_vol: float = 0.0
    _session_date: dt.date | None = None

    def on_tick(self, tick: Tick) -> Candle | None:
        ensure_aware(tick.ts)
        bucket = _bucket(tick.ts, self.minutes)
        if self._session_date != bucket.date():
            self._session_date = bucket.date()
            self._last_cum_vol = 0.0  # cumulative volume resets each session
        vol_delta = max(tick.cum_volume - self._last_cum_vol, 0.0)
        self._last_cum_vol = tick.cum_volume

        completed = None
        if self._cur is not None and self._cur.ts != bucket:
            completed = self._cur
            self._cur = None
        if self._cur is None:
            self._cur = Candle(ts=bucket, open=tick.ltp, high=tick.ltp,
                               low=tick.ltp, close=tick.ltp, volume=vol_delta)
        else:
            c = self._cur
            c.high = max(c.high, tick.ltp)
            c.low = min(c.low, tick.ltp)
            c.close = tick.ltp
            c.volume += vol_delta
        return completed

    def flush(self) -> Candle | None:
        done, self._cur = self._cur, None
        return done


def resample_candles(candles: list[Candle], minutes: int) -> list[Candle]:
    """1-min -> N-min (batch). Input must be one session, ascending."""
    out: list[Candle] = []
    for c in candles:
        b = _bucket(c.ts, minutes)
        if out and out[-1].ts == b:
            last = out[-1]
            last.high = max(last.high, c.high)
            last.low = min(last.low, c.low)
            last.close = c.close
            last.volume += c.volume
        else:
            out.append(Candle(ts=b, open=c.open, high=c.high, low=c.low,
                              close=c.close, volume=c.volume))
    return out
