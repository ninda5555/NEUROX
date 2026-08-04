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
from src.features import xsection as xs
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
                        label_col: str = "label_long",
                        min_barrier_pct: float | None = None) -> tuple[pd.DataFrame, ICReport]:
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

    sector_of = {r["symbol"]: r["sector"] for r in conn.execute(
        "SELECT symbol, sector FROM instruments WHERE sector IS NOT NULL")}
    cached: dict[str, pd.DataFrame] = {}
    sector_ret: dict[str, pd.Series] = {}
    if mode == "SWING":
        # pass 1: equal-weight 20-session return per sector by date
        per_sector: dict[str, list[pd.Series]] = {}
        for sym in symbols:
            candles = store.read_candles(tf, sym)
            cached[sym] = candles
            sec = sector_of.get(sym)
            if sec and len(candles) >= 60:
                r20 = candles["close"].pct_change(20)
                r20.index = pd.DatetimeIndex(candles["ts"]).date
                per_sector.setdefault(sec, []).append(r20)
        for sec, series in per_sector.items():
            if len(series) >= 3:  # need real peers, not a self-comparison
                sector_ret[sec] = pd.concat(series, axis=1).mean(axis=1)

    frames = []
    skipped = 0
    for sym in symbols:
        candles = cached.get(sym)
        if candles is None:
            candles = store.read_candles(tf, sym)
        if len(candles) < 60:
            skipped += 1
            continue
        if mode == "INTRADAY":
            f = intraday_mod.build_symbol_frame(candles, nifty_5m_close,
                                                min_barrier_pct=min_barrier_pct)
        else:
            sec = sector_of.get(sym)
            f = swing_mod.build_symbol_frame(candles, nifty_daily,
                                             sector_ret.get(sec))
        f["symbol"] = sym
        frames.append(f)

    if not frames:
        raise RuntimeError(f"no symbols had enough {tf} candles — backfill first")
    allf = pd.concat(frames, ignore_index=True)

    # merge regime by IST date. Regime stays in the STORED frame (the
    # calibrator bucket and the journal read it) but no longer enters the
    # model's feature matrix — see features/xsection.py.
    allf["regime_date"] = pd.DatetimeIndex(allf["ts"]).strftime("%Y-%m-%d")
    allf = allf.merge(regime, on="regime_date", how="left").drop(columns=["regime_date"])
    all_feature_cols = feature_cols + REGIME_COLS

    keep_cols = (["symbol", "ts"] + all_feature_cols
                 + [c for c in ("label_long", "label_short", "mask_locked") if c in allf])
    out = allf[keep_cols]
    n_written = _write_monthly(features_root, mode, out)

    labeled = out[out[label_col].notna() & ~out["mask_locked"].astype(bool)]

    # Cross-sectional rank WITHIN each bar, then IC on the ranked panel —
    # measuring the only thing a scanner can act on (§ xsection). Regime and
    # other per-bar constants are excluded from the candidate list entirely,
    # so they can neither be selected nor rank to a misleading flat 0.
    model_cols = xs.model_feature_cols(all_feature_cols)
    ranked = xs.cross_sectional_rank(labeled, model_cols)
    rep = compute_ic_report(ranked, model_cols, label_col, mode)
    rep_path = features_root / f"ic_report_{mode}_{ist_date().isoformat()}.json"
    rep_path.parent.mkdir(parents=True, exist_ok=True)
    rep_path.write_text(json.dumps(rep.to_dict(), indent=2))

    print(f"[{mode}] {len(frames)} symbols ({skipped} skipped, <60 bars) | "
          f"{len(out):,} rows written ({n_written:,} incl. merges) | "
          f"{len(labeled):,} labeled+tradable rows | IC report -> {rep_path.name}")
    return labeled, rep
