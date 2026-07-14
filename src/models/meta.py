"""Meta-labeling (T11, default OFF — training.meta_labeling).

Lopez de Prado's second layer: the primary model proposes, a small secondary
model disposes. The meta model takes the primary's RAW score plus a handful
of context features (volatility, regime, direction — whatever survived the
IC filter) and predicts whether the primary's call will actually pay. Its
output replaces the raw score as the input to isotonic calibration
("meta-conditional calibration"), so the confidence the user sees is
P(setup works | primary score AND the conditions it fired in).

Honesty guard: the reported uplift uses PRIOR-FOLD OOF only — fold k is
scored by a meta trained on folds < k, never on itself. The final meta
trains on all OOF rows (each is out-of-sample for its own fold).

Scan path: MetaPipeline duck-types .predict(X)/.spread(X), so livescan and
the registry treat it like any booster. SHAP explanations come from the
PRIMARY (the meta is a filter; the primary's drivers are the trade's
reasoning — see explain.top_contributors).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

META_PARAMS = {
    "objective": "binary",
    "metric": "binary_logloss",
    "num_leaves": 8,           # deliberately tiny: the meta must not overfit
    "max_depth": 3,
    "min_child_samples": 500,
    "learning_rate": 0.05,
    "feature_fraction": 0.9,
    "lambda_l2": 10.0,
    "verbosity": -1,
    "seed": 7,
}
META_ROUNDS = 200


def meta_matrix(raw_scores: np.ndarray, ctx: pd.DataFrame,
                ctx_cols: list[str]) -> pd.DataFrame:
    M = pd.DataFrame({"score_raw": np.asarray(raw_scores, dtype=float)})
    for c in ctx_cols:
        M[c] = (pd.to_numeric(ctx[c], errors="coerce").to_numpy()
                if c in ctx.columns else np.nan)
    return M[["score_raw"] + ctx_cols].astype(np.float32)


def train_meta_model(X_meta: pd.DataFrame, y: np.ndarray):
    import lightgbm as lgb
    d = lgb.Dataset(X_meta, label=np.asarray(y, dtype=np.float32))
    return lgb.train(META_PARAMS, d, num_boost_round=META_ROUNDS)


def prior_fold_report(results, ctx_cols: list[str]) -> dict:
    """Per-fold uplift, leakage-clean: fold k re-ranked by a meta trained on
    folds < k only. Same signal COUNT as the primary took, so the comparison
    isolates ranking skill from threshold placement. Reported whole — good
    and bad folds alike (§17.2)."""
    folds = []
    usable = [r for r in results if r.test_ctx is not None and len(r.test_label)]
    for k in range(1, len(usable)):
        train, test = usable[:k], usable[k]
        X_tr = pd.concat([meta_matrix(r.test_pred_raw, r.test_ctx, ctx_cols)
                          for r in train], ignore_index=True)
        y_tr = np.concatenate([r.test_label for r in train])
        if len(X_tr) < 2000 or len(np.unique(y_tr)) < 2:
            continue
        meta = train_meta_model(X_tr, y_tr)
        X_te = meta_matrix(test.test_pred_raw, test.test_ctx, ctx_cols)
        n = int(test.n_signals)
        if n < 30:
            continue
        top_primary = np.argsort(-test.test_pred_raw)[:n]
        top_meta = np.argsort(-meta.predict(X_te))[:n]
        folds.append({"fold": test.fold, "n": n,
                      "primary_precision": round(float(test.test_label[top_primary].mean()), 4),
                      "meta_precision": round(float(test.test_label[top_meta].mean()), 4)})
    if not folds:
        return {"folds": [], "note": "insufficient prior-fold data for an honest report"}
    up = [f["meta_precision"] - f["primary_precision"] for f in folds]
    return {"folds": folds, "mean_uplift": round(float(np.mean(up)), 4),
            "uplift_std": round(float(np.std(up)), 4)}


class MetaPipeline:
    """primary (Booster or BaggedBooster) -> meta. predict(X) expects the
    same feature frame the primary scores; ctx_cols are a subset of it."""

    def __init__(self, primary, meta_booster, ctx_cols: list[str]):
        self.primary = primary
        self.meta = meta_booster
        self.ctx_cols = list(ctx_cols)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        raw = self.primary.predict(X)
        return self.meta.predict(meta_matrix(raw, X, self.ctx_cols))

    def spread(self, X: pd.DataFrame) -> np.ndarray:
        """Primary-member disagreement (T10) when the primary is bagged."""
        if hasattr(self.primary, "spread"):
            return self.primary.spread(X)
        return np.full(len(X), np.nan)
