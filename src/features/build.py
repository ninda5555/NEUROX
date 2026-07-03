"""Feature pipeline orchestrator (CLAUDE.md §16 P1).

Builds per-symbol frames for the included universe, merges regime features,
writes wide monthly parquet (data/features/{mode}/{yyyy-mm}.parquet, one row
per symbol-bar), and produces the IC report over labeled, unmasked rows.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from src.data.store import CandleStore
from src.features import intraday as intraday_mod
from src.features import swing as swing_mod
from src.features.ic_filter import ICReport, compute_ic_report
from src.features.regime import NIFTY_SYMBOL, regime_feature_frame
from src.timeutil import IST, ist_date, now_ist

REGIME_COLS = ["regime_vix", "regime_vix_rising", "regime_nifty_above_50",
               "regime_nifty_above_200", "regime_breadth"]


def _write_monthly(root: Path, mode: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    df = df.copy()
    ym = pd.DatetimeIndex(df["ts"]).strftime("%Y-%m")
    written = 0
    for month, part in df.groupby(ym):
        mdir = root / mode
        mdir.mkdir(parents=True, exist_ok=True)
        path = mdir / f"{month}.parquet"
        if path.exists():
            old = pd.read_parquet(path)
            old["ts"] = pd.to_datetime(old["ts"]).dt.tz_convert(IST)
            part = pd.concat([old, part], ignore_index=True)
        part = (part.drop_duplicates(subset=["symbol", "ts"], keep="last")
                    .sort_values(["symbol", "ts"]))
        tmp = path.with_suffix(".tmp.parquet")
        part.to_parquet(tmp, index=False)
        tmp.replace(path)
        written += len(part)
    return written


def build_mode_features(mode: str, conn: sqlite3.Connection, store: CandleStore,
                        features_root: Path, symbols: list[str],
                        label_col: str = "label_long") -> tuple[pd.DataFrame, ICReport]:
    """Returns (labeled unmasked frame used for IC, IC report)."""
    regime = regime_feature_frame(conn)
    regime["regime_date"] = regime["regime_date"].astype(str)

    tf = "5min" if mode == "INTRADAY" else "1d"
    nifty = store.read_candles(tf, NIFTY_SYMBOL)
    nifty_5m_close = None
    nifty_daily = None
    if mode == "INTRADAY":
        if not nifty.empty:
            nifty_5m_close = nifty.set_index(pd.DatetimeIndex(nifty["ts"]))["close"]
        feature_cols = intraday_mod.FEATURE_COLS
    else:
        nifty_daily = nifty if not nifty.empty else None
        feature_cols = swing_mod.FEATURE_COLS

    frames = []
    skipped = 0
    for sym in symbols:
        candles = store.read_candles(tf, sym)
        if len(candles) < 60:
            skipped += 1
            continue
        if mode == "INTRADAY":
            f = intraday_mod.build_symbol_frame(candles, nifty_5m_close)
        else:
            f = swing_mod.build_symbol_frame(candles, nifty_daily)
        f["symbol"] = sym
        frames.append(f)

    if not frames:
        raise RuntimeError(f"no symbols had enough {tf} candles — backfill first")
    allf = pd.concat(frames, ignore_index=True)

    # merge regime features by IST date (regime is a feature, §6.4)
    allf["regime_date"] = pd.DatetimeIndex(allf["ts"]).strftime("%Y-%m-%d")
    allf = allf.merge(regime, on="regime_date", how="left").drop(columns=["regime_date"])
    all_feature_cols = feature_cols + REGIME_COLS

    keep_cols = (["symbol", "ts"] + all_feature_cols
                 + [c for c in ("label_long", "label_short", "mask_locked") if c in allf])
    out = allf[keep_cols]
    n_written = _write_monthly(features_root, mode, out)

    labeled = out[out[label_col].notna() & ~out["mask_locked"].astype(bool)]
    rep = compute_ic_report(labeled, all_feature_cols, label_col, mode)
    rep_path = features_root / f"ic_report_{mode}_{ist_date().isoformat()}.json"
    rep_path.parent.mkdir(parents=True, exist_ok=True)
    rep_path.write_text(json.dumps(rep.to_dict(), indent=2))

    print(f"[{mode}] {len(frames)} symbols ({skipped} skipped, <60 bars) | "
          f"{len(out):,} rows written ({n_written:,} incl. merges) | "
          f"{len(labeled):,} labeled+tradable rows | IC report -> {rep_path.name}")
    return labeled, rep
