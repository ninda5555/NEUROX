"""Scheduled outcome evaluator (CLAUDE.md §13): fills signal_outcomes at each
horizon from stored candles. Horizons — intraday: 30m/60m/eod (square-off
15:15); swing: 1d/5d/10d sessions. Status: hit_target if the target traded
before the stop within the horizon, hit_stop if the stop traded first,
expired otherwise; MAE/MFE are the worst/best excursions vs entry."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pandas as pd

from src.data.store import CandleStore
from src.timeutil import INTRADAY_SQUAREOFF, IST, ist_iso, now_ist, parse_iso

HORIZONS = {"INTRADAY": ["30m", "60m", "eod"], "SWING": ["1d", "5d", "10d"]}


def _window(sig: dict, horizon: str, store: CandleStore) -> pd.DataFrame:
    t0 = parse_iso(sig["ts"])
    now = now_ist()
    if sig["mode"] == "INTRADAY":
        df = store.read_candles("5min", sig["symbol"], start=t0)
        df = df[pd.DatetimeIndex(df["ts"]).date == t0.date()]
        # Broker timestamps mark candle OPEN. Only completed candles that
        # started after the decision may contribute to a forward outcome.
        df = df[(df["ts"] >= t0) & (df["ts"] + dt.timedelta(minutes=5) <= now)]
        if horizon in ("30m", "60m"):
            mins = 30 if horizon == "30m" else 60
            df = df[df["ts"] + dt.timedelta(minutes=5) <= t0 + dt.timedelta(minutes=mins)]
        else:  # eod = up to square-off
            deadline = IST.localize(dt.datetime.combine(t0.date(), INTRADAY_SQUAREOFF))
            df = df[df["ts"] + dt.timedelta(minutes=5) <= deadline]
        return df
    df = store.read_candles("1d", sig["symbol"])
    df = df[pd.DatetimeIndex(df["ts"]).date > t0.date()]
    complete = (pd.DatetimeIndex(df["ts"]).normalize() + pd.Timedelta(hours=15, minutes=30)) <= now
    df = df[complete]
    n = {"1d": 1, "5d": 5, "10d": 10}[horizon]
    return df.head(n)


def evaluate_signal_horizon(sig: dict, horizon: str, store: CandleStore) -> dict | None:
    """None -> window not complete yet (stays pending)."""
    w = _window(sig, horizon, store)
    if w.empty:
        return None
    d = int(sig["direction"])
    entry, stop, target = sig["entry"], sig["stop_loss"], sig["target"]
    stop_dist = abs(entry - stop)
    status, exit_px = "expired", float(w["close"].iloc[-1])
    last_index = 0
    for last_index, r in enumerate(w.itertuples()):
        if d > 0 and r.low <= stop:
            status, exit_px = "hit_stop", min(stop, r.open); break
        if d > 0 and r.high >= target:
            status, exit_px = "hit_target", target; break
        if d < 0 and r.high >= stop:
            status, exit_px = "hit_stop", max(stop, r.open); break
        if d < 0 and r.low <= target:
            status, exit_px = "hit_target", target; break
    if status == "expired":
        t0 = parse_iso(sig["ts"])
        if sig["mode"] == "INTRADAY":
            deadline = (t0 + dt.timedelta(minutes=int(horizon[:-1])) if horizon != "eod"
                        else IST.localize(dt.datetime.combine(t0.date(), INTRADAY_SQUAREOFF)))
            if now_ist() < deadline or w["ts"].iloc[-1] + dt.timedelta(minutes=5) < deadline:
                return None
        elif len(w) < {"1d": 1, "5d": 5, "10d": 10}[horizon]:
            return None
    # Excursions after the exit cannot count toward the trade's performance.
    w = w.iloc[:last_index + 1]
    ret_pct = (exit_px / entry - 1) * 100 * d
    r_multiple = (exit_px - entry) * d / stop_dist if stop_dist else 0.0
    mae = (float(w["low"].min()) / entry - 1) * 100 if d > 0 else (1 - float(w["high"].max()) / entry) * 100
    mfe = (float(w["high"].max()) / entry - 1) * 100 if d > 0 else (1 - float(w["low"].min()) / entry) * 100
    return {"price": exit_px, "ret_pct": ret_pct, "r_multiple": r_multiple,
            "mae_pct": mae, "mfe_pct": mfe, "status": status}


def evaluate_pending(conn: sqlite3.Connection, store: CandleStore) -> int:
    """Fill/refresh outcomes for all signals; returns rows written."""
    n = 0
    for sig in [dict(r) for r in conn.execute("SELECT * FROM signals")]:
        for hz in HORIZONS[sig["mode"]]:
            done = conn.execute(
                "SELECT status FROM signal_outcomes WHERE signal_id=? AND horizon=?",
                (sig["signal_id"], hz)).fetchone()
            if done and done["status"] != "pending":
                continue
            res = evaluate_signal_horizon(sig, hz, store)
            if res is None:
                conn.execute(
                    """INSERT OR IGNORE INTO signal_outcomes
                       (signal_id, horizon, status) VALUES (?,?,'pending')""",
                    (sig["signal_id"], hz))
                continue
            conn.execute(
                """INSERT INTO signal_outcomes (signal_id, horizon, price,
                   ret_pct, r_multiple, mae_pct, mfe_pct, status, evaluated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(signal_id, horizon) DO UPDATE SET
                     price=excluded.price, ret_pct=excluded.ret_pct,
                     r_multiple=excluded.r_multiple, mae_pct=excluded.mae_pct,
                     mfe_pct=excluded.mfe_pct, status=excluded.status,
                     evaluated_at=excluded.evaluated_at""",
                (sig["signal_id"], hz, res["price"], res["ret_pct"],
                 res["r_multiple"], res["mae_pct"], res["mfe_pct"],
                 res["status"], ist_iso(now_ist())))
            n += 1
    conn.commit()
    return n
