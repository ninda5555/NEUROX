"""Swing (daily) feature pipeline (CLAUDE.md §6.1 swing-only + shared).

rs_sector is nullable until a sector source is wired (instruments.sector has
no feed yet — flagged at P0); LightGBM handles missing natively.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.features import shared
from src.features.labels import swing_labels


def build_symbol_frame(daily: pd.DataFrame, nifty_daily: pd.DataFrame | None = None,
                       sector_ret20: pd.Series | None = None) -> pd.DataFrame:
    d = daily.reset_index(drop=True).copy()

    # --- shared ---
    d["rsi_14"] = shared.rsi(d["close"])
    d["macd_hist"] = shared.macd_hist(d["close"])
    d["macd_slope"] = shared.macd_slope(d["close"])
    d["vol_zscore"] = shared.volume_zscore(d["volume"])
    d["gk_vol"] = shared.garman_klass_vol(d)
    d["atr14_pct"] = shared.atr_pct(d, 14)
    d["gap_pct"] = shared.gap_pct(d)
    enc = shared.time_encodings(d["ts"])
    d["dow"] = enc["dow"]

    # --- swing-only ---
    d["dist_20dma"] = shared.dist_from_ma(d["close"], 20)
    d["dist_50dma"] = shared.dist_from_ma(d["close"], 50)
    d["dist_200dma"] = shared.dist_from_ma(d["close"], 200)
    d["dma_alignment"] = shared.dma_alignment(d["close"])
    d["dist_52w_high"] = shared.dist_52w_high(d["close"])
    d["weekly_rsi"] = shared.weekly_rsi(d)
    gk10 = shared.garman_klass_vol(d, 10)
    gk60 = shared.garman_klass_vol(d, 60)
    d["gk_vol_trend"] = (gk10 / gk60.replace(0, np.nan)) - 1

    if nifty_daily is not None:
        bench = nifty_daily.set_index(pd.DatetimeIndex(nifty_daily["ts"]).date)["close"]
        own_dates = pd.DatetimeIndex(d["ts"]).date
        aligned = pd.Series([bench.get(x, np.nan) for x in own_dates], index=d.index).ffill()
        d["rs_nifty_20d"] = (d["close"].pct_change(20) - aligned.pct_change(20)) * 100
    else:
        d["rs_nifty_20d"] = np.nan

    if sector_ret20 is not None:
        own_dates = pd.DatetimeIndex(d["ts"]).date
        sec = pd.Series([sector_ret20.get(x, np.nan) for x in own_dates],
                        index=d.index)
        d["rs_sector"] = (d["close"].pct_change(20) - sec) * 100
    else:
        d["rs_sector"] = np.nan  # sector unknown for this symbol

    lab = swing_labels(daily)
    for c in lab.columns:
        d[c] = lab[c].to_numpy()
    return d


FEATURE_COLS = ["rsi_14", "macd_hist", "macd_slope", "vol_zscore", "gk_vol",
                "atr14_pct", "gap_pct", "dow", "dist_20dma", "dist_50dma",
                "dist_200dma", "dma_alignment", "dist_52w_high", "weekly_rsi",
                "gk_vol_trend", "rs_nifty_20d", "rs_sector"]
