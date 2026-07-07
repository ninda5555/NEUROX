"""Paper trading with modeled costs (CLAUDE.md §13): entry/exit fills carry
adverse slippage and brokerage+STT estimate (config, default 0.05%/side).
Exits: target / stop / 15:15 square-off (intraday) or horizon expiry (swing),
evaluated from stored candles."""

from __future__ import annotations

import sqlite3

from src.journal.outcomes import evaluate_signal_horizon
from src.data.store import CandleStore
from src.risk.loss_limit import DayRiskTracker
from src.timeutil import ist_iso, now_ist, parse_iso


def open_paper_trade(conn: sqlite3.Connection, signal_id: int,
                     per_side_pct: float = 0.05) -> int:
    sig = dict(conn.execute("SELECT * FROM signals WHERE signal_id=?",
                            (signal_id,)).fetchone())
    # Fill at the signal price; the all-in per-side cost (brokerage + STT +
    # slippage) is charged once as a round-trip costs line at settlement, so
    # it is not double-counted via adverse fills (CLAUDE.md §13).
    entry_fill = sig["entry"]
    cur = conn.execute(
        """INSERT INTO paper_trades (signal_id, opened_at, entry_fill, qty)
           VALUES (?,?,?,?)""",
        (signal_id, sig["ts"], entry_fill, sig["qty"]))
    conn.commit()
    return int(cur.lastrowid)


def settle_paper_trades(conn: sqlite3.Connection, store: CandleStore,
                        per_side_pct: float = 0.05,
                        tracker: DayRiskTracker | None = None) -> int:
    """Close any open trade whose terminal horizon has resolved."""
    n = 0
    rows = conn.execute(
        """SELECT p.trade_id, p.entry_fill, p.qty, s.* FROM paper_trades p
           JOIN signals s ON s.signal_id = p.signal_id
           WHERE p.closed_at IS NULL""").fetchall()
    for r in rows:
        sig = dict(r)
        hz = "eod" if sig["mode"] == "INTRADAY" else "10d"
        res = evaluate_signal_horizon(sig, hz, store)
        if res is None:
            continue
        d = int(sig["direction"])
        exit_fill = res["price"]
        qty = sig["qty"]
        # single all-in round-trip cost (per_side_pct on each leg's notional)
        costs = (sig["entry_fill"] + exit_fill) * qty * per_side_pct / 100.0
        pnl = d * (exit_fill - sig["entry_fill"]) * qty - costs
        stop_dist = abs(sig["entry"] - sig["stop_loss"])
        r_mult = pnl / (qty * stop_dist) if qty and stop_dist else 0.0
        reason = {"hit_target": "target", "hit_stop": "stop",
                  "expired": "squareoff" if sig["mode"] == "INTRADAY" else "expired"}[res["status"]]
        conn.execute(
            """UPDATE paper_trades SET closed_at=?, exit_fill=?,
               costs_modeled=?, pnl=?, r_multiple=?, exit_reason=?
               WHERE trade_id=?""",
            (ist_iso(now_ist()), exit_fill, costs, pnl, r_mult, reason,
             sig["trade_id"]))
        if tracker is not None and sig["mode"] == "INTRADAY":
            tracker.record_pnl(pnl)
        n += 1
    conn.commit()
    return n
