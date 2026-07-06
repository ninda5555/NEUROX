"""One-command first-time setup for a fresh machine.

    python -m src.scripts.bootstrap

Runs the whole pipeline end to end so that afterwards `python -m src.run`
just works: daily auth -> symbol master + universe -> daily backfill ->
5-min backfill -> sectors + earnings -> features -> train both models.

Every step is resumable (backfill continues from stored data, universe/
features/models re-run cleanly), so if it is interrupted just run it again.
First run downloads ~2 years of history for the whole -EQ universe and can
take 1-3 hours depending on your connection and the Fyers rate limits.
"""

from __future__ import annotations

import sys
import time

from src import db as dbm
from src.config import load_config
from src.data.backfill import backfill_many
from src.data.store import CandleStore
from src.fyers import auth
from src.fyers.client import FyersClient
from src.fyers.symbols import fetch_symbol_master, parse_symbol_master, upsert_instruments
from src.universe.earnings import fetch_earnings_calendar
from src.universe.master import (build_universe, eq_candidate_symbols,
                                 latest_included_symbols)
from src.universe.sectors import update_sectors


def _step(n, total, msg):
    print(f"\n[{n}/{total}] {msg}", flush=True)


def _progress(i, total, sym):
    if i % 100 == 0 or i == total:
        print(f"    …{i}/{total} ({sym})", flush=True)


def main() -> int:
    total = 8
    cfg = load_config()
    if not cfg["fyers.app_id"] or not cfg["fyers.secret_key"]:
        print("config.yaml missing Fyers keys — copy config.example.yaml to "
              "config.yaml and fill fyers.app_id / secret_key first.")
        return 2
    conn = dbm.connect(cfg.path("paths.db")); dbm.init_db(conn)
    store = CandleStore(cfg.path("paths.candles"))

    _step(1, total, "Daily login")
    from src.run import ensure_token
    token = ensure_token(cfg)
    client = FyersClient(cfg, conn, access_token=token)

    _step(2, total, "Symbol master → instruments")
    ins = parse_symbol_master(fetch_symbol_master(client))
    print("   ", upsert_instruments(conn, ins))

    _step(3, total, "Daily history backfill (the long one — whole -EQ universe)")
    eq = eq_candidate_symbols(conn)
    r = backfill_many(client, store, eq + ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX",
                      "NSE:INDIAVIX-INDEX"], "1d", cfg["backfill.daily_days"],
                      on_progress=_progress)
    print(f"    daily: {r['symbols_ok']} ok, {r['symbols_failed']} failed, {r['rows']:,} rows")

    _step(4, total, "Build universe (liquidity + surveillance filters)")
    s = build_universe(conn, cfg, store=store, client=client)
    print(f"    included {s.included} / scanned {s.scanned} | surveillance "
          f"{s.surveillance_src_date or 'MISSING'}")

    _step(5, total, "Sectors + earnings calendar")
    try:
        print("   ", update_sectors(conn))
        print(f"    earnings dates: {len(fetch_earnings_calendar(conn))}")
    except Exception as e:
        print(f"    (skipped, non-fatal: {e})")

    _step(6, total, "5-min backfill (included universe)")
    inc = latest_included_symbols(conn)
    r = backfill_many(client, store, ["NSE:NIFTY50-INDEX"] + inc, "5min",
                      cfg["backfill.fivemin_days"], on_progress=_progress)
    print(f"    5min: {r['symbols_ok']} ok, {r['rows']:,} rows")

    _step(7, total, "Build features (both modes) + IC reports")
    from src.scripts.build_features import main as build_features
    build_features([])

    _step(8, total, "Train + validate + register models")
    from src.scripts.retrain import main as retrain
    retrain(["--mode", "both"])

    print("\n✓ Bootstrap complete. Every trading morning, just run:\n"
          "    python -m src.run\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
