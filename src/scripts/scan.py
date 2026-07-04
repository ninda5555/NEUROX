"""Scanner run over stored features (CLAUDE.md §6.3) — used for post-close
swing scans, development replays, and (until the live WS lands) delayed
intraday scans.

    python -m src.scripts.scan --mode INTRADAY                # latest session
    python -m src.scripts.scan --mode INTRADAY --date 2026-06-10 --time 11:00
    python -m src.scripts.scan --mode SWING --db /tmp/demo.db  # sandbox journal
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import numpy as np
import pandas as pd

from src import db as dbm
from src.config import load_config
from src.data.store import CandleStore
from src.journal.outcomes import evaluate_pending
from src.journal.paper import settle_paper_trades
from src.models.calibrate import RegimeCalibrator, bucket_of
from src.models.registry import load_active
from src.risk.loss_limit import DayRiskTracker
from src.scripts.retrain import load_mode_frame
from src.signals.engine import Candidate, emit
from src.signals.scanner import rank, snapshot_turnover
from src.timeutil import IST


def session_orb(store: CandleStore, symbol: str, day: dt.date) -> tuple[float | None, float | None]:
    t0 = IST.localize(dt.datetime(day.year, day.month, day.day, 9, 15))
    t1 = t0 + dt.timedelta(minutes=14)
    df = store.read_candles("5min", symbol, start=t0, end=t1)
    if df.empty:
        return None, None
    return float(df["low"].min()), float(df["high"].max())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a scanner pass from stored features")
    ap.add_argument("--mode", choices=["INTRADAY", "SWING"], required=True)
    ap.add_argument("--date", help="session date YYYY-MM-DD (default: latest)")
    ap.add_argument("--time", default="14:00", help="intraday as-of time (IST)")
    ap.add_argument("--db", help="alternate SQLite path (sandbox/replay journal)")
    ap.add_argument("--settle", action="store_true",
                    help="after scan: evaluate outcomes + settle paper trades")
    args = ap.parse_args(argv)

    cfg = load_config()
    conn = dbm.connect(args.db or cfg.path("paths.db"))
    dbm.init_db(conn)
    store = CandleStore(cfg.path("paths.candles"))
    model_id, booster, cal, feats = load_active(conn, args.mode)

    df = load_mode_frame(cfg.path("paths.features"), args.mode)
    dcol = pd.DatetimeIndex(df["ts"]).date
    day = dt.date.fromisoformat(args.date) if args.date else max(dcol)
    df = df[dcol == day]
    if args.mode == "INTRADAY":
        hh, mm = map(int, args.time.split(":"))
        asof = IST.localize(dt.datetime(day.year, day.month, day.day, hh, mm))
        df = df[df["ts"] <= asof]
    if df.empty:
        print(f"no {args.mode} feature rows for {day}")
        return 1
    last = df.sort_values("ts").groupby("symbol").tail(1)

    regime_row = conn.execute("SELECT * FROM market_regime WHERE regime_date=?",
                              (day.isoformat(),)).fetchone()
    regime = dict(regime_row) if regime_row else None

    cands: list[Candidate] = []
    directions = (1, -1) if args.mode == "INTRADAY" else (1,)
    for r in last.itertuples():
        feat = {f: getattr(r, f, np.nan) for f in feats if f != "direction"}
        best = None
        for d in directions:
            feat_d = dict(feat)
            if args.mode == "INTRADAY":
                feat_d["direction"] = float(d)
            X = pd.DataFrame([{f: feat_d.get(f, np.nan) for f in feats}])
            raw_p = booster.predict(X.astype(np.float32))
            bkt = bucket_of(feat_d.get("regime_vix"), feat_d.get("regime_breadth"))
            p = float(cal.transform(raw_p, bkt)[0]) if isinstance(cal, RegimeCalibrator) \
                else float(cal.transform(raw_p)[0])
            if best is None or p > best[0]:
                best = (p, d, feat_d)
        p, d, feat_d = best
        atr_pct = getattr(r, "atr_pct", getattr(r, "atr14_pct", np.nan))
        if not np.isfinite(atr_pct) or atr_pct <= 0:
            continue
        price = float(store.read_candles(
            "5min" if args.mode == "INTRADAY" else "1d", r.symbol
        ).pipe(lambda c: c[c["ts"] <= r.ts])["close"].iloc[-1])
        cands.append(Candidate(symbol=r.symbol, mode=args.mode, direction=d,
                               confidence=p, price=price,
                               atr=atr_pct / 100 * price, features=feat_d,
                               ts=r.ts.isoformat(timespec='seconds')))

    turnover = snapshot_turnover(conn)
    top = rank(cands, turnover, cfg["signals.scanner_top_n"])
    thr = cfg["signals.confidence_threshold"]
    tracker = DayRiskTracker(conn, cfg["risk.capital"],
                             cfg["risk.daily_loss_limit_pct"],
                             cfg["risk.loss_limit_warn_frac"])

    print(f"\n{args.mode} scan — {day}"
          + (f" as of {args.time} IST" if args.mode == "INTRADAY" else " (post-close)")
          + f" | model {model_id} | threshold {thr}")
    print(f"universe scored: {len(cands)} | best calibrated p: "
          f"{max((c.confidence for c in cands), default=float('nan')):.3f}")

    emitted = []
    for c in top:
        if c.mode == "INTRADAY":
            c.orb_low, c.orb_high = session_orb(store, c.symbol, day)
        card = emit(conn, store, cfg, model_id=model_id, booster=booster,
                    feature_list=feats, candidate=c, regime=regime, tracker=tracker)
        if card:
            emitted.append(card)

    if not emitted:
        print("\nNo qualifying setups — nothing cleared the confidence and "
              "risk gates. The system saying 'no trade' is a valid, healthy "
              "outcome.\nTop candidates (NOT emitted, below threshold):")
        for c in sorted(top, key=lambda x: -x.confidence)[:5]:
            print(f"  {c.symbol:<20} {'LONG' if c.direction > 0 else 'SHORT':<6} "
                  f"calibrated {c.confidence:.3f}")
    else:
        for card in emitted:
            print(f"\n#{card['signal_id']} {card['symbol']} "
                  f"{'LONG' if card['direction'] > 0 else 'SHORT'} "
                  f"conf {card['confidence']:.2f} | entry {card['entry']:.2f} "
                  f"stop {card['stop']:.2f} target {card['target']:.2f} "
                  f"qty {card['qty']} | ₹ at risk {card['at_risk']:,.0f}")
            print(f"   {card['explanation']}")
            if card["flags"]:
                print(f"   flags: {'; '.join(card['flags'])}")

    if args.settle:
        n_out = evaluate_pending(conn, store)
        n_settled = settle_paper_trades(conn, store, cfg["costs.per_side_pct"], tracker)
        print(f"\noutcomes evaluated: {n_out} rows | paper trades settled: {n_settled}")
        for t in conn.execute(
            """SELECT p.trade_id, s.symbol, p.pnl, p.r_multiple, p.exit_reason,
               p.costs_modeled FROM paper_trades p
               JOIN signals s ON s.signal_id=p.signal_id
               WHERE p.closed_at IS NOT NULL ORDER BY p.trade_id"""):
            print(f"  trade {t['trade_id']} {t['symbol']:<18} pnl ₹{t['pnl']:,.0f} "
                  f"({t['r_multiple']:+.2f}R) exit={t['exit_reason']} "
                  f"costs ₹{t['costs_modeled']:,.0f}")
        print(f"loss-limit state: {tracker.status()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
