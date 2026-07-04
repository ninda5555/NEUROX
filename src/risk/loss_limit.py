"""Daily loss limit (CLAUDE.md §7): paper P&L <= -3% of capital halts new
INTRADAY signals for the day; 75% of the limit is a warning. State persists
in app_state so restarts cannot forget a halt. Port-from-spec of the
prototype's DayRiskTracker (zip still unattached)."""

from __future__ import annotations

import json
import sqlite3

from src import db as dbm
from src.timeutil import ist_date

STATE_KEY = "loss_limit_state"


class DayRiskTracker:
    def __init__(self, conn: sqlite3.Connection, capital: float,
                 limit_pct: float = 3.0, warn_frac: float = 0.75):
        self.conn, self.capital = conn, capital
        self.limit_pct, self.warn_frac = limit_pct, warn_frac

    def _load(self) -> dict:
        raw = dbm.get_state(self.conn, STATE_KEY)
        st = json.loads(raw) if raw else {}
        if st.get("date") != ist_date().isoformat():
            st = {"date": ist_date().isoformat(), "pnl": 0.0}
        return st

    def record_pnl(self, pnl: float) -> None:
        st = self._load()
        st["pnl"] = float(st.get("pnl", 0.0)) + float(pnl)
        dbm.set_state(self.conn, STATE_KEY, json.dumps(st))

    @property
    def day_pnl(self) -> float:
        return float(self._load().get("pnl", 0.0))

    def status(self) -> dict:
        pnl = self.day_pnl
        limit = -self.capital * self.limit_pct / 100.0
        state = "ok"
        if pnl <= limit:
            state = "halted"
        elif pnl <= limit * self.warn_frac:
            state = "warning"
        return {"state": state, "day_pnl": pnl, "limit": limit,
                "pct_of_capital": pnl / self.capital * 100.0}

    def allows_new_intraday(self) -> bool:
        return self.status()["state"] != "halted"
