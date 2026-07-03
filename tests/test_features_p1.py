import datetime as dt

import numpy as np
import pandas as pd
import pytest

from src.data.candles import CandleAggregator, Tick, resample_candles
from src.data.feeds import ReplayFeed
from src.data.store import CandleStore
from src.features import shared
from src.features.ic_filter import compute_ic_report
from src.features.labels import intraday_labels, locked_bar_mask, swing_labels
from src.timeutil import IST


def ist(y, mo, d, h, mi=0):
    return IST.localize(dt.datetime(y, mo, d, h, mi))


def daily_frame(closes, base_price=None):
    n = len(closes)
    ts = [ist(2026, 1, 1, 9, 15) + dt.timedelta(days=i) for i in range(n)]
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({"ts": ts, "open": c.shift(1).fillna(c[0]),
                         "high": c * 1.01, "low": c * 0.99, "close": c,
                         "volume": [1e6] * n})


# ---------- indicators ----------
def test_rsi_bounds_and_direction():
    up = shared.rsi(pd.Series(np.linspace(100, 200, 60)))
    down = shared.rsi(pd.Series(np.linspace(200, 100, 60)))
    assert up.iloc[-1] > 95
    assert down.iloc[-1] < 5
    assert up.dropna().between(0, 100).all()


def test_atr_and_gk_positive():
    df = daily_frame(100 + np.cumsum(np.random.default_rng(0).normal(0, 1, 100)))
    assert (shared.atr(df, 14).dropna() > 0).all()
    assert (shared.garman_klass_vol(df).dropna() >= 0).all()


def test_dma_alignment_bull_stack():
    df = daily_frame(np.linspace(100, 300, 260))
    assert shared.dma_alignment(df["close"]).iloc[-1] == 1.0


# ---------- masks & labels ----------
def test_locked_bar_mask():
    df = daily_frame([100, 100, 100])
    df.loc[1, ["open", "high", "low", "close"]] = 105.0  # high == low -> locked
    assert locked_bar_mask(df).tolist() == [False, True, False]


def bars_5min(day, prices, start_h=9, start_m=15):
    ts = [ist(2026, 1, day, start_h, start_m) + dt.timedelta(minutes=5 * i)
          for i in range(len(prices))]
    p = pd.Series(prices, dtype=float)
    return pd.DataFrame({"ts": ts, "open": p, "high": p + 0.5, "low": p - 0.5,
                         "close": p, "volume": [1000.0] * len(p)})


def test_intraday_triple_barrier_tp_and_sl():
    # flat warm-up (ATR -> ~1.0 range-based), then a strong ramp: early
    # entries inside the window should reach TP before SL -> label 1
    prices = [100] * 12 + list(np.linspace(100, 130, 40))
    df = bars_5min(2, prices)
    lab = intraday_labels(df)
    entry_rows = lab["label_long"].dropna()
    assert len(entry_rows) > 0
    assert entry_rows.iloc[0] == 1.0          # long works on a ramp
    lab_short = lab["label_short"].dropna()
    assert lab_short.iloc[0] == 0.0           # short stopped on a ramp


def test_intraday_no_entries_outside_window():
    df = bars_5min(2, [100] * 74)  # full session 09:15..15:20
    lab = intraday_labels(df)
    times = pd.DatetimeIndex(df["ts"]).time
    labeled = lab["label_long"].notna()
    assert not labeled[times < dt.time(9, 30)].any()
    assert not labeled[times > dt.time(14, 30)].any()


def test_swing_triple_barrier_paths():
    rng = np.random.default_rng(1)
    flat = list(100 + rng.normal(0, 0.2, 30))
    ramp = list(np.linspace(100, 140, 15))      # strong up -> TP
    crash = list(np.linspace(140, 90, 15))      # strong down -> SL for later entries
    df = daily_frame(flat + ramp + crash)
    lab = swing_labels(df)["label_long"]
    assert lab.iloc[29] == 1.0                  # entry before the ramp hits TP
    assert lab.iloc[43] == 0.0                  # entry at the top gets stopped


def test_swing_locked_entry_day_masked():
    closes = [100.0] * 40
    df = daily_frame(closes)
    df.loc[21, ["open", "high", "low", "close"]] = 120.0   # locked day
    lab = swing_labels(df)
    assert np.isnan(lab["label_long"].iloc[20])  # entry day (21) locked -> NaN


# ---------- tick aggregation ----------
def test_aggregator_ticks_to_5min():
    agg = CandleAggregator(minutes=5)
    t0 = ist(2026, 1, 2, 9, 15)
    done = []
    seq = [(0, 100.0, 10), (60, 101.0, 25), (240, 99.5, 40),   # bar 1
           (300, 102.0, 55), (540, 103.0, 90)]                 # bar 2
    for offset, ltp, cumv in seq:
        c = agg.on_tick(Tick(t0 + dt.timedelta(seconds=offset), ltp, cumv))
        if c:
            done.append(c)
    done.append(agg.flush())
    assert len(done) == 2
    b1, b2 = done
    assert (b1.open, b1.high, b1.low, b1.close, b1.volume) == (100.0, 101.0, 99.5, 99.5, 40)
    assert b1.ts == t0
    assert (b2.open, b2.close, b2.volume) == (102.0, 103.0, 50)  # cum 90-40


def test_aggregator_session_volume_reset():
    agg = CandleAggregator(minutes=5)
    agg.on_tick(Tick(ist(2026, 1, 2, 15, 29), 100.0, 5000))
    agg.flush()
    c = None
    c = agg.on_tick(Tick(ist(2026, 1, 3, 9, 16), 101.0, 300))  # next session
    done = agg.flush()
    assert done.volume == 300  # not 300-5000 clamped weirdness


def test_resample_1m_to_5m():
    from src.data.candles import Candle
    ones = [Candle(ist(2026, 1, 2, 9, 15 + i), 100 + i, 101 + i, 99 + i, 100.5 + i, 10)
            for i in range(10)]
    fives = resample_candles(ones, 5)
    assert len(fives) == 2
    assert fives[0].open == 100 and fives[0].close == 104.5 and fives[0].volume == 50


# ---------- replay feed ----------
def test_replay_feed_global_time_order(tmp_path):
    store = CandleStore(tmp_path)
    for sym, minute in (("NSE:A-EQ", 0), ("NSE:B-EQ", 2)):
        rows = [{"ts": ist(2026, 1, 2, 9, 15 + minute + 5 * i), "open": 1.0,
                 "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}
                for i in range(3)]
        store.write_candles("5min", sym, rows)
    seen = []
    ReplayFeed(store, ["NSE:A-EQ", "NSE:B-EQ"]).run(lambda s, c: seen.append((c.ts, s)))
    assert seen == sorted(seen)
    assert len(seen) == 6


# ---------- IC filter ----------
def test_ic_filter_keeps_signal_drops_noise_and_corr():
    rng = np.random.default_rng(7)
    n = 30_000  # large enough that noise IC (~1/sqrt(n)) sits below the floor
    signal = rng.normal(0, 1, n)
    label = (signal + rng.normal(0, 1, n) > 0).astype(float)
    frame = pd.DataFrame({
        "good": signal,
        "good_twin": signal * 1.001 + rng.normal(0, 0.001, n),  # corr ~1 with good
        "noise": rng.normal(0, 1, n),
        "label_long": label,
    })
    rep = compute_ic_report(frame, ["good", "good_twin", "noise"], "label_long", "SWING")
    assert "good" in rep.kept
    assert "noise" in rep.dropped_low_ic
    assert any(d == "good_twin" for d, _ in rep.dropped_corr) or "good_twin" not in rep.kept
