"""Stops & targets (CLAUDE.md §5, §7). Intraday: 1.5×ATR(5-min), snapped
beyond opening-range structure. Swing: 2.5×ATR(daily) — wider, gap-aware;
gaps CAN blow through it and the UI must say so. Target: 2:1 by default."""

from __future__ import annotations

TARGET_RR = 2.0
ORB_BUFFER_FRAC = 0.10  # snap 0.1×ATR beyond the structure level

HOLDING_INTRADAY = "Intraday — auto square-off by 15:15 IST."
HOLDING_SWING = "Swing — planned hold 5–10 sessions (delivery / CNC)."
GAP_NOTE_SWING = ("Holds overnight — gaps can jump the stop; the plan's risk "
                  "is an estimate, not a guarantee.")


def intraday_stop(entry: float, direction: int, atr5: float,
                  orb_low: float | None, orb_high: float | None) -> float:
    """ATR stop, widened beyond the opening-range level when that structure
    sits between entry and the raw ATR stop."""
    raw = entry - direction * 1.5 * atr5
    buf = ORB_BUFFER_FRAC * atr5
    if direction > 0 and orb_low is not None and raw > orb_low - buf and orb_low < entry:
        raw = orb_low - buf
    if direction < 0 and orb_high is not None and raw < orb_high + buf and orb_high > entry:
        raw = orb_high + buf
    return raw


def swing_stop(entry: float, atr14d: float) -> float:
    return entry - 2.5 * atr14d  # swing is long-only (§5)


def target_for(entry: float, stop: float, rr: float = TARGET_RR) -> float:
    return entry + rr * (entry - stop)
