"""Scanner ranking (CLAUDE.md §6.3): rank candidates by confidence ×
liquidity, surface the top N (default 12) — the full universe is NEVER
dumped. 'No qualifying setups' is a first-class, correct outcome."""

from __future__ import annotations

import math
import sqlite3

from src import db as dbm
from src.signals.engine import Candidate


def liquidity_weight(turnover_cr: float | None, lo: float = 5.0,
                     hi: float = 2000.0) -> float:
    """log-scaled ₹-turnover -> (0, 1]."""
    t = max(float(turnover_cr or lo), lo)
    return min(math.log(t / lo) / math.log(hi / lo), 1.0) or 0.01


def snapshot_turnover(conn: sqlite3.Connection) -> dict[str, float]:
    snap = dbm.get_state(conn, "universe_snap_date")
    if not snap:
        return {}
    return {r["symbol"]: r["median_turnover_cr"] for r in conn.execute(
        "SELECT symbol, median_turnover_cr FROM universe_snapshot "
        "WHERE snap_date=? AND included=1", (snap,)) if r["median_turnover_cr"]}


def rank(candidates: list[Candidate], turnover: dict[str, float],
         top_n: int = 12) -> list[Candidate]:
    scored = [(c.confidence * liquidity_weight(turnover.get(c.symbol)), c)
              for c in candidates]
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:top_n]]
