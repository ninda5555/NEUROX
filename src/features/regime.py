"""Daily market regime (CLAUDE.md §6.4) -> market_regime table.

Inputs: India VIX + NIFTY50 daily candles, advance/decline breadth computed
from the included universe's daily candles. Regime is a feature, a journal
field, and a UI banner.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from src.data.store import CandleStore

VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
NIFTY_SYMBOL = "NSE:NIFTY50-INDEX"


def _vix_trend(vix: pd.Series, window: int = 20, flat_band: float = 0.02) -> pd.Series:
    ratio = vix / vix.shift(window) - 1
    out = pd.Series("flat", index=vix.index)
    out[ratio > flat_band] = "rising"
    out[ratio < -flat_band] = "falling"
    out[ratio.isna()] = "flat"
    return out


def _label(row) -> str:
    if not np.isfinite(row["vix"]):
        return "unknown"
    if row["vix"] >= 20:
        return "high_vol"
    above = row["nifty_vs_50dma"] > 0 and row["nifty_vs_200dma"] > 0
    below = row["nifty_vs_50dma"] < 0 and row["nifty_vs_200dma"] < 0
    if row["vix"] < 15 and above:
        return "calm_uptrend"
    if below:
        return "downtrend"
    return "mixed"


def compute_regime(store: CandleStore, included_symbols: list[str]) -> pd.DataFrame:
    vix = store.read_candles("1d", VIX_SYMBOL)
    nifty = store.read_candles("1d", NIFTY_SYMBOL)
    if vix.empty or nifty.empty:
        raise RuntimeError("index candles missing — backfill NIFTY50/INDIAVIX first")

    n = nifty.set_index(pd.DatetimeIndex(nifty["ts"]).date)
    v = vix.set_index(pd.DatetimeIndex(vix["ts"]).date)
    out = pd.DataFrame(index=n.index)
    out["vix"] = v["close"].reindex(out.index)
    out["vix_trend"] = _vix_trend(out["vix"])
    d50 = n["close"].rolling(50).mean()
    d200 = n["close"].rolling(200).mean()
    out["nifty_vs_50dma"] = (n["close"] / d50 - 1) * 100
    out["nifty_vs_200dma"] = (n["close"] / d200 - 1) * 100

    # breadth: fraction of included universe advancing minus declining
    adv = pd.Series(0.0, index=out.index)
    tot = pd.Series(0.0, index=out.index)
    for sym in included_symbols:
        df = store.read_candles("1d", sym)
        if df.empty:
            continue
        dates = pd.DatetimeIndex(df["ts"]).date
        chg = np.sign(df["close"].diff()).to_numpy()
        s = pd.Series(chg, index=dates).reindex(out.index)
        adv = adv.add(s.fillna(0), fill_value=0)
        tot = tot.add(s.notna().astype(float), fill_value=0)
    out["breadth_adv_dec"] = (adv / tot.replace(0, np.nan)).astype(float)

    out["label"] = out.apply(_label, axis=1)
    out.index.name = "regime_date"
    return out.reset_index()


def store_regime(conn: sqlite3.Connection, regime: pd.DataFrame) -> int:
    rows = [(str(r.regime_date), None if pd.isna(r.vix) else float(r.vix),
             r.vix_trend,
             None if pd.isna(r.nifty_vs_50dma) else float(r.nifty_vs_50dma),
             None if pd.isna(r.nifty_vs_200dma) else float(r.nifty_vs_200dma),
             None if pd.isna(r.breadth_adv_dec) else float(r.breadth_adv_dec),
             r.label)
            for r in regime.itertuples()]
    conn.executemany(
        """INSERT INTO market_regime
           (regime_date, vix, vix_trend, nifty_vs_50dma, nifty_vs_200dma,
            breadth_adv_dec, label) VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(regime_date) DO UPDATE SET
             vix=excluded.vix, vix_trend=excluded.vix_trend,
             nifty_vs_50dma=excluded.nifty_vs_50dma,
             nifty_vs_200dma=excluded.nifty_vs_200dma,
             breadth_adv_dec=excluded.breadth_adv_dec, label=excluded.label""",
        rows)
    conn.commit()
    return len(rows)


def regime_feature_frame(conn: sqlite3.Connection) -> pd.DataFrame:
    """market_regime as numeric features keyed by date, for merging into
    both mode pipelines (§6.4: regime is a feature)."""
    df = pd.read_sql_query("SELECT * FROM market_regime", conn)
    df["regime_vix"] = df["vix"]
    df["regime_vix_rising"] = (df["vix_trend"] == "rising").astype(float)
    df["regime_nifty_above_50"] = (df["nifty_vs_50dma"] > 0).astype(float)
    df["regime_nifty_above_200"] = (df["nifty_vs_200dma"] > 0).astype(float)
    df["regime_breadth"] = df["breadth_adv_dec"]
    return df[["regime_date", "regime_vix", "regime_vix_rising",
               "regime_nifty_above_50", "regime_nifty_above_200",
               "regime_breadth"]]
