"""Liquidity filter (CLAUDE.md §4 step 4), computed from daily candles over
a rolling 20-session window:
  - median daily turnover >= ₹5 crore
  - price >= ₹20 (penny guard), listed >= 60 sessions
  - non-zero volume in >= 18 of the last 20 sessions
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

CRORE = 1e7
ATR_PERIOD = 14
ROLLING_SESSIONS = 20


@dataclass
class LiquidityMetrics:
    sessions_listed: int
    last_close: float
    median_turnover_cr: float
    adv_20d: float          # average daily volume, shares, last 20 sessions
    atr_pct: float          # ATR(14) as % of last close
    nonzero_vol_sessions: int  # of the last 20


def compute_metrics(daily: pd.DataFrame) -> LiquidityMetrics:
    """`daily` columns: ts, open, high, low, close, volume — ascending ts."""
    if daily.empty:
        raise ValueError("no daily candles")
    d = daily.sort_values("ts").reset_index(drop=True)
    tail = d.tail(ROLLING_SESSIONS)
    turnover_cr = (tail["close"] * tail["volume"]) / CRORE

    prev_close = d["close"].shift(1)
    tr = pd.concat([
        d["high"] - d["low"],
        (d["high"] - prev_close).abs(),
        (d["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / ATR_PERIOD, adjust=False).mean().iloc[-1]
    last_close = float(d["close"].iloc[-1])

    return LiquidityMetrics(
        sessions_listed=len(d),
        last_close=last_close,
        median_turnover_cr=float(turnover_cr.median()),
        adv_20d=float(tail["volume"].mean()),
        atr_pct=float(atr / last_close * 100) if last_close else 0.0,
        nonzero_vol_sessions=int((tail["volume"] > 0).sum()),
    )


def decide(m: LiquidityMetrics, cfg) -> str | None:
    """Return an exclusion reason code, or None if the stock passes.
    Reason codes are UI-facing (design bundle uses them verbatim)."""
    if m.last_close < cfg["universe.min_price"]:
        return "PRICE_LT_20"
    if m.sessions_listed < cfg["universe.min_listed_sessions"]:
        return "LISTED_LT_60"
    if m.median_turnover_cr < cfg["universe.min_turnover_cr"]:
        return "LOW_TURNOVER"
    if m.nonzero_vol_sessions < cfg["universe.min_nonzero_vol_sessions"]:
        return "ILLIQUID_GAPS"
    return None
