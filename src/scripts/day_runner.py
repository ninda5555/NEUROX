"""Resilient shadow-day runner (P5) for cloud operation: instead of a
long-lived WebSocket, each pass tops up today's 5-min bars via the
rate-limited REST client, then runs the standard intraday emission path on the
production DB. Journals real signals; runs the 15:15 square-off sweep; logs
top candidates each pass so an empty scanner is legible, not silent.

    python -m src.scripts.day_runner --once      # single pass, print + exit
    python -m src.scripts.day_runner             # loop until 15:30 IST
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pandas as pd

from src import db as dbm
from src.config import load_config
from src.data.backfill import backfill_many
from src.data.store import CandleStore
from src.fyers import auth
from src.fyers.client import FyersClient
from src.journal.digest import drift_check
from src.journal.outcomes import evaluate_pending
from src.journal.paper import settle_paper_trades
from src.models.calibrate import RegimeCalibrator, bucket_of
from src.models.registry import load_active
from src.risk.loss_limit import DayRiskTracker
from src.signals.livescan import (_latest_regime_features, intraday_pass)
from src.timeutil import (INTRADAY_ENTRY_END, INTRADAY_SQUAREOFF, MARKET_CLOSE,
                          now_ist)
from src.universe.master import latest_included_symbols
from src.signals.scanner import rank, snapshot_turnover


def _log(msg: str) -> None:
    print(f"{now_ist().strftime('%H:%M:%S')} {msg}", flush=True)


def top_candidates(conn, store, cfg, syms, asof, n=8):
    """Score the universe and return the top-N by calibrated confidence
    (whether or not they cleared the gate) — for a legible empty state."""
    model_id, booster, cal, feats = load_active(conn, "INTRADAY")
    regime_feats = _latest_regime_features(conn)
    from src.features import intraday as im
    from src.features.regime import NIFTY_SYMBOL
    import datetime as dt
    nifty = store.read_candles("5min", NIFTY_SYMBOL,
                               start=asof - dt.timedelta(days=12), end=asof)
    bench = (pd.Series(nifty["close"].tolist(), index=pd.DatetimeIndex(nifty["ts"]))
             if not nifty.empty else None)
    # build the whole cross-section first, then rank, then score — same
    # contract as the emit path (features/xsection.py)
    from src.features import xsection as xs
    raw_rows = []
    for sym in syms:
        df = store.read_candles("5min", sym, start=asof - dt.timedelta(days=12), end=asof)
        if len(df) < 60 or df["ts"].iloc[-1].date() != asof.date():
            continue
        f = im.build_symbol_frame(df, bench).iloc[-1]
        atr_pct = f.get("atr_pct", np.nan)
        if not np.isfinite(atr_pct) or atr_pct <= 0:
            continue  # same tradability filter the emit path applies
        raw_rows.append({"symbol": sym,
                         "features": {c: (None if pd.isna(f[c]) else float(f[c]))
                                      for c in im.FEATURE_COLS}})
    raw_rows = xs.rank_one_bar(raw_rows, xs.model_feature_cols(im.FEATURE_COLS))

    scored = []
    for r in raw_rows:
        sym = r["symbol"]
        feat = {**r["features"], **regime_feats}
        for d in (1, -1):
            fd = {**feat, "direction": float(d)}
            X = pd.DataFrame([{c: fd.get(c, np.nan) for c in feats}])
            raw = booster.predict(X.astype(np.float32))
            bkt = bucket_of(fd.get("regime_vix"), fd.get("regime_breadth"))
            p = float(cal.transform(raw, bkt)[0]) if isinstance(cal, RegimeCalibrator) else float(cal.transform(raw)[0])
            scored.append((p, sym, "LONG" if d > 0 else "SHORT"))
    scored.sort(key=lambda x: -x[0])
    seen, out = set(), []
    for p, sym, side in scored:
        if sym in seen:
            continue
        seen.add(sym); out.append((p, sym, side))
        if len(out) >= n:
            break
    return out


def one_pass(cfg, conn, store, syms, client, show_top=True):
    backfill_many(client, store, syms + ["NSE:NIFTY50-INDEX"], "5min", days=2)
    asof = now_ist()
    tracker = DayRiskTracker(conn, cfg["risk.capital"],
                             cfg["risk.daily_loss_limit_pct"],
                             cfg["risk.loss_limit_warn_frac"])
    cards = []
    if asof.time() <= INTRADAY_ENTRY_END:
        cards = intraday_pass(conn, store, cfg, syms, asof)
    for c in cards:
        _log(f"SIGNAL #{c['signal_id']} {c['symbol'].split(':')[-1]} "
             f"{'LONG' if c['direction']>0 else 'SHORT'} conf {c['confidence']:.2f} "
             f"entry {c['entry']:.2f} stop {c['stop']:.2f} target {c['target']:.2f}")
    if not cards and show_top:
        top = top_candidates(conn, store, cfg, syms, asof)
        if not top:
            _log("no emission: no symbol has a bar for today yet "
                 "(market holiday, or the feed hasn't produced 5-min bars)")
        else:
            _log(f"no emission (best {top[0][0]:.3f} < {cfg['signals.confidence_threshold']}). "
                 "top watched:")
            for p, sym, side in top[:6]:
                _log(f"    {sym.split(':')[-1]:<14} {side:<5} {p:.3f}")
    if asof.time() >= INTRADAY_SQUAREOFF:
        evaluate_pending(conn, store)
        n = settle_paper_trades(conn, store, cfg["costs.per_side_pct"], tracker)
        if n:
            _log(f"square-off: settled {n} paper trades | {tracker.status()}")
    return cards


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval-min", type=int, default=20)
    args = ap.parse_args(argv)

    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db")); dbm.init_db(conn)
    store = CandleStore(cfg.path("paths.candles"))
    client = FyersClient(cfg, conn, access_token=auth.get_valid_token(cfg))
    syms = latest_included_symbols(conn)
    dbm.set_state(conn, "ws_status", "rest-poll")
    _log(f"day-runner: {len(syms)} stocks, interval {args.interval_min}m")

    while True:
        one_pass(cfg, conn, store, syms, client)
        if args.once or now_ist().time() >= MARKET_CLOSE:
            break
        time.sleep(args.interval_min * 60)

    evaluate_pending(conn, store)
    settle_paper_trades(conn, store, cfg["costs.per_side_pct"])
    for m in ("INTRADAY", "SWING"):
        drift_check(conn, m)
    dbm.set_state(conn, "ws_status", "offline")
    _log("day-runner: session complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
