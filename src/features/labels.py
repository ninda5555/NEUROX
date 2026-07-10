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


def _walk_barriers(highs: np.ndarray, lows: np.ndarray, tp: float, sl: float,
                   long_side: bool) -> int:
    """1 if TP touched before SL over the window, else 0."""
    for h, l in zip(highs, lows):
        if long_side:
            if l <= sl:
                return 0
            if h >= tp:
                return 1
        else:
            if h >= sl:
                return 0
            if l <= tp:
                return 1
    return 0


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

    label_long = np.full(len(d), np.nan)
    label_short = np.full(len(d), np.nan)
    highs, lows = d["high"].to_numpy(), d["low"].to_numpy()
    closes = d["close"].to_numpy()

    for i in range(len(d)):
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
        h, l = highs[idx], lows[idx]
        label_long[i] = _walk_barriers(h, l, e + tp_mult * av, e - sl_mult * av, True)
        label_short[i] = _walk_barriers(h, l, e - tp_mult * av, e + sl_mult * av, False)

    return pd.DataFrame({"label_long": label_long, "label_short": label_short,
                         "mask_locked": locked.to_numpy()}, index=df5.index)


def swing_labels(daily: pd.DataFrame, atr_period: int = 14, tp_mult: float = 2.0,
                 sl_mult: float = 1.25, horizon: int = 10) -> pd.DataFrame:
    """daily: one symbol's daily bars. Label on row i uses entry at open[i+1]
    (post-close signal, next-day entry) and walks 10 sessions of barriers."""
    d = daily.reset_index(drop=True)
    a = atr(d, atr_period)
    locked = locked_bar_mask(d)
    label = np.full(len(d), np.nan)
    highs, lows, opens = d["high"].to_numpy(), d["low"].to_numpy(), d["open"].to_numpy()

    for i in range(len(d) - 1):
        if locked.iloc[i] or locked.iloc[i + 1]:
            continue  # signal-day or entry-day in circuit -> unexecutable
        if not np.isfinite(a.iloc[i]) or a.iloc[i] <= 0:
            continue
        entry = opens[i + 1]
        lo_w = lows[i + 1: i + 1 + horizon]
        hi_w = highs[i + 1: i + 1 + horizon]
        if len(lo_w) == 0:
            continue
        label[i] = _walk_barriers(hi_w, lo_w, entry + tp_mult * a.iloc[i],
                                  entry - sl_mult * a.iloc[i], True)

    return pd.DataFrame({"label_long": label, "mask_locked": locked.to_numpy()},
                        index=daily.index)
