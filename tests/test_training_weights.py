"""T9 sample weights (default OFF): time-decay + swing uniqueness.
With both flags at their defaults, prepare() output is byte-identical to
the pre-T9 behavior — that is the non-disruption guarantee."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.scripts.retrain import prepare


def _intraday_df(n_days=3, per_day=2):
    rows = []
    for d in range(n_days):
        for k in range(per_day):
            rows.append({"ts": pd.Timestamp(f"2026-07-{d + 1:02d}T{10 + k}:00:00+05:30"),
                         "symbol": "NSE:X-EQ", "label_long": 1.0, "label_short": 0.0})
    return pd.DataFrame(rows)


def _swing_df(n=30, symbol="NSE:X-EQ", start=1):
    return pd.DataFrame({
        "ts": [pd.Timestamp(2026, 7, 1) + pd.Timedelta(days=start + i) for i in range(n)],
        "symbol": symbol, "label_long": 1.0})


def test_defaults_change_nothing():
    out_i, _, _ = prepare("INTRADAY", _intraday_df())
    # pre-T9 uniqueness weights intact: 4 rows per (symbol, day) -> 0.25
    assert np.allclose(out_i["_w"], 0.25)
    out_s, _, _ = prepare("SWING", _swing_df())
    assert "_w" not in out_s.columns          # swing had no weights before T9


def test_time_decay_halves_per_half_life():
    df = _swing_df(n=1, start=0)
    df = pd.concat([df, _swing_df(n=1, start=60)], ignore_index=True)  # 60d apart
    out, _, _ = prepare("SWING", df, half_life_days=60)
    w = out.sort_values("ts")["_w"].to_numpy()
    assert w[1] == pytest.approx(1.0)          # newest row: no decay
    assert w[0] == pytest.approx(0.5)          # one half-life older
    out2, _, _ = prepare("SWING", df, half_life_days=30)
    assert out2.sort_values("ts")["_w"].to_numpy()[0] == pytest.approx(0.25)


def test_intraday_decay_composes_with_uniqueness():
    out, _, _ = prepare("INTRADAY", _intraday_df(n_days=2), half_life_days=1)
    by_day = out.groupby(pd.DatetimeIndex(out["ts"]).date)["_w"].mean().sort_index()
    # both days keep the 0.25 uniqueness base; the older day is halved
    assert by_day.iloc[-1] == pytest.approx(0.25, abs=0.06)
    assert by_day.iloc[0] == pytest.approx(0.125, abs=0.03)


def test_swing_uniqueness_downweights_overlap():
    out, _, _ = prepare("SWING", _swing_df(n=30), swing_uniqueness=True)
    w = out.sort_values("ts")["_w"].to_numpy()
    assert w[0] == pytest.approx(1 / 11)       # first row: 10 ahead + itself
    assert w[15] == pytest.approx(1 / 21)      # middle: 10 both sides + itself
    assert w[-1] == pytest.approx(1 / 11)
    single, _, _ = prepare("SWING", _swing_df(n=1), swing_uniqueness=True)
    assert single["_w"].iloc[0] == pytest.approx(1.0)   # no overlap -> full weight


def test_swing_uniqueness_is_per_symbol():
    df = pd.concat([_swing_df(n=30, symbol="NSE:A-EQ"),
                    _swing_df(n=1, symbol="NSE:B-EQ")], ignore_index=True)
    out, _, _ = prepare("SWING", df, swing_uniqueness=True)
    assert out.loc[out["symbol"] == "NSE:B-EQ", "_w"].iloc[0] == pytest.approx(1.0)
