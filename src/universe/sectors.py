"""Sector classification (CLAUDE.md §9 instruments.sector; source decided
04-Jul-2026): NSE index constituent files on nsearchives carry an Industry
column. Total Market (~750) + Microcap 250 cover the tradable universe.
NIFTY50 membership is kept as metadata for the 'core' badge (§4)."""

from __future__ import annotations

import csv
import io
import json
import sqlite3

import httpx

from src import db as dbm

BASE = "https://nsearchives.nseindia.com/content/indices/"
SECTOR_FILES = ["ind_niftytotalmarket_list.csv", "ind_niftymicrocap250_list.csv",
                "ind_nifty500list.csv"]
NIFTY50_FILE = "ind_nifty50list.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _rows(content: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig", "replace"))))


def fetch_sector_map() -> tuple[dict[str, str], dict[str, str], list[str]]:
    """-> (symbol->industry, isin->industry, nifty50 symbols). Fails loudly
    if nothing is retrievable; partial sources are fine (first source wins)."""
    by_symbol: dict[str, str] = {}
    by_isin: dict[str, str] = {}
    nifty50: list[str] = []
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as c:
        for f in SECTOR_FILES:
            try:
                r = c.get(BASE + f)
                if r.status_code != 200:
                    continue
                for row in _rows(r.content):
                    sym = (row.get("Symbol") or "").strip().upper()
                    ind = (row.get("Industry") or "").strip()
                    isin = (row.get("ISIN Code") or "").strip().upper()
                    if sym and ind:
                        by_symbol.setdefault(sym, ind)
                    if isin and ind:
                        by_isin.setdefault(isin, ind)
            except httpx.HTTPError:
                continue
        try:
            r = c.get(BASE + NIFTY50_FILE)
            if r.status_code == 200:
                nifty50 = [(row.get("Symbol") or "").strip().upper()
                           for row in _rows(r.content)]
        except httpx.HTTPError:
            pass
    if not by_symbol:
        raise RuntimeError("no sector source retrievable — instruments.sector "
                           "left as-is (flagged, not silent)")
    return by_symbol, by_isin, [s for s in nifty50 if s]


def update_sectors(conn: sqlite3.Connection) -> dict:
    by_symbol, by_isin, nifty50 = fetch_sector_map()
    updated = 0
    for r in conn.execute("SELECT fytoken, nse_code, isin FROM instruments "
                          "WHERE is_index=0").fetchall():
        sector = by_symbol.get(r["nse_code"]) or by_isin.get(r["isin"] or "")
        if sector:
            conn.execute("UPDATE instruments SET sector=? WHERE fytoken=?",
                         (sector, r["fytoken"]))
            updated += 1
    conn.commit()
    if nifty50:
        dbm.set_state(conn, "nifty50_members", json.dumps(nifty50))
    covered = conn.execute(
        "SELECT COUNT(*) n FROM instruments WHERE sector IS NOT NULL").fetchone()["n"]
    return {"mapped": updated, "with_sector": covered, "nifty50": len(nifty50)}
