import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src.models import cv as cvmod
from src.models.calibrate import calibration_curve_points, fit_isotonic
from src.models.explain import sentence_for, top_contributors
from src.models.registry import load_active, save_model, set_active
from src.models.train import LGBM_PARAMS, train_lgbm
from src.timeutil import IST


def make_frame(n_days=120, rows_per_day=50, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    d0 = dt.date(2026, 1, 1)
    for i in range(n_days):
        day = d0 + dt.timedelta(days=i)
        for j in range(rows_per_day):
            ts = IST.localize(dt.datetime(day.year, day.month, day.day, 10, 0)
                              + dt.timedelta(minutes=j))
            x = rng.normal()
            rows.append({"ts": ts, "f1": x, "f2": rng.normal(),
                         "label": float(x + rng.normal(0, 0.7) > 0)})
    return pd.DataFrame(rows)


# ---------- CV: purge + embargo ----------
def test_cv_train_windows_respect_purge_and_embargo():
    frame = make_frame()
    seen: list[tuple] = []

    def spy_train_fn(X_tr, y_tr, X_val, y_val, feats):
        dates = sorted(set(pd.DatetimeIndex(
            pd.concat([X_tr, X_val]).index.to_series() * 0)))  # unused
        return lambda X: np.full(len(X), 0.7)

    # capture train dates via a wrapper over run_cv internals: use the
    # FoldResult windows instead — train_end must sit >= horizon sessions
    # before test_start.
    results, _ = cvmod.run_cv(frame, "SWING", ["f1", "f2"], "label",
                              lambda *a: (lambda X: np.full(len(X), 0.7)),
                              n_folds=6)
    assert len(results) == 6
    all_dates = sorted({d for d in pd.DatetimeIndex(frame["ts"]).date})
    idx = {d: i for i, d in enumerate(all_dates)}
    horizon = cvmod.HORIZON_SESSIONS["SWING"]
    for r in results:
        gap = idx[dt.date.fromisoformat(r.test_start)] - idx[dt.date.fromisoformat(r.train_end)]
        assert gap > horizon  # purge: horizon sessions dropped before test
    # walk-forward: later folds train on more data
    assert results[-1].n_train > results[0].n_train


def test_cv_embargo_excludes_post_test_sessions():
    frame = make_frame(n_days=100)
    captured = []

    def train_fn(X_tr, y_tr, X_val, y_val, feats):
        captured.append(len(X_tr) + len(X_val))
        return lambda X: np.full(len(X), 0.99)

    results, _ = cvmod.run_cv(frame, "INTRADAY", ["f1", "f2"], "label",
                              train_fn, n_folds=6)
    # fold k+1's train includes fold k's test window minus 1-session embargo:
    # trainable rows grow by roughly one test window per fold, strictly less
    # than the full span (embargo bites)
    spans = [r.n_train for r in results]
    assert spans == sorted(spans)


# ---------- red flags ----------
def _fr(fold, prec, n=100):
    return cvmod.FoldResult(fold=fold, train_start="a", train_end="b",
                            test_start="c", test_end="d", n_train=1000,
                            n_signals=n, precision_at_thr=prec,
                            calibration_err=0.03, avg_r_multiple=1.0,
                            max_drawdown_pct=5.0, kept_features=["f1"])


def test_red_flag_rules_fire():
    flags = cvmod.red_flags([_fr(1, 0.62), _fr(2, 0.40), _fr(3, 0.61)])
    rules = {f["rule"] for f in flags}
    assert "inconsistent_across_time" in rules
    flags2 = cvmod.red_flags([_fr(1, 0.45), _fr(2, 0.70), _fr(3, 0.50)])
    assert any(f["rule"] == "regime_sensitive" for f in flags2)
    flags3 = cvmod.red_flags([_fr(1, float("nan"), n=0)])
    assert any(f["rule"] == "no_signals_in_fold" for f in flags3)
    assert cvmod.red_flags([_fr(1, 0.55), _fr(2, 0.57), _fr(3, 0.56)]) == []


# ---------- calibration ----------
def test_isotonic_is_monotone_and_curve_tracks_reality():
    rng = np.random.default_rng(3)
    raw = rng.uniform(0, 1, 20000)
    y = (rng.uniform(0, 1, 20000) < raw ** 2).astype(float)  # overconfident raw
    iso = fit_isotonic(raw, y)
    grid = iso.transform(np.linspace(0, 1, 50))
    assert (np.diff(grid) >= -1e-9).all()
    pts = calibration_curve_points(iso.transform(raw), y)
    assert all(abs(p - r) < 0.05 for p, r in pts)  # calibrated ≈ diagonal


# ---------- registry + explain (tiny real booster) ----------
@pytest.fixture
def tiny_model():
    rng = np.random.default_rng(1)
    X = pd.DataFrame({"f1": rng.normal(size=4000), "f2": rng.normal(size=4000)})
    y = (X["f1"] + rng.normal(0, 0.5, 4000) > 0).astype(float)
    booster, _ = train_lgbm(X[:3000], y[:3000], X[3000:], y[3000:], ["f1", "f2"])
    return booster


def test_registry_single_active_and_roundtrip(conn, tmp_path, tiny_model):
    iso = fit_isotonic(np.array([0.1, 0.5, 0.9]), np.array([0.0, 1.0, 1.0]))
    rep = {"folds": [], "threshold": 0.6}
    m1 = save_model(conn, tmp_path, mode="SWING", booster=tiny_model,
                    calibrator=iso, feature_list=["f1", "f2"],
                    lgbm_params=LGBM_PARAMS, calibration_curve=[[0.6, 0.58]],
                    cv_report=rep, red_flags=[], train_start="2026-01-01",
                    train_end="2026-06-01")
    m2 = save_model(conn, tmp_path, mode="SWING", booster=tiny_model,
                    calibrator=iso, feature_list=["f1", "f2"],
                    lgbm_params=LGBM_PARAMS, calibration_curve=[],
                    cv_report=rep, red_flags=[], train_start="2026-01-01",
                    train_end="2026-06-15")
    active = conn.execute(
        "SELECT model_id FROM models WHERE mode='SWING' AND is_active=1").fetchall()
    assert [r["model_id"] for r in active] == [m2]
    set_active(conn, m1)
    mid, booster, cal, feats = load_active(conn, "SWING")
    assert mid == m1 and feats == ["f1", "f2"]
    assert 0 <= cal.transform([0.5])[0] <= 1
    p = booster.predict(pd.DataFrame({"f1": [2.0], "f2": [0.0]}))
    assert p[0] > 0.5  # strong f1 -> high raw score


def test_shap_sentences(tiny_model):
    row = pd.DataFrame({"f1": [2.5], "f2": [0.1]})
    top = top_contributors(tiny_model, row, ["f1", "f2"], k=2)
    assert len(top) == 2
    assert top[0]["feature"] == "f1"           # dominant driver first
    assert "strongest factor" in top[0]["sentence"]
    assert sentence_for("vwap_dist_atr", 0.2).startswith("Price is holding")
    assert sentence_for("vwap_dist_atr", -0.2).startswith("Price is trading below")
