"""Universe pipeline orchestrator (CLAUDE.md §4).

Order of filters — first matching exclusion wins and is recorded:
  1. symbol master ingest -> instruments
  2. series filter: only -EQ trades; -BE/-BZ are T2T (cannot be intraday-traded)
  3. surveillance filter: GSM (all stages), ASM ST/LT stage >= threshold
  4. liquidity filter from rolling 20 daily sessions
Every symbol keeps its exclusion reason so the UI can answer
"why isn't stock X shown?".
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from src import db as dbm
from src.fyers import symbols as symmod
from src.timeutil import ist_date
from src.universe import liquidity as liqmod
from src.universe import surveillance as survmod

T2T_SERIES = {"BE", "BZ"}


@dataclass
class UniverseSummary:
    snap_date: str
    scanned: int = 0
    included: int = 0
    excluded: int = 0
    by_reason: dict[str, int] = field(default_factory=dict)
    surveillance_src_date: str | None = None
    surveillance_stale: bool = False
    surveillance_missing: bool = False
    liquidity_skipped: bool = False
    no_history: int = 0


def _series_reason(series: str) -> str | None:
    if series == "EQ":
        return None
    if series in T2T_SERIES:
        return "T2T"
    return f"SERIES_{series}"


def _surveillance_reason(measures: list[survmod.Measure], cfg) -> str | None:
    # GSM first (harsher measure), then ASM by stage threshold.
    gsm = [m for m in measures if m.kind == "GSM"]
    if gsm and cfg["universe.exclude_gsm_all"]:
        return f"GSM_{max(m.stage for m in gsm)}"
    min_stage = cfg["universe.exclude_asm_min_stage"]
    asm = [m for m in measures if m.kind in ("ASM_LT", "ASM_ST") and m.stage >= min_stage]
    if asm:
        worst = max(asm, key=lambda m: m.stage)
        return f"{worst.kind}{worst.stage}"
    return None


def build_universe(conn: sqlite3.Connection, cfg, store=None,
                   master_content: bytes | None = None,
                   client=None, skip_liquidity: bool = False) -> UniverseSummary:
    """Build today's universe_snapshot. `master_content` lets an offline copy
    of the symbol master be used; otherwise it is fetched via `client`
    (through the shared rate limiter). `store` is the candle store used for
    liquidity metrics; symbols without history are excluded as NO_HISTORY —
    an unverifiable stock is never silently included."""
    snap_date = ist_date().isoformat()
    summary = UniverseSummary(snap_date=snap_date)

    # 1. symbol master -> instruments
    if master_content is None:
        if client is None:
            raise ValueError("need either master_content or a FyersClient")
        master_content = symmod.fetch_symbol_master(client)
    instruments = symmod.parse_symbol_master(master_content)
    symmod.upsert_instruments(conn, instruments)

    # 3. surveillance list (fetched once for the run)
    surv = survmod.get_surveillance(
        conn, Path(cfg.path("paths.surveillance_cache")),
        lookback_days=cfg["universe.surveillance_lookback_days"],
    )
    if surv is None:
        summary.surveillance_missing = True
    else:
        summary.surveillance_src_date = surv.src_date.isoformat()
        summary.surveillance_stale = surv.stale
    summary.liquidity_skipped = skip_liquidity

    rows = []
    for ins in instruments:
        if ins.is_index:
            continue
        summary.scanned += 1
        reason: str | None = _series_reason(ins.series)
        metrics: liqmod.LiquidityMetrics | None = None

        if reason is None and surv is not None:
            reason = _surveillance_reason(surv.for_instrument(ins.nse_code, ins.isin), cfg)

        if reason is None and not skip_liquidity:
            daily = store.read_candles("1d", ins.symbol) if store is not None else None
            if daily is None or daily.empty:
                reason = "NO_HISTORY"
                summary.no_history += 1
            else:
                metrics = liqmod.compute_metrics(daily)
                reason = liqmod.decide(metrics, cfg)

        included = reason is None
        summary.included += included
        summary.excluded += not included
        if reason:
            summary.by_reason[reason] = summary.by_reason.get(reason, 0) + 1
        rows.append((
            snap_date, ins.symbol, int(included), reason,
            metrics.median_turnover_cr if metrics else None,
            metrics.adv_20d if metrics else None,
            metrics.atr_pct if metrics else None,
            summary.surveillance_src_date,
        ))

    conn.execute("DELETE FROM universe_snapshot WHERE snap_date = ?", (snap_date,))
    conn.executemany(
        """INSERT INTO universe_snapshot
           (snap_date, symbol, included, exclude_reason, median_turnover_cr,
            adv_20d, atr_pct, surveillance_src_date)
           VALUES (?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    dbm.set_state(conn, "universe_snap_date", snap_date)
    return summary


def latest_included_symbols(conn: sqlite3.Connection) -> list[str]:
    snap = dbm.get_state(conn, "universe_snap_date")
    if not snap:
        return []
    return [r["symbol"] for r in conn.execute(
        "SELECT symbol FROM universe_snapshot WHERE snap_date=? AND included=1 "
        "ORDER BY symbol", (snap,),
    )]


def eq_candidate_symbols(conn: sqlite3.Connection) -> list[str]:
    """All -EQ instruments — the set daily backfill must cover so the
    liquidity filter can run."""
    return [r["symbol"] for r in conn.execute(
        "SELECT symbol FROM instruments WHERE series='EQ' AND is_index=0 "
        "ORDER BY symbol",
    )]
