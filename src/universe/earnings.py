"""Earnings-date proximity (CLAUDE.md §5 swing extra-risk: earnings flag).
Source: NSE corporate event calendar API (cookie warm-up like the other NSE
endpoints). Stored in app_state as {nse_code: 'YYYY-MM-DD'} for the nearest
upcoming 'Results' event; consumers flag proximity, never block (V1)."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

import httpx

from src import db as dbm
from src.timeutil import ist_date

API = "https://www.nseindia.com/api/event-calendar"
WARMUP = "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar"
HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/126.0.0.0 Safari/537.36"),
           "Accept": "application/json,*/*",
           "Referer": WARMUP}
STATE_KEY = "earnings_calendar"


def fetch_earnings_calendar(conn: sqlite3.Connection) -> dict[str, str]:
    """Fetch + persist {symbol: nearest upcoming results date}. On failure the
    previous stored map stays (staleness visible via its own dates)."""
    try:
        with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as c:
            c.get(WARMUP)
            rows = c.get(API).json()
    except (httpx.HTTPError, ValueError):
        return load_earnings(conn)
    out: dict[str, str] = {}
    today = ist_date()
    for r in rows if isinstance(rows, list) else []:
        sym = (r.get("symbol") or "").strip().upper()
        purpose = (r.get("purpose") or r.get("bm_desc") or "").lower()
        if not sym or "result" not in purpose:
            continue
        try:
            d = dt.datetime.strptime(r.get("date", ""), "%d-%b-%Y").date()
        except ValueError:
            continue
        if d >= today and (sym not in out or d < dt.date.fromisoformat(out[sym])):
            out[sym] = d.isoformat()
    if out:
        dbm.set_state(conn, STATE_KEY, json.dumps(out))
    return out or load_earnings(conn)


def load_earnings(conn: sqlite3.Connection) -> dict[str, str]:
    raw = dbm.get_state(conn, STATE_KEY)
    return json.loads(raw) if raw else {}


def earnings_flag(conn: sqlite3.Connection, nse_code: str,
                  within_days: int = 7) -> str | None:
    cal = load_earnings(conn)
    iso = cal.get(nse_code.upper())
    if not iso:
        return None
    days = (dt.date.fromisoformat(iso) - ist_date()).days
    if 0 <= days <= within_days:
        return f"Earnings in {days} day{'s' if days != 1 else ''} ({iso})"
    return None
