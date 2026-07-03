"""Shared indicator library (CLAUDE.md §6.1 'Shared').

Pure functions over ascending OHLCV frames (columns ts/open/high/low/close/
volume, IST tz-aware ts). Each returns a Series aligned to the input index.
NaNs during warm-up are expected and preserved — the IC filter and model
handle missing natively.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(100.0).where(loss.notna(), np.nan)


def macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    line = ema_f - ema_s
    sig = line.ewm(span=signal, adjust=False).mean()
    return line - sig


def macd_slope(close: pd.Series, lookback: int = 3, **kw) -> pd.Series:
    h = macd_hist(close, **kw)
    return h.diff(lookback) / lookback


def volume_zscore(volume: pd.Series, window: int = 20) -> pd.Series:
    mean = volume.rolling(window).mean()
    std = volume.rolling(window).std()
    return (volume - mean) / std.replace(0, np.nan)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return atr(df, period) / df["close"] * 100


def garman_klass_vol(df: pd.DataFrame, window: int = 10) -> pd.Series:
    """Rolling Garman-Klass realized volatility (per-bar, annualization-free)."""
    log_hl = np.log(df["high"] / df["low"].replace(0, np.nan)) ** 2
    log_co = np.log(df["close"] / df["open"].replace(0, np.nan)) ** 2
    gk = 0.5 * log_hl - (2 * np.log(2) - 1) * log_co
    return np.sqrt(gk.rolling(window).mean().clip(lower=0))


def gap_pct(df: pd.DataFrame) -> pd.Series:
    return (df["open"] / df["close"].shift(1) - 1) * 100


def relative_strength(close: pd.Series, bench_close: pd.Series, window: int = 20) -> pd.Series:
    """Rolling return differential vs a benchmark (both indexed by ts/date)."""
    r_own = close.pct_change(window)
    r_bench = bench_close.reindex(close.index).ffill().pct_change(window)
    return (r_own - r_bench) * 100


def dist_from_ma(close: pd.Series, window: int) -> pd.Series:
    ma = close.rolling(window).mean()
    return (close / ma - 1) * 100


def dma_alignment(close: pd.Series) -> pd.Series:
    """Bullish stack score in [0,1]: close>20DMA, 20>50, 50>200 (each 1/3)."""
    d20 = close.rolling(20).mean()
    d50 = close.rolling(50).mean()
    d200 = close.rolling(200).mean()
    score = ((close > d20).astype(float) + (d20 > d50).astype(float)
             + (d50 > d200).astype(float)) / 3.0
    return score.where(d200.notna(), np.nan)


def dist_52w_high(close: pd.Series, window: int = 252) -> pd.Series:
    hi = close.rolling(window, min_periods=60).max()
    return (close / hi - 1) * 100


def weekly_rsi(daily: pd.DataFrame, period: int = 14) -> pd.Series:
    """RSI on weekly closes, forward-filled back onto daily rows."""
    w = (daily.set_index("ts")["close"].resample("W-FRI").last().dropna())
    wr = rsi(w, period)
    return wr.reindex(daily.set_index("ts").index, method="ffill").reset_index(drop=True)


def time_encodings(ts: pd.Series) -> pd.DataFrame:
    t = pd.DatetimeIndex(ts)
    minutes = t.hour * 60 + t.minute
    return pd.DataFrame({
        "dow": t.dayofweek.astype(float),
        "tod_frac": ((minutes - 555) / 375.0).astype(float),  # 09:15→0, 15:30→1
    }, index=ts.index)
