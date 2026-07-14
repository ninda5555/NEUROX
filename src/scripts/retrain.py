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
from src.models.ensemble import train_ensemble
from src.models.train import LGBM_PARAMS, train_fn_for_cv
from src.models.registry import promote_if_better, save_model
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


SWING_HORIZON_SESSIONS = 10  # §5: swing labels walk 10 sessions of barriers


def _apply_time_decay(out: pd.DataFrame, half_life_days: float) -> None:
    """T9 (off by default): halve a sample's weight every half_life_days of
    age so the model leans toward current market behaviour. Multiplies into
    _w (creating it at 1.0) so it composes with uniqueness weights."""
    ts = pd.DatetimeIndex(out["ts"])
    age_days = (ts.max() - ts).days.astype(float)
    decay = np.power(0.5, age_days / half_life_days)
    out["_w"] = out.get("_w", pd.Series(1.0, index=out.index)) * decay


def _swing_uniqueness(out: pd.DataFrame) -> pd.Series:
    """T9 (off by default): overlapping swing labels are serially dependent —
    a 10-session barrier walk starting today shares most of its path with
    one starting tomorrow. Weight each row by 1/(number of same-symbol rows
    whose label windows overlap it). Positional approximation: labeled daily
    rows are ~one per session, so overlap count for row i in a symbol group
    of n rows is min(i,h) + min(n-1-i,h) + 1 with h = the label horizon."""
    h = SWING_HORIZON_SESSIONS

    def per_symbol(g: pd.Series) -> pd.Series:
        n = len(g)
        i = np.arange(n, dtype=float)
        concurrent = np.minimum(i, h) + np.minimum(n - 1 - i, h) + 1
        return pd.Series(1.0 / concurrent, index=g.index)

    ordered = out.sort_values("ts")
    w = ordered.groupby("symbol", sort=False)["label"].transform(
        lambda g: per_symbol(g))
    return w.reindex(out.index)


def prepare(mode: str, df: pd.DataFrame, *, half_life_days: float = 0.0,
            swing_uniqueness: bool = False) -> tuple[pd.DataFrame, list[str], str]:
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
        if half_life_days > 0:
            _apply_time_decay(out, half_life_days)
        return out, base + ["direction"], "label"
    base = swing_mod.FEATURE_COLS + REGIME_COLS
    out = df[df["label_long"].notna()].copy()
    out["label"] = out["label_long"]
    if swing_uniqueness:
        out["_w"] = _swing_uniqueness(out)
    if half_life_days > 0:
        _apply_time_decay(out, half_life_days)
    return out, base, "label"


def fmt(v, spec=".2f"):
    return "  nan" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:{spec}}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Train, validate, register models")
    ap.add_argument("--mode", choices=["INTRADAY", "SWING", "both"], default="both")
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--no-activate", action="store_true",
                    help="register only; skip the promotion gate entirely")
    ap.add_argument("--force-activate", action="store_true",
                    help="activate even if the promotion gate says hold "
                         "(the override is recorded on the model row)")
    args = ap.parse_args(argv)

    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db"))
    dbm.init_db(conn)
    threshold = cfg["signals.confidence_threshold"]

    for mode in (["INTRADAY", "SWING"] if args.mode == "both" else [args.mode]):
        print(f"\n=== {mode}: loading features ===")
        df = load_mode_frame(cfg.path("paths.features"), mode)
        frame, feature_cols, label_col = prepare(
            mode, df,
            half_life_days=cfg["training.time_decay_half_life_days"],
            swing_uniqueness=cfg["training.swing_uniqueness_weights"])
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
        # the CV's out-of-fold test predictions. CV validates the approach
        # with single boosters (k× CV cost buys nothing); the FINAL model is
        # the ensemble when training.ensemble_size > 1 (T10, default 1=off).
        n_members = cfg["training.ensemble_size"]
        print(f"\ntraining final model ({n_members} member(s)) …")
        kept = cvmod.select_features(frame, feature_cols, label_col, mode)
        dates = np.array(sorted(pd.DatetimeIndex(frame["ts"]).date))
        val_from = dates[int(len(dates) * 0.9)]
        dcol = pd.DatetimeIndex(frame["ts"]).date
        tr, va = frame[dcol < val_from], frame[dcol >= val_from]
        booster = train_ensemble(tr, tr[label_col], va, va[label_col], kept,
                                 n_members=n_members)

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
        from src.models.drift import compute_featstats
        featstats = compute_featstats(frame, kept)
        model_id = save_model(
            conn, cfg.path("paths.models"), mode=mode, booster=booster,
            calibrator=iso, feature_list=kept,
            lgbm_params={**LGBM_PARAMS, "ensemble_size": n_members},
            calibration_curve=curve, cv_report=cv_report, red_flags=flags,
            train_start=str(dates[0]), train_end=str(dates[-1]),
            activate=False, featstats=featstats)
        print(f"registered {model_id} | {len(kept)} features | "
              f"calibration curve: {curve}")

        # Champion/challenger gate (§6.2): a retrained model no longer
        # auto-activates — it must beat (or match, within tolerance) the
        # current champion on its own CV report. Decision + reasons land on
        # the model row and the Model page.
        if args.no_activate:
            print("promotion gate skipped (--no-activate); model registered only")
        else:
            d = promote_if_better(conn, model_id, force=args.force_activate)
            print(f"promotion gate: {d['decision'].upper()}"
                  + (f" (vs {d['compared_to']})" if d["compared_to"] else ""))
            for r in d["reasons"]:
                print(f"  - {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
