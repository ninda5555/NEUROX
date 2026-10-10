"""Behavioral regressions from the October 2026 repository review."""
import datetime as dt
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src import db as dbm
from src.config import ConfigError, load_config
from src.data.store import CandleStore
from src.fyers import auth
from src.models.cv import ece
from src.risk.sizing import size_position
from src.signals import screener
from src.timeutil import IST


def test_paths_with_quotes_and_windows_separators_roundtrip(tmp_path):
    store = CandleStore(tmp_path / "user's data")
    row = {"ts": IST.localize(dt.datetime(2026, 10, 9, 9, 15)),
           "open": 100, "high": 101, "low": 99, "close": 100, "volume": 500}
    store.write_candles("1d", "NSE:ABC-EQ", [row])
    directory = tmp_path / "user's data" / "1d" / "ABC-EQ"
    (directory / "2099-01.tmp.parquet").write_bytes(b"incomplete write")
    assert len(store.read_candles("1d", "NSE:ABC-EQ")) == 1
    assert store.last_ts("1d", "NSE:ABC-EQ").date() == dt.date(2026, 10, 9)


@pytest.mark.parametrize("symbol", ["../escape", "NSE:..", "NSE:A/B", "NSE:A\\B", ""])
def test_candle_paths_cannot_escape_root(tmp_path, symbol):
    with pytest.raises(ValueError):
        CandleStore(tmp_path).read_candles("1d", symbol)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 0])
def test_invalid_capital_never_produces_a_position(bad):
    assert size_position(capital=bad, risk_pct=1, entry=100, stop=90) == (0, False)


@pytest.mark.parametrize("yaml", ["risk: {capital: 0}", "risk: {capital: .nan}",
    "fyers: {auto_login: 'false'}", "signals: {scanner_top_n: -2}",
    "training: {retrain_schedule: typo}", "costs: {per_side_pct: -1}"])
def test_bad_configuration_fails_before_startup(tmp_path, yaml):
    path = tmp_path / "config.yaml"
    path.write_text(yaml, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("payload", ["{broken", "[]", "null"])
def test_corrupt_token_prompts_reauth_instead_of_crashing(cfg, payload):
    path = cfg.path("paths.tokens") / auth.TOKEN_FILE
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding="utf-8")
    assert auth.token_status(cfg)["valid"] is False
    with pytest.raises(auth.NeedsReauth):
        auth.get_valid_token(cfg)


def test_constant_predictions_have_measurable_calibration_error():
    assert ece(np.array([0.6, 0.6]), np.array([0, 1])) == pytest.approx(0.1)


def test_scoped_connections_close_on_exception(tmp_path):
    connection = None
    with pytest.raises(RuntimeError):
        with dbm.connection_scope():
            connection = dbm.connect(tmp_path / "test.db")
            raise RuntimeError("failed request")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_default_scheduler_does_not_retrain(cfg):
    from src.jobs.scheduler import build_scheduler
    jobs = build_scheduler(cfg).get_jobs()
    assert not any("retrain" in job.func.__name__ for job in jobs)


def test_missing_journal_is_not_backed_up_as_empty_database(tmp_path):
    from src.jobs.backup import backup_sqlite
    source = tmp_path / "missing.db"
    result = backup_sqlite(source, tmp_path / "backups")
    assert result["status"] == "error"
    assert not source.exists()
    assert not list(tmp_path.glob("backups/*.gz"))


@pytest.fixture
def small_screen(conn, cfg, monkeypatch):
    now = IST.localize(dt.datetime(2026, 10, 9, 17))
    monkeypatch.setattr(screener, "now_ist", lambda: now)
    symbols = [f"NSE:T{i:03}-EQ" for i in range(35)]
    frames = {}
    for i, symbol in enumerate(symbols + ["NSE:NIFTY50-INDEX"]):
        frames[symbol] = pd.DataFrame({
            "ts": pd.date_range(end=now.replace(hour=9, minute=15), periods=220, freq="D"),
            "close": 100.0 + i, "atr14_pct": 2.0,
            **{factor: float(i) for factor in screener.FACTORS}})
    class Store:
        def read_candles(self, tf, symbol):
            return frames[symbol]
    monkeypatch.setattr(screener.swing_mod, "build_symbol_frame", lambda d, n: d)
    return conn, Store(), cfg, symbols, frames


