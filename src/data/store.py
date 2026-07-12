"""Parquet candle store, queried via DuckDB (CLAUDE.md §2, §9).

Layout: data/candles/{tf}/{SYMBOL}/{yyyy-mm}.parquet, tf in {'1d','5min'}.
Timestamps are stored tz-aware and always returned in IST. Nothing
time-series goes into SQLite.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pandas as pd

from src.timeutil import IST, ensure_aware

CANDLE_COLS = ["ts", "open", "high", "low", "close", "volume"]


def _safe_symbol(symbol: str) -> str:
    """NSE:RELIANCE-EQ -> RELIANCE-EQ (colon is not filesystem-friendly)."""
    return symbol.split(":", 1)[-1]


class CandleStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _dir(self, tf: str, symbol: str) -> Path:
        return self.root / tf / _safe_symbol(symbol)

    def write_candles(self, tf: str, symbol: str, records: list[dict] | pd.DataFrame) -> int:
        """Merge candles into monthly partitions, de-duplicated on ts."""
        df = pd.DataFrame(records)
        if df.empty:
            return 0
        missing = set(CANDLE_COLS) - set(df.columns)
        if missing:
            raise ValueError(f"candle frame missing columns: {sorted(missing)}")
        df = df[CANDLE_COLS].copy()
        df["ts"] = df["ts"].map(lambda t: ensure_aware(t).astimezone(IST))
        df = df.sort_values("ts")

        new_rows = 0
        for (y, m), part in df.groupby([df["ts"].dt.year, df["ts"].dt.month]):
            pdir = self._dir(tf, symbol)
            pdir.mkdir(parents=True, exist_ok=True)
            path = pdir / f"{y:04d}-{m:02d}.parquet"
            n_existing = 0
            if path.exists():
                existing = pd.read_parquet(path)
                existing["ts"] = pd.to_datetime(existing["ts"]).dt.tz_convert(IST)
                n_existing = len(existing)
                part = pd.concat([existing, part], ignore_index=True)
            part = (part.drop_duplicates(subset="ts", keep="last")
                        .sort_values("ts").reset_index(drop=True))
            tmp = path.with_suffix(".tmp.parquet")
            part.to_parquet(tmp, index=False)
            tmp.replace(path)
            new_rows += len(part) - n_existing
        return new_rows

    def read_candles(self, tf: str, symbol: str,
                     start: dt.datetime | None = None,
                     end: dt.datetime | None = None) -> pd.DataFrame:
        pdir = self._dir(tf, symbol)
        files = sorted(pdir.glob("*.parquet")) if pdir.exists() else []
        if not files:
            return pd.DataFrame(columns=CANDLE_COLS)
        con = duckdb.connect()
        try:
            query = f"SELECT * FROM read_parquet({[str(f) for f in files]!r}) ORDER BY ts"
            df = con.execute(query).df()
        finally:
            con.close()
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(IST)
        if start is not None:
            df = df[df["ts"] >= ensure_aware(start)]
        if end is not None:
            df = df[df["ts"] <= ensure_aware(end)]
        return df.reset_index(drop=True)

    def last_ts(self, tf: str, symbol: str) -> dt.datetime | None:
        """Most recent stored candle timestamp (for backfill resume)."""
        pdir = self._dir(tf, symbol)
        files = sorted(pdir.glob("*.parquet")) if pdir.exists() else []
        if not files:
            return None
        df = pd.read_parquet(files[-1], columns=["ts"])
        if df.empty:
            return None
        return pd.to_datetime(df["ts"]).max().tz_convert(IST).to_pydatetime()

    def symbols_with_data(self, tf: str) -> list[str]:
        tdir = self.root / tf
        if not tdir.exists():
            return []
        return sorted(p.name for p in tdir.iterdir() if p.is_dir())
