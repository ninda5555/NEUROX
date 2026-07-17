"""FastAPI backend (CLAUDE.md §10 api/): REST + /ws push. The UI is a pure
consumer — every number it shows comes from the journal/DB, never invented.

    uvicorn src.api.app:app --port 8000
    NEUROX_DB=/path/alt.db uvicorn src.api.app:app   # demo/sandbox journal
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from src import db as dbm
from src.config import load_config
from src.data.store import CandleStore
from src.fyers import auth
from src.journal.journal import list_signals
from src.risk.loss_limit import DayRiskTracker
from src.timeutil import ist_date, now_ist

cfg = load_config()
from src.logsafe import install_redaction  # noqa: E402
install_redaction(cfg)  # §12: secrets never reach uvicorn's log stream (T8)
DB_PATH = os.environ.get("NEUROX_DB") or str(cfg.path("paths.db"))
app = FastAPI(title="NSE Trading Assistant", version="0.4.0")

DISCLOSURE = ("This is an independent-developer analytics tool, not an "
              "institutional trading system. It estimates probabilities; it "
              "does not predict the future. Most retail intraday traders lose "
              "money (SEBI studies). Not investment advice.")

# T13: always-on hardening headers. CSP is tuned to the SPA — bundled JS is
# 'self', React inline style attributes need style-src 'unsafe-inline', /ws is
# same-origin (connect-src 'self'). The one external resource is the Geist
# webfont (index.html links Google Fonts; the viewer's browser fetches it
# directly, so it works even on an egress-restricted server) — its two hosts
# are the only third parties allowed, and scripts/frames/objects stay locked.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'"),
}


def _identity_ok(headers) -> bool:
    """T13 optional gate (off by default). Trust the Tailscale-User-Login
    header ONLY because the documented deployment reaches this app solely via
    `tailscale serve` on 127.0.0.1, which sets it and which off-tailnet
    callers cannot reach (§12). Not a replacement for the network perimeter —
    defense in depth + the multi-user allowlist path."""
    if not cfg["security.require_tailscale_identity"]:
        return True
    login = headers.get("tailscale-user-login")
    allowed = cfg["security.allowed_logins"]
    return bool(login) and (not allowed or login in allowed)


@app.middleware("http")
async def _security(request, call_next):
    if not _identity_ok(request.headers):
        return JSONResponse({"detail": "tailnet identity required"}, status_code=403)
    resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp


def conn():
    c = dbm.connect(DB_PATH)
    dbm.init_db(c)
    return c


def store() -> CandleStore:
    return CandleStore(cfg.path("paths.candles"))


@app.get("/api/status")
def status():
    c = conn()
    tok = auth.token_status(cfg)
    snap = dbm.get_state(c, "universe_snap_date")
    uni = {"snap_date": snap, "included": 0, "scanned": 0}
    if snap:
        row = c.execute(
            "SELECT SUM(included) inc, COUNT(*) n FROM universe_snapshot "
            "WHERE snap_date=?", (snap,)).fetchone()
        uni.update(included=row["inc"] or 0, scanned=row["n"] or 0)
    surv_date = dbm.get_state(c, "surveillance_file_date")
    surv_err = dbm.get_state(c, "surveillance_fetch_error") or ""
    regime = c.execute("SELECT * FROM market_regime ORDER BY regime_date DESC "
                       "LIMIT 1").fetchone()
    tracker = DayRiskTracker(c, cfg["risk.capital"],
                             cfg["risk.daily_loss_limit_pct"],
                             cfg["risk.loss_limit_warn_frac"])
    drift = {}
    for mode in ("INTRADAY", "SWING"):
        outcome = dbm.get_state(c, f"drift_{mode}")
        psi = dbm.get_state(c, f"psi_{mode}")
        drift[mode] = {"outcome": json.loads(outcome) if outcome else None,
                       "psi": json.loads(psi) if psi else None}
    return {
        "now_ist": now_ist().isoformat(timespec="seconds"),
        "token": tok,
        "ws_feed": dbm.get_state(c, "ws_status", "offline"),
        "universe": uni,
        "surveillance": {"date": surv_date, "error": surv_err,
                         "stale": bool(surv_err) or surv_date != (snap or surv_date)},
        "regime": dict(regime) if regime else None,
        "loss_limit": tracker.status(),
        "drift": drift,
        "disclosure": DISCLOSURE,
        "mode_thresholds": _thresholds(dict(regime) if regime else None),
    }


def _thresholds(regime: dict | None) -> dict:
    """T12 banner: base threshold, per-bucket bumps, and what the CURRENT
    regime effectively demands — shown, never silent."""
    from src.models.calibrate import bucket_of
    base = cfg["signals.confidence_threshold"]
    bumps = cfg["signals.regime_threshold_bump"]
    bucket = bucket_of((regime or {}).get("vix"),
                       (regime or {}).get("breadth_adv_dec"))
    return {"confidence": base, "regime_bumps": bumps,
            "current_bucket": bucket,
            "effective": round(base + bumps[bucket], 4),
            "tightened": bumps[bucket] > 0}


def _spark(symbol: str, mode: str) -> list[float]:
    tf = "5min" if mode == "INTRADAY" else "1d"
    df = store().read_candles(tf, symbol)
    return [round(float(x), 2) for x in df["close"].tail(24)]


@app.get("/api/scanner")
def scanner(mode: str = "INTRADAY"):
    c = conn()
    today = ist_date().isoformat()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM signals WHERE mode=? AND substr(ts,1,10)=? "
        "ORDER BY confidence DESC LIMIT ?",
        (mode, today, cfg["signals.scanner_top_n"]))]
    if not rows:  # most recent day with signals (dashboards stay reviewable)
        last = c.execute("SELECT substr(ts,1,10) d FROM signals WHERE mode=? "
                         "ORDER BY ts DESC LIMIT 1", (mode,)).fetchone()
        if last:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM signals WHERE mode=? AND substr(ts,1,10)=? "
                "ORDER BY confidence DESC LIMIT ?",
                (mode, last["d"], cfg["signals.scanner_top_n"]))]
    cards = []
    for i, s in enumerate(rows):
        shap = json.loads(s["shap_top"] or "[]")
        cards.append({
            "signal_id": s["signal_id"], "rank": i + 1, "ts": s["ts"],
            "symbol": s["symbol"].split(":")[-1].replace("-EQ", ""),
            "full_symbol": s["symbol"], "direction": s["direction"],
            "confidence": s["confidence"],
            "phrase": f"{round(s['confidence'] * 10)} of 10 similar setups "
                      "worked historically",
            "spark": _spark(s["symbol"], mode),
            "shap3": [x["sentence"] for x in shap[:3]],
            "shap_full": shap,
            "features": json.loads(s["features"] or "{}"),
            "regime": json.loads(s["regime"]) if s["regime"] else None,
            "entry": s["entry"], "stop": s["stop_loss"], "target": s["target"],
            "qty": s["qty"], "at_risk": s["capital_at_risk"],
            "risk_pct": s["risk_pct"],
            "flags": json.loads(s["risk_flags"] or "[]"),
            "holding": s["holding_stmt"], "explanation": s["explanation"],
        })
    return {"mode": mode, "cards": cards, "signal_date": rows[0]["ts"][:10] if rows else None,
            "is_today": bool(rows) and rows[0]["ts"][:10] == today,
            "empty_reason": None if cards else
            "Nothing cleared the confidence and risk gates this scan. The "
            "system saying 'no trade' is a valid, healthy outcome."}


@app.get("/api/journal")
def journal(mode: str = "INTRADAY", q: str = ""):
    c = conn()
    sigs = list_signals(c, mode=mode, query=q or None)
    out = []
    for s in sigs:
        oc = {r["horizon"]: dict(r) for r in c.execute(
            "SELECT * FROM signal_outcomes WHERE signal_id=?", (s["signal_id"],))}
        out.append({**{k: s[k] for k in ("signal_id", "ts", "symbol", "direction",
                                         "confidence")},
                    "outcomes": {h: {"r": o.get("r_multiple"), "status": o.get("status"),
                                     "mae": o.get("mae_pct"), "mfe": o.get("mfe_pct")}
                                 for h, o in oc.items()}})
    trades = [dict(r) for r in c.execute(
        """SELECT p.*, s.mode FROM paper_trades p JOIN signals s
           ON s.signal_id = p.signal_id WHERE s.mode=? AND p.closed_at IS NOT NULL
           ORDER BY p.opened_at""", (mode,))]
    curve, cum = [], 0.0
    for t in trades:
        cum += t["r_multiple"] or 0.0
        curve.append(round(cum, 3))
    pnl = sum(t["pnl"] or 0 for t in trades)
    return {"mode": mode, "signals": out, "equity_r": curve,
            "paper": {"n": len(trades), "pnl": round(pnl, 0),
                      "costs": round(sum(t["costs_modeled"] or 0 for t in trades), 0)},
            "horizons": ["30m", "60m", "eod"] if mode == "INTRADAY" else ["1d", "5d", "10d"]}


@app.get("/api/model")
def model(mode: str = "INTRADAY"):
    c = conn()
    m = c.execute("SELECT * FROM models WHERE mode=? AND is_active=1", (mode,)).fetchone()
    if not m:
        return {"mode": mode, "active": None}
    folds = [dict(r) for r in c.execute(
        "SELECT * FROM cv_folds WHERE model_id=? ORDER BY fold", (m["model_id"],))]
    history = []
    for r in c.execute(
            "SELECT model_id, trained_at, is_active, red_flags, promotion "
            "FROM models WHERE mode=? ORDER BY trained_at DESC LIMIT 6", (mode,)):
        h = dict(r)
        h["promotion"] = json.loads(h["promotion"]) if h["promotion"] else None
        history.append(h)
    # the newest registration's champion/challenger decision, promoted or
    # held — the Model page shows WHY, never just the outcome
    latest_decision = history[0]["promotion"] if history else None
    ic = {}
    ic_files = sorted(Path(cfg.path("paths.features")).glob(f"ic_report_{mode}_*.json"))
    if ic_files:
        rep = json.loads(ic_files[-1].read_text())
        ic = {k: v for k, v in sorted(rep["ic"].items(),
                                      key=lambda kv: -abs(kv[1] or 0))
              if v == v and k in json.loads(m["feature_list"])}
    cvr = json.loads(m["cv_report"])
    return {"mode": mode,
            "active": {"model_id": m["model_id"], "trained_at": m["trained_at"],
                       "train_start": m["train_start"], "train_end": m["train_end"],
                       "n_features": len(json.loads(m["feature_list"])),
                       "n_folds": len(folds),
                       "precision_mean": cvr.get("precision_mean"),
                       "precision_std": cvr.get("precision_std")},
            "folds": folds,
            "red_flags": json.loads(m["red_flags"] or "[]"),
            "calibration": json.loads(m["calibration"]),
            "ic": ic, "history": history, "latest_decision": latest_decision}


@app.get("/api/universe")
def universe():
    c = conn()
    snap = dbm.get_state(c, "universe_snap_date")
    if not snap:
        return {"snap_date": None, "rows": [], "reasons": {}}
    rows = [dict(r) for r in c.execute(
        """SELECT u.*, i.nse_code, i.sector FROM universe_snapshot u
           LEFT JOIN instruments i ON i.symbol = u.symbol
           WHERE u.snap_date=? ORDER BY u.included DESC,
           u.median_turnover_cr DESC NULLS LAST LIMIT 400""", (snap,))]
    agg = c.execute(
        "SELECT SUM(included) inc, COUNT(*) n FROM universe_snapshot "
        "WHERE snap_date=?", (snap,)).fetchone()
    reasons = {r["exclude_reason"]: r["n"] for r in c.execute(
        """SELECT exclude_reason, COUNT(*) n FROM universe_snapshot
           WHERE snap_date=? AND included=0 GROUP BY exclude_reason
           ORDER BY n DESC""", (snap,))}
    return {"snap_date": snap, "included": agg["inc"], "scanned": agg["n"],
            "reasons": reasons, "rows": rows,
            "surveillance": {"date": dbm.get_state(c, "surveillance_file_date"),
                             "error": dbm.get_state(c, "surveillance_fetch_error") or ""}}


@app.get("/api/search")
def search(q: str = ""):
    c = conn()
    snap = dbm.get_state(c, "universe_snap_date")
    like = f"%{q.upper()}%"
    rows = [dict(r) for r in c.execute(
        """SELECT i.nse_code, i.symbol, i.sector, i.series, u.included,
           u.exclude_reason, u.median_turnover_cr, u.atr_pct
           FROM instruments i LEFT JOIN universe_snapshot u
           ON u.symbol = i.symbol AND u.snap_date = ?
           WHERE i.is_index = 0 AND (i.nse_code LIKE ? OR i.sector LIKE ?)
           ORDER BY u.included DESC, u.median_turnover_cr DESC NULLS LAST
           LIMIT 60""", (snap, like, like))]
    today = ist_date().isoformat()
    setups = {r["symbol"]: dict(r) for r in c.execute(
        "SELECT symbol, mode, direction, confidence FROM signals "
        "WHERE substr(ts,1,10)=?", (today,))}
    for r in rows:
        s = setups.get(r["symbol"])
        r["setup"] = (f"{s['mode'].title()} {'LONG' if s['direction'] > 0 else 'SHORT'}"
                      f" · {s['confidence']:.2f}") if s else None
    return {"rows": rows, "q": q}


@app.websocket("/ws")
async def ws(sock: WebSocket):
    if not _identity_ok(sock.headers):        # T13: http middleware misses WS
        await sock.close(code=1008)           # policy violation
        return
    await sock.accept()
    try:
        while True:
            await sock.send_json({"type": "heartbeat", "status": status()})
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass


ui_dist = Path(__file__).resolve().parents[1] / "ui" / "dist"
if ui_dist.exists():
    app.mount("/", StaticFiles(directory=str(ui_dist), html=True), name="ui")
