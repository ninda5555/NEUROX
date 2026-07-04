"""Portfolio flags (CLAUDE.md §7) — shown, never silently enforced (V1 is
decision support). Sector: 2+ open positions in one sector. Correlation:
pairwise 60-session daily-return correlation > 0.7 with any open position.
instruments.sector has no feed yet, so sector flags no-op until it does."""

from __future__ import annotations

import sqlite3

import pandas as pd

from src.data.store import CandleStore

CORR_MAX = 0.7
CORR_WINDOW = 60


def open_positions(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT p.trade_id, s.symbol, s.mode, s.direction
           FROM paper_trades p JOIN signals s ON s.signal_id = p.signal_id
           WHERE p.closed_at IS NULL""").fetchall()
    return [dict(r) for r in rows]


def sector_of(conn: sqlite3.Connection, symbol: str) -> str | None:
    r = conn.execute("SELECT sector FROM instruments WHERE symbol=?", (symbol,)).fetchone()
    return r["sector"] if r and r["sector"] else None


def flags_for_candidate(conn: sqlite3.Connection, store: CandleStore,
                        symbol: str) -> list[str]:
    out: list[str] = []
    opened = open_positions(conn)
    sec = sector_of(conn, symbol)
    if sec:
        same = [o for o in opened if sector_of(conn, o["symbol"]) == sec]
        if len(same) >= 1:
            out.append(f"Sector: {len(same) + 1} open {sec} positions")

    if opened:
        cand = _returns(store, symbol)
        for o in opened:
            other = _returns(store, o["symbol"])
            if cand is None or other is None:
                continue
            j = pd.concat([cand, other], axis=1, join="inner").dropna()
            if len(j) >= 30:
                c = float(j.iloc[:, 0].corr(j.iloc[:, 1]))
                if c > CORR_MAX:
                    out.append(f"Correlated {c:.2f} with open {o['symbol']}")
    return out


def _returns(store: CandleStore, symbol: str) -> pd.Series | None:
    df = store.read_candles("1d", symbol)
    if len(df) < 30:
        return None
    s = df.set_index(pd.DatetimeIndex(df["ts"]).date)["close"].pct_change()
    return s.tail(CORR_WINDOW)
