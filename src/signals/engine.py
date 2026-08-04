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
from src.universe.earnings import earnings_flag


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


def expected_r(confidence: float, entry: float, stop: float, target: float,
               round_trip_per_share: float) -> float:
    """Expected R-multiple after modeled round-trip costs.

        E[R] = p * (reward/risk) - (1 - p) * 1 - cost/risk

    R is denominated in the plan's own risk unit (entry -> stop), so this is
    directly comparable across setups with different stop widths. Costs are
    charged in the same unit, which is where intraday hurts: a 0.30% 5-min
    ATR against a 0.10% round trip spends ~0.33R on costs before the trade
    has done anything, versus ~0.05R for a 2% daily ATR.
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return float("-inf")
    rr = abs(target - entry) / risk
    return confidence * rr - (1.0 - confidence) - round_trip_per_share / risk


def emit(conn: sqlite3.Connection, store: CandleStore, cfg, *, model_id: str,
         booster, feature_list: list[str], candidate: Candidate,
         regime: dict | None, tracker: DayRiskTracker) -> dict | None:
    """Returns the journaled signal card, or None (gate refused)."""
    from src.models.calibrate import bucket_of
    c = candidate
    thr = cfg["signals.confidence_threshold"]
    # T12 (all-zero bumps = off): regimes that historically punished setups
    # can demand more confidence before anything is emitted
    bucket = bucket_of(c.features.get("regime_vix"), c.features.get("regime_breadth"))
    bump = cfg["signals.regime_threshold_bump"][bucket]
    if c.confidence < thr + bump:
        return None
    if c.mode == "INTRADAY" and not tracker.allows_new_intraday():
        return None

    if c.mode == "INTRADAY":
        stop = stopmod.intraday_stop(c.price, c.direction, c.atr, c.orb_low,
                                     c.orb_high, cfg["labels.min_barrier_atr_pct"])
        holding, gap_note = stopmod.HOLDING_INTRADAY, None
        risk_pct_cfg = cfg["risk.intraday_risk_pct"]
    else:
        stop = stopmod.swing_stop(c.price, c.atr)
        holding, gap_note = stopmod.HOLDING_SWING, stopmod.GAP_NOTE_SWING
        risk_pct_cfg = cfg["risk.swing_risk_pct"]
    target = stopmod.target_for(c.price, stop)

    # Cost-viability gate (§7): if the natural stop is so tight that modeled
    # round-trip cost dominates the risk, the setup has no edge after costs.
    round_trip_per_share = 2 * cfg["costs.per_side_pct"] / 100.0 * c.price
    if abs(c.price - stop) < cfg["risk.min_stop_to_cost"] * round_trip_per_share:
        return None

    # Post-cost expectancy gate (added 2026-08-01). The confidence gate above
    # is a probability threshold, but whether a setup is worth taking depends
    # on the PAYOFF too: at TP 1.5x / SL 1.0x ATR, break-even is p = 0.40, so
    # 0.60 silently demands +0.50R per trade while a 2.5:1 setup would be
    # profitable far lower. This checks the thing that actually matters —
    # expected R after modeled costs — so the bar is consistent with each
    # setup's own risk/reward instead of assuming one payoff shape.
    #
    # It runs AFTER the confidence gate and can only ever REJECT more, never
    # admit anything the confidence gate refused. Loosening confidence to
    # emit more signals is explicitly not what this is for.
    exp_r = expected_r(c.confidence, c.price, stop, target, round_trip_per_share)
    if exp_r < cfg["risk.min_expected_r"]:
        return None

    qty, capped = size_position(capital=cfg["risk.capital"], risk_pct=risk_pct_cfg,
                                entry=c.price, stop=stop,
                                max_notional_pct=cfg["risk.max_position_notional_pct"])
    if qty < 1:
        return None
    at_risk = qty * abs(c.price - stop)

    row = pd.DataFrame([{f: c.features.get(f, np.nan) for f in feature_list}])
    shap_top = top_contributors(booster, row, feature_list, k=6)
    flags = flags_for_candidate(conn, store, c.symbol)
    if c.mode == "SWING":
        ef = earnings_flag(conn, c.symbol.split(":")[-1].rsplit("-", 1)[0])
        if ef:
            flags.append(ef)
    if capped:
        flags.append("Position capped at 20% of capital notional")
    if bump > 0:
        flags.append(f"Regime-tightened gate in effect: this tape required "
                     f"{thr + bump:.2f} confidence (base {thr:.2f})")
    ls = tracker.status()
    if ls["state"] == "warning":
        flags.append("Daily loss limit warning (75% reached)")

    n10 = round(c.confidence * 10)
    explanation = (f"{n10} of 10 similar setups worked historically "
                   f"(calibrated {c.confidence:.2f}). "
                   + " ".join(s["sentence"] for s in shap_top[:3])
                   + f" Plan risks ₹{at_risk:,.0f} "
                   f"({at_risk / cfg['risk.capital'] * 100:.2f}% of capital). "
                   f"Expected value after modeled costs: {exp_r:+.2f}R. {holding}")

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
            "at_risk": at_risk, "expected_r": exp_r, "flags": flags,
            "shap_top": shap_top, "explanation": explanation}
