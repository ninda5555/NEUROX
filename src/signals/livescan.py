"""Live scan passes (shadow operation): features computed on-the-fly from
stored candles (including bars written seconds ago by the live feed), scored
with the active model, gated and journaled through the standard emission
path. Same code path the replay demos exercised."""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from src.config import load_config
from src.data.store import CandleStore
from src.features import intraday as intraday_mod
from src.features import swing as swing_mod
from src.features.build import REGIME_COLS
from src.features.regime import NIFTY_SYMBOL, regime_feature_frame
from src.journal.scorelog import log_scores
from src.models.calibrate import RegimeCalibrator, bucket_of
from src.models.registry import load_active
from src.risk.loss_limit import DayRiskTracker
from src.signals.engine import Candidate, emit
from src.signals.scanner import rank, snapshot_turnover
from src.timeutil import IST, ist_date, in_intraday_entry_window

log = logging.getLogger(__name__)


def _latest_regime_features(conn: sqlite3.Connection) -> dict:
    df = regime_feature_frame(conn)
    if df.empty:
        return {c: np.nan for c in REGIME_COLS}
    return df.iloc[-1][REGIME_COLS].to_dict()


def _intraday_row(args) -> dict | None:
    """Worker: build today's latest intraday feature row for one symbol."""
    candles_root, symbol, asof_iso, nifty_close = args
    try:
        store = CandleStore(candles_root)
        asof = dt.datetime.fromisoformat(asof_iso)
        df = store.read_candles("5min", symbol,
                                start=asof - dt.timedelta(days=12), end=asof)
        if len(df) < 60 or df["ts"].iloc[-1].date() != asof.date():
            return None
        bench = None
        if nifty_close is not None:
            bench = pd.Series(nifty_close["v"],
                              index=pd.DatetimeIndex(nifty_close["t"]))
        f = intraday_mod.build_symbol_frame(df, bench)
        last = f.iloc[-1]
        atr_pct = last.get("atr_pct", np.nan)
        if not np.isfinite(atr_pct) or atr_pct <= 0:
            return None
        day_bars = df[pd.DatetimeIndex(df["ts"]).date == asof.date()]
        orb = day_bars[[t.time() < dt.time(9, 30)
                        for t in pd.DatetimeIndex(day_bars["ts"])]]
        return {"symbol": symbol, "ts": last["ts"].isoformat(),
                "price": float(df["close"].iloc[-1]),
                "atr": float(atr_pct) / 100 * float(df["close"].iloc[-1]),
                "features": {c: (None if pd.isna(last[c]) else float(last[c]))
                             for c in intraday_mod.FEATURE_COLS},
                "orb_low": float(orb["low"].min()) if len(orb) else None,
                "orb_high": float(orb["high"].max()) if len(orb) else None}
    except Exception:
        log.exception("intraday row failed for %s", symbol)
        return None


def intraday_pass(conn: sqlite3.Connection, store: CandleStore, cfg,
                  symbols: list[str], asof: dt.datetime,
                  ofi_lookup=None, workers: int = 4) -> list[dict]:
    """One in-session scan sweep. Returns emitted cards (already journaled)."""
    if not in_intraday_entry_window(asof):
        log.info("outside 09:30-14:30 entry window — no intraday emission")
        return []
    model_id, booster, cal, feats = load_active(conn, "INTRADAY")
    regime_feats = _latest_regime_features(conn)
    regime_row = conn.execute(
        "SELECT * FROM market_regime ORDER BY regime_date DESC LIMIT 1").fetchone()

    nifty = store.read_candles("5min", NIFTY_SYMBOL,
                               start=asof - dt.timedelta(days=12), end=asof)
    nifty_ser = ({"t": [t.isoformat() for t in nifty["ts"]],
                  "v": nifty["close"].tolist()} if not nifty.empty else None)

    jobs = [(str(store.root), s, asof.isoformat(), nifty_ser) for s in symbols]
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(_intraday_row, jobs, chunksize=32):
            if r is not None:
                rows.append(r)

    cands, score_rows = [], []
    for r in rows:
        feat = {**r["features"], **regime_feats}
        if ofi_lookup is not None:
            feat["ofi_top"] = ofi_lookup(r["symbol"])
        best = None
        for d in (1, -1):
            fd = {**feat, "direction": float(d)}
            X = pd.DataFrame([{f: fd.get(f, np.nan) for f in feats}])
            raw_p = booster.predict(X.astype(np.float32))
            bkt = bucket_of(fd.get("regime_vix"), fd.get("regime_breadth"))
            p = float(cal.transform(raw_p, bkt)[0]) if isinstance(cal, RegimeCalibrator) \
                else float(cal.transform(raw_p)[0])
            if best is None or p > best[0]:
                best = (p, d, fd, float(raw_p[0]), bkt)
        p, d, fd, raw, bkt = best
        cands.append(Candidate(symbol=r["symbol"], mode="INTRADAY", direction=d,
                               confidence=p, price=r["price"], atr=r["atr"],
                               features=fd, orb_low=r["orb_low"],
                               orb_high=r["orb_high"], ts=r["ts"]))
        score_rows.append({"ts": r["ts"], "symbol": r["symbol"],
                           "mode": "INTRADAY", "model_id": model_id,
                           "direction": d, "score_raw": raw, "confidence": p,
                           "bucket": bkt, "emitted": 0, **fd})

    top = rank(cands, snapshot_turnover(conn), cfg["signals.scanner_top_n"])
    tracker = DayRiskTracker(conn, cfg["risk.capital"],
                             cfg["risk.daily_loss_limit_pct"],
                             cfg["risk.loss_limit_warn_frac"])
    emitted = []
    already = {r["symbol"] for r in conn.execute(
        "SELECT DISTINCT symbol FROM signals WHERE mode='INTRADAY' AND "
        "substr(ts,1,10)=?", (asof.date().isoformat(),))}
    for c in top:
        if c.symbol in already:
            continue  # one signal per symbol per day
        card = emit(conn, store, cfg, model_id=model_id, booster=booster,
                    feature_list=feats, candidate=c,
                    regime=dict(regime_row) if regime_row else None,
                    tracker=tracker)
        if card:
            emitted.append(card)
    done = {card["symbol"] for card in emitted}
    for sr in score_rows:
        sr["emitted"] = int(sr["symbol"] in done)
    log_scores(cfg.path("paths.scores"), "INTRADAY", score_rows,
               enabled=cfg["observability.score_log"])
    best_p = max((c.confidence for c in cands), default=float("nan"))
    log.info("intraday pass %s: %d scored, best p %.3f, %d emitted",
             asof.strftime("%H:%M"), len(cands), best_p, len(emitted))
    return emitted


