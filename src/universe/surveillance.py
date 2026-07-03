"""NSE surveillance list (GSM / ASM) — CLAUDE.md §4 step 3.

Downloads NSE's daily consolidated surveillance indicator file
(REG_INDddmmyy.csv). NSE needs browser-like headers + a cookie warm-up
request. The file is cached locally per day; if today's download fails we
fall back to the newest cached file and record staleness — the UI must show
it, never silently trade an unscreened universe (§17.7).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from src import db as dbm
from src.timeutil import ist_date

URL_PATTERNS = [
    "https://nsearchives.nseindia.com/content/press/REG_IND{ddmmyy}.csv",
    "https://www.nseindia.com/content/press/REG_IND{ddmmyy}.csv",
]
WARMUP_URL = "https://www.nseindia.com"
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/csv,text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


class SurveillanceUnavailable(RuntimeError):
    pass


@dataclass
class Measure:
    kind: str    # 'GSM' | 'ASM_LT' | 'ASM_ST' | other indicator name
    stage: int   # 0 when the file gives no stage number


@dataclass
class SurveillanceList:
    src_date: dt.date
    stale: bool
    measures: dict[str, list[Measure]] = field(default_factory=dict)  # nse_code -> measures

    def for_symbol(self, nse_code: str) -> list[Measure]:
        return self.measures.get(nse_code.upper(), [])


def _cache_path(cache_dir: Path, day: dt.date) -> Path:
    return cache_dir / f"REG_IND_{day.isoformat()}.csv"


def _download_for_day(day: dt.date) -> bytes | None:
    ddmmyy = day.strftime("%d%m%y")
    with httpx.Client(headers=BROWSER_HEADERS, timeout=30, follow_redirects=True) as client:
        try:
            client.get(WARMUP_URL)  # cookie warm-up; failures here are fine
        except httpx.HTTPError:
            pass
        for pattern in URL_PATTERNS:
            url = pattern.format(ddmmyy=ddmmyy)
            try:
                r = client.get(url)
                if r.status_code == 200 and r.content.strip():
                    return r.content
            except httpx.HTTPError:
                continue
    return None


_STAGE_RE = re.compile(r"(\d+)")


def _parse_stage(cell: str) -> int | None:
    """'1' / 'Stage I'... -> stage number; blank/'-'/'NIL' -> None."""
    cell = cell.strip().upper()
    if not cell or cell in {"-", "NA", "NIL", "N.A.", "0"}:
        return None
    m = _STAGE_RE.search(cell)
    if m:
        return int(m.group(1))
    roman = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
    for token in reversed(cell.replace("STAGE", " ").split()):
        if token in roman:
            return roman[token]
    return 1  # flagged but unnumbered -> treat as stage 1, never ignore


def parse_reg_ind(content: bytes, src_date: dt.date, stale: bool = False) -> SurveillanceList:
    """Header-driven parse of the consolidated REG_IND file. NSE has reshaped
    this file before, so columns are located by header text; missing
    essentials fail loudly."""
    text = content.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    header_idx = None
    for i, row in enumerate(rows[:10]):
        if any("SYMBOL" in c.strip().upper() for c in row):
            header_idx = i
            break
    if header_idx is None:
        raise SurveillanceUnavailable(
            "REG_IND file has no SYMBOL header row — NSE format changed; "
            "update src/universe/surveillance.py and CLAUDE.md §4"
        )
    header = [c.strip().upper() for c in rows[header_idx]]
    sym_col = next(i for i, c in enumerate(header) if "SYMBOL" in c)

    def tokens(i: int) -> set[str]:
        return set(re.split(r"[^A-Z]+", header[i]))

    def find_cols(word: str) -> list[int]:
        return [i for i in range(len(header)) if word in tokens(i)]

    gsm_cols = find_cols("GSM")
    asm_lt_cols = [i for i in find_cols("ASM") if tokens(i) & {"LT", "LONG"}]
    asm_st_cols = [i for i in find_cols("ASM") if tokens(i) & {"ST", "SHORT"}]
    # An ASM column with no LT/ST qualifier still counts (format drift guard).
    asm_plain = [i for i in find_cols("ASM")
                 if i not in asm_lt_cols and i not in asm_st_cols]
    if not gsm_cols and not (asm_lt_cols or asm_st_cols or asm_plain):
        raise SurveillanceUnavailable(
            f"REG_IND header {header!r} has no GSM/ASM columns — NSE format "
            "changed; update src/universe/surveillance.py and CLAUDE.md §4"
        )

    out = SurveillanceList(src_date=src_date, stale=stale)
    for row in rows[header_idx + 1:]:
        if len(row) <= sym_col or not row[sym_col].strip():
            continue
        code = row[sym_col].strip().upper()
        measures: list[Measure] = []
        for cols, kind in ((gsm_cols, "GSM"), (asm_lt_cols, "ASM_LT"),
                           (asm_st_cols, "ASM_ST"), (asm_plain, "ASM_LT")):
            for ci in cols:
                if ci < len(row):
                    stage = _parse_stage(row[ci])
                    if stage is not None:
                        measures.append(Measure(kind=kind, stage=stage))
        if measures:
            out.measures.setdefault(code, []).extend(measures)
    return out


def get_surveillance(conn: sqlite3.Connection, cache_dir: Path,
                     lookback_days: int = 7,
                     today: dt.date | None = None) -> SurveillanceList | None:
    """Fetch today's list (searching back over weekends/holidays), else fall
    back to the newest cached file (stale=True), else None. Result date and
    any fetch error are recorded in app_state for UI staleness display."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    today = today or ist_date()

    for delta in range(lookback_days + 1):
        day = today - dt.timedelta(days=delta)
        cached = _cache_path(cache_dir, day)
        content = cached.read_bytes() if cached.exists() else None
        if content is None:
            content = _download_for_day(day)
            if content:
                cached.write_bytes(content)
        if content:
            lst = parse_reg_ind(content, src_date=day, stale=(delta > 0))
            dbm.set_state(conn, "surveillance_file_date", day.isoformat())
            dbm.set_state(conn, "surveillance_fetch_error", "")
            return lst

    # nothing downloadable in the window -> newest cached file of any age
    cached_files = sorted(cache_dir.glob("REG_IND_*.csv"), reverse=True)
    if cached_files:
        day = dt.date.fromisoformat(cached_files[0].stem.replace("REG_IND_", ""))
        lst = parse_reg_ind(cached_files[0].read_bytes(), src_date=day, stale=True)
        dbm.set_state(conn, "surveillance_file_date", day.isoformat())
        dbm.set_state(conn, "surveillance_fetch_error",
                      f"download failed; reusing cached file dated {day.isoformat()}")
        return lst

    dbm.set_state(conn, "surveillance_fetch_error",
                  "no surveillance file has ever been fetched — universe is UNSCREENED")
    return None
