"""Chunked history backfill (CLAUDE.md §3.3, §16 P0).

Every request goes through the shared rate limiter via FyersClient. Chunk
ceilings: daily 366 days/request, 5-min 100 days/request. Resumes from the
last stored candle per symbol.
"""

from __future__ import annotations

import datetime as dt
import logging

from src.data.store import CandleStore
from src.fyers.client import (FyersClient, MAX_DAYS_PER_REQUEST,
                              candles_to_records, chunk_date_ranges,
                              FyersApiError)
from src.timeutil import ist_date

log = logging.getLogger(__name__)


def backfill_symbol(client: FyersClient, store: CandleStore, symbol: str,
                    tf: str, days: int, end: dt.date | None = None) -> dict:
    """Backfill one symbol/timeframe. Returns counts for reporting."""
    end = end or ist_date()
    start = end - dt.timedelta(days=days - 1)

    last = store.last_ts(tf, symbol)
    if last is not None and last.date() >= start:
        start = last.date()  # re-fetch the last stored day to complete it

    written = requests = 0
    for c_from, c_to in chunk_date_ranges(start, end, MAX_DAYS_PER_REQUEST[tf]):
        raw = client.history(symbol, tf, c_from, c_to)
        requests += 1
        if raw:
            written += store.write_candles(tf, symbol, candles_to_records(raw))
    return {"symbol": symbol, "tf": tf, "requests": requests, "rows": written}


def backfill_many(client: FyersClient, store: CandleStore, symbols: list[str],
                  tf: str, days: int, on_progress=None) -> dict:
    """Backfill a list of symbols; failures are collected, not fatal —
    a single delisted symbol must not kill a 1,500-symbol run."""
    ok = failed = total_rows = total_reqs = 0
    errors: list[tuple[str, str]] = []
    for i, sym in enumerate(symbols):
        try:
            r = backfill_symbol(client, store, sym, tf, days)
            ok += 1
            total_rows += r["rows"]
            total_reqs += r["requests"]
        except FyersApiError as e:
            failed += 1
            errors.append((sym, str(e.response)[:200]))
            log.warning("backfill failed for %s: %s", sym, e)
        if on_progress:
            on_progress(i + 1, len(symbols), sym)
    return {"tf": tf, "symbols_ok": ok, "symbols_failed": failed,
            "rows": total_rows, "requests": total_reqs,
            "errors": errors[:20],
            "calls_today": client.limiter.calls_today()}
