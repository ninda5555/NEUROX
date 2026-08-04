"""T10: seed-bagged ensemble + JSON calibrator (default OFF at size 1)."""

from __future__ import annotations

import json
import pickle

import numpy as np
import pandas as pd
import pytest

from src.models.calibrate import RegimeCalibrator, fit_isotonic
from src.models.ensemble import (BaggedBooster, JsonIsotonic,
                                 calibrator_from_json, calibrator_to_json,
                                 train_ensemble)
from src.models.explain import top_contributors
from src.models.registry import (FeatureSpaceMismatch, load_active, save_model,
                                 set_active)

FEATS = ["f1", "f2"]


def _data(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"f1": rng.normal(size=n), "f2": rng.normal(size=n)})
    y = ((X["f1"] + 0.3 * rng.normal(size=n)) > 0).astype(float)
    return X, y


@pytest.fixture(scope="module")
def data():
    X, y = _data()
    return X[:2500], y[:2500], X[2500:], y[2500:]


def test_size_one_is_a_plain_booster(data):
    b = train_ensemble(*data, FEATS, n_members=1)
    assert not isinstance(b, BaggedBooster)     # today's single-model path


def test_bagged_predict_is_member_mean_with_positive_spread(data):
    ens = train_ensemble(*data, FEATS, n_members=3)
    assert isinstance(ens, BaggedBooster) and len(ens.members) == 3
    X = data[0][:50]
    members = ens.predict_members(X)
    assert members.shape == (3, 50)
    assert np.allclose(ens.predict(X), members.mean(axis=0))
    assert (ens.spread(X) >= 0).all()
    # different seeds -> members genuinely differ somewhere
    assert ens.spread(X).max() > 0


def test_json_calibrator_roundtrip_regime():
    rng = np.random.default_rng(1)
    raw = rng.uniform(0, 1, 4000)
    y = (rng.uniform(0, 1, 4000) < raw).astype(float)
    buckets = np.where(rng.uniform(size=4000) < 0.5, "lv_bp", "hv_bn")
    cal = RegimeCalibrator().fit(raw, y, buckets)
    back = calibrator_from_json(calibrator_to_json(cal))
    assert isinstance(back, RegimeCalibrator)   # scan-path isinstance survives
    probe = np.array([0.1, 0.33, 0.5, 0.71, 0.9])
    for bkt in (None, "lv_bp", "hv_bn", "unseen"):
        np.testing.assert_allclose(back.transform(probe, bkt),
                                   cal.transform(probe, bkt), atol=1e-9)


def test_json_calibrator_roundtrip_plain_isotonic():
    raw = np.linspace(0, 1, 500)
    y = (raw > 0.5).astype(float)
    iso = fit_isotonic(raw, y)
    back = calibrator_from_json(calibrator_to_json(iso))
    assert isinstance(back, JsonIsotonic)
    np.testing.assert_allclose(back.transform([0.2, 0.6, 0.9]),
                               iso.transform([0.2, 0.6, 0.9]), atol=1e-9)


def _cv_report():
    return {"precision_mean": 0.5, "precision_std": 0.05, "folds": []}


def test_registry_roundtrip_ensemble_and_json_calibrator(conn, tmp_path, data):
    ens = train_ensemble(*data, FEATS, n_members=3)
    raw = np.linspace(0.01, 0.99, 1000)
    y = (raw > 0.5).astype(float)
    cal = RegimeCalibrator().fit(raw, y, ["lv_bp"] * 1000)
    mid = save_model(conn, tmp_path, mode="INTRADAY", booster=ens,
                     calibrator=cal, feature_list=FEATS, lgbm_params={},
                     calibration_curve=[], cv_report=_cv_report(),
                     red_flags=[], train_start="a", train_end="b",
                     activate=False)
    assert (tmp_path / f"{mid}.txt").exists()
    assert (tmp_path / f"{mid}.m1.txt").exists()
    assert (tmp_path / f"{mid}.m2.txt").exists()
    assert (tmp_path / f"{mid}.calib.json").exists()
    assert not (tmp_path / f"{mid}.pkl").exists()    # no pickle in new artifacts
    set_active(conn, mid)

    mid2, booster, cal2, feats = load_active(conn, "INTRADAY")
    assert mid2 == mid and feats == FEATS
    assert isinstance(booster, BaggedBooster) and len(booster.members) == 3
    X = data[0][:20]
    np.testing.assert_allclose(booster.predict(X), ens.predict(X), atol=1e-9)
    np.testing.assert_allclose(cal2.transform([0.4], "lv_bp"),
                               cal.transform([0.4], "lv_bp"), atol=1e-9)


