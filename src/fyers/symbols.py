"""Fyers NSE_CM symbol master ingest (CLAUDE.md §3.3, §4 step 1).

The master is a public CSV with NO header row; columns have changed before,
so we parse defensively BY POSITION, validate every extracted field, and fail
loudly on surprises rather than silently ingesting garbage. If this parser
fails after a Fyers format change: fix COLUMNS below AND update CLAUDE.md.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from dataclasses import dataclass

from src.timeutil import ist_iso, now_ist

SYMBOL_MASTER_URL = "https://public.fyers.in/sym_details/NSE_CM.csv"

# Positional layout observed for the v3 NSE_CM master. Only the positions we
# consume are listed; parsing validates each field and fails loudly if the
# layout drifted.
COLUMNS = {
    "fytoken": 0,       # e.g. 10100000002885
    "name": 1,          # RELIANCE INDUSTRIES LTD
    "lot_size": 3,
    "tick_size": 4,
    "isin": 5,
    "ticker": 9,        # NSE:RELIANCE-EQ / NSE:NIFTY50-INDEX
}
MIN_ROW_WIDTH = max(COLUMNS.values()) + 1

RE_FYTOKEN = re.compile(r"^\d{8,}$")
RE_TICKER = re.compile(r"^[A-Z]+:[A-Z0-9&\-]+-[A-Z0-9]+$")
RE_ISIN = re.compile(r"^IN[A-Z0-9]{10}$")

# Tolerate a small fraction of odd rows (Fyers occasionally ships stray
# entries) but anything beyond this is a format change -> fail loudly.
MAX_BAD_ROW_FRACTION = 0.005


class SymbolMasterFormatError(RuntimeError):
    pass


@dataclass
class Instrument:
    fytoken: str
    symbol: str      # NSE:RELIANCE-EQ
    nse_code: str    # RELIANCE
    isin: str | None
    series: str      # EQ / BE / BZ / INDEX / ...
    name: str
    lot_size: int
    tick_size: float
    is_index: bool


def _split_ticker(ticker: str) -> tuple[str, str]:
    body = ticker.split(":", 1)[1]
    code, _, series = body.rpartition("-")
    return code, series


def parse_symbol_master(content: bytes | str) -> list[Instrument]:
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if r and any(cell.strip() for cell in r)]
    if not rows:
        raise SymbolMasterFormatError("symbol master is empty")

    widths = {len(r) for r in rows}
    if min(widths) < MIN_ROW_WIDTH:
        raise SymbolMasterFormatError(
            f"symbol master rows have widths {sorted(widths)} but the parser "
            f"needs at least {MIN_ROW_WIDTH} columns — the Fyers layout has "
            "changed; update COLUMNS in src/fyers/symbols.py and CLAUDE.md §3.3"
        )

    out: list[Instrument] = []
    bad: list[tuple[int, str, list[str]]] = []
    for i, row in enumerate(rows):
        fytoken = row[COLUMNS["fytoken"]].strip()
        name = row[COLUMNS["name"]].strip()
        lot_raw = row[COLUMNS["lot_size"]].strip()
        tick_raw = row[COLUMNS["tick_size"]].strip()
        isin = row[COLUMNS["isin"]].strip()
        ticker = row[COLUMNS["ticker"]].strip()

        problem = None
        if not RE_FYTOKEN.match(fytoken):
            problem = f"fytoken {fytoken!r} not numeric"
        elif not RE_TICKER.match(ticker):
            problem = f"ticker {ticker!r} unexpected format"
        elif isin and not RE_ISIN.match(isin):
            problem = f"isin {isin!r} unexpected format"
        if problem:
            bad.append((i, problem, row))
            continue

        code, series = _split_ticker(ticker)
        try:
            lot = int(float(lot_raw)) if lot_raw else 1
            tick = float(tick_raw) if tick_raw else 0.0
        except ValueError:
            bad.append((i, f"lot/tick not numeric: {lot_raw!r}/{tick_raw!r}", row))
            continue

        out.append(Instrument(
            fytoken=fytoken, symbol=ticker, nse_code=code,
            isin=isin or None, series=series, name=name,
            lot_size=lot, tick_size=tick, is_index=(series == "INDEX"),
        ))

    if len(bad) > max(5, int(len(rows) * MAX_BAD_ROW_FRACTION)):
        examples = "\n".join(f"  row {i}: {why}" for i, why, _ in bad[:5])
        raise SymbolMasterFormatError(
            f"{len(bad)}/{len(rows)} rows failed validation — the Fyers "
            f"NSE_CM layout has changed. Examples:\n{examples}\n"
            "Fix COLUMNS in src/fyers/symbols.py and update CLAUDE.md §3.3."
        )
    if not out:
        raise SymbolMasterFormatError("no valid rows parsed from symbol master")
    return out


def fetch_symbol_master(client) -> bytes:
    """Download via the shared rate limiter (it is a Fyers REST call)."""
    return client.get_public_file(SYMBOL_MASTER_URL)


def upsert_instruments(conn: sqlite3.Connection, instruments: list[Instrument]) -> dict:
    now = ist_iso(now_ist())
    before = conn.execute("SELECT COUNT(*) AS n FROM instruments").fetchone()["n"]
    conn.executemany(
        """INSERT INTO instruments
           (fytoken, symbol, nse_code, isin, series, name, lot_size,
            tick_size, is_index, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(fytoken) DO UPDATE SET
             symbol=excluded.symbol, nse_code=excluded.nse_code,
             isin=excluded.isin, series=excluded.series,
             name=excluded.name, lot_size=excluded.lot_size,
             tick_size=excluded.tick_size, is_index=excluded.is_index,
             last_seen=excluded.last_seen""",
        [(i.fytoken, i.symbol, i.nse_code, i.isin, i.series, i.name,
          i.lot_size, i.tick_size, int(i.is_index), now, now)
         for i in instruments],
    )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) AS n FROM instruments").fetchone()["n"]
    return {"parsed": len(instruments), "inserted": total - before,
            "updated": len(instruments) - (total - before), "total_in_db": total}
