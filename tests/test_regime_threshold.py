"""T12 per-bucket regime threshold tightening (default OFF at all-zero).
The gate stays journal-first: a bucket-tightened refusal writes nothing."""

from __future__ import annotations

import copy

import numpy as np
import pandas as pd
import pytest

from src.config import Config, DEFAULTS
from src.data.store import CandleStore
from src.models.train import train_lgbm
from src.risk.loss_limit import DayRiskTracker
from src.signals.engine import Candidate, emit

FEATS = ["f1", "direction"]
HV_BN = {"regime_vix": 22.0, "regime_breadth": -0.4}   # -> bucket hv_bn
LV_BP = {"regime_vix": 11.0, "regime_breadth": 0.4}    # -> bucket lv_bp


@pytest.fixture
def booster():
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"f1": rng.normal(size=3000),
                      "direction": rng.choice([1.0, -1.0], 3000)})
    y = (X["f1"] > 0).astype(float)
    b, _ = train_lgbm(X[:2500], y[:2500], X[2500:], y[2500:], FEATS)
    return b


def _cfg(tmp_path, bumps=None):
    data = copy.deepcopy(DEFAULTS)
    if bumps:
        data["signals"]["regime_threshold_bump"].update(bumps)
    return Config(data, root=tmp_path)


def _emit(conn, cfg, tmp_path, booster, conf, regime_feats):
    conn.execute("INSERT OR IGNORE INTO models (model_id, mode, trained_at, "
                 "feature_list, lgbm_params, calibration, cv_report, "
                 "artifact_path, is_active) "
                 "VALUES ('m1','INTRADAY','x','[]','{}','{}','{}','p',1)")
    cand = Candidate(symbol="NSE:X-EQ", mode="INTRADAY", direction=1,
                     confidence=conf, price=100.0, atr=1.0,
                     features={"f1": 2.0, "direction": 1.0, **regime_feats})
    return emit(conn, CandleStore(tmp_path / "c"), cfg, model_id="m1",
                booster=booster, feature_list=FEATS, candidate=cand,
                regime=None, tracker=DayRiskTracker(conn, 1_000_000))


def _n_signals(conn):
    return conn.execute("SELECT COUNT(*) n FROM signals").fetchone()["n"]


def test_all_zero_bumps_change_nothing(conn, tmp_path, booster):
    cfg = _cfg(tmp_path)
    assert _emit(conn, cfg, tmp_path, booster, 0.61, HV_BN) is not None


def test_bumped_bucket_demands_more(conn, tmp_path, booster):
    cfg = _cfg(tmp_path, bumps={"hv_bn": 0.05})
    # 0.62 clears the base 0.60 but not the tightened 0.65 -> refused,
    # journal-first: nothing persisted
    assert _emit(conn, cfg, tmp_path, booster, 0.62, HV_BN) is None
    assert _n_signals(conn) == 0
    # same confidence in an untightened bucket sails through
    assert _emit(conn, cfg, tmp_path, booster, 0.62, LV_BP) is not None
    assert _n_signals(conn) == 1


def test_emitted_signal_carries_the_tightening_flag(conn, tmp_path, booster):
    cfg = _cfg(tmp_path, bumps={"hv_bn": 0.05})
    card = _emit(conn, cfg, tmp_path, booster, 0.71, HV_BN)
    assert card is not None
    assert any("Regime-tightened" in f and "0.65" in f for f in card["flags"])


def test_status_banner_reports_effective_threshold(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("NEUROX_DB", str(tmp_path / "api.db"))
    import src.api.app as appmod
    importlib.reload(appmod)
    from fastapi.testclient import TestClient
    client = TestClient(appmod.app)
    mt = client.get("/api/status").json()["mode_thresholds"]
    # fresh DB: no regime row -> "unknown" bucket, zero bump, not tightened
    assert mt["current_bucket"] == "unknown"
    assert mt["effective"] == mt["confidence"] and mt["tightened"] is False
    assert set(mt["regime_bumps"]) == {"hv_bn", "hv_bp", "lv_bn", "lv_bp", "unknown"}
