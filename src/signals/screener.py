"""Factor screener (added 06-Aug-2026) — the honest replacement for the
prediction engine.

WHY THIS EXISTS
---------------
The ML path was tested to destruction (CLAUDE.md §0). Intraday has no
cross-sectional edge at all. Swing has real but small ranking skill that
could not be turned into a calibrated probability: the final calibrator
refused to build because its top confidence band rested on 2 samples.

So this module deliberately does LESS than the model did, and says less:

  * it RANKS stocks against each other today. It does not estimate a
    probability, and nothing here may ever be displayed as a win rate, a
    hit rate, or a chance of success (§17.1). There is no evidence for
    such a number, and inventing one is the specific failure this project
    already diagnosed once.
  * it is SWING ONLY. Intraday's per-symbol features measured ~0.000
    cross-sectional IC, so an intraday ranking would be ordering noise.
  * every input is shown with its own contribution, so the user can see
    exactly what pushed a stock up the list and disagree with it.

WHAT THE SCORE IS
-----------------
An equal-weighted composite of the seven swing features that survived a
cross-sectional IC test with Newey-West correction for overlapping labels.
Each feature is ranked within today's universe, signed by the DIRECTION its
IC measured, and averaged. The output is a percentile position in today's
cross-section — "this stock ranks 3rd of 847 on these factors" — which is a
statement about relative ordering and nothing more.

Measured cross-sectional IC (12 folds, 5y daily, 430,622 labelled rows):
    rsi_14        +0.0397 (t +5.0)     dist_20dma    +0.0354 (t +5.1)
    dist_50dma    +0.0296 (t +3.4)     rs_nifty_20d  +0.0292 (t +4.0)
    gk_vol_trend  -0.0291 (t -4.8)     macd_hist     +0.0188 (t +3.6)
    vol_zscore    +0.0152 (t +4.0)
An IC of 0.03 is a real but weak effect. It is consistent with documented
cross-sectional momentum. It is NOT a basis for automated trading, which is
why this produces a shortlist for a human to review rather than orders.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import numpy as np
import pandas as pd

from src.data.store import CandleStore
from src.features import swing as swing_mod
from src.features.regime import NIFTY_SYMBOL
from src.risk import stops as stopmod
from src.risk.sizing import size_position
from src.timeutil import now_ist

# feature -> sign of its measured cross-sectional IC. The sign is the whole
# reason a factor points the way it does; gk_vol_trend is negative because
# rising realised vol ranked WORSE, so falling vol scores higher.
FACTORS: dict[str, float] = {
    "rsi_14": +1.0,
    "dist_20dma": +1.0,
    "dist_50dma": +1.0,
    "rs_nifty_20d": +1.0,
    "gk_vol_trend": -1.0,
    "macd_hist": +1.0,
    "vol_zscore": +1.0,
}

# plain-English reading for each factor, high end and low end
_PHRASE = {
    "rsi_14": ("Momentum is firmer than most of the market",
               "Momentum is weaker than most of the market"),
    "dist_20dma": ("Trading well above its 20-day average",
                   "Trading below its 20-day average"),
    "dist_50dma": ("Holding above its 50-day average",
                   "Sitting below its 50-day average"),
    "rs_nifty_20d": ("Outperforming NIFTY over the last month",
                     "Lagging NIFTY over the last month"),
    "gk_vol_trend": ("Volatility is settling down",
                     "Volatility is picking up"),
    "macd_hist": ("MACD momentum is positive",
                  "MACD momentum is negative"),
    "vol_zscore": ("Volume is running above its own average",
                   "Volume is thinner than usual"),
}

MIN_NAMES = 30          # a cross-section thinner than this is not a ranking
MIN_HISTORY_BARS = 210  # 200-DMA + headroom


def _rank_pct(s: pd.Series) -> pd.Series:
    """Rank to [0, 1] within the cross-section; constant -> 0.5 exactly."""
    n = s.notna().sum()
    if n < 2:
        return pd.Series(np.nan, index=s.index)
    return (s.rank() - 1) / (n - 1)


def build_screen(conn: sqlite3.Connection, store: CandleStore, cfg,
                 symbols: list[str], top_n: int = 20) -> dict:
    """Rank today's universe on the surviving swing factors.

    Returns a dict ready for the API. Never raises on a single bad symbol —
    a stock without enough history is skipped and counted, not fatal.
    """
    nifty = store.read_candles("1d", NIFTY_SYMBOL)
    nifty_daily = nifty if not nifty.empty else None
    sector_of = {r["symbol"]: r["sector"] for r in conn.execute(
        "SELECT symbol, sector FROM instruments WHERE sector IS NOT NULL")}

    rows, skipped = [], 0
    asof_date = None
    for sym in symbols:
        try:
            d = store.read_candles("1d", sym)
            if len(d) < MIN_HISTORY_BARS:
                skipped += 1
                continue
            f = swing_mod.build_symbol_frame(d, nifty_daily).iloc[-1]
            atr_pct = f.get("atr14_pct", np.nan)
            price = float(d["close"].iloc[-1])
            if not np.isfinite(atr_pct) or atr_pct <= 0 or price <= 0:
                skipped += 1
                continue
            bar_date = pd.Timestamp(f["ts"]).date()
            asof_date = max(asof_date, bar_date) if asof_date else bar_date
            rec = {"symbol": sym,
                   "nse_code": sym.split(":")[-1].replace("-EQ", ""),
                   "sector": sector_of.get(sym),
                   "price": price, "atr_pct": float(atr_pct),
                   "bar_date": bar_date.isoformat()}
            for c in FACTORS:
                v = f.get(c, np.nan)
                rec[c] = None if pd.isna(v) else float(v)
            rows.append(rec)
        except Exception:
            skipped += 1
            continue

    if len(rows) < MIN_NAMES:
        return {"mode": "SWING", "asof": None, "generated_at":
                now_ist().isoformat(timespec="seconds"),
                "n_screened": len(rows), "n_skipped": skipped, "rows": [],
                "empty_reason":
                    f"Only {len(rows)} stocks had enough daily history to rank "
                    f"(need {MIN_NAMES}). This is a data problem, not a market "
                    "call — check that the daily backfill has run."}

    df = pd.DataFrame(rows)

    # cross-sectional rank per factor, signed by its measured IC direction
    parts = {}
    for c, sign in FACTORS.items():
        r = _rank_pct(df[c])
        parts[c] = (r if sign > 0 else 1.0 - r)
    P = pd.DataFrame(parts, index=df.index)
    df["_score"] = P.mean(axis=1, skipna=True)
    df["_n_factors"] = P.notna().sum(axis=1)
    # Carry each factor's percentile ON the row before sorting. Reading them
    # out of a separate frame after reset_index() silently pairs every stock
    # with another stock's reasons — caught in testing, and precisely the
    # kind of quiet mismatch this tool exists to avoid.
    for c in FACTORS:
        df[f"_pct_{c}"] = P[c]
    # a stock scored on fewer than half the factors is not comparable
    df = df[df["_n_factors"] >= 4].copy()
    df = df.sort_values("_score", ascending=False).reset_index(drop=True)
    df["_rank"] = np.arange(1, len(df) + 1)

    total = len(df)
    out = []
    for _, r in df.head(top_n).iterrows():
        price, atr_pct = float(r["price"]), float(r["atr_pct"])
        atr = atr_pct / 100.0 * price
        stop = stopmod.swing_stop(price, atr)
        target = stopmod.target_for(price, stop)
        qty, capped = size_position(
            capital=cfg["risk.capital"], risk_pct=cfg["risk.swing_risk_pct"],
            entry=price, stop=stop,
            max_notional_pct=cfg["risk.max_position_notional_pct"])
        at_risk = qty * abs(price - stop)

        drivers = []
        for c in FACTORS:
            p = r.get(f"_pct_{c}", np.nan)
            if pd.isna(p):
                continue
            hi, lo = _PHRASE[c]
            drivers.append({"factor": c, "percentile": round(float(p) * 100, 1),
                            "value": r[c],
                            "sentence": hi if p >= 0.5 else lo})
        drivers.sort(key=lambda x: -abs(x["percentile"] - 50))

        out.append({
            "rank": int(r["_rank"]), "symbol": r["symbol"],
            "nse_code": r["nse_code"], "sector": r["sector"],
            "price": round(price, 2), "atr_pct": round(atr_pct, 2),
            "bar_date": r["bar_date"],
            # NOT a probability. Position in today's cross-section.
            "score_pct": round(float(r["_score"]) * 100, 1),
            "percentile_of": total,
            "entry": round(price, 2), "stop": round(stop, 2),
            "target": round(target, 2),
            "risk_per_share": round(abs(price - stop), 2),
            "reward_risk": round(abs(target - price) / abs(price - stop), 2),
            "qty": qty, "at_risk": round(at_risk, 0),
            "risk_pct_of_capital": round(at_risk / cfg["risk.capital"] * 100, 2),
            "position_capped": capped,
            "drivers": drivers[:5], "all_drivers": drivers,
            "holding": stopmod.HOLDING_SWING,
            "gap_note": stopmod.GAP_NOTE_SWING,
        })

    return {"mode": "SWING",
            "asof": asof_date.isoformat() if asof_date else None,
            "generated_at": now_ist().isoformat(timespec="seconds"),
            "n_screened": total, "n_skipped": skipped, "rows": out,
            "empty_reason": None,
            "factors": {c: ("higher is better" if s > 0 else "lower is better")
                        for c, s in FACTORS.items()}}


def screen_is_stale(asof: str | None, max_age_days: int = 5) -> bool:
    """True when the newest daily bar behind a screen is old enough that the
    ranking is describing a market that has moved on."""
    if not asof:
        return True
    try:
        d = dt.date.fromisoformat(asof)
    except ValueError:
        return True
    return (now_ist().date() - d).days > max_age_days
