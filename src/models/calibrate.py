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
    """[[predicted_bucket_mean, realized_rate], ...].

    WARNING — in-sample when `pred_cal` comes from the isotonic that was fit
    on this same `label`. Isotonic's fitted value for a block IS that block's
    mean outcome, so binning by the output and re-averaging returns the input
    by construction: x == y at every point, always, however wrong the model
    is. Kept because the pooled shape is still worth seeing, but it CANNOT
    detect miscalibration. Use `calibration_curve_oos` for that.
    """
    import pandas as pd
    if len(pred_cal) == 0:
        return []
    df = pd.DataFrame({"p": pred_cal, "y": label})
    df["b"] = pd.qcut(df["p"], bins, duplicates="drop")
    g = df.groupby("b", observed=True).agg(p=("p", "mean"), y=("y", "mean"))
    return [[round(float(a), 4), round(float(b), 4)] for a, b in zip(g.p, g.y)]


def calibration_curve_oos(raw_fit: np.ndarray, y_fit: np.ndarray,
                          raw_test: np.ndarray, y_test: np.ndarray,
                          bins: int = 8) -> list[list[float]]:
    """A calibration curve that CAN show a lie (added 2026-08-05).

    Fits the isotonic on one slice of out-of-fold predictions and evaluates
    it on a later, disjoint slice. Because the map never saw `y_test`, the
    points are free to fall off the diagonal — which is the entire purpose
    of the chart per CLAUDE.md §6.2, and something the in-sample version
    above is structurally incapable of doing.

    Returns [] when either slice is too thin to bin meaningfully; the caller
    reports the absence rather than substituting the flattering curve.
    """
    import pandas as pd
    if len(raw_fit) < 200 or len(raw_test) < 200:
        return []
    if len(np.unique(y_fit)) < 2:
        return []
    iso = fit_isotonic(raw_fit, y_fit)
    p = iso.transform(np.asarray(raw_test, dtype=float))
    df = pd.DataFrame({"p": p, "y": np.asarray(y_test, dtype=float)})
    try:
        df["b"] = pd.qcut(df["p"], bins, duplicates="drop")
    except ValueError:
        return []
    g = df.groupby("b", observed=True).agg(p=("p", "mean"), y=("y", "mean"),
                                           n=("y", "size"))
    g = g[g.n >= 20]
    return [[round(float(a), 4), round(float(b), 4)] for a, b in zip(g.p, g.y)]


REGIME_VIX_SPLIT = 15.0
MIN_BUCKET_N = 500
# Minimum samples in the TOP isotonic block (added 2026-08-05). MIN_BUCKET_N
# guards a bucket's total size but says nothing about the block that sets its
# CEILING — and the ceiling is the only part the emission gate ever consults.
# The 2026-08-05 swing model had two buckets whose ceiling was exactly 1.0,
# i.e. a top block where every sample happened to win, which can only be a
# handful of rows. Those were also the ONLY buckets that could clear a 0.60
# gate: every signal it could ever emit would have come through the two least
# trustworthy maps in the model.
MIN_TOP_BLOCK_N = 50


class DegenerateCalibration(RuntimeError):
    """The pooled fallback's top block is too small to trust. Per-bucket maps
    can be rejected and fall back; the fallback itself has nothing to fall
    back TO, so a degenerate one is fatal rather than quietly served."""


def top_block_n(iso, raw) -> int:
    """Samples sharing the map's maximum output — the block that fixes the
    ceiling. Isotonic is piecewise-constant, so this is exactly the evidence
    behind the highest confidence the model can ever report."""
    pred = np.asarray(iso.transform(np.asarray(raw, dtype=float)))
    if pred.size == 0:
        return 0
    return int((pred >= pred.max() - 1e-12).sum())


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
        # why each rejected bucket was rejected — surfaced by the retrain CLI
        # and stored on the model, never swallowed
        self.rejected: dict[str, str] = {}

    def fit(self, raw, y, buckets, min_top_block: int = MIN_TOP_BLOCK_N) -> "RegimeCalibrator":
        import pandas as pd
        raw = np.asarray(raw, float); y = np.asarray(y, float)
        self.fallback = fit_isotonic(raw, y)
        fb_top = top_block_n(self.fallback, raw)
        if fb_top < min_top_block:
            raise DegenerateCalibration(
                f"pooled fallback ceiling rests on {fb_top} sample(s) "
                f"(< {min_top_block}). Every confidence this model reports at "
                "the top of its range would be an artefact of a handful of "
                "rows. Refusing to build a calibrator that cannot be trusted "
                "where the emission gate reads it.")
        df = pd.DataFrame({"r": raw, "y": y, "b": list(buckets)})
        for b, g in df.groupby("b"):
            if len(g) < MIN_BUCKET_N:
                self.rejected[b] = f"only {len(g)} samples (< {MIN_BUCKET_N})"
                continue
            if g["y"].nunique() <= 1:
                self.rejected[b] = "single-valued outcomes — nothing to calibrate"
                continue
            iso = fit_isotonic(g["r"].to_numpy(), g["y"].to_numpy())
            n_top = top_block_n(iso, g["r"].to_numpy())
            if n_top < min_top_block:
                # rejected, NOT silently kept: a bucket whose ceiling rests on
                # a few all-winning rows is exactly how a 1.0 confidence gets
                # manufactured and sails through the gate
                self.rejected[b] = (f"ceiling rests on {n_top} sample(s) "
                                    f"(< {min_top_block}) — using pooled map")
                continue
            self.buckets[b] = iso
        return self

    def transform(self, raw, bucket: str | None = None):
        iso = self.buckets.get(bucket) if bucket else None
        return (iso or self.fallback).transform(np.asarray(raw, float))
