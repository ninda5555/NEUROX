import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from src.config import Config, DEFAULTS
from src.data.store import CandleStore
from src.journal.journal import insert_signal
from src.journal.outcomes import evaluate_pending, evaluate_signal_horizon
from src.journal.paper import open_paper_trade, settle_paper_trades
from src.models.train import train_lgbm
from src.risk.loss_limit import DayRiskTracker
from src.risk.sizing import size_position
from src.risk.stops import intraday_stop, swing_stop, target_for
from src.signals.engine import Candidate, emit
from src.signals.scanner import liquidity_weight, rank
from src.timeutil import IST
import copy


def ist(y, mo, d, h, mi=0):
    return IST.localize(dt.datetime(y, mo, d, h, mi))


# ---------- risk ----------
def test_sizing_vol_targeted_and_capped():
    # 1% of 10L = ₹10k risk; stop distance 2 -> 5000 shares; but 5000×100 =
    # ₹5L notional > 20% cap (₹2L) -> capped to 2000 shares
    qty, capped = size_position(capital=1_000_000, risk_pct=1.0, entry=100, stop=98)
    assert qty == 2000 and capped
    # wider stop -> smaller size, cap not binding: 10k/8 = 1250 (₹1.25L)
    qty2, capped2 = size_position(capital=1_000_000, risk_pct=1.0, entry=100, stop=92)
    assert qty2 == 1250 and not capped2


def test_sizing_notional_cap_applies():
    qty, capped = size_position(capital=1_000_000, risk_pct=1.0, entry=100, stop=99.5)
    assert capped and qty == 2000              # cap 200k/100


def test_stops_snap_beyond_orb_and_target_rr():
    stop = intraday_stop(100.0, +1, atr5=1.0, orb_low=99.2, orb_high=None)
    assert stop < 99.2                          # snapped beyond structure
    t = target_for(100.0, stop)
    assert t == pytest.approx(100 + 2 * (100 - stop))
    assert swing_stop(100.0, 2.0) == 95.0


def test_loss_limit_states(conn):
    tr = DayRiskTracker(conn, capital=1_000_000, limit_pct=3.0)
    assert tr.status()["state"] == "ok" and tr.allows_new_intraday()
    tr.record_pnl(-23_000)
    assert tr.status()["state"] == "warning"
    tr.record_pnl(-8_000)
    assert tr.status()["state"] == "halted" and not tr.allows_new_intraday()
    tr2 = DayRiskTracker(conn, capital=1_000_000)   # restart survives
    assert tr2.status()["state"] == "halted"


# ---------- scanner ----------
def test_rank_confidence_times_liquidity_top_n():
    cands = [Candidate(f"NSE:S{i}-EQ", "INTRADAY", 1, 0.5 + i / 100, 100, 1, {})
             for i in range(20)]
    turn = {c.symbol: 10.0 for c in cands}
    turn["NSE:S0-EQ"] = 2000.0                  # low conf, huge liquidity
    top = rank(cands, turn, top_n=12)
    assert len(top) == 12                        # never dumps the universe
    assert top[0].symbol in ("NSE:S19-EQ", "NSE:S0-EQ")
    assert liquidity_weight(2000) == 1.0 and liquidity_weight(None) < 0.02


# ---------- engine: gate + journal-first ----------
def _fix(conn, sig_kw=None):
    row = conn.execute("SELECT COUNT(*) n FROM signals").fetchone()
    return row["n"]


@pytest.fixture
def cfg_t(tmp_path):
    return Config(copy.deepcopy(DEFAULTS), root=tmp_path)


@pytest.fixture
def booster2():
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"f1": rng.normal(size=3000), "direction": rng.choice([1.0, -1.0], 3000)})
    y = (X["f1"] > 0).astype(float)
    b, _ = train_lgbm(X[:2500], y[:2500], X[2500:], y[2500:], ["f1", "direction"])
    return b


