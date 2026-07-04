"""Signal emission gate (CLAUDE.md §6.3, §17.4): a signal is emitted only
when calibrated confidence >= the mode threshold AND the risk engine
approves. The COMPLETE context is journaled before anything is returned for
display — journal-first, always."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.data.store import CandleStore
from src.journal.journal import insert_signal
from src.journal.paper import open_paper_trade
from src.models.explain import top_contributors
from src.risk import stops as stopmod
from src.risk.loss_limit import DayRiskTracker
from src.risk.portfolio_flags import flags_for_candidate
from src.risk.sizing import size_position


@dataclass
class Candidate:
    symbol: str
    mode: str                 # INTRADAY | SWING
    direction: int            # +1 / -1 (intraday only may be -1)
    confidence: float         # CALIBRATED
    price: float              # entry reference (bar close / next open ref)
    atr: float                # mode-appropriate ATR (5-min or 14d)
    features: dict            # full feature vector at emission
    orb_low: float | None = None
    orb_high: float | None = None
    liquidity_cr: float | None = None
    ts: str | None = None    # bar time (replay); None -> live now


def emit(conn: sqlite3.Connection, store: CandleStore, cfg, *, model_id: str,
         booster, feature_list: list[str], candidate: Candidate,
         regime: dict | None, tracker: DayRiskTracker) -> dict | None:
    """Returns the journaled signal card, or None (gate refused)."""
    c = candidate
    thr = cfg["signals.confidence_threshold"]
    if c.confidence < thr:
        return None
    if c.mode == "INTRADAY" and not tracker.allows_new_intraday():
        return None

    if c.mode == "INTRADAY":
        stop = stopmod.intraday_stop(c.price, c.direction, c.atr, c.orb_low, c.orb_high)
        holding, gap_note = stopmod.HOLDING_INTRADAY, None
        risk_pct_cfg = cfg["risk.intraday_risk_pct"]
    else:
        stop = stopmod.swing_stop(c.price, c.atr)
        holding, gap_note = stopmod.HOLDING_SWING, stopmod.GAP_NOTE_SWING
        risk_pct_cfg = cfg["risk.swing_risk_pct"]
    target = stopmod.target_for(c.price, stop)

    qty, capped = size_position(capital=cfg["risk.capital"], risk_pct=risk_pct_cfg,
                                entry=c.price, stop=stop,
                                max_notional_pct=cfg["risk.max_position_notional_pct"])
    if qty < 1:
        return None
    at_risk = qty * abs(c.price - stop)

    row = pd.DataFrame([{f: c.features.get(f, np.nan) for f in feature_list}])
    shap_top = top_contributors(booster, row, feature_list, k=6)
    flags = flags_for_candidate(conn, store, c.symbol)
    if capped:
        flags.append("Position capped at 20% of capital notional")
    ls = tracker.status()
    if ls["state"] == "warning":
        flags.append("Daily loss limit warning (75% reached)")

    n10 = round(c.confidence * 10)
    explanation = (f"{n10} of 10 similar setups worked historically "
                   f"(calibrated {c.confidence:.2f}). "
                   + " ".join(s["sentence"] for s in shap_top[:3])
                   + f" Plan risks ₹{at_risk:,.0f} "
                   f"({at_risk / cfg['risk.capital'] * 100:.2f}% of capital). {holding}")

    signal_id = insert_signal(
        conn, mode=c.mode, symbol=c.symbol, direction=c.direction,
        confidence=c.confidence, model_id=model_id, features=c.features,
        shap_top=shap_top, regime=regime, entry=c.price, stop=stop,
        target=target, qty=qty, capital_at_risk=at_risk,
        risk_pct=at_risk / cfg["risk.capital"] * 100, risk_flags=flags,
        holding_stmt=holding + (" " + gap_note if gap_note else ""),
        explanation=explanation, ts=c.ts)
    open_paper_trade(conn, signal_id, cfg["costs.per_side_pct"])
    return {"signal_id": signal_id, "symbol": c.symbol, "mode": c.mode,
            "direction": c.direction, "confidence": c.confidence,
            "entry": c.price, "stop": stop, "target": target, "qty": qty,
            "at_risk": at_risk, "flags": flags, "shap_top": shap_top,
            "explanation": explanation}
