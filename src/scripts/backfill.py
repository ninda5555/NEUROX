"""History backfill CLI (CLAUDE.md §16 P0).

    python -m src.scripts.backfill --tf both              # universe-driven
    python -m src.scripts.backfill --tf 1d --symbols NSE:RELIANCE-EQ,NSE:TCS-EQ
    python -m src.scripts.backfill --tf 5min --days 60

Daily bars cover every -EQ candidate (the liquidity filter needs them);
5-min bars cover the included universe.
"""

from __future__ import annotations

import argparse
import sys

from src import db as dbm
from src.config import load_config
from src.data.backfill import backfill_many
from src.data.store import CandleStore
from src.fyers import auth
from src.fyers.client import FyersClient
from src.universe.master import eq_candidate_symbols, latest_included_symbols


def _progress(i: int, n: int, sym: str) -> None:
    if i % 25 == 0 or i == n:
        print(f"  …{i}/{n} ({sym})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill daily / 5-min candles")
    ap.add_argument("--tf", choices=["1d", "5min", "both"], default="both")
    ap.add_argument("--days", type=int, help="override config lookback")
    ap.add_argument("--symbols", help="comma-separated Fyers symbols (default: universe-driven)")
    args = ap.parse_args(argv)

    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db"))
    dbm.init_db(conn)
    store = CandleStore(cfg.path("paths.candles"))
    client = FyersClient(cfg, conn, access_token=auth.get_valid_token(cfg))

    plans = []
    if args.tf in ("1d", "both"):
        syms = (args.symbols.split(",") if args.symbols else eq_candidate_symbols(conn))
        plans.append(("1d", args.days or cfg["backfill.daily_days"], syms))
    if args.tf in ("5min", "both"):
        syms = (args.symbols.split(",") if args.symbols
                else latest_included_symbols(conn) or eq_candidate_symbols(conn))
        plans.append(("5min", args.days or cfg["backfill.fivemin_days"], syms))

    for tf, days, syms in plans:
        if not syms:
            print(f"[{tf}] no symbols to backfill — ingest the symbol master / "
                  "build the universe first")
            continue
        print(f"[{tf}] backfilling {len(syms)} symbols × {days} days "
              "(rate-limited 10/s · 200/min · 100k/day)")
        r = backfill_many(client, store, syms, tf, days, on_progress=_progress)
        print(f"[{tf}] done: {r['symbols_ok']} ok, {r['symbols_failed']} failed, "
              f"{r['rows']} rows via {r['requests']} requests "
              f"(API calls today: {r['calls_today']})")
        for sym, err in r["errors"]:
            print(f"    failed {sym}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