def test_emit_gate_and_journal_first(conn, cfg_t, tmp_path, booster2):
    conn.execute("INSERT INTO models (model_id, mode, trained_at, feature_list,"
                 "lgbm_params, calibration, cv_report, artifact_path, is_active)"
                 " VALUES ('m1','INTRADAY','x','[]','{}','{}','{}','p',1)")
    store = CandleStore(tmp_path / "c")
    tr = DayRiskTracker(conn, 1_000_000)
    base = dict(symbol="NSE:X-EQ", mode="INTRADAY", direction=1, price=100.0,
                atr=1.0, features={"f1": 2.0, "direction": 1.0})
    low = Candidate(confidence=0.40, **base)
    assert emit(conn, store, cfg_t, model_id="m1", booster=booster2,
                feature_list=["f1", "direction"], candidate=low, regime=None,
                tracker=tr) is None
    assert _fix(conn) == 0                       # refused -> nothing journaled

    ok = Candidate(confidence=0.71, **base)
    card = emit(conn, store, cfg_t, model_id="m1", booster=booster2,
                feature_list=["f1", "direction"], candidate=ok, regime=None,
                tracker=tr)
    assert card is not None and _fix(conn) == 1  # journaled at emission
    row = dict(conn.execute("SELECT * FROM signals").fetchone())
    assert row["confidence"] == 0.71 and json.loads(row["shap_top"])
    assert row["explanation"].startswith("7 of 10 similar setups")
    open_trades = conn.execute("SELECT COUNT(*) n FROM paper_trades").fetchone()["n"]
    assert open_trades == 1                      # paper trade opened

    tr.record_pnl(-40_000)                       # halt -> intraday gate closes
    assert emit(conn, store, cfg_t, model_id="m1", booster=booster2,
                feature_list=["f1", "direction"], candidate=ok, regime=None,
                tracker=tr) is None


# ---------- outcomes + paper settlement ----------
def _seed_signal(conn, ts, entry=100.0, stop=98.0, target=104.0, mode="INTRADAY"):
    cur = conn.execute(
        """INSERT INTO signals (ts, mode, symbol, direction, confidence,
           model_id, features, shap_top, entry, stop_loss, target, qty,
           explanation) VALUES (?,?,?,1,0.7,'m1','{}','[]',?,?,?,100,'e')""",
        (ts, mode, "NSE:T-EQ", entry, stop, target))
    conn.commit()
    return cur.lastrowid


def test_outcome_hit_target_and_paper_costs(conn, tmp_path):
    conn.execute("INSERT INTO models (model_id, mode, trained_at, feature_list,"
                 "lgbm_params, calibration, cv_report, artifact_path)"
                 " VALUES ('m1','INTRADAY','x','[]','{}','{}','{}','p')")
    store = CandleStore(tmp_path)
    t0 = ist(2026, 7, 1, 10, 0)
    bars = []
    px = [100, 101, 102, 103, 104.5, 103.8, 103.2]   # target 104 trades at bar 5
    for i, p in enumerate(px):
        bars.append({"ts": t0 + dt.timedelta(minutes=5 * (i + 1)), "open": p,
                     "high": p + 0.4, "low": p - 0.4, "close": p, "volume": 1e5})
    store.write_candles("5min", "NSE:T-EQ", bars)
    sid = _seed_signal(conn, t0.isoformat(timespec="seconds"))
    res = evaluate_signal_horizon(dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id=?", (sid,)).fetchone()), "eod", store)
    assert res["status"] == "hit_target" and res["r_multiple"] == pytest.approx(2.0)
    assert res["mae_pct"] < 0 < res["mfe_pct"]

    evaluate_pending(conn, store)
    st = conn.execute("SELECT status FROM signal_outcomes WHERE signal_id=? "
                      "AND horizon='eod'", (sid,)).fetchone()["status"]
    assert st == "hit_target"

    open_paper_trade(conn, sid, per_side_pct=0.05)
    n = settle_paper_trades(conn, store, per_side_pct=0.05)
    assert n == 1
    t = dict(conn.execute("SELECT * FROM paper_trades").fetchone())
    assert t["exit_reason"] == "target" and t["costs_modeled"] > 0
    gross = (104.0 - 100.0) * 100
    assert 0 < t["pnl"] < gross                   # costs+slippage < gross


def test_outcome_hit_stop(conn, tmp_path):
    conn.execute("INSERT INTO models (model_id, mode, trained_at, feature_list,"
                 "lgbm_params, calibration, cv_report, artifact_path)"
                 " VALUES ('m1','INTRADAY','x','[]','{}','{}','{}','p')")
    store = CandleStore(tmp_path)
    t0 = ist(2026, 7, 1, 10, 0)
    bars = [{"ts": t0 + dt.timedelta(minutes=5), "open": 99, "high": 99.5,
             "low": 97.8, "close": 98.2, "volume": 1e5}]
    store.write_candles("5min", "NSE:T-EQ", bars)
    sid = _seed_signal(conn, t0.isoformat(timespec="seconds"))
    res = evaluate_signal_horizon(dict(conn.execute(
        "SELECT * FROM signals WHERE signal_id=?", (sid,)).fetchone()), "eod", store)
    assert res["status"] == "hit_stop" and res["r_multiple"] == pytest.approx(-1.0)
