"""Read-only diagnostic for the 2026-07-29 "constant 0.534 for every symbol"
report. Prints, and changes nothing:

  1. The active INTRADAY model's trained feature_list vs. the feature names
     the CURRENTLY RUNNING code actually produces at scan time — a set diff
     catches a training/serving name mismatch directly.
  2. market_regime freshness — no scheduled job calls compute_regime/
     store_regime (confirmed by code read), so this table may be far
     staler than "today" on an unattended VPS.
  3. A sample of today's (or the latest available) score log
     (data/scores/INTRADAY/*.parquet, written by src.journal.scorelog
     whenever observability.score_log is on, the default) — this is the
     single most direct piece of evidence: raw pre-calibration scores,
     calibrated confidence, regime bucket, and the actual feature values
     used, per symbol, per pass.
  4. The training-time IC report for the active model, in full (the
     dashboard only shows the top 8) — near-zero IC across the board would
     mean the model had little to learn from even at training time.

    python -m src.scripts.diag_intraday_scores
    python -m src.scripts.diag_intraday_scores --date 2026-07-29
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src import db as dbm
from src.config import load_config
from src.features import intraday as intraday_mod
from src.features.build import REGIME_COLS
from src.models.registry import load_active
from src.timeutil import ist_date


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD score-log date (default: today, "
                                   "falling back to the most recent file present)")
    args = ap.parse_args(argv)

    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db"))
    dbm.init_db(conn)

    print("=" * 78)
    print("1. FEATURE NAME MATCH — trained feature_list vs. current live code")
    print("=" * 78)
    try:
        model_id, booster, cal, feats = load_active(conn, "INTRADAY")
        print(f"active model: {model_id}")
        print(f"trained feature_list ({len(feats)}): {feats}")
        live_names = set(intraday_mod.FEATURE_COLS) | set(REGIME_COLS) | {"direction"}
        missing_from_live = sorted(set(feats) - live_names)
        extra_in_live = sorted(live_names - set(feats))
        if missing_from_live:
            print(f"\n  *** {len(missing_from_live)} trained feature(s) NOT produced "
                  f"by the current live code — these silently become NaN on every "
                  f"scoring pass: {missing_from_live}")
        else:
            print("\n  OK: every trained feature name is produced by current live code.")
        print(f"  (live-only, not requested by this model, harmless: {extra_in_live})")
        is_meta = hasattr(booster, "meta")
        print(f"  meta-labeling wrapper active on this model: {is_meta}")
    except Exception as e:
        print(f"  COULD NOT LOAD ACTIVE MODEL: {e!r}")
        model_id = None

    print()
    print("=" * 78)
    print("2. market_regime freshness (no scheduled job refreshes this table)")
    print("=" * 78)
    rows = conn.execute(
        "SELECT * FROM market_regime ORDER BY regime_date DESC LIMIT 3").fetchall()
    if not rows:
        print("  *** market_regime is EMPTY. Every regime_* feature is NaN, always.")
    else:
        latest = rows[0]["regime_date"]
        gap = (ist_date().isoformat() > latest)
        print(f"  most recent regime_date: {latest} "
              f"({'STALE vs today ' + ist_date().isoformat() if gap else 'current'})")
        for r in rows:
            print(f"    {dict(r)}")

    print()
    print("=" * 78)
    print("3. Today's live score log (the direct evidence)")
    print("=" * 78)
    scores_dir = cfg.path("paths.scores") / "INTRADAY"
    date_str = args.date or ist_date().isoformat()
    path = scores_dir / f"{date_str}.parquet"
    if not path.exists():
        avail = sorted(scores_dir.glob("*.parquet")) if scores_dir.exists() else []
        if not avail:
            print(f"  no score log files found under {scores_dir} — "
                  f"observability.score_log may be off, or no pass has run yet")
            return 1
        path = avail[-1]
        print(f"  {date_str}.parquet not found, using most recent available: {path.name}")
    df = pd.read_parquet(path)
    print(f"  {len(df)} scored rows in {path.name}")
    show_cols = ["ts", "symbol", "direction", "score_raw", "confidence", "bucket"]
    feat_sample_cols = [c for c in ("rsi_14", "macd_hist", "vwap_dist_atr", "gap_pct",
                                    "orb_position", "regime_vix", "regime_breadth")
                        if c in df.columns]
    print(f"\n  score_raw  : nunique={df['score_raw'].nunique()}, "
          f"min={df['score_raw'].min():.6f}, max={df['score_raw'].max():.6f}")
    print(f"  confidence : nunique={df['confidence'].nunique()}, "
          f"min={df['confidence'].min():.6f}, max={df['confidence'].max():.6f}")
    for c in feat_sample_cols:
        nun = df[c].nunique(dropna=False)
        nnull = df[c].isna().sum()
        print(f"  {c:<16}: nunique={nun}, n_null={nnull}/{len(df)}, "
              f"sample={df[c].head(3).tolist()}")
    print("\n  first 8 rows (subset of columns):")
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(df[show_cols + feat_sample_cols].head(8).to_string(index=False))

    print()
    print("=" * 78)
    print("4. Full training-time IC report (dashboard only shows top 8)")
    print("=" * 78)
    ic_files = sorted(Path(cfg.path("paths.features")).glob("ic_report_INTRADAY_*.json"))
    if not ic_files:
        print("  no IC report file found")
    else:
        rep = json.loads(ic_files[-1].read_text())
        print(f"  {ic_files[-1].name}")
        ic = rep.get("ic", {})
        for k, v in sorted(ic.items(), key=lambda kv: -abs(kv[1] or 0)):
            kept_mark = "KEPT" if k in (feats or []) else "dropped"
            print(f"    {k:<24} IC={v!s:<10} {kept_mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
