"""Score log (T6): every scored candidate lands in the daily parquet
sidecar; a telemetry failure never breaks a scan."""

from __future__ import annotations

import pandas as pd
import pytest

from src.journal.scorelog import log_scores, read_scores


def _row(sym="NSE:X-EQ", ts="2026-07-14T10:30:00+05:30", conf=0.55, **kw):
    return {"ts": ts, "symbol": sym, "mode": "INTRADAY", "model_id": "m1",
            "direction": 1, "score_raw": 0.61, "confidence": conf,
            "bucket": "lv_bp", "emitted": 0, "rsi_14": 61.2,
            "vwap_dist_atr": 0.8, **kw}


def test_writes_daily_parquet_and_reads_back(tmp_path):
    n = log_scores(tmp_path, "INTRADAY", [_row(), _row(sym="NSE:Y-EQ", conf=0.71,
                                                       emitted=1)])
    assert n == 2
    assert (tmp_path / "INTRADAY" / "2026-07-14.parquet").exists()
    df = read_scores(tmp_path, "INTRADAY")
    assert len(df) == 2
    assert set(df["symbol"]) == {"NSE:X-EQ", "NSE:Y-EQ"}
    assert df.loc[df["symbol"] == "NSE:Y-EQ", "emitted"].iloc[0] == 1
    assert "rsi_14" in df.columns          # feature vector rides along (PSI food)


def test_appends_across_passes_and_dedups_same_bar(tmp_path):
    log_scores(tmp_path, "INTRADAY", [_row(conf=0.50)])
    log_scores(tmp_path, "INTRADAY", [_row(conf=0.52),                # same bar re-scored
                                      _row(ts="2026-07-14T10:35:00+05:30")])
    df = read_scores(tmp_path, "INTRADAY")
    assert len(df) == 2                    # dedup kept the latest of the dup bar
    same_bar = df[df["ts"] == "2026-07-14T10:30:00+05:30"]
    assert same_bar["confidence"].iloc[0] == pytest.approx(0.52)


def test_disabled_and_empty_are_noops(tmp_path):
    assert log_scores(tmp_path, "INTRADAY", [_row()], enabled=False) == 0
    assert log_scores(tmp_path, "INTRADAY", []) == 0
    assert not (tmp_path / "INTRADAY").exists()


def test_write_failure_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(pd.DataFrame, "to_parquet",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert log_scores(tmp_path, "INTRADAY", [_row()]) == 0   # logged, not raised


def test_read_scores_day_filter_and_empty(tmp_path):
    assert read_scores(tmp_path, "SWING").empty
    log_scores(tmp_path, "SWING", [_row(ts="2026-07-14T15:50:00+05:30", mode="SWING")])
    log_scores(tmp_path, "SWING", [_row(ts="2026-07-15T15:50:00+05:30", mode="SWING")])
    assert len(read_scores(tmp_path, "SWING")) == 2
    assert len(read_scores(tmp_path, "SWING", days=["2026-07-15"])) == 1
