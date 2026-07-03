import datetime as dt

from src.data.store import CandleStore
from src.timeutil import IST, ist_date
from src.universe.master import build_universe, latest_included_symbols


def master_row(fytoken, name, ticker, isin="INE000A01001"):
    return ",".join([fytoken, name, "0", "1", "0.05", isin, "0915-1530",
                     "20260703", "", ticker, "10", "10", "1", name.split()[0]])


MASTER = "\n".join([
    master_row("10100000000001", "GOOD CO", "NSE:GOODCO-EQ", "INE001A01001"),
    master_row("10100000000002", "PENNY CO", "NSE:PENNY-EQ", "INE002A01001"),
    master_row("10100000000003", "THIN CO", "NSE:THINCO-EQ", "INE003A01001"),
    master_row("10100000000004", "NEW CO", "NSE:NEWCO-EQ", "INE004A01001"),
    master_row("10100000000005", "GAPPY CO", "NSE:GAPPY-EQ", "INE005A01001"),
    master_row("10100000000006", "NOHIST CO", "NSE:NOHIST-EQ", "INE006A01001"),
    master_row("10100000000007", "T2T CO", "NSE:T2TCO-BE", "INE007A01001"),
    master_row("10100000000008", "GSM CO", "NSE:GSMCO-EQ", "INE008A01001"),
    master_row("10100000000009", "ASM CO", "NSE:ASMCO-EQ", "INE009A01001"),
    master_row("10100000000010", "ASM ST ONE", "NSE:ASMST1-EQ", "INE010A01001"),
    master_row("10100000000011", "NIFTY 50", "NSE:NIFTY50-INDEX", ""),
]).encode()

REG_IND = b"""\
Consolidated list as on today
SYMBOL,SECURITY NAME,GSM STAGE,ASM LT STAGE,ASM ST STAGE
GSMCO,GSM Co,2,,
ASMCO,ASM Co,,2,
ASMST1,ASM ST One,,,1
"""


def daily(store, symbol, sessions=80, price=100.0, volume=1_000_000.0,
          zero_vol_last=0):
    base = IST.localize(dt.datetime(2026, 3, 2, 9, 15))
    rows = []
    for i in range(sessions):
        vol = 0.0 if (zero_vol_last and i >= sessions - zero_vol_last) else volume
        rows.append({"ts": base + dt.timedelta(days=i), "open": price,
                     "high": price * 1.01, "low": price * 0.99,
                     "close": price, "volume": vol})
    store.write_candles("1d", symbol, rows)


def test_full_pipeline_exclusion_reasons(conn, cfg, tmp_path):
    store = CandleStore(tmp_path / "candles")
    daily(store, "NSE:GOODCO-EQ")                                   # ₹10cr/day
    daily(store, "NSE:PENNY-EQ", price=15.0)                        # < ₹20
    daily(store, "NSE:THINCO-EQ", volume=30_000.0)                  # ₹0.3cr
    daily(store, "NSE:NEWCO-EQ", sessions=30)                       # < 60 sessions
    daily(store, "NSE:GAPPY-EQ", zero_vol_last=5)                   # 15/20 nonzero
    # NOHIST gets no candles at all

    # pre-seed the surveillance cache so no network is touched
    cache = cfg.path("paths.surveillance_cache")
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"REG_IND_{ist_date().isoformat()}.csv").write_bytes(REG_IND)

    s = build_universe(conn, cfg, store=store, master_content=MASTER)

    got = {r["symbol"]: (r["included"], r["exclude_reason"]) for r in conn.execute(
        "SELECT symbol, included, exclude_reason FROM universe_snapshot "
        "WHERE snap_date=?", (s.snap_date,))}

    assert got["NSE:GOODCO-EQ"] == (1, None)
    assert got["NSE:PENNY-EQ"] == (0, "PRICE_LT_20")
    assert got["NSE:THINCO-EQ"] == (0, "LOW_TURNOVER")
    assert got["NSE:NEWCO-EQ"] == (0, "LISTED_LT_60")
    assert got["NSE:GAPPY-EQ"] == (0, "ILLIQUID_GAPS")
    assert got["NSE:NOHIST-EQ"] == (0, "NO_HISTORY")
    assert got["NSE:T2TCO-BE"] == (0, "T2T")
    assert got["NSE:GSMCO-EQ"] == (0, "GSM_2")
    assert got["NSE:ASMCO-EQ"] == (0, "ASM_LT2")
    assert got["NSE:ASMST1-EQ"] == (0, "ASM_ST1")
    assert "NSE:NIFTY50-INDEX" not in got        # indices are metadata, not universe

    assert s.included == 1 and s.scanned == 10
    assert not s.surveillance_missing and not s.surveillance_stale
    assert latest_included_symbols(conn) == ["NSE:GOODCO-EQ"]

    # metrics persisted for the UI ("why isn't stock X shown?")
    row = conn.execute(
        "SELECT median_turnover_cr, atr_pct FROM universe_snapshot "
        "WHERE snap_date=? AND symbol='NSE:GOODCO-EQ'", (s.snap_date,)).fetchone()
    assert abs(row["median_turnover_cr"] - 10.0) < 0.01
    assert row["atr_pct"] > 0


def test_surveillance_missing_is_flagged_not_silent(conn, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr("src.universe.surveillance._download_for_day", lambda d: None)
    store = CandleStore(tmp_path / "candles")
    daily(store, "NSE:GOODCO-EQ")
    s = build_universe(conn, cfg, store=store, master_content=MASTER)
    assert s.surveillance_missing is True
    # snapshot rows carry NULL surveillance_src_date -> UI shows staleness
    row = conn.execute("SELECT surveillance_src_date FROM universe_snapshot "
                       "WHERE snap_date=? LIMIT 1", (s.snap_date,)).fetchone()
    assert row["surveillance_src_date"] is None


def test_rebuild_same_day_is_idempotent(conn, cfg, tmp_path):
    store = CandleStore(tmp_path / "candles")
    cache = cfg.path("paths.surveillance_cache")
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"REG_IND_{ist_date().isoformat()}.csv").write_bytes(REG_IND)
    build_universe(conn, cfg, store=store, master_content=MASTER)
    build_universe(conn, cfg, store=store, master_content=MASTER)
    n = conn.execute("SELECT COUNT(*) AS n FROM universe_snapshot").fetchone()["n"]
    assert n == 10  # no duplicate rows for the same snap_date
