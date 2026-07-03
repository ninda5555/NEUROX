"""APScheduler wiring (CLAUDE.md §10 jobs/).

P0 wires the nightly universe rebuild. Later phases register here:
  - weekly retrain (P2)
  - outcome evaluation sweeps (P3)
  - 15:15 IST intraday square-off sweep (P3)
All schedules run in IST.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src import db as dbm
from src.config import load_config
from src.data.store import CandleStore
from src.fyers import auth
from src.fyers.client import FyersClient
from src.timeutil import IST
from src.universe.master import build_universe

log = logging.getLogger(__name__)

# NSE publishes the consolidated surveillance file in the evening; rebuild
# the universe after that and after the session is fully closed.
UNIVERSE_REBUILD_CRON = CronTrigger(hour=20, minute=30, timezone=IST)


def nightly_universe_job() -> None:
    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db"))
    dbm.init_db(conn)
    store = CandleStore(cfg.path("paths.candles"))
    try:
        client = FyersClient(cfg, conn, access_token=auth.get_valid_token(cfg))
    except auth.NeedsReauth:
        log.error("nightly universe rebuild skipped: no valid token "
                  "(daily re-auth required)")
        return
    summary = build_universe(conn, cfg, store=store, client=client)
    log.info("universe rebuilt: %s included / %s scanned (%s)",
             summary.included, summary.scanned, summary.snap_date)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sched = BlockingScheduler(timezone=IST)
    sched.add_job(nightly_universe_job, UNIVERSE_REBUILD_CRON,
                  id="nightly_universe", name="Nightly universe rebuild")
    log.info("scheduler starting (IST) — jobs: nightly_universe @ 20:30")
    sched.start()


if __name__ == "__main__":
    main()