def test_registry_legacy_pickle_fallback(conn, tmp_path, data):
    single = train_ensemble(*data, FEATS, n_members=1)
    single.save_model(str(tmp_path / "old.txt"))
    iso = fit_isotonic(np.linspace(0, 1, 100),
                       (np.linspace(0, 1, 100) > 0.5).astype(float))
    with open(tmp_path / "old.pkl", "wb") as fh:      # pre-T10 artifact shape
        pickle.dump({"calibrator": iso, "feature_list": FEATS}, fh)
    conn.execute(
        "INSERT INTO models (model_id, mode, trained_at, feature_list, "
        "lgbm_params, calibration, cv_report, artifact_path, is_active) "
        "VALUES ('old', 'SWING', 'x', '[]', '{}', '{}', ?, ?, 1)",
        (json.dumps(_cv_report()), str(tmp_path / "old.txt")))
    conn.commit()
    # the legacy artifact still LOADS (for inspection/backfill), but only
    # when the caller explicitly opts out of the feature-space check
    mid, booster, cal, feats = load_active(conn, "SWING", require_current_space=False)
    assert mid == "old" and feats == FEATS
    assert not isinstance(booster, BaggedBooster)
    assert cal.transform([0.7])[0] > 0.5


def test_legacy_model_is_refused_for_serving(conn, tmp_path, data):
    """A model trained before the 2026-08-01 cross-sectional fix saw raw
    feature values; the scan path now produces within-bar ranks. Serving it
    would be silent train/serve skew, so the default load REFUSES."""
    single = train_ensemble(*data, FEATS, n_members=1)
    single.save_model(str(tmp_path / "old2.txt"))
    iso = fit_isotonic(np.linspace(0, 1, 100),
                       (np.linspace(0, 1, 100) > 0.5).astype(float))
    with open(tmp_path / "old2.pkl", "wb") as fh:
        pickle.dump({"calibrator": iso, "feature_list": FEATS}, fh)
    conn.execute(
        "INSERT INTO models (model_id, mode, trained_at, feature_list, "
        "lgbm_params, calibration, cv_report, artifact_path, is_active) "
        "VALUES ('old2', 'INTRADAY', 'x', '[]', '{}', '{}', ?, ?, 1)",
        (json.dumps(_cv_report()), str(tmp_path / "old2.txt")))
    conn.commit()
    with pytest.raises(FeatureSpaceMismatch):
        load_active(conn, "INTRADAY")


def test_shap_averaging_over_members(data):
    ens = train_ensemble(*data, FEATS, n_members=2)
    row = data[0][:1]
    out = top_contributors(ens, row, FEATS, k=2)
    assert len(out) == 2 and {o["feature"] for o in out} == set(FEATS)
    # ensemble attribution = mean of member attributions for the same row
    per_member = [top_contributors(m, row, FEATS, k=2) for m in ens.members]
    f1_members = [next(o["shap"] for o in pm if o["feature"] == "f1")
                  for pm in per_member]
    f1_ens = next(o["shap"] for o in out if o["feature"] == "f1")
    assert f1_ens == pytest.approx(np.mean(f1_members), abs=1e-3)