def test_screen_uses_one_session_and_unique_symbols(small_screen):
    conn, store, cfg, symbols, frames = small_screen
    for symbol in symbols[-5:]:
        frames[symbol] = frames[symbol].iloc[:-1]
    result = screener.build_screen(conn, store, cfg, symbols + symbols, top_n=100)
    assert result["n_screened"] == 30
    assert result["n_skipped"] == 5
    assert {row["bar_date"] for row in result["rows"]} == {"2026-10-09"}


def test_screen_requires_enough_comparable_names(small_screen):
    conn, store, cfg, symbols, frames = small_screen
    for symbol in symbols[:10]:
        for factor in screener.FACTORS:
            frames[symbol][factor] = np.inf
    result = screener.build_screen(conn, store, cfg, symbols)
    assert result["rows"] == []
    assert result["empty_reason"]


def test_screen_excludes_current_unfinished_daily_bar(small_screen, monkeypatch):
    conn, store, cfg, symbols, _ = small_screen
    monkeypatch.setattr(screener, "now_ist", lambda: IST.localize(dt.datetime(2026, 10, 9, 12)))
    result = screener.build_screen(conn, store, cfg, symbols)
    assert result["asof"] == "2026-10-08"


def test_invalid_screen_parameters_rejected_by_api(tmp_path, monkeypatch):
    import src.api.app as appmod
    monkeypatch.setattr(appmod, "DB_PATH", str(tmp_path / "api.db"))
    client = TestClient(appmod.app)
    for query in ("top_n=0", "top_n=-1", "top_n=101", "refresh=2"):
        assert client.get("/api/screen?" + query).status_code == 422
    empty = client.get("/api/screen").json()
    assert empty["stale"] is True and "disclosure" in empty


def test_denied_responses_have_security_headers(tmp_path, monkeypatch):
    import src.api.app as appmod
    monkeypatch.setitem(appmod.cfg._data["security"], "require_tailscale_identity", True)
    response = TestClient(appmod.app).get("/api/status")
    assert response.status_code == 403
    assert response.headers["x-content-type-options"] == "nosniff"


def test_screen_cache_tracks_universe_membership(tmp_path, monkeypatch):
    import src.api.app as appmod
    import src.universe.master as master
    monkeypatch.setattr(appmod, "DB_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setattr(appmod, "_SCREEN_CACHE", {"key": None, "at": None, "payload": None})
    names = ["NSE:ONE-EQ"]
    monkeypatch.setattr(master, "latest_included_symbols", lambda c: list(names))
    called = []
    def build(c, store, cfg, symbols, top_n):
        called.append(symbols)
        return {"mode": "SWING", "rows": [], "asof": "2026-10-09", "n_screened": 1}
    monkeypatch.setattr(screener, "build_screen", build)
    client = TestClient(appmod.app)
    assert client.get("/api/screen").json()["cached"] is False
    assert client.get("/api/screen").json()["cached"] is True
    names[:] = ["NSE:TWO-EQ"]
    assert client.get("/api/screen").json()["cached"] is False
    assert len(called) == 2


@pytest.mark.parametrize("precision", [None, float("nan"), float("inf")])
def test_first_model_with_no_valid_precision_is_not_promoted(conn, precision):
    from src.models.registry import promote_if_better
    conn.execute("INSERT INTO models (model_id,mode,trained_at,feature_list,lgbm_params,"
                 "calibration,cv_report,artifact_path) VALUES (?,?,?,?,?,?,?,?)",
                 ("candidate", "SWING", "now", "[]", "{}", "{}",
                  json.dumps({"precision_mean": precision}), "unused"))
    conn.commit()
    assert promote_if_better(conn, "candidate")["decision"] == "held"
    assert not conn.execute("SELECT 1 FROM models WHERE is_active=1").fetchone()


def test_no_model_is_daily_screener_mode(tmp_path, monkeypatch):
    import src.api.app as appmod
    monkeypatch.setattr(appmod, "DB_PATH", str(tmp_path / "status.db"))
    response = TestClient(appmod.app).get("/api/status").json()
    assert response["feed"]["label"] == "Daily screener"
