"""Cross-sectional normalisation (added 2026-08-01, root-cause fix).

A scanner scores ~860 symbols at one instant and RANKS THEM AGAINST EACH
OTHER. So the only thing that can help it is a feature that varies across
symbols at that instant. Absolute levels — and anything shared by the whole
market — are noise to that decision, however well they correlate with
outcomes over time.

The intraday model learned this the wrong way round. 58% of its gain sat on
`regime_breadth` (25.4%), `regime_vix` (21.1%) and `tod_frac` (11.5%): three
columns that are ONE VALUE PER BAR, identical for every symbol in the scan.
They cannot separate stocks even in principle, so the model was really
fitting day-level base rates and then applying the same offset to all 860
names. Observed live on 2026-07-29: regime_vix=13.15 and
regime_breadth=-0.347541 byte-identical across all 858 symbols, 837 of which
came out SHORT with barely separated scores.

Two changes here, applied IDENTICALLY at training and at scan time (they
share this module precisely so the two paths cannot drift apart):

1. `cross_sectional_rank` — replace each feature by its percentile rank
   within its own bar, centred on 0. The model can then only express "this
   stock looks better than its peers right now", never "the market looks
   good today". A market-wide constant ranks to a flat 0.0 and carries
   exactly zero information, so the representation itself makes the old
   failure mode impossible rather than relying on the feature list staying
   correct.

2. Regime leaves the feature matrix entirely (see MODEL_EXCLUDED). It is not
   discarded — it moves to the gating layer where it belongs: the regime
   bucket already selects the isotonic calibrator (RegimeCalibrator, §6.2)
   and tags every journalled signal. That is regime acting as CONTEXT for
   how much to trust a cross-sectional score, instead of competing with it
   as a pseudo-feature.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Columns that must never enter the cross-sectional feature matrix: one value
# per bar shared by every symbol. Kept in the frame (the calibrator bucket and
# the journal still read them) — just not offered to the model.
MODEL_EXCLUDED = ("regime_vix", "regime_vix_rising", "regime_nifty_above_50",
                  "regime_nifty_above_200", "regime_breadth",
                  "tod_frac", "dow")

MIN_NAMES = 10   # a rank across fewer names than this is not a cross-section


def _unit_rank(r, n):
    """Ranks -> [0, 1] EXACTLY: (r-1)/(n-1), not pandas' pct=True.

    pct=True divides by n, so a constant column (every rank == (n+1)/2)
    lands on (n+1)/2n — 0.5167 for a 30-name bar, 0.5006 for 858 — i.e. a
    small non-zero offset that encodes how many symbols were in the bar.
    A market-wide constant must contribute exactly nothing, and the
    endpoints must be a true 0 and 1 regardless of cross-section size, or
    the same feature means different things on a thin day and a full one.
    """
    return (r - 1) / (n - 1).where(n > 1) if hasattr(n, "where") else (r - 1) / (n - 1)


def model_feature_cols(feature_cols: list[str]) -> list[str]:
    """The subset of `feature_cols` the model may train/score on."""
    return [c for c in feature_cols if c not in MODEL_EXCLUDED]


def cross_sectional_rank(frame: pd.DataFrame, cols: list[str],
                         ts_col: str = "ts",
                         min_names: int = MIN_NAMES) -> pd.DataFrame:
    """Percentile-rank `cols` within each bar, centred on 0 -> [-0.5, +0.5].

    NaN in, NaN out (LightGBM handles missing natively). Bars with fewer than
    `min_names` quoted symbols yield NaN rather than a rank across too few
    names to be a cross-section. A constant column ranks to a flat 0.0.

    Returns a copy; the input frame is not mutated.
    """
    out = frame.copy()
    if not cols:
        return out
    g = out.groupby(ts_col, sort=False)
    for c in cols:
        if c not in out.columns:
            continue
        n = g[c].transform("count")
        out[c] = np.where(n >= min_names, _unit_rank(g[c].rank(), n) - 0.5, np.nan)
    return out


def rank_one_bar(rows: list[dict], cols: list[str],
                 min_names: int = MIN_NAMES) -> list[dict]:
    """Scan-time equivalent of `cross_sectional_rank` for a single bar's
    worth of already-built feature dicts (one per symbol).

    The live scanner holds exactly one cross-section in memory at a time, so
    it needs the same transform without a ts column to group on. Routed
    through the same percentile-rank definition to guarantee the live values
    match what the model was trained on.
    """
    if not rows:
        return rows
    df = pd.DataFrame([r["features"] for r in rows])
    n = len(df)
    ranked = {}
    for c in cols:
        if c not in df.columns:
            continue
        cnt = int(df[c].notna().sum())
        if cnt >= min_names and cnt > 1:
            ranked[c] = (_unit_rank(df[c].rank(), cnt) - 0.5).to_numpy()
        else:
            ranked[c] = np.full(n, np.nan)
    out = []
    for i, r in enumerate(rows):
        f = dict(r["features"])
        for c, vals in ranked.items():
            v = vals[i]
            f[c] = None if v != v else float(v)
        out.append({**r, "features": f})
    return out
