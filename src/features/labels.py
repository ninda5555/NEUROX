"""Triple-barrier labels (CLAUDE.md §5) + tradability masks (§17.5).

Masks: bars where the stock sat in a price band/circuit are unexecutable and
must never train the model. Without an official circuit feed we detect them
structurally: a bar with high == low is locked; a daily move beyond ~19.9%
that closes at its extreme is a hit band. Masked entries get label = NaN.

Labels:
  INTRADAY (5-min bars): entry at bar close, only 09:30-14:30 IST entries.
    Long: +1 if entry + 1.5*ATR5 is touched before entry - 1.0*ATR5 before
    the 15:15 square-off, else 0. Short is mirrored.
  SWING (daily bars): entry at NEXT session's open (signals are generated
    post-close). +1 if entry + 2*ATR14d touched before entry - 1.25*ATR14d
    within 10 sessions, else 0 (stop-first or timeout).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from src.features.shared import atr
from src.timeutil import INTRADAY_ENTRY_END, INTRADAY_ENTRY_START, INTRADAY_SQUAREOFF

CIRCUIT_DAILY_PCT = 19.9  # widest common band; conservative mask
MIN_BARRIER_ATR_PCT = 0.30  # §5: floor barrier/stop ATR at 0.30% of price


def locked_bar_mask(df: pd.DataFrame) -> pd.Series:
    """True where the bar is untradable (circuit-locked)."""
    locked = (df["high"] == df["low"])
    move = (df["close"] / df["close"].shift(1) - 1).abs() * 100
    at_extreme = (df["close"] == df["high"]) | (df["close"] == df["low"])
    return locked | ((move >= CIRCUIT_DAILY_PCT) & at_extreme)


# Three-way barrier outcomes (corrected 2026-08-05). The binary label alone
# conflated "stopped out" with "went nowhere for the whole window", and the
# R accounting then charged BOTH a full -1R. That is wrong in a knowable
# direction: a timeout exits near flat, not at the stop, so measured
# expectancy was biased pessimistic everywhere it was reported. Swing's
# observed base rate of 0.274 sits well below the 0.385 a driftless random
# walk gives on these barriers — the signature of timeouts being booked as
# losses rather than of a genuinely awful edge.
OUTCOME_TARGET = "target"
OUTCOME_STOP = "stop"
OUTCOME_TIMEOUT = "timeout"


def _walk_barriers(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                   entry: float, tp: float, sl: float,
                   long_side: bool) -> tuple[int, str, float]:
    """Walk the barrier window once and report what actually happened.

    Returns (label, outcome, r_multiple):
      label       1 if TP touched before SL, else 0 — UNCHANGED, so the
                  classifier target and every existing model contract are
                  exactly as before.
      outcome     'target' | 'stop' | 'timeout'
      r_multiple  realised R in units of the planned risk (entry->sl):
                  +tp_distance/risk on a target, -1.0 on a stop, and for a
                  timeout the MARK-TO-MARKET at the window's last close,
                  which is the trade you would actually have exited.
    """
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    for h, l in zip(highs, lows):
        if long_side:
            if l <= sl:
                return 0, OUTCOME_STOP, -1.0
            if h >= tp:
                return 1, OUTCOME_TARGET, reward / risk
        else:
            if h >= sl:
                return 0, OUTCOME_STOP, -1.0
            if l <= tp:
                return 1, OUTCOME_TARGET, reward / risk
    if len(closes) == 0 or risk <= 0:
        return 0, OUTCOME_TIMEOUT, 0.0
    move = (closes[-1] - entry) if long_side else (entry - closes[-1])
    return 0, OUTCOME_TIMEOUT, float(move / risk)


def intraday_labels(df5: pd.DataFrame, atr_period: int = 5,
                    tp_mult: float = 1.5, sl_mult: float = 1.0,
                    min_barrier_pct: float = MIN_BARRIER_ATR_PCT) -> pd.DataFrame:
    """df5: one symbol's 5-min bars (ascending, IST). Returns label_long,
    label_short, mask columns aligned to df5. The barrier ATR is floored at
    min_barrier_pct% of price (§5) so calm-stock barriers stay above the
    trading-cost/noise floor rather than measuring sub-cost wiggles."""
    d = df5.reset_index(drop=True)
    a = atr(d, atr_period).clip(lower=min_barrier_pct / 100.0 * d["close"])
    locked = locked_bar_mask(d)
    times = pd.DatetimeIndex(d["ts"])
    dates = times.date

    n = len(d)
    label_long = np.full(n, np.nan)
    label_short = np.full(n, np.nan)
    r_long = np.full(n, np.nan)
    r_short = np.full(n, np.nan)
    out_long = np.full(n, None, dtype=object)
    out_short = np.full(n, None, dtype=object)
    highs, lows = d["high"].to_numpy(), d["low"].to_numpy()
    closes = d["close"].to_numpy()

    for i in range(n):
        t = times[i].time()
        if not (INTRADAY_ENTRY_START <= t <= INTRADAY_ENTRY_END):
            continue
        if locked.iloc[i] or not np.isfinite(a.iloc[i]) or a.iloc[i] <= 0:
            continue
        # same-day bars after entry, up to the 15:15 square-off
        j = i + 1
        idx = []
        while j < len(d) and dates[j] == dates[i] and times[j].time() <= INTRADAY_SQUAREOFF:
            idx.append(j)
            j += 1
        if not idx:
            continue
        e, av = closes[i], a.iloc[i]
        h, l, c = highs[idx], lows[idx], closes[idx]
        label_long[i], out_long[i], r_long[i] = _walk_barriers(
            h, l, c, e, e + tp_mult * av, e - sl_mult * av, True)
        label_short[i], out_short[i], r_short[i] = _walk_barriers(
            h, l, c, e, e - tp_mult * av, e + sl_mult * av, False)

    return pd.DataFrame({"label_long": label_long, "label_short": label_short,
                         "r_long": r_long, "r_short": r_short,
                         "outcome_long": out_long, "outcome_short": out_short,
                         "mask_locked": locked.to_numpy()}, index=df5.index)


def swing_labels(daily: pd.DataFrame, atr_period: int = 14, tp_mult: float = 2.0,
                 sl_mult: float = 1.25, horizon: int = 10) -> pd.DataFrame:
    """daily: one symbol's daily bars. Label on row i uses entry at open[i+1]
    (post-close signal, next-day entry) and walks 10 sessions of barriers."""
    d = daily.reset_index(drop=True)
    a = atr(d, atr_period)
    locked = locked_bar_mask(d)
    n = len(d)
    label = np.full(n, np.nan)
    r_mult = np.full(n, np.nan)
    outcome = np.full(n, None, dtype=object)
    highs, lows, opens = d["high"].to_numpy(), d["low"].to_numpy(), d["open"].to_numpy()
    closes = d["close"].to_numpy()

    for i in range(n - 1):
        if locked.iloc[i] or locked.iloc[i + 1]:
            continue  # signal-day or entry-day in circuit -> unexecutable
        if not np.isfinite(a.iloc[i]) or a.iloc[i] <= 0:
            continue
        entry = opens[i + 1]
        lo_w = lows[i + 1: i + 1 + horizon]
        hi_w = highs[i + 1: i + 1 + horizon]
        cl_w = closes[i + 1: i + 1 + horizon]
        if len(lo_w) == 0:
            continue
        label[i], outcome[i], r_mult[i] = _walk_barriers(
            hi_w, lo_w, cl_w, entry, entry + tp_mult * a.iloc[i],
            entry - sl_mult * a.iloc[i], True)

    return pd.DataFrame({"label_long": label, "r_long": r_mult,
                         "outcome_long": outcome,
                         "mask_locked": locked.to_numpy()}, index=daily.index)
