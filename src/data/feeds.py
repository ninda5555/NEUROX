"""Feed abstraction: Live / Delayed / Replay (CLAUDE.md §15 — port of the
prototype's feeds.py concept; prototype not attached, implemented from spec).

Replay mode is gold for development: it replays stored candles through the
same callback interface the live WebSocket will use, so the whole signal path
runs identically off-line. LiveFeed lands in P3 with ws.py (5k-symbol socket
+ DepthUpdate); its interface is fixed here.
"""

from __future__ import annotations

import abc
import datetime as dt
from typing import Callable, Iterator

from src.data.candles import Candle
from src.data.store import CandleStore

OnBar = Callable[[str, Candle], None]   # (symbol, completed bar)


class Feed(abc.ABC):
    @abc.abstractmethod
    def run(self, on_bar: OnBar) -> None: ...


class ReplayFeed(Feed):
    """Replays stored candles in global timestamp order — deterministic
    development/backtest feed."""

    def __init__(self, store: CandleStore, symbols: list[str], tf: str = "5min",
                 start: dt.datetime | None = None, end: dt.datetime | None = None,
                 speed: float = 0.0):
        self.store, self.symbols, self.tf = store, symbols, tf
        self.start, self.end, self.speed = start, end, speed

    def _events(self) -> Iterator[tuple[dt.datetime, str, Candle]]:
        frames = []
        for sym in self.symbols:
            df = self.store.read_candles(self.tf, sym, self.start, self.end)
            for row in df.itertuples():
                frames.append((row.ts, sym, Candle(
                    ts=row.ts, open=row.open, high=row.high,
                    low=row.low, close=row.close, volume=row.volume)))
        frames.sort(key=lambda x: (x[0], x[1]))
        yield from frames

    def run(self, on_bar: OnBar) -> None:
        import time
        prev = None
        for ts, sym, candle in self._events():
            if self.speed > 0 and prev is not None:
                gap = (ts - prev).total_seconds() / self.speed
                if gap > 0:
                    time.sleep(min(gap, 1.0))
            prev = ts
            on_bar(sym, candle)


class LiveFeed(Feed):
    """P3: wraps the Fyers data WebSocket (SymbolUpdate ticks -> aggregator ->
    5-min bars, DepthUpdate -> OFI for the top-N shortlist)."""

    def __init__(self, *a, **kw):
        raise NotImplementedError("LiveFeed ships in P3 with src/fyers/ws.py")
