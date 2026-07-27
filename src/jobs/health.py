"""Upstream health probes (raised by a 2026-07-27 incident: History API
returning -403 while quotes worked fine, with no way to see that from the
dashboard). profile/quotes/history are checked SEPARATELY and persisted
distinctly so the exact failing Fyers surface is always known, never
inferred from a downstream symptom like "no signals today". Run once at
scheduler startup and folded into the existing hourly heartbeat job."""

from __future__ import annotations

import datetime as dt
import json

from src import db as dbm
from src.fyers.client import FyersApiError
from src.timeutil import now_ist

PROBE_SYMBOL = "NSE:RELIANCE-EQ"
PROBE_KEYS = ("profile", "quotes", "history")


def _ok() -> dict:
    return {"ok": True, "message": None, "checked_at": now_ist().isoformat(timespec="seconds")}


def _fail(exc: Exception) -> dict:
    if isinstance(exc, FyersApiError) and isinstance(exc.response, dict):
        code, msg = exc.response.get("code"), exc.response.get("message")
        message = f"{code}: {msg}" if code is not None else (msg or str(exc.response))
    else:
        message = str(exc)[:300]
    return {"ok": False, "message": message, "checked_at": now_ist().isoformat(timespec="seconds")}


def probe_profile(client) -> dict:
    try:
        client.profile()
        return _ok()
    except Exception as e:
        return _fail(e)


def probe_quotes(client, symbol: str = PROBE_SYMBOL) -> dict:
    try:
        client.quotes([symbol])
        return _ok()
    except Exception as e:
        return _fail(e)


def probe_history(client, symbol: str = PROBE_SYMBOL) -> dict:
    try:
        end = now_ist().date()
        client.history(symbol, "1d", end - dt.timedelta(days=5), end)
        return _ok()
    except Exception as e:
        return _fail(e)


def run_probes(conn, client) -> dict:
    """Probe all three surfaces independently and persist each one under its
    own app_state key (health_profile / health_quotes / health_history) —
    a failure in one must never mask or get averaged into the others."""
    result = {"profile": probe_profile(client),
              "quotes": probe_quotes(client),
              "history": probe_history(client)}
    for k, v in result.items():
        dbm.set_state(conn, f"health_{k}", json.dumps(v))
    return result


def read_health(conn) -> dict:
    """Last known probe results. May be older than the caller expects (e.g.
    the scheduler has been down) — callers that care about freshness should
    check each entry's checked_at, which is exactly the failure mode this
    exists to catch."""
    out = {}
    for k in PROBE_KEYS:
        raw = dbm.get_state(conn, f"health_{k}")
        out[k] = json.loads(raw) if raw else None
    return out
