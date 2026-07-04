"""Live-vs-CV divergence + weekly digest (CLAUDE.md §8, §13).

The calibration promise audited against reality: realized hit-rate vs stated
confidence per bucket, and the §8 drift red flag — live/paper divergence from
CV expectation > 10 points over the last 30 signals. Displayed, never
suppressed; stored in app_state for the UI banner."""

from __future__ import annotations

import json
import sqlite3

import numpy as np

from src import db as dbm
from src.timeutil import ist_iso, now_ist

DRIFT_PTS = 0.10
DRIFT_WINDOW = 30


def _terminal_outcomes(conn: sqlite3.Connection, mode: str) -> list[dict]:
    hz = "eod" if mode == "INTRADAY" else "10d"
    return [dict(r) for r in conn.execute(
        """SELECT s.signal_id, s.ts, s.confidence, o.status
           FROM signals s JOIN signal_outcomes o ON o.signal_id = s.signal_id
           WHERE s.mode = ? AND o.horizon = ? AND o.status != 'pending'
           ORDER BY s.ts""", (mode, hz))]


def confidence_buckets(conn: sqlite3.Connection, mode: str, width: float = 0.05) -> list[dict]:
    """Stated confidence bucket -> realized hit rate. The honesty audit."""
    rows = _terminal_outcomes(conn, mode)
    out: dict[float, list[int]] = {}
    for r in rows:
        b = round(float(r["confidence"]) // width * width, 2)
        out.setdefault(b, []).append(1 if r["status"] == "hit_target" else 0)
    return [{"bucket": b, "stated": b + width / 2, "n": len(v),
             "realized": round(float(np.mean(v)), 3)}
            for b, v in sorted(out.items())]


def drift_check(conn: sqlite3.Connection, mode: str) -> dict:
    """§8: live/paper divergence from CV expectation over the last 30
    terminal signals. Positive gap = reality WORSE than stated confidence."""
    rows = _terminal_outcomes(conn, mode)[-DRIFT_WINDOW:]
    state = {"mode": mode, "n": len(rows), "checked_at": ist_iso(now_ist()),
             "drift": None, "flag": False}
    if len(rows) >= DRIFT_WINDOW:
        stated = float(np.mean([r["confidence"] for r in rows]))
        realized = float(np.mean([r["status"] == "hit_target" for r in rows]))
        gap = stated - realized
        state.update(stated=round(stated, 3), realized=round(realized, 3),
                     drift=round(gap, 3), flag=abs(gap) > DRIFT_PTS)
        if state["flag"]:
            state["detail"] = (f"last {DRIFT_WINDOW} signals: stated {stated:.2f} "
                               f"vs realized {realized:.2f} ({gap * 100:+.0f} pts) — "
                               "live/paper drift beyond 10 pts")
    dbm.set_state(conn, f"drift_{mode}", json.dumps(state))
    return state


def weekly_digest(conn: sqlite3.Connection, mode: str) -> dict:
    buckets = confidence_buckets(conn, mode)
    drift = drift_check(conn, mode)
    trades = conn.execute(
        """SELECT COUNT(*) n, COALESCE(SUM(p.pnl),0) pnl,
           COALESCE(SUM(p.costs_modeled),0) costs
           FROM paper_trades p JOIN signals s ON s.signal_id=p.signal_id
           WHERE s.mode=? AND p.closed_at IS NOT NULL""", (mode,)).fetchone()
    return {"mode": mode, "buckets": buckets, "drift": drift,
            "paper": {"n": trades["n"], "pnl": round(trades["pnl"], 0),
                      "costs": round(trades["costs"], 0)}}
