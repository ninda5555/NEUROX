"""Factor screener — the honest replacement for the prediction engine.

Two properties matter more than anything else here:
  1. it never emits a probability, however the code is refactored;
  2. the reasons shown on a row belong to THAT row. A sort-then-lookup bug
     paired every stock with another stock's reasons during development,
     which is exactly the sort of quiet mismatch this tool exists to avoid.
"""

import numpy as np
import pandas as pd
import pytest

from src import db as dbm
from src.config import load_config
from src.data.store import CandleStore
from src.signals.screener import FACTORS, build_screen, screen_is_stale
from src.timeutil import IST, now_ist

BANNED_KEYS = ("confidence", "probability", "accuracy", "win_rate", "chance")


@pytest.fixture
def world(tmp_path):
    cfg = load_config()
    conn = dbm.connect(tmp_path / "app.db")
    dbm.init_db(conn)
    store = CandleStore(tmp_path / "candles")
    rng = np.random.default_rng(11)
    end = pd.Timestamp(now_ist().date(), tz=IST)
    days = pd.bdate_range(end - pd.Timedelta(days=520), end, tz=IST)
    today = days[-1].date().isoformat()
    syms = [f"NSE:T{i:03d}-EQ" for i in range(45)]
    for i, s in enumerate(syms + ["NSE:NIFTY50-INDEX"]):
        r = rng.normal(rng.normal(0.0004, 0.0005), 0.015, len(days))
        close = 500 * np.exp(np.cumsum(r))
        # volume must VARY — a constant series makes vol_zscore undefined
        # (zero rolling std), which is a fixture artifact, not real market data
        vol = rng.lognormal(13.5, 0.4, len(days))
        store.write_candles("1d", s, [
            {"ts": t, "open": float(c), "high": float(c * 1.006),
             "low": float(c * 0.994), "close": float(c), "volume": float(v)}
            for t, c, v in zip(days, close, vol)])
    for i, s in enumerate(syms):
        conn.execute(
            "INSERT OR REPLACE INTO instruments (fytoken,symbol,nse_code,series,"
            "name,sector,first_seen,last_seen) VALUES (?,?,?,'EQ',?,?,?,?)",
            (f"t{i}", s, s.split(":")[-1].replace("-EQ", ""), s, "IT", today, today))
    conn.commit()
    return conn, store, cfg, syms


def test_screen_ranks_and_returns_tradeable_levels(world):
    conn, store, cfg, syms = world
    out = build_screen(conn, store, cfg, syms, top_n=10)
    assert out["empty_reason"] is None
    assert out["mode"] == "SWING"
    assert len(out["rows"]) == 10
    ranks = [r["rank"] for r in out["rows"]]
    assert ranks == sorted(ranks) == list(range(1, 11))
    scores = [r["score_pct"] for r in out["rows"]]
    assert scores == sorted(scores, reverse=True), "rows must be ordered by score"
    for r in out["rows"]:
        assert r["stop"] < r["entry"] < r["target"], "swing is long-only"
        assert r["risk_per_share"] > 0 and r["reward_risk"] > 0
        assert r["qty"] >= 0 and r["at_risk"] >= 0


def test_never_reports_a_probability(world):
    """The whole point of Option A. No confidence number, anywhere, ever."""
    conn, store, cfg, syms = world
    out = build_screen(conn, store, cfg, syms, top_n=5)
    blob = repr(out).lower()
    for k in BANNED_KEYS:
        assert k not in blob, f"screener output leaked a {k!r} field"
    # score_pct is a rank position, and must be reported against a denominator
    for r in out["rows"]:
        assert 0.0 <= r["score_pct"] <= 100.0
        assert r["percentile_of"] >= len(out["rows"])


def test_drivers_belong_to_their_own_row(world):
    """Regression: percentiles were read from a pre-sort frame after
    reset_index, so each card showed another stock's reasons."""
    conn, store, cfg, syms = world
    out = build_screen(conn, store, cfg, syms, top_n=8)
    for r in out["rows"]:
        pcts = [d["percentile"] for d in r["all_drivers"]]
        # score IS the mean of the signed factor percentiles, by definition
        assert abs(np.mean(pcts) - r["score_pct"]) < 0.6, (
            f"{r['nse_code']}: drivers {np.mean(pcts):.1f} vs score {r['score_pct']}")
    # and the top-ranked name must genuinely look better than the last shown
    assert (np.mean([d["percentile"] for d in out["rows"][0]["all_drivers"]])
            > np.mean([d["percentile"] for d in out["rows"][-1]["all_drivers"]]))


def test_every_driver_sentence_matches_its_percentile_side(world):
    conn, store, cfg, syms = world
    out = build_screen(conn, store, cfg, syms, top_n=6)
    from src.signals.screener import _PHRASE
    for r in out["rows"]:
        for d in r["all_drivers"]:
            hi, lo = _PHRASE[d["factor"]]
            assert d["sentence"] == (hi if d["percentile"] >= 50 else lo)


def test_thin_universe_explains_itself_rather_than_ranking_noise(world):
    conn, store, cfg, syms = world
    out = build_screen(conn, store, cfg, syms[:5], top_n=10)
    assert out["rows"] == []
    assert "history" in out["empty_reason"]
    assert "data problem" in out["empty_reason"]


def test_all_seven_measured_factors_are_used(world):
    conn, store, cfg, syms = world
    out = build_screen(conn, store, cfg, syms, top_n=3)
    used = {d["factor"] for r in out["rows"] for d in r["all_drivers"]}
    assert used == set(FACTORS), f"missing factors: {set(FACTORS) - used}"


def test_staleness_is_flagged():
    today = now_ist().date().isoformat()
    assert screen_is_stale(None)
    assert not screen_is_stale(today)
    old = (now_ist().date() - pd.Timedelta(days=30)).isoformat()
    assert screen_is_stale(old)
