"""Re-run feature selection under the CORRECTED cross-sectional IC, against
the feature parquets already on disk — no rebuild, no training, no Fyers
calls. Read-only; answers "what actually survives?" in minutes.

    python -m src.scripts.ic_recheck                 # both modes
    python -m src.scripts.ic_recheck --mode SWING
    python -m src.scripts.ic_recheck --months 6      # cap history scanned

Prints, per mode, every candidate feature with:
    xs IC   cross-sectional IC (within-bar Spearman, averaged) — SELECTS
    t       t-stat of that IC time series
    bars    how many bars it was measurable on
    pooled  the OLD conflated measure, for contrast only

and then the surviving list. A near-empty survivor list is a real and
useful result — see CLAUDE.md §17.2. Do not tune thresholds until something
passes; report the negative.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from src.config import load_config
from src.features import intraday as intraday_mod
from src.features import swing as swing_mod
from src.features import xsection as xs
from src.features.build import REGIME_COLS
from src.features.ic_filter import compute_ic_report, format_report
from src.timeutil import IST


def load_mode_frame(features_root, mode: str, months: int | None) -> pd.DataFrame:
    files = sorted((features_root / mode).glob("*.parquet"))
    if not files:
        raise SystemExit(f"no feature files for {mode} under {features_root/mode} "
                         "— run build_features first")
    if months:
        files = files[-months:]
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["INTRADAY", "SWING", "both"], default="both")
    ap.add_argument("--months", type=int, help="only the most recent N monthly files")
    args = ap.parse_args(argv)
    cfg = load_config()

    for mode in (["INTRADAY", "SWING"] if args.mode == "both" else [args.mode]):
        print("=" * 78)
        print(f"{mode}: feature selection under CROSS-SECTIONAL IC")
        print("=" * 78)
        df = load_mode_frame(cfg.path("paths.features"), mode, args.months)
        df = df[~df["mask_locked"].astype(bool)]
        label_col = "label_long"
        d = df[df[label_col].notna()]
        if d.empty:
            print("  no labeled rows — nothing to measure")
            continue

        base = (intraday_mod.FEATURE_COLS if mode == "INTRADAY"
                else swing_mod.FEATURE_COLS) + REGIME_COLS
        model_cols = xs.model_feature_cols(base)
        excluded = [c for c in base if c not in model_cols]

        print(f"  {len(d):,} labeled rows | {d['ts'].nunique():,} bars | "
              f"{d['symbol'].nunique():,} symbols")
        print(f"  excluded up-front as per-bar constants: {excluded}")
        print()

        ranked = xs.cross_sectional_rank(d, model_cols)
        rep = compute_ic_report(ranked, model_cols, label_col, mode)
        print(format_report(rep))
        print()
        print(f"  SURVIVORS ({len(rep.kept)} of {len(model_cols)}): {rep.kept or '— none —'}")

        if mode == "INTRADAY":
            # long/short base rates: the reason every symbol scored SHORT
            for c in ("label_long", "label_short"):
                s = df[c].dropna()
                if len(s):
                    print(f"  base rate {c}: {s.mean():.4f}  (n={len(s):,})")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
