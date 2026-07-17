"""CPCV + Deflated Sharpe + PBO (T15) — the weekend/offline honesty pass.

Walk-forward CV (§8) gives one chronological path. CPCV (Lopez de Prado)
splits the session calendar into N groups and evaluates EVERY combination of
k test groups (train = the rest, purged + embargoed around each test
group), so the model is judged across C(N,k) different arrangements of
history instead of one. From those splits:

- Sharpe of the pooled out-of-sample R series, then the DEFLATED Sharpe
  ratio (Bailey & Lopez de Prado): the probability the true Sharpe exceeds
  zero after accounting for how many looks we took (n_splits trials),
  non-normality (skew/kurtosis) and track length. A great-looking Sharpe
  found by trying many windows deflates toward zero.

- PBO (probability of backtest overfitting) via CSCV over per-split
  performance of THRESHOLD VARIANTS: the realistic overfitting risk in this
  system is picking the emission threshold that looked best historically.
  For each of many IS/OOS split-half combinations, pick the best variant
  in-sample and record whether it beats the median out-of-sample; PBO is
  the fraction of times it does not.

This is compute-heavy (trains C(N,k) models) and runs OFFLINE via
`python -m src.scripts.validate` — deliberately not a scheduler job.
Everything lands in the validation_runs table, spread first (§17.1/2).
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd

from src.models.cv import HORIZON_SESSIONS, R_WIN, select_features
from src.models.calibrate import fit_isotonic

EULER_GAMMA = 0.5772156649015329


# ---- combinatorial splits --------------------------------------------------

def session_groups(dates: list, n_groups: int) -> list[list]:
    """Contiguous, near-equal groups of the session calendar."""
    if len(dates) < n_groups * 2:
        raise ValueError(f"{len(dates)} sessions cannot fill {n_groups} groups")
    edges = np.linspace(0, len(dates), n_groups + 1).astype(int)
    return [list(dates[a:b]) for a, b in zip(edges[:-1], edges[1:])]


def cpcv_splits(groups: list[list], k_test: int, horizon: int):
    """Yield (test_dates, train_dates) per combination, with purge+embargo:
    `horizon` sessions before AND after every test group are dropped from
    training so overlapping label windows can't leak."""
    dates = [d for g in groups for d in g]
    idx = {d: i for i, d in enumerate(dates)}
    for combo in itertools.combinations(range(len(groups)), k_test):
        test = [d for gi in combo for d in groups[gi]]
        banned = set()
        for gi in combo:
            lo = idx[groups[gi][0]]
            hi = idx[groups[gi][-1]]
            for j in range(max(lo - horizon, 0), min(hi + horizon + 1, len(dates))):
                banned.add(dates[j])
        train = [d for d in dates if d not in banned]
        yield combo, sorted(test), sorted(train)


# ---- deflated Sharpe -------------------------------------------------------

