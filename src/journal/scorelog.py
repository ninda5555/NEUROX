"""Score log (T6): every scored candidate, not just emitted signals.

Emitted signals are journaled in SQLite with full context (§13); everything
that scored below the gate used to vanish. Drift monitoring (PSI, T7),
threshold work (T12) and meta-labeling (T11) all need the full score
distribution, so each scan pass appends its scored candidates — meta columns
plus the feature vector that was scored — to a daily parquet sidecar:

    data/scores/{mode}/{yyyy-mm-dd}.parquet

Parquet, not SQLite: this is a wide time-series scanned columnarly (§9 —
nothing time-series goes into SQLite). Telemetry must never break a scan:
log_scores() catches everything and returns 0 on failure.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

META_COLS = ["ts", "symbol", "mode", "model_id", "direction", "score_raw",
             "confidence", "bucket", "emitted"]


def log_scores(scores_root: Path, mode: str, rows: list[dict],
               enabled: bool = True) -> int:
    """Append one pass's scored candidates. Returns rows written (0 when
    disabled, empty, or on any failure — never raises)."""
    if not enabled or not rows:
        return 0
    try:
        df = pd.DataFrame(rows)
        day = str(pd.to_datetime(df["ts"].iloc[0]).date())
        mdir = scores_root / mode
        mdir.mkdir(parents=True, exist_ok=True)
        path = mdir / f"{day}.parquet"
        if path.exists():
            old = pd.read_parquet(path)
            df = pd.concat([old, df], ignore_index=True)
        # same bar re-scored by an overlapping poll -> keep the latest row
        df = (df.drop_duplicates(subset=["symbol", "ts", "direction"], keep="last")
                .sort_values(["ts", "symbol"]))
        tmp = path.with_suffix(".tmp.parquet")
        df.to_parquet(tmp, index=False)
        tmp.replace(path)
        return len(df)
    except Exception:
        log.exception("score log write failed (scan unaffected)")
        return 0


def read_scores(scores_root: Path, mode: str,
                days: list[str] | None = None) -> pd.DataFrame:
    """All logged scores for a mode (optionally only the given YYYY-MM-DD
    days). Empty frame when nothing is logged yet."""
    mdir = scores_root / mode
    files = sorted(mdir.glob("*.parquet")) if mdir.exists() else []
    if days is not None:
        wanted = set(days)
        files = [f for f in files if f.stem in wanted]
    if not files:
        return pd.DataFrame(columns=META_COLS)
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
