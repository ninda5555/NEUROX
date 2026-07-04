"""LightGBM training (CLAUDE.md §6.2): one binary classifier per mode,
regularized hard — shallow trees, high min_child_samples, feature/bagging
fractions < 1, early stopping on the validation fold."""

from __future__ import annotations

import numpy as np
import pandas as pd

LGBM_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 31,          # <= 31 (§6.2)
    "max_depth": 6,            # <= 6
    "min_child_samples": 200,  # high: kills leaf overfitting on noisy labels
    "learning_rate": 0.05,
    "feature_fraction": 0.8,   # < 1
    "bagging_fraction": 0.8,   # < 1
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "verbosity": -1,
    "seed": 42,
}
NUM_ROUNDS = 600
EARLY_STOP = 50


def train_lgbm(X_tr: pd.DataFrame, y_tr, X_val: pd.DataFrame, y_val,
               feature_names: list[str]):
    """Returns (booster, predict_fn). predict_fn(X)->raw probability."""
    import lightgbm as lgb

    w_tr = X_tr["_w"].to_numpy() if "_w" in X_tr.columns else None
    w_va = X_val["_w"].to_numpy() if "_w" in X_val.columns else None
    dtr = lgb.Dataset(X_tr[feature_names].astype(np.float32),
                      label=np.asarray(y_tr, dtype=np.float32), weight=w_tr)
    dva = lgb.Dataset(X_val[feature_names].astype(np.float32),
                      label=np.asarray(y_val, dtype=np.float32), weight=w_va,
                      reference=dtr)
    booster = lgb.train(LGBM_PARAMS, dtr, num_boost_round=NUM_ROUNDS,
                        valid_sets=[dva],
                        callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)])

    def predict(X: pd.DataFrame) -> np.ndarray:
        return booster.predict(X[feature_names].astype(np.float32),
                               num_iteration=booster.best_iteration)

    return booster, predict


def train_fn_for_cv(X_tr, y_tr, X_val, y_val, feature_names):
    """Adapter matching cv.run_cv's train_fn signature."""
    _, predict = train_lgbm(X_tr, y_tr, X_val, y_val, feature_names)
    return predict
