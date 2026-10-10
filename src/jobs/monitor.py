"""Model-independent, bounded intraday observation. Never places orders."""
from __future__ import annotations

import datetime as dt
import json

from src import db as dbm
from src.config import load_config
from src.data.backfill import backfill_many
from src.data.store import CandleStore
from src.fyers import auth
from src.fyers.client import FyersClient
from src.timeutil import now_ist

INDEXES = ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX", "NSE:INDIAVIX-INDEX"]


def record(conn, **values):
    values["checked_at"] = now_ist().isoformat(timespec="seconds")
    dbm.set_state(conn, "observation", json.dumps(values))


@dbm.scoped
def pulse():
    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db"))
    dbm.init_db(conn)
    dbm.set_state(conn, "scheduler_pulse", now_ist().isoformat(timespec="seconds"))


@dbm.scoped
def observe():
    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db"))
    dbm.init_db(conn)
    if not cfg["fyers.app_id"] or not cfg["fyers.secret_key"]:
        record(conn, state="setup_needed", message="Connect a Fyers API app to collect real market data.")
        return
    try:
        token = auth.get_valid_token(cfg)
    except auth.NeedsReauth:
        record(conn, state="login_needed", message="Complete broker login locally; collection resumes automatically.")
        return
    now = now_ist()
    # Weekday clock window only; exchange holidays are detected by absent fresh bars.
    if now.weekday() >= 5 or not dt.time(9, 25) <= now.time() <= dt.time(15, 40):
        record(conn, state="outside_session", message="Outside the weekday collection window. Exchange holidays may also have no fresh bars.")
        return
    rows = conn.execute("""SELECT symbol FROM universe_snapshot
        WHERE snap_date=(SELECT MAX(snap_date) FROM universe_snapshot) AND included=1
        ORDER BY median_turnover_cr DESC, symbol LIMIT 50""").fetchall()
    symbols = list(dict.fromkeys([r["symbol"] for r in rows] + INDEXES))
    store = CandleStore(cfg.path("paths.candles"))
    record(conn, state="collecting", message="Fetching five-minute bars.", requested=len(symbols))
    try:
        client = FyersClient(cfg, conn, access_token=token)
        result = backfill_many(client, store, symbols, "5min", days=3)
        from src.journal.outcomes import evaluate_pending
        from src.journal.paper import settle_paper_trades
        evaluate_pending(conn, store)
        settle_paper_trades(conn, store, per_side_pct=cfg["costs.per_side_pct"])
        fresh = 0
        latest = None
        for symbol in symbols:
            bars = store.read_candles("5min", symbol, start=now - dt.timedelta(days=1), end=now)
            bars = bars[bars["ts"] <= now - dt.timedelta(minutes=5)]
            if bars.empty:
                continue
            last = bars["ts"].iloc[-1].to_pydatetime()
            latest = max(latest, last) if latest else last
            fresh += int(last.date() == now.date() and now - last <= dt.timedelta(minutes=20))
        record(conn, state="observing" if fresh == len(symbols) else "stale_or_partial",
               message="Five-minute observations; no validated intraday prediction model is enabled.",
               requested=len(symbols), fresh_symbols=fresh, failed_symbols=result["symbols_failed"],
               latest_completed_bar=latest.isoformat() if latest else None,
               universe_ready=bool(rows))
    except Exception:
        record(conn, state="error", message="Collection failed. Check the local service log and broker access.")
        raise