def swing_pass(conn: sqlite3.Connection, store: CandleStore, cfg,
               symbols: list[str]) -> list[dict]:
    """Post-close swing scan for next-day entry."""
    model_id, booster, cal, feats = load_active(conn, "SWING")
    regime_feats = _latest_regime_features(conn)
    regime_row = conn.execute(
        "SELECT * FROM market_regime ORDER BY regime_date DESC LIMIT 1").fetchone()
    nifty = store.read_candles("1d", NIFTY_SYMBOL)
    today = ist_date().isoformat()
    tracker = DayRiskTracker(conn, cfg["risk.capital"],
                             cfg["risk.daily_loss_limit_pct"],
                             cfg["risk.loss_limit_warn_frac"])
    cands, score_rows = [], []
    for sym in symbols:
        d = store.read_candles("1d", sym)
        if len(d) < 210:
            continue
        f = swing_mod.build_symbol_frame(d, nifty if not nifty.empty else None)
        last = f.iloc[-1]
        atr_pct = last.get("atr14_pct", np.nan)
        if not np.isfinite(atr_pct) or atr_pct <= 0:
            continue
        feat = {**{c: (None if pd.isna(last[c]) else float(last[c]))
                   for c in swing_mod.FEATURE_COLS}, **regime_feats}
        X = pd.DataFrame([{f2: feat.get(f2, np.nan) for f2 in feats}])
        raw_p = booster.predict(X.astype(np.float32))
        bkt = bucket_of(feat.get("regime_vix"), feat.get("regime_breadth"))
        p = float(cal.transform(raw_p, bkt)[0]) if isinstance(cal, RegimeCalibrator) \
            else float(cal.transform(raw_p)[0])
        price = float(d["close"].iloc[-1])
        cands.append(Candidate(symbol=sym, mode="SWING", direction=1,
                               confidence=p, price=price,
                               atr=atr_pct / 100 * price, features=feat,
                               ts=last["ts"].isoformat()))
        score_rows.append({"ts": last["ts"].isoformat(), "symbol": sym,
                           "mode": "SWING", "model_id": model_id,
                           "direction": 1, "score_raw": float(raw_p[0]),
                           "confidence": p, "bucket": bkt, "emitted": 0, **feat})
    top = rank(cands, snapshot_turnover(conn), cfg["signals.scanner_top_n"])
    emitted = []
    for c in top:
        card = emit(conn, store, cfg, model_id=model_id, booster=booster,
                    feature_list=feats, candidate=c,
                    regime=dict(regime_row) if regime_row else None,
                    tracker=tracker)
        if card:
            emitted.append(card)
    done = {card["symbol"] for card in emitted}
    for sr in score_rows:
        sr["emitted"] = int(sr["symbol"] in done)
    log_scores(cfg.path("paths.scores"), "SWING", score_rows,
               enabled=cfg["observability.score_log"])
    best_p = max((c.confidence for c in cands), default=float("nan"))
    log.info("swing pass %s: %d scored, best p %.3f, %d emitted",
             today, len(cands), best_p, len(emitted))
    return emitted
