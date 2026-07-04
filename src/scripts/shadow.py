"""P5 shadow-operation session runner (CLAUDE.md §16 P5).

Run once each trading morning after daily_auth:

    python -m src.scripts.shadow

Live WS ticks -> 5-min bars -> features -> active model -> risk gate ->
journal-first signals -> paper trades, all day; square-off sweep at 15:15;
digest at close. Signals + paper trading ONLY — no orders exist (§17.8).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import threading
import time

from src import db as dbm
from src.config import load_config
from src.data.store import CandleStore
from src.fyers import auth
from src.fyers.ws import LiveDataSocket
from src.journal.digest import drift_check, weekly_digest
from src.journal.outcomes import evaluate_pending
from src.journal.paper import settle_paper_trades
from src.risk.loss_limit import DayRiskTracker
from src.signals.livescan import intraday_pass
from src.timeutil import (INTRADAY_SQUAREOFF, IST, MARKET_CLOSE, MARKET_OPEN,
                          now_ist)
from src.universe.master import latest_included_symbols

log = logging.getLogger("shadow")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Shadow-mode live session")
    ap.add_argument("--no-ws", action="store_true",
                    help="skip the live socket (bars must arrive another way)")
    ap.add_argument("--once", action="store_true", help="single scan pass and exit")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db"))
    dbm.init_db(conn)
    store = CandleStore(cfg.path("paths.candles"))
    token = auth.get_valid_token(cfg)  # raises NeedsReauth loudly
    symbols = latest_included_symbols(conn)
    if not symbols:
        print("no included universe — run build_universe first")
        return 2

    write_lock = threading.Lock()

    def on_bar(sym, candle):
        with write_lock:
            store.write_candles("5min", sym, [candle.as_dict()])

    sock = None
    if not args.no_ws:
        sock = LiveDataSocket(cfg, token, symbols + ["NSE:NIFTY50-INDEX"], on_bar)
        threading.Thread(target=sock.start, daemon=True).start()
        dbm.set_state(conn, "ws_status", "live")
    log.info("shadow session: %d symbols, WS %s", len(symbols),
             "off" if args.no_ws else "on")

    tracker = DayRiskTracker(conn, cfg["risk.capital"],
                             cfg["risk.daily_loss_limit_pct"],
                             cfg["risk.loss_limit_warn_frac"])
    try:
        while True:
            now = now_ist()
            t = now.time()
            if t >= MARKET_CLOSE or args.once:
                if not args.once:
                    break
            if t < MARKET_OPEN:
                time.sleep(20)
                continue
            # wait for the next 5-min boundary (+15s so bars close & write)
            if not args.once:
                secs = (5 - now.minute % 5) * 60 - now.second + 15
                time.sleep(max(secs, 1))
            ofi = (lambda s: sock.ofi(s)) if sock else None
            emitted = intraday_pass(conn, store, cfg, symbols, now_ist(),
                                    ofi_lookup=ofi)
            for card in emitted:
                log.info("SIGNAL #%s %s %s conf %.2f entry %.2f",
                         card["signal_id"], card["symbol"],
                         "LONG" if card["direction"] > 0 else "SHORT",
                         card["confidence"], card["entry"])
            if now_ist().time() >= INTRADAY_SQUAREOFF:
                evaluate_pending(conn, store)
                n = settle_paper_trades(conn, store, cfg["costs.per_side_pct"], tracker)
                if n:
                    log.info("square-off sweep settled %d paper trades | %s",
                             n, tracker.status())
            if args.once:
                break
    finally:
        if sock:
            sock.flush_session()
            sock.stop()
            dbm.set_state(conn, "ws_status", "offline")

    evaluate_pending(conn, store)
    settle_paper_trades(conn, store, cfg["costs.per_side_pct"], tracker)
    drift = drift_check(conn, "INTRADAY")
    digest = weekly_digest(conn, "INTRADAY")
    log.info("session done | paper: %s | drift: %s",
             digest["paper"], drift.get("detail") or "within bounds "
             f"(n={drift['n']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
