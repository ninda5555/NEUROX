"""P1 CLI: regime table + feature pipelines + IC report.

    python -m src.scripts.build_features                 # both modes
    python -m src.scripts.build_features --mode SWING
    python -m src.scripts.build_features --limit 50      # cap symbols (dev)
"""

from __future__ import annotations

import argparse
import sys

from src import db as dbm
from src.config import load_config
from src.data.store import CandleStore
from src.features.build import build_mode_features
from src.features.ic_filter import format_report
from src.features.regime import compute_regime, store_regime
from src.universe.master import latest_included_symbols


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build P1 features + regime + IC report")
    ap.add_argument("--mode", choices=["INTRADAY", "SWING", "both"], default="both")
    ap.add_argument("--limit", type=int, help="cap symbol count (dev runs)")
    ap.add_argument("--skip-regime", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db"))
    dbm.init_db(conn)
    store = CandleStore(cfg.path("paths.candles"))
    symbols = latest_included_symbols(conn)
    if not symbols:
        print("no included universe — run build_universe first")
        return 2
    if args.limit:
        symbols = symbols[: args.limit]

    if not args.skip_regime:
        regime = compute_regime(store, symbols)
        n = store_regime(conn, regime)
        last = regime.dropna(subset=["vix"]).iloc[-1]
        print(f"regime table: {n} dates upserted | latest {last.regime_date}: "
              f"VIX {last.vix:.1f} ({last.vix_trend}), "
              f"NIFTY vs 50DMA {last.nifty_vs_50dma:+.1f}%, "
              f"vs 200DMA {last.nifty_vs_200dma:+.1f}%, "
              f"breadth {last.breadth_adv_dec:+.2f} -> {last.label}")

    modes = ["INTRADAY", "SWING"] if args.mode == "both" else [args.mode]
    for mode in modes:
        _, rep = build_mode_features(mode, conn, store,
                                     cfg.path("paths.features"), symbols)
        print()
        print(format_report(rep))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
