"""Three-way barrier outcomes and the calibration guards (2026-08-05).

These pin the two measurement defects found while judging the swing model:
a timeout was booked as a full stop-out, and a calibration bucket whose
ceiling rested on a handful of all-winning rows was served silently.
"""

import numpy as np
import pandas as pd
import pytest

from src.features.labels import (OUTCOME_STOP, OUTCOME_TARGET, OUTCOME_TIMEOUT,
                                 _walk_barriers, swing_labels)
from src.models.calibrate import (MIN_TOP_BLOCK_N, DegenerateCalibration,
                                  RegimeCalibrator, calibration_curve_oos,
                                  calibration_curve_points, top_block_n)


# ---------- three-way outcomes ----------
def test_target_hit_reports_reward_over_risk():
    # entry 100, tp 103, sl 98 -> reward/risk = 3/2 = 1.5
    hi, lo, cl = np.array([103.5]), np.array([99.5]), np.array([103.0])
    label, outcome, r = _walk_barriers(hi, lo, cl, 100.0, 103.0, 98.0, True)
    assert (label, outcome) == (1, OUTCOME_TARGET)
    assert abs(r - 1.5) < 1e-12


def test_stop_hit_reports_exactly_minus_one_r():
    hi, lo, cl = np.array([100.5]), np.array([97.0]), np.array([97.5])
    label, outcome, r = _walk_barriers(hi, lo, cl, 100.0, 103.0, 98.0, True)
    assert (label, outcome, r) == (0, OUTCOME_STOP, -1.0)


def test_timeout_is_marked_to_market_not_to_the_stop():
    """The defect that biased every reported expectancy: a trade that drifts
    sideways exits near flat, and used to be charged a full -1R."""
    hi = np.array([101.0, 101.2, 100.8])
    lo = np.array([99.5, 99.2, 99.6])
    cl = np.array([100.5, 100.1, 100.4])       # ends +0.4 on a 2.0 risk
    label, outcome, r = _walk_barriers(hi, lo, cl, 100.0, 103.0, 98.0, True)
    assert (label, outcome) == (0, OUTCOME_TIMEOUT)
    assert abs(r - 0.2) < 1e-12                # +0.4 / 2.0, NOT -1.0
    assert r > -1.0


def test_timeout_can_be_negative_without_being_a_full_stop():
    hi = np.array([100.2, 100.1])
    lo = np.array([99.1, 99.0])
    cl = np.array([99.5, 99.2])                # -0.8 on a 2.0 risk
    _, outcome, r = _walk_barriers(hi, lo, cl, 100.0, 103.0, 98.0, True)
    assert outcome == OUTCOME_TIMEOUT
    assert abs(r - (-0.4)) < 1e-12
    assert r > -1.0


def test_short_side_mirrors():
    hi, lo, cl = np.array([100.5]), np.array([96.5]), np.array([97.0])
    label, outcome, r = _walk_barriers(hi, lo, cl, 100.0, 97.0, 101.0, False)
    assert (label, outcome) == (1, OUTCOME_TARGET)
    assert abs(r - 3.0) < 1e-12                # reward 3 / risk 1


def test_swing_labels_emit_r_and_outcome_columns():
    n = 40
    d = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="D", tz="Asia/Kolkata"),
        "open": np.linspace(100, 110, n), "high": np.linspace(101, 111, n),
        "low": np.linspace(99, 109, n), "close": np.linspace(100, 110, n),
        "volume": np.full(n, 1e6)})
    out = swing_labels(d)
    assert {"label_long", "r_long", "outcome_long", "mask_locked"} <= set(out.columns)
    seen = set(pd.Series(out["outcome_long"]).dropna().unique())
    assert seen and seen <= {OUTCOME_TARGET, OUTCOME_STOP, OUTCOME_TIMEOUT}
    # every labelled row carries a realised R
    assert out.loc[out["label_long"].notna(), "r_long"].notna().all()


