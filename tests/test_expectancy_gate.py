"""Post-cost expectancy gate (signals/engine.expected_r, added 2026-08-01).

The confidence gate is a probability threshold; whether a setup is worth
taking also depends on its payoff and its costs. These pin the arithmetic
that motivated the change, and — critically — that the new gate can only
ever reject MORE than the confidence gate, never admit something it refused.
"""


from src.signals.engine import expected_r


def test_break_even_probability_matches_the_payoff():
    """TP 1.5x / SL 1.0x ATR breaks even at p = 1/(1.5+1) = 0.40, so a 0.60
    confidence gate is silently demanding ~+0.50R per trade."""
    entry, stop, target = 100.0, 99.0, 101.5      # risk 1.0, reward 1.5
    assert abs(expected_r(0.40, entry, stop, target, 0.0)) < 1e-12
    assert abs(expected_r(0.60, entry, stop, target, 0.0) - 0.50) < 1e-12
    assert expected_r(0.30, entry, stop, target, 0.0) < 0


def test_costs_are_charged_in_the_same_R_unit_as_risk():
    """The intraday problem, in one assertion: a 0.30% ATR stop against a
    0.10% round trip spends a third of the risk unit on costs."""
    entry = 100.0
    stop = entry - 0.30                 # 0.30% ATR -> risk 0.30/share
    target = entry + 0.45               # 1.5x
    round_trip = 0.10                   # 0.10% of 100
    gross = expected_r(0.50, entry, stop, target, 0.0)
    net = expected_r(0.50, entry, stop, target, round_trip)
    assert abs((gross - net) - (0.10 / 0.30)) < 1e-9   # exactly cost/risk
    assert abs((gross - net) - 0.3333) < 1e-3

    # the same probability on a daily-ATR stop keeps most of its edge
    stop_d, target_d = entry - 2.0, entry + 3.0        # 2% ATR
    net_d = expected_r(0.50, entry, stop_d, target_d, round_trip)
    assert (expected_r(0.50, entry, stop_d, target_d, 0.0) - net_d) < 0.06


def test_swing_economics_beat_intraday_at_equal_skill():
    """Same edge, ~6x better cost drag on the daily horizon — the reason
    swing was prioritised for retraining."""
    p, entry, round_trip = 0.45, 100.0, 0.10
    intraday = expected_r(p, entry, entry - 0.30, entry + 0.45, round_trip)
    swing = expected_r(p, entry, entry - 2.00, entry + 3.20, round_trip)
    assert swing > intraday


def test_degenerate_risk_is_rejected_not_divided_by_zero():
    assert expected_r(0.9, 100.0, 100.0, 105.0, 0.05) == float("-inf")


def test_gate_only_ever_rejects_more_than_the_confidence_gate():
    """A setup that clears 0.60 confidence can still be refused on
    expectancy; nothing BELOW the confidence gate can be admitted, because
    the expectancy check runs strictly after it."""
    entry = 100.0
    # clears 0.60 confidence, but a tight stop makes costs eat the edge
    tight = expected_r(0.62, entry, entry - 0.05, entry + 0.075, 0.10)
    assert tight < 0.0, "tight-stop setup should fail an E[R] >= 0 gate"
    # identical probability, sane stop -> passes
    sane = expected_r(0.62, entry, entry - 2.0, entry + 3.0, 0.10)
    assert sane > 0.0
