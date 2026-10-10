import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from src import db as dbm
from src.data.store import CandleStore
from src.jobs import monitor
from src.journal import outcomes
from src.journal.validation import forward_report, wilson
from src.timeutil import IST


def moment(day=5, hour=10, minute=0):
    return IST.localize(dt.datetime(2026, 10, day, hour, minute))


def candle(ts, high=101, low=99, opening=100):
    return dict(ts=ts, open=opening, high=high, low=low, close=100, volume=1000)


def sig(mode="INTRADAY"):
    return dict(ts=moment().isoformat(), mode=mode, symbol="NSE:T-EQ",
                direction=1, entry=100, stop_loss=98, target=104)


def test_intraday_waits_for_deadline_and_complete_candle(tmp_path, monkeypatch):
    store = CandleStore(tmp_path)
    store.write_candles("5min", "NSE:T-EQ", [candle(moment(minute=5))])
    monkeypatch.setattr(outcomes, "now_ist", lambda: moment(minute=20))
    assert outcomes.evaluate_signal_horizon(sig(), "30m", store) is None
    store.write_candles("5min", "NSE:T-EQ", [candle(moment(minute=25), high=105)])
    assert outcomes.evaluate_signal_horizon(sig(), "30m", store) is None
    monkeypatch.setattr(outcomes, "now_ist", lambda: moment(minute=30))
    assert outcomes.evaluate_signal_horizon(sig(), "30m", store)["status"] == "hit_target"


def test_no_expiry_with_missing_terminal_data(tmp_path, monkeypatch):
    store = CandleStore(tmp_path)
    store.write_candles("5min", "NSE:T-EQ", [candle(moment(minute=5))])
    monkeypatch.setattr(outcomes, "now_ist", lambda: moment(hour=16))
    assert outcomes.evaluate_signal_horizon(sig(), "eod", store) is None
    store.write_candles("5min", "NSE:T-EQ", [candle(moment(hour=15, minute=10))])
    assert outcomes.evaluate_signal_horizon(sig(), "eod", store)["status"] == "expired"


def test_gap_stop_and_excursions_stop_at_exit(tmp_path, monkeypatch):
    store = CandleStore(tmp_path)
    store.write_candles("5min", "NSE:T-EQ", [candle(moment(minute=5), high=97, low=95, opening=96),
                                              candle(moment(minute=10), high=150, low=80)])
    monkeypatch.setattr(outcomes, "now_ist", lambda: moment(hour=16))
    result = outcomes.evaluate_signal_horizon(sig(), "eod", store)
    assert result["price"] == 96
    assert result["mae_pct"] == pytest.approx(-5)
    assert result["mfe_pct"] == pytest.approx(-3)


def test_swing_waits_for_full_sessions(tmp_path, monkeypatch):
    store = CandleStore(tmp_path)
    store.write_candles("1d", "NSE:T-EQ", [candle(moment(day=6, hour=0))])
    monkeypatch.setattr(outcomes, "now_ist", lambda: moment(day=6, hour=12))
    assert outcomes.evaluate_signal_horizon(sig("SWING"), "1d", store) is None
    monkeypatch.setattr(outcomes, "now_ist", lambda: moment(day=6, hour=16))
    assert outcomes.evaluate_signal_horizon(sig("SWING"), "1d", store)["status"] == "expired"
    assert outcomes.evaluate_signal_horizon(sig("SWING"), "5d", store) is None


def test_observer_without_credentials_does_not_contact_broker(cfg, monkeypatch):
    monkeypatch.setattr(monitor, "load_config", lambda: cfg)
    monkeypatch.setattr(monitor, "FyersClient", lambda *a, **k: pytest.fail("must not contact broker"))
    monitor.observe()
    with dbm.connection_scope():
        c = dbm.connect(cfg.path("paths.db"))
        assert json.loads(dbm.get_state(c, "observation"))["state"] == "setup_needed"


