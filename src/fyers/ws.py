"""Fyers data WebSocket (CLAUDE.md §3.3, §10): up to 5,000 symbols on one
socket. SymbolUpdate ticks feed the tick->5-min aggregator; DepthUpdate
(5-level) feeds OFI for the scanner's top-N shortlist only. This is the
LiveFeed transport — pure data, zero order capability (§17.8)."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from src.data.candles import Candle, CandleAggregator, Tick
from src.features.ofi import DepthSnapshot, compute_ofi
from src.timeutil import from_epoch, now_ist

log = logging.getLogger(__name__)

OnBar = Callable[[str, Candle], None]

MAX_SYMBOLS = 5000


class LiveDataSocket:
    """Wraps fyers_apiv3 FyersDataSocket. on_bar fires on every completed
    5-min bar; OFI per depth-subscribed symbol is available via ofi()."""

    def __init__(self, cfg, access_token: str, symbols: list[str],
                 on_bar: OnBar, depth_symbols: list[str] | None = None):
        if len(symbols) > MAX_SYMBOLS:
            raise ValueError(f"{len(symbols)} symbols exceeds the {MAX_SYMBOLS}/socket limit")
        self._cfg = cfg
        self._token = f"{cfg['fyers.app_id']}:{access_token}"
        self.symbols = symbols
        self.depth_symbols = depth_symbols or []
        self._on_bar = on_bar
        self._aggs: dict[str, CandleAggregator] = {}
        self._depth_prev: dict[str, DepthSnapshot] = {}
        self._ofi: dict[str, float] = {}
        self._lock = threading.Lock()
        self._sock = None

    # -- message handlers ----------------------------------------------------
    def _handle_message(self, msg: dict) -> None:
        try:
            t = msg.get("type")
            if t == "sf":     # SymbolUpdate
                self._on_tick(msg)
            elif t == "dp":   # DepthUpdate
                self._on_depth(msg)
        except Exception:
            log.exception("ws message handling failed: %.200s", msg)

    def _on_tick(self, m: dict) -> None:
        sym = m.get("symbol")
        ltp = m.get("ltp")
        if not sym or ltp is None:
            return
        ts = from_epoch(m.get("exch_feed_time") or m.get("last_traded_time")
                        or now_ist().timestamp())
        vol = float(m.get("vol_traded_today") or 0.0)
        with self._lock:
            agg = self._aggs.setdefault(sym, CandleAggregator(minutes=5))
            done = agg.on_tick(Tick(ts=ts, ltp=float(ltp), cum_volume=vol))
        if done is not None:
            self._on_bar(sym, done)

    def _on_depth(self, m: dict) -> None:
        sym = m.get("symbol")
        if not sym:
            return
        bids = [(float(b.get("price", 0)), float(b.get("volume", 0)))
                for b in (m.get("bids") or [])][:5]
        asks = [(float(a.get("price", 0)), float(a.get("volume", 0)))
                for a in (m.get("ask") or m.get("asks") or [])][:5]
        cur = DepthSnapshot(bids=bids, asks=asks)
        prev = self._depth_prev.get(sym)
        if prev is not None:
            self._ofi[sym] = compute_ofi(prev, cur)
        self._depth_prev[sym] = cur

    def ofi(self, symbol: str) -> float | None:
        return self._ofi.get(symbol)

    def flush_session(self) -> None:
        """Close dangling bars (call at 15:30)."""
        with self._lock:
            for sym, agg in self._aggs.items():
                done = agg.flush()
                if done is not None:
                    self._on_bar(sym, done)

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        from fyers_apiv3.FyersWebsocket import data_ws  # only src/fyers/ imports SDK

        def on_open():
            self._sock.subscribe(symbols=self.symbols, data_type="SymbolUpdate")
            if self.depth_symbols:
                self._sock.subscribe(symbols=self.depth_symbols, data_type="DepthUpdate")
            self._sock.keep_running()
            log.info("ws subscribed: %d symbols, %d depth", len(self.symbols),
                     len(self.depth_symbols))

        self._sock = data_ws.FyersDataSocket(
            access_token=self._token, log_path=str(self._cfg.path("fyers.log_dir")),
            litemode=False, write_to_file=False, reconnect=True,
            on_connect=on_open, on_message=self._handle_message,
            on_error=lambda m: log.error("ws error: %s", m),
            on_close=lambda m: log.warning("ws closed: %s", m),
        )
        self._sock.connect()

    def stop(self) -> None:
        if self._sock is not None:
            self._sock.close_connection()
