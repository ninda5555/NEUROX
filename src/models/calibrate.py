"""Isotonic calibration (CLAUDE.md §6.2): fit on out-of-fold predictions.
The UI only ever sees the calibrated probability; the calibration curve is
where over-confidence becomes visible."""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression


def fit_isotonic(pred_oof: np.ndarray, label_oof: np.ndarray) -> IsotonicRegression:
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(np.asarray(pred_oof, dtype=float), np.asarray(label_oof, dtype=float))
    return iso


def calibration_curve_points(pred_cal: np.ndarray, label: np.ndarray,
                             bins: int = 8) -> list[list[float]]:
    """[[predicted_bucket_mean, realized_rate], ...] for the model page."""
    import pandas as pd
    if len(pred_cal) == 0:
        return []
    df = pd.DataFrame({"p": pred_cal, "y": label})
    df["b"] = pd.qcut(df["p"], bins, duplicates="drop")
    g = df.groupby("b", observed=True).agg(p=("p", "mean"), y=("y", "mean"))
    return [[round(float(a), 4), round(float(b), 4)] for a, b in zip(g.p, g.y)]


REGIME_VIX_SPLIT = 15.0
MIN_BUCKET_N = 500


def bucket_of(vix, breadth) -> str:
    """Regime bucket key from features present at emission time."""
    import math
    if vix is None or (isinstance(vix, float) and math.isnan(vix)):
        return "unknown"
    hv = "hv" if float(vix) >= REGIME_VIX_SPLIT else "lv"
    b = 0.0 if breadth is None or (isinstance(breadth, float) and math.isnan(breadth)) else float(breadth)
    return f"{hv}_{'bp' if b >= 0 else 'bn'}"


class RegimeCalibrator:
    """Isotonic calibration fit PER REGIME BUCKET with a pooled fallback.
    Pooled isotonic flattens regime differences (observed: fold-local
    calibration reached 0.6+ in favorable regimes while the pooled map
    capped near 0.45); conditioning on regime keeps the map honest in both
    kinds of market. Buckets with < MIN_BUCKET_N samples use the fallback."""

    def __init__(self):
        self.buckets: dict[str, IsotonicRegression] = {}
        self.fallback: IsotonicRegression | None = None

    def fit(self, raw, y, buckets) -> "RegimeCalibrator":
        import pandas as pd
        raw = np.asarray(raw, float); y = np.asarray(y, float)
        self.fallback = fit_isotonic(raw, y)
        df = pd.DataFrame({"r": raw, "y": y, "b": list(buckets)})
        for b, g in df.groupby("b"):
            if len(g) >= MIN_BUCKET_N and g["y"].nunique() > 1:
                self.buckets[b] = fit_isotonic(g["r"].to_numpy(), g["y"].to_numpy())
        return self

    def transform(self, raw, bucket: str | None = None):
        iso = self.buckets.get(bucket) if bucket else None
        return (iso or self.fallback).transform(np.asarray(raw, float))
