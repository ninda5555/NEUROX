"""Journal-first persistence (CLAUDE.md §13, §17.4): every emitted signal is
written with its COMPLETE context before it is shown anywhere — hindsight
cannot edit history."""

from __future__ import annotations

import json
import sqlite3

from src.timeutil import ist_iso, now_ist


def insert_signal(conn: sqlite3.Connection, *, mode: str, symbol: str,
                  direction: int, confidence: float, model_id: str,
                  features: dict, shap_top: list[dict], regime: dict | None,
                  entry: float, stop: float, target: float, qty: int,
                  capital_at_risk: float, risk_pct: float,
                  risk_flags: list[str], holding_stmt: str,
                  explanation: str, ts: str | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO signals (ts, mode, symbol, direction, confidence,
           model_id, features, shap_top, regime, entry, stop_loss, target,
           qty, capital_at_risk, risk_pct, risk_flags, holding_stmt, explanation)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ts or ist_iso(now_ist()), mode, symbol, direction, confidence, model_id,
         json.dumps(features), json.dumps(shap_top),
         json.dumps(regime) if regime else None, entry, stop, target, qty,
         capital_at_risk, risk_pct, json.dumps(risk_flags), holding_stmt,
         explanation))
    conn.commit()  # durable BEFORE anything is displayed
    return int(cur.lastrowid)


def list_signals(conn: sqlite3.Connection, mode: str | None = None,
                 query: str | None = None, limit: int = 200) -> list[dict]:
    sql = """SELECT s.*, GROUP_CONCAT(o.horizon || '=' ||
             COALESCE(o.r_multiple, 'pending'), ', ') AS outcomes
             FROM signals s LEFT JOIN signal_outcomes o
             ON o.signal_id = s.signal_id"""
    where, args = [], []
    if mode:
        where.append("s.mode = ?"); args.append(mode)
    if query:
        where.append("s.symbol LIKE ?"); args.append(f"%{query.upper()}%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY s.signal_id ORDER BY s.ts DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args)]
