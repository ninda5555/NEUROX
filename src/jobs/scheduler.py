"""APScheduler wiring (CLAUDE.md §10 jobs/) — the off-session automation.
All times IST. The in-session shadow runner is a foreground command
(src/scripts/shadow.py) so its logs stay in front of the operator.

Every job is wrapped by `_safe` (deploy/ unattended VPS): one job raising
must never take the whole scheduler down, so a single failing step (a stale
surveillance fetch, a Fyers timeout) logs and the rest of the day proceeds.

    python -m src.jobs.scheduler
"""

from __future__ import annotations

import functools
import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src import db as dbm
from src.config import load_config
from src.data.backfill import backfill_many
from src.data.store import CandleStore
from src.fyers import auth
from src.fyers.client import FyersClient
from src.journal.digest import drift_check, weekly_digest
from src.journal.outcomes import evaluate_pending
from src.models.drift import psi_check
from src.journal.paper import settle_paper_trades
from src.signals.livescan import swing_pass
from src.timeutil import IST
from src.universe.earnings import fetch_earnings_calendar
from src.universe.master import (build_universe, eq_candidate_symbols,
                                 latest_included_symbols)
from src.universe.sectors import update_sectors

log = logging.getLogger(__name__)


def _safe(fn):
    """One job's exception must not kill the scheduler thread (deploy/
    unattended VPS): log the full traceback, keep the process alive so the
    next scheduled run still fires."""
    @functools.wraps(fn)
    def wrapper():
        try:
            fn()
        except Exception:
            log.exception("job %s failed — continuing (next run still scheduled)",
                          fn.__name__)
    return wrapper


def job_daily_auto_login():
    """~06:00 IST: opt-in headless re-auth (fyers.auto_login: true only).
    No-op when off (the default) — the manual flow in src.run handles it."""
    cfg = load_config()
    if not cfg["fyers.auto_login"]:
        return
    auth.headless_login(cfg)
    log.info("headless daily re-auth complete")


def _ctx():
    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db"))
    dbm.init_db(conn)
    return cfg, conn, CandleStore(cfg.path("paths.candles"))


def _client(cfg, conn):
    return FyersClient(cfg, conn, access_token=auth.get_valid_token(cfg))


def job_swing_scan():
    """15:50 IST: post-close swing signals for next-day entry."""
    cfg, conn, store = _ctx()
    cards = swing_pass(conn, store, cfg, latest_included_symbols(conn))
    log.info("swing scan emitted %d signals", len(cards))


def job_candle_topup():
    """16:20 IST: pull the day's final candles so outcomes settle on
    exchange-confirmed bars (WS bars get corrected here too)."""
    cfg, conn, store = _ctx()
    client = _client(cfg, conn)
    syms = latest_included_symbols(conn)
    backfill_many(client, store, syms + ["NSE:NIFTY50-INDEX",
                  "NSE:NIFTYBANK-INDEX", "NSE:INDIAVIX-INDEX"], "1d", days=7)
    backfill_many(client, store, syms + ["NSE:NIFTY50-INDEX"], "5min", days=3)
    n = evaluate_pending(conn, store)
    settle_paper_trades(conn, store, cfg["costs.per_side_pct"])
    try:
        cal = fetch_earnings_calendar(conn)
        log.info("earnings calendar: %d upcoming results dates", len(cal))
    except Exception:
        log.exception("earnings calendar refresh failed (previous map kept)")
    try:
        update_sectors(conn)
    except Exception:
        log.exception("sector refresh failed (existing sectors kept)")
    for mode in ("INTRADAY", "SWING"):
        drift_check(conn, mode)
        psi_check(conn, cfg.path("paths.scores"), mode,
                  window_days=cfg["observability.psi_window_days"],
                  flag_threshold=cfg["observability.psi_flag_threshold"])
    log.info("top-up done; %d outcome rows refreshed", n)


def job_universe_rebuild():
    """20:30 IST: nightly universe rebuild (surveillance + liquidity)."""
    cfg, conn, store = _ctx()
    try:
        client = _client(cfg, conn)
    except auth.NeedsReauth:
        log.error("universe rebuild skipped: no valid token")
        return
    s = build_universe(conn, cfg, store=store, client=client)
    log.info("universe rebuilt %s: %d included / %d scanned%s", s.snap_date,
             s.included, s.scanned,
             " | SURVEILLANCE MISSING — UNSCREENED" if s.surveillance_missing
             else f" | surveillance {s.surveillance_src_date}"
                  + (" (STALE)" if s.surveillance_stale else ""))


def job_daily_backfill_universe_candles():
    """21:00 IST: keep daily history complete for every -EQ candidate so the
    liquidity filter never starves."""
    cfg, conn, store = _ctx()
    client = _client(cfg, conn)
    backfill_many(client, store, eq_candidate_symbols(conn), "1d", days=7)


def _retrain_both():
    from src.scripts.build_features import main as build_features
    from src.scripts.retrain import main as retrain
    build_features([])
    retrain(["--mode", "both"])


def job_weekly_retrain():
    """Saturday 10:00 IST (training.retrain_schedule: weekly, the default):
    retrain both modes on refreshed features (§6.2)."""
    _retrain_both()
    log.info("weekly retrain complete")


def job_daily_retrain():
    """~16:45 IST Mon-Fri, post-close (training.retrain_schedule: daily):
    same retrain, run every trading day instead of once a week. Every run
    still registers as a new, never-overwritten row in `models` (§9) — old
    models stay queryable even at daily cadence."""
    _retrain_both()
    log.info("daily retrain complete")


def job_weekly_digest():
    """Friday 16:45 IST: the calibration promise audited against reality."""
    _, conn, _ = _ctx()
    for mode in ("INTRADAY", "SWING"):
        d = weekly_digest(conn, mode)
        log.info("digest %s: paper %s | drift %s", mode, d["paper"], d["drift"])


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    s = BlockingScheduler(timezone=IST)
    wd = "mon-fri"
    s.add_job(_safe(job_daily_auto_login),
              CronTrigger(day_of_week=wd, hour=6, minute=0, timezone=IST))
    s.add_job(_safe(job_swing_scan), CronTrigger(day_of_week=wd, hour=15, minute=50, timezone=IST))
    s.add_job(_safe(job_candle_topup), CronTrigger(day_of_week=wd, hour=16, minute=20, timezone=IST))
    s.add_job(_safe(job_universe_rebuild), CronTrigger(day_of_week=wd, hour=20, minute=30, timezone=IST))
    s.add_job(_safe(job_daily_backfill_universe_candles),
              CronTrigger(day_of_week=wd, hour=21, minute=0, timezone=IST))

    retrain_schedule = cfg["training.retrain_schedule"]
    if retrain_schedule == "daily":
        s.add_job(_safe(job_daily_retrain), CronTrigger(day_of_week=wd, hour=16, minute=45, timezone=IST))
        retrain_desc = "retrain daily 16:45"
    else:
        s.add_job(_safe(job_weekly_retrain), CronTrigger(day_of_week="sat", hour=10, minute=0, timezone=IST))
        retrain_desc = "retrain Sat 10:00"
    s.add_job(_safe(job_weekly_digest), CronTrigger(day_of_week="fri", hour=16, minute=45, timezone=IST))

    log.info("scheduler up (IST): auto-login 06:00%s · swing 15:50 · top-up 16:20 "
             "· universe 20:30 · backfill 21:00 · %s · digest Fri 16:45",
             " (auto_login off)" if not cfg["fyers.auto_login"] else "", retrain_desc)
    s.start()


if __name__ == "__main__":
    main()
