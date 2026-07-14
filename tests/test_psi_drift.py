"""PSI feature-drift monitor (T7): featstats baseline at training time,
PSI vs the live score log, red flag in app_state — never raises."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from src import db as dbm
from src.journal.scorelog import log_scores
from src.models.drift import (compute_featstats, featstats_path, psi,
                              psi_check, _bin_props)


def test_psi_zero_for_identical_and_large_for_shifted():
    rng = np.random.default_rng(1)
    base = rng.normal(0, 1, 20_000)
    stats = compute_featstats(pd.DataFrame({"f": base}), ["f"])["f"]
    same = _bin_props(rng.normal(0, 1, 20_000), stats["edges"])
    shifted = _bin_props(rng.normal(2.5, 1, 20_000), stats["edges"])
    assert psi(np.array(stats["props"]), same) < 0.02
    assert psi(np.array(stats["props"]), shifted) > 1.0


def test_featstats_skips_sparse_features():
    df = pd.DataFrame({"good": np.arange(500, dtype=float),
                       "ofi_top": [np.nan] * 500})
    stats = compute_featstats(df, ["good", "ofi_top", "absent"])
    assert "good" in stats and "ofi_top" not in stats and "absent" not in stats
    assert stats["good"]["n"] == 500


def _seed_model_with_baseline(conn, tmp_path, mode="INTRADAY"):
    rng = np.random.default_rng(0)
    train = pd.DataFrame({"rsi_14": rng.normal(50, 10, 5000),
                          "vwap_dist_atr": rng.normal(0, 1, 5000)})
    stats = compute_featstats(train, ["rsi_14", "vwap_dist_atr"])
    artifact = tmp_path / "m1.txt"
    artifact.write_text("stub")
    featstats_path(artifact).write_text(json.dumps(stats))
    conn.execute(
        "INSERT INTO models (model_id, mode, trained_at, feature_list, "
        "lgbm_params, calibration, cv_report, artifact_path, is_active) "
        "VALUES ('m1', ?, 'x', '[]', '{}', '{}', '{}', ?, 1)",
        (mode, str(artifact)))
    conn.commit()
    return rng


def _log_live(scores_root, rsi_mu, n=300):
    rng = np.random.default_rng(7)
    rows = [{"ts": f"2026-07-14T10:{30 + i % 25:02d}:00+05:30",
             "symbol": f"NSE:S{i}-EQ", "mode": "INTRADAY", "model_id": "m1",
             "direction": 1, "score_raw": 0.5, "confidence": 0.5,
             "bucket": "lv_bp", "emitted": 0,
             "rsi_14": float(rng.normal(rsi_mu, 10)),
             "vwap_dist_atr": float(rng.normal(0, 1))} for i in range(n)]
    log_scores(scores_root, "INTRADAY", rows)


def test_psi_check_flags_shifted_feature_and_stores_state(conn, tmp_path):
    _seed_model_with_baseline(conn, tmp_path)
    _log_live(tmp_path / "scores", rsi_mu=80)         # rsi shifted 3 sigma
    res = psi_check(conn, tmp_path / "scores", "INTRADAY")
    assert res["status"] == "ok"
    flagged = {f["feature"] for f in res["flagged"]}
    assert "rsi_14" in flagged and "vwap_dist_atr" not in flagged
    stored = json.loads(dbm.get_state(conn, "psi_INTRADAY"))
    assert stored["flagged"][0]["feature"] == "rsi_14"


def test_psi_check_clean_when_distributions_match(conn, tmp_path):
    _seed_model_with_baseline(conn, tmp_path)
    _log_live(tmp_path / "scores", rsi_mu=50)
    res = psi_check(conn, tmp_path / "scores", "INTRADAY")
    assert res["status"] == "ok" and res["flagged"] == []
    assert res["features"]["rsi_14"] < 0.10


def test_psi_check_degrades_gracefully(conn, tmp_path):
    # no active model
    assert psi_check(conn, tmp_path / "scores", "SWING")["status"] == "no_model"
    # active model but no featstats sidecar (predates T7)
    conn.execute(
        "INSERT INTO models (model_id, mode, trained_at, feature_list, "
        "lgbm_params, calibration, cv_report, artifact_path, is_active) "
        "VALUES ('old', 'SWING', 'x', '[]', '{}', '{}', '{}', ?, 1)",
        (str(tmp_path / "old.txt"),))
    conn.commit()
    assert psi_check(conn, tmp_path / "scores", "SWING")["status"] == "no_baseline"


def test_psi_check_insufficient_live_data(conn, tmp_path):
    _seed_model_with_baseline(conn, tmp_path)
    _log_live(tmp_path / "scores", rsi_mu=50, n=10)   # < MIN_LIVE_ROWS
    res = psi_check(conn, tmp_path / "scores", "INTRADAY")
    assert res["status"] == "insufficient_live_data"
    assert json.loads(dbm.get_state(conn, "psi_INTRADAY"))["n_live"] == 10


def test_psi_check_never_raises(conn, tmp_path, monkeypatch):
    _seed_model_with_baseline(conn, tmp_path)
    monkeypatch.setattr("src.models.drift.read_scores",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert psi_check(conn, tmp_path / "scores", "INTRADAY")["status"] == "error"