def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _phi_inv(p: float) -> float:
    # Acklam-style rational approximation is overkill; bisect erf is enough here
    lo, hi = -10.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _phi(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """E[max SR] across n_trials of noise (Bailey/LdP): the bar an observed
    Sharpe must clear before it means anything."""
    if n_trials <= 1 or var_sr <= 0:
        return 0.0
    return math.sqrt(var_sr) * (
        (1 - EULER_GAMMA) * _phi_inv(1 - 1 / n_trials)
        + EULER_GAMMA * _phi_inv(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(returns: np.ndarray, n_trials: int,
                    sr_trials_var: float) -> dict:
    """P(true Sharpe > 0) after deflating for trials, skew/kurtosis, length."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    T = len(r)
    if T < 3 or np.std(r) == 0:
        return {"sharpe": None, "dsr": None,
                "note": "too few/degenerate returns for a Sharpe"}
    sr = float(np.mean(r) / np.std(r, ddof=1))
    m = r - r.mean()
    g3 = float(np.mean(m ** 3) / (np.std(r) ** 3))
    g4 = float(np.mean(m ** 4) / (np.std(r) ** 4))
    sr0 = expected_max_sharpe(n_trials, sr_trials_var)
    denom = 1 - g3 * sr + (g4 - 1) / 4 * sr ** 2
    if denom <= 0:
        return {"sharpe": round(sr, 4), "dsr": None,
                "note": "non-normality too extreme for the DSR formula"}
    dsr = _phi((sr - sr0) * math.sqrt(T - 1) / math.sqrt(denom))
    return {"sharpe": round(sr, 4), "sr0_expected_max_noise": round(sr0, 4),
            "skew": round(g3, 3), "kurtosis": round(g4, 3), "n_returns": T,
            "n_trials": n_trials, "dsr": round(dsr, 4)}


# ---- PBO via CSCV over threshold variants ----------------------------------

def pbo_cscv(perf_matrix: np.ndarray, n_combos: int = 200, seed: int = 7) -> dict:
    """perf_matrix: (n_splits, n_variants) OOS performance per split per
    threshold variant. CSCV: for many half-splits of the rows, pick the
    best variant in-sample; PBO = fraction of combos where that pick lands
    in the bottom half out-of-sample."""
    M = np.asarray(perf_matrix, dtype=float)
    M = M[~np.isnan(M).all(axis=1)]
    S, N = M.shape
    if S < 4 or N < 2:
        return {"pbo": None, "note": f"need >=4 splits and >=2 variants (have {S}x{N})"}
    rng = np.random.default_rng(seed)
    half = S // 2
    logits = []
    for _ in range(n_combos):
        rows = rng.permutation(S)
        is_rows, oos_rows = rows[:half], rows[half:]
        is_perf = np.nanmean(M[is_rows], axis=0)
        oos_perf = np.nanmean(M[oos_rows], axis=0)
        best = int(np.nanargmax(is_perf))
        # relative OOS rank of the IS-best variant, in (0, 1)
        w = (np.sum(oos_perf < oos_perf[best]) + 0.5 * np.sum(
            oos_perf == oos_perf[best]) ) / N
        w = min(max(w, 1e-6), 1 - 1e-6)
        logits.append(math.log(w / (1 - w)))
    pbo = float(np.mean(np.array(logits) <= 0.0))
    return {"pbo": round(pbo, 4), "n_combos": n_combos,
            "n_splits": S, "n_variants": N,
            "verdict": ("overfitting likely — the best-looking threshold does "
                        "not hold up out-of-sample" if pbo > 0.5 else
                        "in-sample threshold choice generalizes more often than not")}


# ---- the full CPCV run -----------------------------------------------------

def run_cpcv(frame: pd.DataFrame, mode: str, feature_cols: list[str],
             label_col: str, train_fn, *, n_groups: int = 8, k_test: int = 2,
             thresholds: tuple = (0.55, 0.60, 0.65, 0.70)) -> dict:
    """Returns the full result dict (per-split metrics, DSR, PBO). train_fn
    matches cv.run_cv's contract. Spread reported whole, never averaged
    away (§17.2)."""
    horizon = HORIZON_SESSIONS[mode]
    f = frame.dropna(subset=[label_col]).copy()
    f["_date"] = pd.DatetimeIndex(f["ts"]).date
    dates = sorted(f["_date"].unique())
    groups = session_groups(dates, n_groups)

    splits, sharpes, pooled_r = [], [], []
    perf_matrix = []
    for combo, test_dates, train_dates in cpcv_splits(groups, k_test, horizon):
        tr = f[f["_date"].isin(train_dates)]
        te = f[f["_date"].isin(test_dates)]
        if len(tr) < 1000 or len(te) == 0:
            splits.append({"groups": list(combo), "skipped": True,
                           "n_train": len(tr), "n_test": len(te)})
            continue
        kept = select_features(tr, feature_cols, label_col, mode)
        tr_dates = sorted(tr["_date"].unique())
        val_from = tr_dates[int(len(tr_dates) * 0.85)]
        va, tr_core = tr[tr["_date"] >= val_from], tr[tr["_date"] < val_from]
        cols = kept + (["_w"] if "_w" in tr_core.columns else [])
        predict = train_fn(tr_core[cols], tr_core[label_col],
                           va[cols], va[label_col], kept)
        iso = fit_isotonic(predict(va[kept]), va[label_col].to_numpy())
        p_te = iso.transform(predict(te[kept]))
        y_te = te[label_col].to_numpy()
        r_win = R_WIN[mode]

        row_perf = []
        for thr in thresholds:
            sig = p_te >= thr
            r = np.where(y_te[sig] > 0, r_win, -1.0)
            row_perf.append(float(r.mean()) if sig.any() else np.nan)
        perf_matrix.append(row_perf)

        base_sig = p_te >= thresholds[1]      # 0.60 = the production gate
        r_base = np.where(y_te[base_sig] > 0, r_win, -1.0)
        pooled_r.extend(r_base.tolist())
        sr = (float(np.mean(r_base) / np.std(r_base, ddof=1))
              if base_sig.sum() >= 3 and np.std(r_base) > 0 else np.nan)
        sharpes.append(sr)
        splits.append({"groups": list(combo),
                       "test_start": str(test_dates[0]), "test_end": str(test_dates[-1]),
                       "n_train": len(tr), "n_signals": int(base_sig.sum()),
                       "precision": (float(y_te[base_sig].mean())
                                     if base_sig.any() else None),
                       "avg_r": (float(r_base.mean()) if base_sig.any() else None),
                       "sharpe": None if np.isnan(sr) else round(sr, 4)})

    sr_arr = np.array([s for s in sharpes if not np.isnan(s)])
    dsr = deflated_sharpe(np.array(pooled_r), n_trials=max(len(sr_arr), 1),
                          sr_trials_var=float(np.var(sr_arr)) if len(sr_arr) > 1 else 0.0)
    pbo = pbo_cscv(np.array(perf_matrix)) if perf_matrix else {"pbo": None,
                                                               "note": "no usable splits"}
    precs = [s["precision"] for s in splits
             if not s.get("skipped") and s.get("precision") is not None]
    return {"n_groups": n_groups, "k_test": k_test,
            "thresholds": list(thresholds),
            "n_splits_run": len([s for s in splits if not s.get("skipped")]),
            "splits": splits,
            "precision_mean": round(float(np.mean(precs)), 4) if precs else None,
            "precision_std": round(float(np.std(precs)), 4) if precs else None,
            "deflated_sharpe": dsr, "pbo": pbo}
