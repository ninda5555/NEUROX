"""Seed-bagged LightGBM ensemble + JSON calibrator serialization (T10).

Ensemble (training.ensemble_size, default 1 = OFF): k members trained on
identical data, differing only in seeds (tree seed, bagging seed, feature
seed). The score is the member mean; the member SPREAD (std) is a free
model-uncertainty readout — wide spread means the members disagree and the
mean is fragile. With size 1 every path reduces exactly to today's single
booster.

JSON calibrator: isotonic maps are just monotone point sets, so new model
artifacts serialize the calibrator to {model_id}.calib.json instead of
pickle — removing unpickling from the load path for every model trained
from now on (old .pkl artifacts still load; see registry.load_active).
Reconstruction keeps the real RegimeCalibrator class so isinstance checks
in the scan path stay true.
"""

from __future__ import annotations

import numpy as np

from src.models.calibrate import RegimeCalibrator
from src.models.train import LGBM_PARAMS, train_lgbm


class BaggedBooster:
    """k LightGBM boosters; predict() is the member mean, spread() the
    member std. Duck-types Booster.predict for the scan path."""

    def __init__(self, members: list):
        if not members:
            raise ValueError("BaggedBooster needs at least one member")
        self.members = members

    def predict_members(self, X) -> np.ndarray:
        """(k, n_rows) raw member scores."""
        return np.vstack([m.predict(X) for m in self.members])

    def predict(self, X) -> np.ndarray:
        return self.predict_members(X).mean(axis=0)

    def spread(self, X) -> np.ndarray:
        return self.predict_members(X).std(axis=0)


def train_ensemble(X_tr, y_tr, X_val, y_val, feature_names: list[str],
                   n_members: int = 1):
    """n_members == 1 -> the plain single booster (today's behavior,
    byte-identical params). > 1 -> BaggedBooster of seed-varied members."""
    if n_members <= 1:
        booster, _ = train_lgbm(X_tr, y_tr, X_val, y_val, feature_names)
        return booster
    members = []
    for i in range(n_members):
        params = {**LGBM_PARAMS,
                  "seed": LGBM_PARAMS["seed"] + i,
                  "bagging_seed": LGBM_PARAMS["seed"] + 100 + i,
                  "feature_fraction_seed": LGBM_PARAMS["seed"] + 200 + i}
        booster, _ = train_lgbm(X_tr, y_tr, X_val, y_val, feature_names,
                                params=params)
        members.append(booster)
    return BaggedBooster(members)


# ---- calibrator <-> JSON ---------------------------------------------------

def _iso_points(iso) -> dict:
    return {"x": [float(v) for v in np.asarray(iso.X_thresholds_)],
            "y": [float(v) for v in np.asarray(iso.y_thresholds_)]}


class JsonIsotonic:
    """np.interp over the fitted isotonic thresholds — sklearn's isotonic
    transform IS linear interpolation over (X_thresholds_, y_thresholds_)
    with endpoint clipping, so this reproduces it exactly."""

    def __init__(self, x: list[float], y: list[float]):
        self._x = np.asarray(x, dtype=float)
        self._y = np.asarray(y, dtype=float)

    def transform(self, raw):
        return np.interp(np.asarray(raw, dtype=float), self._x, self._y)


def calibrator_to_json(cal) -> dict:
    if isinstance(cal, RegimeCalibrator):
        return {"kind": "regime",
                "fallback": _iso_points(cal.fallback),
                "buckets": {b: _iso_points(iso) for b, iso in cal.buckets.items()}}
    return {"kind": "isotonic", "points": _iso_points(cal)}


def calibrator_from_json(d: dict):
    if d["kind"] == "regime":
        cal = RegimeCalibrator()   # real class: isinstance checks stay true
        cal.fallback = JsonIsotonic(**d["fallback"])
        cal.buckets = {b: JsonIsotonic(**p) for b, p in d["buckets"].items()}
        return cal
    return JsonIsotonic(**d["points"])
