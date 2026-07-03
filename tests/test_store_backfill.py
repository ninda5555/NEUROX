import datetime as dt

import pytest

from src.data.backfill import backfill_symbol
from src.data.store import CandleStore
from src.timeutil import IST


def ist(y, mo, d, h=9, mi=15):
    return IST.localize(dt.datetime(y, mo, d, h, mi))


def candles(days, base=ist(2026, 6, 1)):
    return [{"ts": base + dt.timedelta(days=i), "open": 100.0 + i,
             "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i,
             "volume": 1_000_000.0} for i in range(days)]


def test_roundtrip_ist_and_monthly_partitions(tmp_path):
    store = CandleStore(tmp_path)
    rows = candles(45)  # spans June + July -> two monthly files
    store.write_candles("1d", "NSE:RELIANCE-EQ", rows)
    files = sorted((tmp_path / "1d" / "RELIANCE-EQ").glob("*.parquet"))
    assert [f.name for f in files] == ["2026-06.parquet", "2026-07.parquet"]
    df = store.read_candles("1d", "NSE:RELIANCE-EQ")
    assert len(df) == 45
    assert str(df["ts"].dt.tz) == "Asia/Kolkata"
    assert df["ts"].is_monotonic_increasing


def test_write_deduplicates_on_ts(tmp_path):
    store = CandleStore(tmp_path)
    store.write_candles("1d", "NSE:X-EQ", candles(5))
    updated = candles(5)
    updated[4]["close"] = 999.0
    store.write_candles("1d", "NSE:X-EQ", updated)  # overlapping re-fetch
    df = store.read_candles("1d", "NSE:X-EQ")
    assert len(df) == 5
    assert df["close"].iloc[-1] == 999.0  # keep='last' wins


def test_naive_ts_rejected(tmp_path):
    store = CandleStore(tmp_path)
    with pytest.raises(Exception, match="naive"):
        store.write_candles("1d", "NSE:X-EQ", [{
            "ts": dt.datetime(2026, 7, 1), "open": 1, "high": 1, "low": 1,
            "close": 1, "volume": 1}])


def test_last_ts_for_resume(tmp_path):
    store = CandleStore(tmp_path)
    assert store.last_ts("1d", "NSE:X-EQ") is None
    store.write_candles("1d", "NSE:X-EQ", candles(3))
    assert store.last_ts("1d", "NSE:X-EQ") == ist(2026, 6, 3)


class FakeClient:
    """Emulates FyersClient.history: epoch candles per requested chunk."""

    def __init__(self):
        self.calls: list[tuple[dt.date, dt.date]] = []

    def history(self, symbol, tf, range_from, range_to):
        self.calls.append((range_from, range_to))
        out, cur = [], range_from
        while cur <= range_to:
            epoch = int(IST.localize(dt.datetime(cur.year, cur.month, cur.day, 9, 15)).timestamp())
            out.append([epoch, 100, 101, 99, 100.5, 1_000_000])
            cur += dt.timedelta(days=1)
        return out


def test_backfill_chunks_and_resumes(tmp_path):
    store = CandleStore(tmp_path)
    client = FakeClient()
    end = dt.date(2026, 7, 2)
    r = backfill_symbol(client, store, "NSE:X-EQ", "1d", days=400, end=end)
    assert len(client.calls) == 2                      # 400d > 366d ceiling
    assert all((b - a).days + 1 <= 366 for a, b in client.calls)
    assert r["rows"] == 400

    client2 = FakeClient()
    r2 = backfill_symbol(client2, store, "NSE:X-EQ", "1d", days=400, end=end)
    assert len(client2.calls) == 1                     # resume: only the tail
    assert client2.calls[0][0] == end                  # re-fetch last stored day