def test_collects_indexes_without_any_model(cfg, monkeypatch):
    cfg._data["fyers"].update(app_id="test", secret_key="test")
    monkeypatch.setattr(monitor, "load_config", lambda: cfg)
    monkeypatch.setattr(monitor, "now_ist", lambda: moment())
    monkeypatch.setattr(monitor.auth, "get_valid_token", lambda c: "test")
    monkeypatch.setattr(monitor, "FyersClient", lambda *a, **k: object())
    def backfill(client, store, symbols, tf, days):
        assert symbols == monitor.INDEXES
        for s in symbols:
            store.write_candles(tf, s, [candle(moment(hour=9, minute=50))])
        return {"symbols_failed": 0}
    monkeypatch.setattr(monitor, "backfill_many", backfill)
    monitor.observe()
    with dbm.connection_scope():
        c = dbm.connect(cfg.path("paths.db"))
        state = json.loads(dbm.get_state(c, "observation"))
        assert state["state"] == "observing"
        assert state["fresh_symbols"] == 3
        assert not state["universe_ready"]


def test_old_bars_are_not_reported_as_live(cfg, monkeypatch):
    cfg._data["fyers"].update(app_id="test", secret_key="test")
    monkeypatch.setattr(monitor, "load_config", lambda: cfg)
    monkeypatch.setattr(monitor, "now_ist", lambda: moment())
    monkeypatch.setattr(monitor.auth, "get_valid_token", lambda c: "test")
    monkeypatch.setattr(monitor, "FyersClient", lambda *a, **k: object())
    monkeypatch.setattr(monitor, "backfill_many", lambda *a, **k: {"symbols_failed": 0})
    monitor.observe()
    with dbm.connection_scope():
        c = dbm.connect(cfg.path("paths.db"))
        state = json.loads(dbm.get_state(c, "observation"))
        assert state["state"] == "stale_or_partial"
        assert state["fresh_symbols"] == 0


def test_forward_report_denominator_costs_and_duplicates(conn):
    conn.execute("""INSERT INTO models (model_id, mode, trained_at, feature_list,
        lgbm_params, calibration, cv_report, artifact_path)
        VALUES ('m','INTRADAY','2026-10-01T10:00:00+05:30','[]','{}','{}','{}','x')""")
    for i, (reason, pnl) in enumerate([("target", 200), ("target", -10), ("squareoff", 5), (None, None)]):
        sid = conn.execute("""INSERT INTO signals (ts, mode, symbol, direction, confidence,
            model_id, features, shap_top, explanation)
            VALUES (?, 'INTRADAY','NSE:T-EQ',1,.8,'m','{}','[]','test')""", (moment(minute=i).isoformat(),)).lastrowid
        conn.execute("INSERT INTO paper_trades (signal_id, closed_at, pnl, exit_reason) VALUES (?,?,?,?)",
                     (sid, moment(hour=16).isoformat() if reason else None, pnl, reason))
        if i == 0:
            conn.execute("INSERT INTO paper_trades (signal_id, closed_at, pnl, exit_reason) VALUES (?,?,200,'target')",
                         (sid, moment(hour=16).isoformat()))
    report = forward_report(conn, moment(hour=9).isoformat())
    r = report["models"][0]
    assert (r["resolved"], r["pending"], r["successes"]) == (3, 1, 1)
    assert r["success_rate"] == pytest.approx(1 / 3)
    assert r["net_pnl"] == 195
    assert report["target_status"] == "unverified"
    assert forward_report(conn, moment(day=6).isoformat())["models"] == []
    assert wilson(0, 0) is None
    assert wilson(7, 10)[0] < .70


def test_monitor_api_detects_dead_scheduler_and_no_secrets(cfg, monkeypatch):
    from src.api import app as api
    monkeypatch.setattr(api, "DB_PATH", str(cfg.path("paths.db")))
    monkeypatch.setattr(api, "load_config", lambda: cfg)
    with TestClient(api.app) as client:
        response = client.get("/api/monitor")
    assert response.status_code == 200
    data = response.json()
    assert not data["scheduler"]["running"]
    assert not data["broker_configured"]
    assert data["validation"]["models"] == []
    assert "secret_key" not in response.text


def test_scheduler_keeps_monitor_independent(cfg):
    from src.jobs.scheduler import build_scheduler
    s = build_scheduler(cfg)
    jobs = {j.id: j for j in s.get_jobs()}
    assert jobs["liveness"].executor == "pulse"
    assert jobs["observation"].executor == "default"
