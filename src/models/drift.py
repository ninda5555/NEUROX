"""Feature drift via PSI (T7).

At training time, retrain saves each kept feature's distribution (decile
edges + bin proportions) as a featstats sidecar next to the model artifacts:
models/{model_id}.featstats.json. In operation, psi_check() bins the live
score log (T6) against those training edges and computes the Population
Stability Index per feature:

    PSI = sum((live_prop - train_prop) * ln(live_prop / train_prop))

Convention: < 0.10 stable, 0.10-0.25 moderate shift, > 0.25 major shift.
Features above the flag threshold become a red flag in app_state
(psi_{mode}) — displayed, never suppressed (§17.2 spirit); the check itself
never raises (observability must not take down the scheduler, §17.11).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from src import db as dbm
from src.journal.scorelog import read_scores
from src.timeutil import ist_date, ist_iso, now_ist

log = logging.getLogger(__name__)

N_BINS = 10
MIN_LIVE_ROWS = 100     # need a real sample before PSI means anything
_EPS = 1e-4             # proportion floor: PSI is undefined on empty bins


def compute_featstats(df: pd.DataFrame, features: list[str]) -> dict:
    """Training-side baseline: decile edges + observed bin proportions per
    feature (finite values only). Features with too few finite values (e.g.
    ofi_top, all-NaN in backtests) are skipped — no baseline, no PSI."""
    out = {}
    for f in features:
        if f not in df.columns:
            continue
        v = pd.to_numeric(df[f], errors="coerce").to_numpy()
        v = v[np.isfinite(v)]
        if v.size < MIN_LIVE_ROWS:
            continue
        edges = np.unique(np.quantile(v, np.linspace(0, 1, N_BINS + 1)[1:-1]))
        props = _bin_props(v, edges)
        out[f] = {"edges": [float(e) for e in edges],
                  "props": [float(p) for p in props],
                  "n": int(v.size)}
    return out


def _bin_props(values: np.ndarray, edges: list[float] | np.ndarray) -> np.ndarray:
    """Proportions across len(edges)+1 bins with open-ended outer bins."""
    idx = np.searchsorted(np.asarray(edges, dtype=float), values, side="right")
    counts = np.bincount(idx, minlength=len(edges) + 1).astype(float)
    return counts / counts.sum()


def psi(train_props: np.ndarray, live_props: np.ndarray) -> float:
    e = np.clip(np.asarray(train_props, dtype=float), _EPS, None)
    a = np.clip(np.asarray(live_props, dtype=float), _EPS, None)
    e, a = e / e.sum(), a / a.sum()
    return float(np.sum((a - e) * np.log(a / e)))


def featstats_path(artifact_path: str | Path) -> Path:
    return Path(artifact_path).with_suffix(".featstats.json")


def psi_check(conn: sqlite3.Connection, scores_root: Path, mode: str, *,
              window_days: int = 5, flag_threshold: float = 0.25) -> dict:
    """PSI of the last `window_days` of live scored candidates vs the active
    model's training baseline. Result (flags included) is stored in
    app_state[psi_{mode}] for the UI. Never raises."""
    try:
        row = conn.execute("SELECT model_id, artifact_path FROM models "
                           "WHERE mode=? AND is_active=1", (mode,)).fetchone()
        if row is None:
            return {"status": "no_model"}
        fp = featstats_path(row["artifact_path"])
        if not fp.exists():
            return {"status": "no_baseline",
                    "note": f"{row['model_id']} predates featstats — PSI "
                            "available after its next retrain"}
        baseline = json.loads(fp.read_text())

        live = read_scores(scores_root, mode)
        if not live.empty:
            days = sorted(pd.to_datetime(live["ts"]).dt.date.astype(str).unique())
            live = live[pd.to_datetime(live["ts"]).dt.date.astype(str)
                        .isin(days[-window_days:])]
        result = {"status": "ok", "model_id": row["model_id"],
                  "checked_at": ist_iso(now_ist()), "window_days": window_days,
                  "n_live": int(len(live)), "features": {}, "flagged": []}
        if len(live) < MIN_LIVE_ROWS:
            result["status"] = "insufficient_live_data"
            dbm.set_state(conn, f"psi_{mode}", json.dumps(result))
            return result

        for f, stats in baseline.items():
            if f not in live.columns:
                continue
            v = pd.to_numeric(live[f], errors="coerce").to_numpy()
            v = v[np.isfinite(v)]
            if v.size < MIN_LIVE_ROWS:
                continue
            value = psi(np.array(stats["props"]), _bin_props(v, stats["edges"]))
            result["features"][f] = round(value, 4)
            if value > flag_threshold:
                result["flagged"].append(
                    {"feature": f, "psi": round(value, 4),
                     "detail": f"{f} live distribution has shifted materially "
                               f"vs training (PSI {value:.2f} > {flag_threshold})"})
        result["flagged"].sort(key=lambda x: -x["psi"])
        dbm.set_state(conn, f"psi_{mode}", json.dumps(result))
        if result["flagged"]:
            log.warning("PSI drift %s: %d feature(s) flagged: %s", mode,
                        len(result["flagged"]),
                        [x["feature"] for x in result["flagged"]])
        return result
    except Exception:
        log.exception("psi_check(%s) failed (observability only — continuing)", mode)
        return {"status": "error"}
