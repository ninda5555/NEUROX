"""T15 CLI: offline CPCV + Deflated Sharpe + PBO. Weekend/idle-time job —
deliberately NOT scheduled (it trains C(N,k) models; on the VPS run it
outside market hours, or run it on any machine with the feature store).

    python -m src.scripts.validate --mode INTRADAY
    python -m src.scripts.validate --mode both --groups 8 --k-test 2

Every run is a row in validation_runs; the newest per mode shows on the
Model page. Spread first, never averaged away (§17.1/2).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from src import db as dbm
from src.config import load_config
from src.models.cpcv import run_cpcv
from src.models.train import train_fn_for_cv
from src.scripts.retrain import load_mode_frame, prepare
from src.timeutil import ist_iso, now_ist


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CPCV + DSR + PBO validation")
    ap.add_argument("--mode", choices=["INTRADAY", "SWING", "both"], default="both")
    ap.add_argument("--groups", type=int, default=8)
    ap.add_argument("--k-test", type=int, default=2)
    args = ap.parse_args(argv)

    cfg = load_config()
    conn = dbm.connect(cfg.path("paths.db"))
    dbm.init_db(conn)

    for mode in (["INTRADAY", "SWING"] if args.mode == "both" else [args.mode]):
        print(f"\n=== {mode}: CPCV ({args.groups} groups, {args.k_test} test) ===")
        df = load_mode_frame(cfg.path("paths.features"), mode)
        frame, feature_cols, label_col = prepare(
            mode, df,
            half_life_days=cfg["training.time_decay_half_life_days"],
            swing_uniqueness=cfg["training.swing_uniqueness_weights"])
        run_id = f"cpcv_{mode.lower()}_{uuid.uuid4().hex[:6]}"
        started = ist_iso(now_ist())
        params = {"n_groups": args.groups, "k_test": args.k_test}
        conn.execute(
            "INSERT INTO validation_runs (run_id, mode, kind, started_at, params)"
            " VALUES (?,?,?,?,?)",
            (run_id, mode, "cpcv", started, json.dumps(params)))
        conn.commit()

        result = run_cpcv(frame, mode, feature_cols, label_col, train_fn_for_cv,
                          n_groups=args.groups, k_test=args.k_test)
        conn.execute("UPDATE validation_runs SET finished_at=?, result=? "
                     "WHERE run_id=?",
                     (ist_iso(now_ist()), json.dumps(result), run_id))
        conn.commit()

        print(f"splits run: {result['n_splits_run']} | precision "
              f"{result['precision_mean']} ± {result['precision_std']}")
        d = result["deflated_sharpe"]
        print(f"pooled Sharpe {d.get('sharpe')} | expected-max-from-noise "
              f"{d.get('sr0_expected_max_noise')} | DEFLATED Sharpe (P[true SR>0]) "
              f"{d.get('dsr')}")
        p = result["pbo"]
        print(f"PBO {p.get('pbo')} — {p.get('verdict', p.get('note', ''))}")
        print(f"stored as {run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
