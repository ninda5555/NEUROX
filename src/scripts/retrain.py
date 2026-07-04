"""P2 CLI: train + calibrate + validate + register a model per mode.

    python -m src.scripts.retrain --mode both
    python -m src.scripts.retrain --mode SWING --no-activate
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from src import db as dbm
from src.config import load_config
from src.features import intraday as intraday_mod
from src.features import swing as swing_mod
from src.features.build import REGIME_COLS
from src.models import cv as cvmod
from src.models.calibrate import (RegimeCalibrator, calibration_curve_points,
                                  fit_isotonic)
from src.models.train import LGBM_PARAMS, train_fn_for_cv, train_lgbm
from src.models.registry import save_model
from src.timeutil import IST


def load_mode_frame(features_root: Path, mode: str) -> pd.DataFrame:
    files = sorted((features_root / mode).glob("*.parquet"))
    if not files:
        raise SystemExit(f"no feature files for {mode} — run build_features first")
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(IST)
    df = df[~df["mask_locked"].astype(bool)]
    for c in df.columns:
        if df[c].dtype == np.float64:
            df[c] = df[c].astype(np.float32)
    return df


def prepare(mode: str, df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], str]:
    if mode == "INTRADAY":
        base = intraday_mod.FEATURE_COLS + REGIME_COLS
        longs = df[df["label_long"].notna()].copy()
        longs["direction"], longs["label"] = 1.0, longs["label_long"]
        shorts = df[df["label_short"].notna()].copy()
        shorts["direction"], shorts["label"] = -1.0, shorts["label_short"]
        out = pd.concat([longs, shorts], ignore_index=True)
        # Lopez de Prado sample uniqueness: overlapping same-day labels per
        # symbol are serially dependent; weight each row by 1/N(symbol, day)
        day = pd.DatetimeIndex(out["ts"]).strftime("%Y-%m-%d")
        out["_w"] = 1.0 / out.groupby([out["symbol"], day])["label"].transform("size")
        return out, base + ["direction"], "label"
    base = swing_mod.FEATURE_COLS + REGIME_COLS
    out = df[df["label_long"].notna()].copy()
    out["label"] = out["label_long"]
    return out, base, "label"


def fmt(v, spec=".2f"):
    return "  nan" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:{spec}}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Train, validate, register models")
    ap.add_argument("--mode", choices=["INTRADAY", "SWING", "both"], default="both")
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--no-activate", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db"))
    dbm.init_db(conn)
    threshold = cfg["signals.confidence_threshold"]

    for mode in (["INTRADAY", "SWING"] if args.mode == "both" else [args.mode]):
        print(f"\n=== {mode}: loading features ===")
        df = load_mode_frame(cfg.path("paths.features"), mode)
        frame, feature_cols, label_col = prepare(mode, df)
        print(f"{len(frame):,} labeled tradable rows | positive rate "
              f"{frame[label_col].mean():.3f}")

        print(f"purged walk-forward CV ({args.folds} folds, threshold {threshold}) …")
        results, flags = cvmod.run_cv(frame, mode, feature_cols, label_col,
                                      train_fn_for_cv, n_folds=args.folds,
                                      threshold=threshold)

        print(f"\n{'fold':<5}{'test window':<26}{'train n':>9}{'signals':>8}"
              f"{'precision':>10}{'calib err':>10}{'avg R':>7}{'max DD%':>8}")
        for r in results:
            print(f"{r.fold:<5}{r.test_start + '..' + r.test_end:<26}"
                  f"{r.n_train:>9,}{r.n_signals:>8,}"
                  f"{fmt(r.precision_at_thr):>10}{fmt(r.calibration_err, '.3f'):>10}"
                  f"{fmt(r.avg_r_multiple):>7}{fmt(r.max_drawdown_pct):>8}")
        precs = [r.precision_at_thr for r in results if not np.isnan(r.precision_at_thr)]
        print(f"precision mean ± std: {np.mean(precs):.3f} ± {np.std(precs):.3f} "
              "(spread shown, never averaged away)")
        if flags:
            print("\nRED FLAGS (displayed, never suppressed):")
            for fl in flags:
                print(f"  ⚠ {fl['rule']}: {fl['detail']}")
        else:
            print("no red flags raised across folds")

        # final model: train on everything with a validation tail; isotonic on
        # the CV's out-of-fold test predictions
        print("\ntraining final model …")
        kept = cvmod.select_features(frame, feature_cols, label_col, mode)
        dates = np.array(sorted(pd.DatetimeIndex(frame["ts"]).date))
        val_from = dates[int(len(dates) * 0.9)]
        dcol = pd.DatetimeIndex(frame["ts"]).date
        tr, va = frame[dcol < val_from], frame[dcol >= val_from]
        booster, _ = train_lgbm(tr, tr[label_col], va, va[label_col], kept)

        oof_pred = np.concatenate([r.test_pred_raw for r in results])
        oof_y = np.concatenate([r.test_label for r in results])
        oof_b = np.concatenate([r.test_bucket for r in results])
        iso = RegimeCalibrator().fit(oof_pred, oof_y, oof_b)
        curve = calibration_curve_points(iso.transform(oof_pred), oof_y)
        per_bucket = {b: [round(float(iso.transform([oof_pred.max()], b)[0]), 3)]
                      for b in iso.buckets}
        print(f"regime calibration buckets: {list(iso.buckets)} | "
              f"honest ceiling per bucket at max raw score: {per_bucket}")

        cv_report = {"threshold": threshold,
                     "precision_mean": float(np.mean(precs)) if precs else None,
                     "precision_std": float(np.std(precs)) if precs else None,
                     "folds": [{k: getattr(r, k) for k in
                                ("fold", "train_start", "train_end", "test_start",
                                 "test_end", "n_train", "n_signals",
                                 "precision_at_thr", "calibration_err",
                                 "avg_r_multiple", "max_drawdown_pct")}
                               for r in results],
                     "ic_kept": kept}
        model_id = save_model(
            conn, cfg.path("paths.models"), mode=mode, booster=booster,
            calibrator=iso, feature_list=kept, lgbm_params=LGBM_PARAMS,
            calibration_curve=curve, cv_report=cv_report, red_flags=flags,
            train_start=str(dates[0]), train_end=str(dates[-1]),
            activate=not args.no_activate)
        print(f"registered {model_id} (active={not args.no_activate}) | "
              f"{len(kept)} features | calibration curve: {curve}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