# ---------- calibration guards ----------
def _raw_y(n=4000, seed=0):
    rng = np.random.default_rng(seed)
    raw = rng.uniform(0.1, 0.9, n)
    y = (raw + rng.normal(0, 0.3, n) > 0.5).astype(float)
    return raw, y


def test_degenerate_bucket_is_rejected_and_reported_not_served():
    """A bucket whose ceiling rests on a few all-winning rows must not become
    the map that the emission gate reads."""
    raw, y = _raw_y()
    # healthy bucket: a broad top block, so the POOLED fallback is fine and
    # only the per-bucket guard is under test here
    good_raw = np.concatenate([raw, np.full(250, 0.99)])
    good_y = np.concatenate([y, np.ones(250)])
    good_b = np.array(["lv_bp"] * len(good_raw), dtype=object)
    # degenerate bucket: passes MIN_BUCKET_N on total size, but its ceiling
    # rests on a 5-row all-winning sliver
    bad_raw = np.concatenate([np.full(700, 0.2), np.full(5, 0.99)])
    bad_y = np.concatenate([np.zeros(700), np.ones(5)])
    bad_b = np.array(["hv_bn"] * len(bad_raw), dtype=object)

    raw2 = np.concatenate([good_raw, bad_raw])
    y2 = np.concatenate([good_y, bad_y])
    b2 = np.concatenate([good_b, bad_b])

    cal = RegimeCalibrator().fit(raw2, y2, b2)
    assert "hv_bn" in cal.rejected
    assert "hv_bn" not in cal.buckets
    assert "sample" in cal.rejected["hv_bn"]
    # a rejected bucket routes to the pooled map instead of its own — the
    # 5-row sliver never gets to set a ceiling the gate would read
    probe = [0.2, 0.5, raw2.max()]
    np.testing.assert_allclose(cal.transform(probe, "hv_bn"),
                               cal.fallback.transform(np.asarray(probe, float)))
    # the healthy bucket keeps its own map
    assert "lv_bp" in cal.buckets


def test_degenerate_pooled_fallback_raises_rather_than_being_served():
    raw = np.concatenate([np.full(600, 0.2), np.full(3, 0.99)])
    y = np.concatenate([np.zeros(600), np.ones(3)])
    b = np.array(["lv_bp"] * len(raw), dtype=object)
    with pytest.raises(DegenerateCalibration):
        RegimeCalibrator().fit(raw, y, b)


def test_top_block_n_counts_the_ceiling_evidence():
    from src.models.calibrate import fit_isotonic
    raw = np.concatenate([np.full(500, 0.2), np.full(7, 0.95)])
    y = np.concatenate([np.zeros(500), np.ones(7)])
    iso = fit_isotonic(raw, y)
    assert top_block_n(iso, raw) == 7
    assert top_block_n(iso, raw) < MIN_TOP_BLOCK_N


def test_in_sample_curve_is_tautological_and_oos_curve_is_not():
    """Why §6.2's promise needed fixing: the in-sample curve returns x == y
    by construction, so it can never reveal a miscalibrated model."""
    from src.models.calibrate import fit_isotonic
    raw, y = _raw_y(6000, seed=3)
    iso = fit_isotonic(raw, y)
    same = calibration_curve_points(iso.transform(raw), y)
    assert same and all(abs(a - b) < 1e-9 for a, b in same)

    cut = int(len(raw) * 0.7)
    oos = calibration_curve_oos(raw[:cut], y[:cut], raw[cut:], y[cut:])
    assert oos, "an out-of-sample curve should be computable at this size"
    # it is FREE to depart from the diagonal — that is the entire point
    assert any(abs(a - b) > 1e-9 for a, b in oos)


def test_oos_curve_reports_absence_rather_than_a_flattering_substitute():
    raw, y = _raw_y(100)
    assert calibration_curve_oos(raw[:70], y[:70], raw[70:], y[70:]) == []
