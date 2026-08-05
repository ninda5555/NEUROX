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
from src.models.calibrate import bucket_of

HORIZON_SESSIONS = {"INTRADAY": 1, "SWING": 11}
# context columns snapshotted per test row for meta-labeling (T11);
# the meta model finally uses the subset that survived the IC filter
META_CTX_CANDIDATES = ["atr_pct", "atr14_pct", "regime_vix", "regime_breadth",
                       "regime_vix_rising", "vol_zscore", "direction"]
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
    # top-k: always computable, unlike the threshold-gated fields above
    precision_top_k: float = float("nan")
    n_top_k: int = 0
    avg_r_top_k: float = float("nan")
    test_pred_raw: np.ndarray = field(repr=False, default=None)
    test_label: np.ndarray = field(repr=False, default=None)
    test_bucket: np.ndarray = field(repr=False, default=None)
    test_ctx: pd.DataFrame = field(repr=False, default=None)  # meta-label context (T11)


def select_features(frame: pd.DataFrame, feature_cols: list[str], label_col: str,
                    mode: str) -> list[str]:
    """IC selection. For stacked long+short frames a directional feature's
    MARGINAL IC cancels to ~0 even when it is strongly predictive per side,
    so IC is computed per direction subset and the max |IC| wins."""
    base = [c for c in feature_cols if c != "direction"]
    if "direction" in feature_cols:
        rep_l = compute_ic_report(frame[frame["direction"] > 0], base, label_col, mode)
        rep_s = compute_ic_report(frame[frame["direction"] < 0], base, label_col, mode)
        kept = sorted(set(rep_l.kept) | set(rep_s.kept),
                      key=lambda f: base.index(f))
        return (kept or base) + ["direction"]
    rep = compute_ic_report(frame, base, label_col, mode)
    return rep.kept or base


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


def _realised_r(frame: pd.DataFrame, mode: str, label_col: str) -> np.ndarray:
    """Per-row realised R, preferring the stored three-way outcome.

    INTRADAY stacks long and short rows, so the correct column depends on
    each row's `direction`. Pre-2026-08-05 feature files have no r_* column;
    those fall back to the old win/lose assumption, which OVERSTATES losses
    by charging timeouts a full stop-out — the fallback exists so old
    parquets still run, not because it is right.
    """
    y = frame[label_col].to_numpy()
    if mode == "INTRADAY" and {"r_long", "r_short"} <= set(frame.columns):
        d = frame["direction"].to_numpy() if "direction" in frame.columns \
            else np.ones(len(frame))
        r = np.where(d > 0, frame["r_long"].to_numpy(), frame["r_short"].to_numpy())
    elif "r_long" in frame.columns:
        r = frame["r_long"].to_numpy()
    else:
        return np.where(y > 0, R_WIN[mode], -1.0)
    # any row whose R never got stored keeps the old assumption
    return np.where(np.isfinite(r), r, np.where(y > 0, R_WIN[mode], -1.0))


def top_k_precision(te: pd.DataFrame, p: np.ndarray, y: np.ndarray,
                    r: np.ndarray, k: int) -> tuple[float, int, float]:
    """What the SCANNER actually does: rank each session's cross-section by
    calibrated confidence and take the top k, rather than everything over a
    threshold.

    This is reported because a threshold-gated metric goes blind exactly
    when the gate is unreachable — the 2026-08-05 swing run produced `nan`
    precision in 2 of 6 folds for that reason, which says the gate could not
    be reached but nothing about whether the ranking has skill. Top-k is
    always computable and is the number that matches how signals are chosen.

    Returns (precision, n_selected, avg realised R).
    """
    if len(te) == 0 or k <= 0:
        return float("nan"), 0, float("nan")
    df = pd.DataFrame({"_d": pd.DatetimeIndex(te["ts"]).date,
                       "_p": p, "_y": y, "_r": r})
    sel = (df.sort_values("_p", ascending=False)
             .groupby("_d", sort=False).head(k))
    if sel.empty:
        return float("nan"), 0, float("nan")
    return float(sel["_y"].mean()), int(len(sel)), float(sel["_r"].mean())


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
           threshold: float = 0.60,
           top_k: int = 12) -> tuple[list[FoldResult], list[dict]]:
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
        kept = select_features(tr, feature_cols, label_col, mode)

        # validation tail of the training window (early stopping + fold calib)
        tr_dates = sorted(tr["_date"].unique())
        val_from = tr_dates[int(len(tr_dates) * 0.85)]
        va, tr_core = tr[tr["_date"] >= val_from], tr[tr["_date"] < val_from]

        cols = kept + (["_w"] if "_w" in tr_core.columns else [])
        predict = train_fn(tr_core[cols], tr_core[label_col],
                           va[cols], va[label_col], kept)
        iso = fit_isotonic(predict(va[kept]), va[label_col].to_numpy())
        raw_te = predict(te[kept])
        p_te = iso.transform(raw_te)
        y_te = te[label_col].to_numpy()

        sig = p_te >= threshold
        te_sig = te[sig].assign(_p=p_te[sig])
        # Realised R from the stored three-way outcome when available: a
        # timeout exits near flat, NOT at the stop, and the old
        # where(win, r_win, -1.0) charged every non-win a full stop-out.
        # Falls back to the old assumption only for pre-2026-08-05 feature
        # files that carry no r_* column.
        r = _realised_r(te, mode, label_col)[sig]
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
            **dict(zip(("precision_top_k", "n_top_k", "avg_r_top_k"),
                       top_k_precision(te, p_te, y_te,
                                       _realised_r(te, mode, label_col), top_k))),
            kept_features=kept,
            test_pred_raw=raw_te, test_label=y_te,
            test_bucket=np.array([bucket_of(v, b) for v, b in zip(
                te.get("regime_vix", pd.Series(np.nan, index=te.index)),
                te.get("regime_breadth", pd.Series(np.nan, index=te.index)))]),
            test_ctx=te[[c for c in META_CTX_CANDIDATES if c in te.columns]]
                .reset_index(drop=True),
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
