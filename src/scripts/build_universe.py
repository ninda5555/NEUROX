"""Universe build CLI (CLAUDE.md §4). Nightly job runs the same pipeline;
this is the on-demand entry point.

    python -m src.scripts.build_universe
    python -m src.scripts.build_universe --offline-master path/to/NSE_CM.csv
    python -m src.scripts.build_universe --skip-liquidity
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src import db as dbm
from src.config import load_config
from src.data.store import CandleStore
from src.fyers import auth
from src.fyers.client import FyersClient
from src.universe.master import build_universe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build today's trading universe")
    ap.add_argument("--offline-master", type=Path,
                    help="use a local copy of the Fyers NSE_CM.csv instead of downloading")
    ap.add_argument("--skip-liquidity", action="store_true",
                    help="series + surveillance filters only (no daily-history metrics)")
    args = ap.parse_args(argv)

    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db"))
    dbm.init_db(conn)
    store = CandleStore(cfg.path("paths.candles"))

    master_content = None
    client = None
    if args.offline_master:
        master_content = args.offline_master.read_bytes()
    else:
        token = None
        try:
            token = auth.get_valid_token(cfg)
        except auth.NeedsReauth:
            pass  # symbol master is public; token only needed for other calls
        client = FyersClient(cfg, conn, access_token=token)

    s = build_universe(conn, cfg, store=store, master_content=master_content,
                       client=client, skip_liquidity=args.skip_liquidity)

    print(f"\nUniverse snapshot {s.snap_date}")
    print(f"  -EQ & other series scanned : {s.scanned}")
    print(f"  included                   : {s.included}")
    print(f"  excluded                   : {s.excluded}")
    if s.by_reason:
        print("  exclusions by reason:")
        for reason, n in sorted(s.by_reason.items(), key=lambda kv: -kv[1]):
            print(f"    {reason:<16} {n}")
    if s.liquidity_skipped:
        print("  NOTE: liquidity filter skipped (--skip-liquidity) — this "
              "snapshot is NOT fully screened.")
    if s.no_history > 0:
        print(f"  NOTE: {s.no_history} symbols excluded as NO_HISTORY — run "
              "the daily backfill, then rebuild the universe.")
    if s.surveillance_missing:
        print("\n  *** WARNING: NO SURVEILLANCE LIST AVAILABLE — UNIVERSE IS "
              "UNSCREENED FOR GSM/ASM. Never trade on this snapshot. ***")
    elif s.surveillance_stale:
        print(f"\n  WARNING: surveillance list is STALE (dated "
              f"{s.surveillance_src_date}) — shown, never silent (§17.7).")
    else:
        print(f"  surveillance list date     : {s.surveillance_src_date} (current)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
