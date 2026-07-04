import datetime as dt
import json

import numpy as np

from src import db as dbm
from src.journal.digest import confidence_buckets, drift_check
from src.fyers.ws import LiveDataSocket
from src.data.candles import Candle


def _seed(conn, n, conf, hit_frac, mode="INTRADAY"):
    conn.execute("INSERT OR IGNORE INTO models (model_id, mode, trained_at, "
                 "feature_list, lgbm_params, calibration, cv_report, artifact_path)"
                 " VALUES ('m1', ?, 'x','[]','{}','{}','{}','p')", (mode,))
    for i in range(n):
        cur = conn.execute(
            """INSERT INTO signals (ts, mode, symbol, direction, confidence,
               model_id, features, shap_top, entry, stop_loss, target, qty,
               explanation) VALUES (?,?,?,1,?,'m1','{}','[]',100,98,104,10,'e')""",
            (f"2026-07-{(i % 28) + 1:02d}T10:00:00+05:30", mode, f"NSE:S{i}-EQ", conf))
        status = "hit_target" if i < n * hit_frac else "hit_stop"
        conn.execute(
            "INSERT INTO signal_outcomes (signal_id, horizon, status, r_multiple)"
            " VALUES (?, 'eod', ?, ?)",
            (cur.lastrowid, status, 1.5 if status == "hit_target" else -1.0))
    conn.commit()


def test_drift_flag_fires_beyond_10pts(conn):
    _seed(conn, 30, conf=0.70, hit_frac=0.5)   # stated 0.70, realized 0.50
    d = drift_check(conn, "INTRADAY")
    assert d["flag"] is True and d["drift"] > 0.10
    assert "drift" in (dbm.get_state(conn, "drift_INTRADAY") or "")


def test_drift_ok_within_bounds(conn):
    _seed(conn, 30, conf=0.62, hit_frac=0.6)
    d = drift_check(conn, "INTRADAY")
    assert d["flag"] is False


def test_confidence_buckets_audit(conn):
    _seed(conn, 40, conf=0.62, hit_frac=0.5)
    b = confidence_buckets(conn, "INTRADAY")
    assert b and b[0]["n"] == 40
    assert abs(b[0]["realized"] - 0.5) < 0.01


def test_ws_tick_to_bar_pipeline(cfg):
    from src.timeutil import IST
    bars = []
    sock = LiveDataSocket.__new__(LiveDataSocket)  # no real socket
    sock._aggs, sock._depth_prev, sock._ofi = {}, {}, {}
    sock._on_bar = lambda s, c: bars.append((s, c))
    import threading
    sock._lock = threading.Lock()
    t0 = int(IST.localize(dt.datetime(2026, 7, 6, 9, 15)).timestamp())
    for i, (off, px, vol) in enumerate([(0, 100.0, 10), (120, 101.0, 30),
                                        (300, 102.0, 55)]):
        sock._on_tick({"type": "sf", "symbol": "NSE:X-EQ", "ltp": px,
                       "exch_feed_time": t0 + off, "vol_traded_today": vol})
    assert len(bars) == 1
    sym, bar = bars[0]
    assert sym == "NSE:X-EQ" and bar.close == 101.0 and bar.volume == 30
    sock.flush_session()
    assert len(bars) == 2 and bars[1][1].volume == 25  # 55-30


def test_ws_depth_to_ofi():
    sock = LiveDataSocket.__new__(LiveDataSocket)
    sock._depth_prev, sock._ofi = {}, {}
    mk = lambda bq, aq: {"type": "dp", "symbol": "NSE:X-EQ",
                         "bids": [{"price": 99.9, "volume": bq}],
                         "ask": [{"price": 100.1, "volume": aq}]}
    sock._on_depth(mk(1000, 1000))
    sock._on_depth(mk(1600, 900))   # bids added, asks pulled -> positive OFI
    assert sock.ofi("NSE:X-EQ") > 0
