"""Intraday (5-min) feature pipeline (CLAUDE.md §6.1 intraday-only + shared).

One row per symbol-bar within the entry window. OFI is nullable: historical
depth doesn't exist, so backtests carry NaN and the live scanner fills it for
the top-N shortlist only (LightGBM handles missing natively).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.features import shared
from src.features.labels import intraday_labels

ORB_MINUTES = 15  # opening range = first 15 minutes (09:15-09:30)


def _session_groups(d: pd.DataFrame):
    return d.groupby(pd.DatetimeIndex(d["ts"]).date, sort=False, group_keys=False)


def build_symbol_frame(df5: pd.DataFrame, nifty_5min_close: pd.Series | None = None,
                       min_barrier_pct: float | None = None) -> pd.DataFrame:
    """df5: ascending 5-min bars for one symbol (IST). Returns the wide
    feature frame including labels and masks."""
    d = df5.reset_index(drop=True).copy()
    t = pd.DatetimeIndex(d["ts"])

    # --- shared ---
    d["rsi_14"] = shared.rsi(d["close"])
    d["macd_hist"] = shared.macd_hist(d["close"])
    d["macd_slope"] = shared.macd_slope(d["close"])
    d["vol_zscore"] = shared.volume_zscore(d["volume"])
    d["gk_vol"] = shared.garman_klass_vol(d)
    d["atr_pct"] = shared.atr_pct(d, 5)
    enc = shared.time_encodings(d["ts"])
    d["dow"], d["tod_frac"] = enc["dow"], enc["tod_frac"]

    # gap % vs previous session close
    day = np.asarray(t.date)
    sess_open = d.groupby(day)["open"].transform("first")
    last_close_by_day = d.groupby(day)["close"].last()
    prev_map = dict(zip(last_close_by_day.index, last_close_by_day.shift(1)))
    prev_day_close = pd.Series([prev_map.get(x, np.nan) for x in day], index=d.index)
    d["gap_pct"] = (sess_open / prev_day_close - 1) * 100

    # --- intraday-only ---
    atr5 = shared.atr(d, 5)

    # session VWAP
    pv = d["close"] * d["volume"]
    cum_pv = pv.groupby(day).cumsum()
    cum_v = d.groupby(day)["volume"].cumsum()
    vwap = cum_pv / cum_v.replace(0, np.nan)
    d["vwap_dist_atr"] = (d["close"] - vwap) / atr5.replace(0, np.nan)

    # opening-range position (first 15 min high/low), clipped to [-0.5, 1.5]
    minutes = pd.Series(t.hour * 60 + t.minute, index=d.index)
    in_or = minutes < (9 * 60 + 15 + ORB_MINUTES)
    or_hi = d["high"].where(in_or).groupby(day).cummax()
    or_hi = or_hi.groupby(day).ffill()
    or_lo = d["low"].where(in_or).groupby(day).cummin()
    or_lo = or_lo.groupby(day).ffill()
    rng = (or_hi - or_lo).replace(0, np.nan)
    d["orb_position"] = ((d["close"] - or_lo) / rng).clip(-0.5, 1.5)

    # 15-min trend agreement: sign of 3-bar (15-min) slope vs 1-bar move
    slope15 = d["close"].diff(3)
    d["trend_agree_15m"] = (np.sign(slope15) == np.sign(d["close"].diff(1))).astype(float)
    d.loc[slope15.isna(), "trend_agree_15m"] = np.nan

    # cumulative session volume vs same-time-of-day average (prior sessions)
    tmp = pd.DataFrame({"cumv": cum_v, "minute": minutes})
    avg_prior = (tmp.groupby("minute", group_keys=False)["cumv"]
                 .apply(lambda s: s.expanding().mean().shift(1)))
    d["cum_vol_ratio"] = tmp["cumv"] / avg_prior.replace(0, np.nan)

    # relative strength vs NIFTY (20-bar return differential), if provided
    if nifty_5min_close is not None:
        bench = nifty_5min_close.reindex(pd.DatetimeIndex(d["ts"])).ffill()
        d["rs_nifty"] = (d["close"].pct_change(20).to_numpy()
                         - pd.Series(bench).pct_change(20).to_numpy()) * 100
    else:
        d["rs_nifty"] = np.nan

    d["ofi_top"] = np.nan  # nullable: live-only feature (5-level depth, top-N)

    from src.features.labels import MIN_BARRIER_ATR_PCT
    lab = intraday_labels(df5, min_barrier_pct=(min_barrier_pct
                          if min_barrier_pct is not None else MIN_BARRIER_ATR_PCT))
    for c in lab.columns:
        d[c] = lab[c].to_numpy()
    return d


FEATURE_COLS = ["rsi_14", "macd_hist", "macd_slope", "vol_zscore", "gk_vol",
                "atr_pct", "dow", "tod_frac", "gap_pct", "vwap_dist_atr",
                "orb_position", "trend_agree_15m", "cum_vol_ratio", "rs_nifty",
                "ofi_top"]
