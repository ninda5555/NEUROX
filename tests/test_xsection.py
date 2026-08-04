"""Cross-sectional normalisation (features/xsection.py) — the 2026-08-01
root-cause fix. The critical property is that the TRAINING transform
(cross_sectional_rank, over a stored panel) and the LIVE transform
(rank_one_bar, over one bar held in memory) agree exactly; if they drift,
the model scores values it never saw in training and nothing announces it.
"""

import numpy as np
import pandas as pd

from src.features import xsection as xs


def _bar(vals, ts="2026-08-01T10:00:00+05:30"):
    return pd.DataFrame({"ts": ts, "symbol": [f"S{i}" for i in range(len(vals))],
                         "feat": vals})


def test_train_and_live_transforms_agree_exactly():
    """The whole point: same numbers from the panel path and the live path."""
    rng = np.random.default_rng(5)
    vals = rng.normal(0, 1, 40)
    panel = _bar(vals)
    trained = xs.cross_sectional_rank(panel, ["feat"])["feat"].to_numpy()

    rows = [{"symbol": f"S{i}", "features": {"feat": float(v)}}
            for i, v in enumerate(vals)]
    live = np.array([r["features"]["feat"] for r in xs.rank_one_bar(rows, ["feat"])])

    np.testing.assert_allclose(trained, live, rtol=0, atol=1e-12)


def test_rank_is_centred_and_bounded():
    panel = _bar([10.0, 20.0, 30.0, 40.0] * 10)
    out = xs.cross_sectional_rank(panel, ["feat"])["feat"]
    assert out.min() >= -0.5 and out.max() <= 0.5
    assert abs(out.mean()) < 1e-9          # centred on 0
    # order is preserved: bigger raw value -> bigger rank
    assert out.iloc[0] < out.iloc[3]


def test_market_wide_constant_ranks_to_zero_information():
    """A column identical across every symbol in the bar (regime_vix,
    tod_frac) carries no cross-sectional information and must flatten,
    whatever its absolute level."""
    panel = _bar([13.15] * 30)
    out = xs.cross_sectional_rank(panel, ["feat"])["feat"]
    assert out.nunique() == 1
    assert abs(float(out.iloc[0])) < 1e-12


def test_regime_and_per_bar_constants_are_excluded_from_the_model():
    cols = ["rsi_14", "vwap_dist_atr", "regime_vix", "regime_breadth",
            "tod_frac", "dow", "atr_pct"]
    kept = xs.model_feature_cols(cols)
    assert kept == ["rsi_14", "vwap_dist_atr", "atr_pct"]
    for banned in ("regime_vix", "regime_breadth", "tod_frac", "dow"):
        assert banned not in kept


def test_thin_cross_section_yields_nan_not_a_fake_rank():
    panel = _bar([1.0, 2.0, 3.0])          # 3 names, below MIN_NAMES
    out = xs.cross_sectional_rank(panel, ["feat"])["feat"]
    assert out.isna().all()

    rows = [{"symbol": "A", "features": {"feat": 1.0}},
            {"symbol": "B", "features": {"feat": 2.0}}]
    live = xs.rank_one_bar(rows, ["feat"])
    assert all(r["features"]["feat"] is None for r in live)


def test_nan_in_nan_out_and_ranks_use_only_present_names():
    vals = [1.0, np.nan, 3.0, 4.0] * 10
    panel = _bar(vals)
    out = xs.cross_sectional_rank(panel, ["feat"])["feat"]
    assert out.isna().sum() == 10          # the NaNs stay NaN
    assert out.notna().sum() == 30

    rows = [{"symbol": f"S{i}", "features": {"feat": (None if v != v else float(v))}}
            for i, v in enumerate(vals)]
    live = xs.rank_one_bar(rows, ["feat"])
    assert sum(1 for r in live if r["features"]["feat"] is None) == 10


def test_bars_are_ranked_independently_of_each_other():
    """Ranks must be within-bar; a level shift between bars must not leak."""
    a = _bar([1.0, 2.0, 3.0, 4.0] * 5, ts="2026-08-01T10:00:00+05:30")
    b = _bar([101.0, 102.0, 103.0, 104.0] * 5, ts="2026-08-01T10:05:00+05:30")
    out = xs.cross_sectional_rank(pd.concat([a, b], ignore_index=True), ["feat"])
    g = out.groupby("ts")["feat"]
    # despite a 100x level difference, both bars produce the same rank profile
    np.testing.assert_allclose(sorted(g.get_group("2026-08-01T10:00:00+05:30")),
                               sorted(g.get_group("2026-08-01T10:05:00+05:30")),
                               atol=1e-12)


def test_original_frame_is_not_mutated():
    panel = _bar([1.0, 2.0, 3.0] * 10)
    before = panel["feat"].copy()
    xs.cross_sectional_rank(panel, ["feat"])
    pd.testing.assert_series_equal(panel["feat"], before)
