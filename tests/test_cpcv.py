"""T15: CPCV splits (purge/embargo), Deflated Sharpe, PBO, end-to-end run."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src.models.cpcv import (cpcv_splits, deflated_sharpe,
                             expected_max_sharpe, pbo_cscv, run_cpcv,
                             session_groups)


def _dates(n, start=dt.date(2026, 1, 1)):
    return [start + dt.timedelta(days=i) for i in range(n)]


def test_session_groups_cover_everything_contiguously():
    d = _dates(83)
    g = session_groups(d, 8)
    assert len(g) == 8
    flat = [x for grp in g for x in grp]
    assert flat == d                       # nothing lost, order kept


def test_cpcv_splits_purge_and_embargo():
    d = _dates(40)
    g = session_groups(d, 4)               # groups of 10
    splits = list(cpcv_splits(g, k_test=1, horizon=3))
    assert len(splits) == 4                # C(4,1)
    combo, test, train = splits[1]         # test = group 1 (days 10..19)
    assert combo == (1,)
    # purge/embargo: 3 sessions on each side of the test block are banned
    banned = set(d[7:23])
    assert not (set(train) & banned)
    assert set(test) == set(d[10:20])
    # everything else trains
    assert set(train) == set(d) - banned


def test_expected_max_sharpe_grows_with_trials():
    lo = expected_max_sharpe(5, 0.04)
    hi = expected_max_sharpe(100, 0.04)
    assert 0 < lo < hi                     # more looks -> higher noise bar


def test_deflated_sharpe_behaviour():
    rng = np.random.default_rng(3)
    strong = rng.normal(0.4, 1.0, 400)     # real edge
    weak = rng.normal(0.0, 1.0, 400)       # noise
    d_strong = deflated_sharpe(strong, n_trials=10, sr_trials_var=0.02)
    d_weak = deflated_sharpe(weak, n_trials=10, sr_trials_var=0.02)
    assert d_strong["dsr"] > 0.95
    assert d_weak["dsr"] < 0.6
    # more trials deflate the same track record
    d_many = deflated_sharpe(strong, n_trials=500, sr_trials_var=0.02)
    assert d_many["dsr"] <= d_strong["dsr"]
    assert deflated_sharpe(np.array([0.1]), 5, 0.01)["dsr"] is None


def test_pbo_low_for_dominant_variant_high_for_noise():
    rng = np.random.default_rng(0)
    n_splits = 24
    # variant 2 genuinely dominates on every split -> stable, low PBO
    dominant = np.column_stack([rng.normal(0.0, 0.05, n_splits),
                                rng.normal(0.0, 0.05, n_splits),
                                rng.normal(0.5, 0.05, n_splits),
                                rng.normal(0.0, 0.05, n_splits)])
    assert pbo_cscv(dominant)["pbo"] < 0.1
    # iid noise: any single finite matrix is unstable (a variant that got
    # lucky over the whole sample stays lucky in both CSCV halves), but the
    # EXPECTED PBO is 0.5 — assert on the mean across matrices
    vals = [pbo_cscv(np.random.default_rng(100 + s).normal(0, 0.05, (n_splits, 4)),
                     n_combos=300, seed=s)["pbo"] for s in range(10)]
    assert 0.35 < float(np.mean(vals)) < 0.65
    assert pbo_cscv(np.zeros((2, 4)))["pbo"] is None  # too few splits


def _fast_train_fn(X_tr, y_tr, X_val, y_val, feats):
    """Logistic-free stub: score = the single informative feature."""
    return lambda X: 1 / (1 + np.exp(-3 * np.asarray(X["f1"], dtype=float)))


def test_run_cpcv_end_to_end_on_synthetic():
    rng = np.random.default_rng(1)
    days = _dates(60)
    rows = []
    for d in days:
        for k in range(30):
            f1 = rng.normal()
            rows.append({"ts": pd.Timestamp(d) + pd.Timedelta(hours=10, minutes=k),
                         "symbol": f"NSE:S{k}-EQ", "f1": f1,
                         "label": float((f1 + 0.5 * rng.normal()) > 0)})
    frame = pd.DataFrame(rows)
    res = run_cpcv(frame, "INTRADAY", ["f1"], "label", _fast_train_fn,
                   n_groups=4, k_test=1, thresholds=(0.55, 0.60, 0.65))
    assert res["n_splits_run"] >= 3
    assert res["precision_mean"] is not None and res["precision_mean"] > 0.5
    assert res["deflated_sharpe"]["dsr"] is not None
    assert res["pbo"]["pbo"] is not None
    for s in res["splits"]:
        if not s.get("skipped"):
            assert s["n_signals"] > 0


def test_validation_runs_table_exists(conn):
    conn.execute("INSERT INTO validation_runs (run_id, mode, kind, started_at,"
                 " params) VALUES ('r1','INTRADAY','cpcv','t','{}')")
    conn.commit()
    row = conn.execute("SELECT * FROM validation_runs").fetchone()
    assert row["run_id"] == "r1" and row["result"] is None
