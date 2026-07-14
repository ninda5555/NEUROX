"""T11 meta-labeling (default OFF): meta scores the primary's calls,
prior-fold OOF reporting, MetaPipeline scan-path compatibility."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.models.cv import FoldResult
from src.models.ensemble import train_ensemble
from src.models.explain import top_contributors
from src.models.meta import (MetaPipeline, meta_matrix, prior_fold_report,
                             train_meta_model)
from src.models.registry import load_active, save_model, set_active

FEATS = ["f1", "atr_pct"]


def _primary(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({"f1": rng.normal(size=n),
                      "atr_pct": rng.uniform(0.1, 3.0, n)})
    y = ((X["f1"] + 0.3 * rng.normal(size=n)) > 0).astype(float)
    return train_ensemble(X[:2500], y[:2500], X[2500:], y[2500:], FEATS), X


def test_meta_matrix_fills_missing_ctx_with_nan():
    M = meta_matrix(np.array([0.4, 0.7]), pd.DataFrame({"atr_pct": [1.0, 2.0]}),
                    ["atr_pct", "regime_vix"])
    assert list(M.columns) == ["score_raw", "atr_pct", "regime_vix"]
    assert M["regime_vix"].isna().all()
    assert M["score_raw"].tolist() == pytest.approx([0.4, 0.7])


def _fold(fold, n, seed, informative=True):
    """OOF fold where atr_pct decides whether high primary scores pay:
    the primary is blind to it, the meta can learn it."""
    rng = np.random.default_rng(seed)
    raw = rng.uniform(0, 1, n)
    atr = rng.uniform(0, 2, n)
    good = atr > 1.0 if informative else np.ones(n, dtype=bool)
    label = np.where(good, (raw > 0.4).astype(float),
                     (rng.uniform(0, 1, n) < 0.2).astype(float))
    return FoldResult(fold=fold, train_start="a", train_end="b",
                      test_start="c", test_end="d", n_train=n, n_signals=200,
                      precision_at_thr=0.5, calibration_err=0.1,
                      avg_r_multiple=0.1, max_drawdown_pct=1.0,
                      kept_features=FEATS, test_pred_raw=raw,
                      test_label=label, test_bucket=np.array(["lv_bp"] * n),
                      test_ctx=pd.DataFrame({"atr_pct": atr}))


def test_prior_fold_report_shows_uplift_when_ctx_matters():
    results = [_fold(k, 3000, seed=k) for k in range(1, 5)]
    rep = prior_fold_report(results, ["atr_pct"])
    assert len(rep["folds"]) >= 2
    assert rep["mean_uplift"] > 0.03       # meta sees what the primary can't
    for f in rep["folds"]:
        assert f["fold"] >= 2              # fold 1 has no prior folds — never scored


def test_prior_fold_report_degrades_explicitly_when_starved():
    rep = prior_fold_report([_fold(1, 500, 1), _fold(2, 500, 2)], ["atr_pct"])
    assert rep["folds"] == [] and "insufficient" in rep["note"]


def test_pipeline_predict_and_spread(tmp_path):
    primary, X = _primary()
    raw = primary.predict(X[:2000])
    meta = train_meta_model(meta_matrix(raw, X[:2000], ["atr_pct"]),
                            (raw > 0.5).astype(float))
    pipe = MetaPipeline(primary, meta, ["atr_pct"])
    out = pipe.predict(X[:20])
    assert out.shape == (20,) and ((out >= 0) & (out <= 1)).all()
    assert np.isnan(pipe.spread(X[:5])).all()   # single primary -> NaN spread


def test_registry_roundtrip_meta_pipeline(conn, tmp_path):
    primary, X = _primary()
    raw = primary.predict(X[:2000])
    meta = train_meta_model(meta_matrix(raw, X[:2000], ["atr_pct"]),
                            (raw > 0.5).astype(float))
    pipe = MetaPipeline(primary, meta, ["atr_pct"])
    from src.models.calibrate import fit_isotonic
    iso = fit_isotonic(np.linspace(0, 1, 200),
                       (np.linspace(0, 1, 200) > 0.5).astype(float))
    mid = save_model(conn, tmp_path, mode="INTRADAY", booster=pipe,
                     calibrator=iso, feature_list=FEATS, lgbm_params={},
                     calibration_curve=[], cv_report={"folds": []},
                     red_flags=[], train_start="a", train_end="b",
                     activate=False)
    assert (tmp_path / f"{mid}.meta.txt").exists()
    set_active(conn, mid)
    _, loaded, _, feats = load_active(conn, "INTRADAY")
    assert isinstance(loaded, MetaPipeline) and loaded.ctx_cols == ["atr_pct"]
    np.testing.assert_allclose(loaded.predict(X[:30]), pipe.predict(X[:30]),
                               atol=1e-9)


def test_explanations_come_from_the_primary(tmp_path):
    primary, X = _primary()
    raw = primary.predict(X[:2000])
    meta = train_meta_model(meta_matrix(raw, X[:2000], ["atr_pct"]),
                            (raw > 0.5).astype(float))
    pipe = MetaPipeline(primary, meta, ["atr_pct"])
    out_pipe = top_contributors(pipe, X[:1], FEATS, k=2)
    out_primary = top_contributors(primary, X[:1], FEATS, k=2)
    assert [o["shap"] for o in out_pipe] == [o["shap"] for o in out_primary]
