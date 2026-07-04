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
