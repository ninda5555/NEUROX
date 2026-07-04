"""Purged walk-forward CV with embargo (CLAUDE.md §8) — honest by construction.

- >= 6 chronological folds over the session calendar.
- Train only on data strictly before the test window.
- PURGE: drop training samples whose label horizon overlaps the test window
  (intraday labels resolve same-day -> horizon 1 session; swing labels use
  next-day entry + 10 sessions -> horizon 11 sessions).
- EMBARGO: 1 label-horizon of sessions after each earlier test window is
  excluded from later training.
- Feature selection (IC filter) runs INSIDE each fold on train data only.
- Red flags are computed automatically and never suppressed (§17.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.features.ic_filter import compute_ic_report

HORIZON_SESSIONS = {"INTRADAY": 1, "SWING": 11}
RISK_PCT = {"INTRADAY": 1.0, "SWING": 1.5}       # §7 defaults, R -> % capital
R_WIN = {"INTRADAY": 1.5, "SWING": 1.6}          # TP/SL barrier ratios (§5)
PRECISION_FLOOR = 0.45
PRECISION_STD_MAX = 0.10
MIN_SIGNALS_FOR_FLAG = 30


@dataclass
class FoldResult:
    fold: int
    train_start: str; train_end: str
    test_start: str; test_end: str
    n_train: int
    n_signals: int
    precision_at_thr: float          # NaN if no signals
    calibration_err: float           # ECE over deciles, all test rows
    avg_r_multiple: float            # NaN if no signals
    max_drawdown_pct: float          # R-drawdown x risk_pct
    kept_features: list[str]
    test_pred_raw: np.ndarray = field(repr=False, default=None)
    test_label: np.ndarray = field(repr=False, default=None)


def session_folds(dates: list, n_folds: int, initial_train_frac: float = 0.3):
    """Split the session calendar: warmup for first training, then n_folds
    equal contiguous test windows."""
    n = len(dates)
    first_test = int(n * initial_train_frac)
    test_span = (n - first_test) // n_folds
    if test_span < 1:
        raise ValueError("not enough sessions for the requested folds")
    out = []
    for k in range(n_folds):
        a = first_test + k * test_span
        b = n if k == n_folds - 1 else a + test_span
        out.append((dates[a], dates[b - 1]))
    return out


def ece(pred: np.ndarray, label: np.ndarray, bins: int = 10) -> float:
    if len(pred) == 0:
        return float("nan")
    q = pd.qcut(pd.Series(pred), bins, duplicates="drop")
    df = pd.DataFrame({"p": pred, "y": label, "b": q})
    g = df.groupby("b", observed=True).agg(p=("p", "mean"), y=("y", "mean"), n=("y", "size"))
    return float((np.abs(g.p - g.y) * g.n).sum() / g.n.sum())


def max_drawdown_r(r_series: np.ndarray) -> float:
    eq = np.cumsum(r_series)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    return float(np.max(peak - eq)) if len(eq) else 0.0


def run_cv(frame: pd.DataFrame, mode: str, feature_cols: list[str],
           label_col: str, train_fn, n_folds: int = 6,
           threshold: float = 0.60) -> tuple[list[FoldResult], list[dict]]:
    """`train_fn(X_tr, y_tr, X_val, y_val, features) -> predict_fn(X)->raw p`.
    Returns (fold results, red flags). Calibration for fold metrics is fit on
    the fold's validation tail (train-only data), applied to test preds."""
    from src.models.calibrate import fit_isotonic

    horizon = HORIZON_SESSIONS[mode]
    f = frame.dropna(subset=[label_col]).copy()
    f["_date"] = pd.DatetimeIndex(f["ts"]).date
    dates = sorted(f["_date"].unique())
    folds = session_folds(dates, n_folds)

    results: list[FoldResult] = []
    skipped: list[dict] = []
    idx_of = {d: i for i, d in enumerate(dates)}
    for k, (t0, t1) in enumerate(folds):
        i0 = idx_of[t0]
        # train: strictly before test, minus purge tail, minus embargoes
        allowed = set(dates[: max(i0 - horizon, 0)])
        for (p0, p1) in folds[:k]:
            e1 = idx_of[p1]
            for j in range(e1 + 1, min(e1 + 1 + horizon, len(dates))):
                allowed.discard(dates[j])
        tr = f[f["_date"].isin(allowed)]
        te = f[(f["_date"] >= t0) & (f["_date"] <= t1)]
        if len(tr) < 1000 or len(te) == 0:
            skipped.append({"rule": "fold_skipped",
                            "detail": f"fold {k + 1} skipped: {len(tr)} train / "
                                      f"{len(te)} test rows ({t0}..{t1}) — "
                                      "reported, not hidden"})
            continue

        # leakage-clean feature selection on train only
        rep = compute_ic_report(tr, feature_cols, label_col, mode)
        kept = rep.kept or feature_cols
        if "direction" in feature_cols and "direction" not in kept:
            kept = kept + ["direction"]

        # validation tail of the training window (early stopping + fold calib)
        tr_dates = sorted(tr["_date"].unique())
        val_from = tr_dates[int(len(tr_dates) * 0.85)]
        va, tr_core = tr[tr["_date"] >= val_from], tr[tr["_date"] < val_from]

        predict = train_fn(tr_core[kept], tr_core[label_col],
                           va[kept], va[label_col], kept)
        iso = fit_isotonic(predict(va[kept]), va[label_col].to_numpy())
        raw_te = predict(te[kept])
        p_te = iso.transform(raw_te)
        y_te = te[label_col].to_numpy()

        sig = p_te >= threshold
        te_sig = te[sig].assign(_p=p_te[sig])
        r_win = R_WIN[mode]
        r = np.where(y_te[sig] > 0, r_win, -1.0)
        order = np.argsort(te_sig["ts"].to_numpy())
        results.append(FoldResult(
            fold=k + 1,
            train_start=str(min(allowed)) if allowed else "-",
            train_end=str(max(allowed)) if allowed else "-",
            test_start=str(t0), test_end=str(t1),
            n_train=len(tr),
            n_signals=int(sig.sum()),
            precision_at_thr=float(y_te[sig].mean()) if sig.any() else float("nan"),
            calibration_err=ece(p_te, y_te),
            avg_r_multiple=float(r.mean()) if sig.any() else float("nan"),
            max_drawdown_pct=max_drawdown_r(r[order]) * RISK_PCT[mode],
            kept_features=kept,
            test_pred_raw=raw_te, test_label=y_te,
        ))

    flags = skipped + red_flags(results)
    return results, flags


def red_flags(results: list[FoldResult]) -> list[dict]:
    flags = []
    for r in results:
        if r.n_signals >= MIN_SIGNALS_FOR_FLAG and r.precision_at_thr < PRECISION_FLOOR:
            flags.append({"rule": "inconsistent_across_time",
                          "detail": f"fold {r.fold} precision "
                                    f"{r.precision_at_thr:.2f} < {PRECISION_FLOOR} "
                                    f"({r.n_signals} signals, {r.test_start}..{r.test_end})"})
        if r.n_signals == 0:
            flags.append({"rule": "no_signals_in_fold",
                          "detail": f"fold {r.fold} produced no signals at threshold "
                                    f"({r.test_start}..{r.test_end})"})
    precs = [r.precision_at_thr for r in results
             if r.n_signals >= MIN_SIGNALS_FOR_FLAG and not np.isnan(r.precision_at_thr)]
    if len(precs) >= 3 and float(np.std(precs)) > PRECISION_STD_MAX:
        flags.append({"rule": "regime_sensitive",
                      "detail": f"fold-to-fold precision std {np.std(precs):.3f} > "
                                f"{PRECISION_STD_MAX}"})
    return flags
