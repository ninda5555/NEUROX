"""Volatility-targeted sizing (CLAUDE.md §7): qty = (capital × risk_pct) /
stop_distance — size shrinks as volatility expands. Hard cap: single position
<= 20% of capital notional."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RiskPlan:
    entry: float
    stop: float
    target: float
    qty: int
    capital_at_risk: float     # ₹ lost if stop hits (ex-costs)
    risk_pct: float            # % of capital at risk
    notional: float
    notional_capped: bool
    holding_stmt: str
    gap_note: str | None = None


def size_position(*, capital: float, risk_pct: float, entry: float, stop: float,
                  max_notional_pct: float = 20.0) -> tuple[int, bool]:
    stop_dist = abs(entry - stop)
    if stop_dist <= 0 or entry <= 0:
        return 0, False
    qty = math.floor((capital * risk_pct / 100.0) / stop_dist)
    cap_qty = math.floor(capital * max_notional_pct / 100.0 / entry)
    if qty > cap_qty:
        return max(cap_qty, 0), True
    return max(qty, 0), False
