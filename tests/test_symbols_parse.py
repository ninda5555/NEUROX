import pytest

from src.fyers.symbols import (Instrument, SymbolMasterFormatError,
                               parse_symbol_master, upsert_instruments)


def row(fytoken="10100000002885", name="RELIANCE INDUSTRIES LTD", lot="1",
        tick="0.05", isin="INE002A01018", ticker="NSE:RELIANCE-EQ"):
    # v3 NSE_CM positional layout; only consumed positions are meaningful
    return [fytoken, name, "0", lot, tick, isin, "0915-1530", "20260703",
            "", ticker, "10", "10", "2885", "RELIANCE"]


def to_csv(rows):
    return "\n".join(",".join(r) for r in rows).encode()


def test_parses_valid_master():
    rows = [
        row(),
        row(fytoken="10100000011460", name="TATA STEEL LTD", isin="INE081A01020",
            ticker="NSE:TATASTEEL-EQ"),
        row(fytoken="10100000000045", name="JAIN IRRIGATION",
            isin="INE175A01038", ticker="NSE:JISLJALEQS-BE"),
        row(fytoken="10100000026009", name="NIFTY 50", isin="",
            ticker="NSE:NIFTY50-INDEX"),
        row(fytoken="10100000031111", name="M&M LTD", isin="INE101A01026",
            ticker="NSE:M&M-EQ"),
    ]
    parsed = parse_symbol_master(to_csv(rows))
    by_code = {i.nse_code: i for i in parsed}
    assert by_code["RELIANCE"].series == "EQ"
    assert by_code["RELIANCE"].tick_size == 0.05
    assert by_code["JISLJALEQS"].series == "BE"
    assert by_code["NIFTY50"].is_index and by_code["NIFTY50"].series == "INDEX"
    assert by_code["M&M"].symbol == "NSE:M&M-EQ"


def test_too_narrow_rows_fail_loudly():
    with pytest.raises(SymbolMasterFormatError, match="layout has changed"):
        parse_symbol_master(b"123,too,short\n456,also,short")


def test_widespread_bad_rows_fail_loudly():
    bad = row(ticker="RELIANCE")  # no NSE: prefix / series suffix
    with pytest.raises(SymbolMasterFormatError, match="failed validation"):
        parse_symbol_master(to_csv([bad] * 50))


def test_few_stray_rows_tolerated():
    rows = [row(fytoken=f"1010000000{i:04d}", ticker=f"NSE:SYM{i}-EQ",
                isin=f"INE{i % 999:03d}A01018") for i in range(2000)]
    rows.append(row(ticker="BROKEN"))  # 1 stray row in 2001
    parsed = parse_symbol_master(to_csv(rows))
    assert len(parsed) == 2000


def test_upsert_counts_and_first_seen(conn):
    ins = [Instrument("101", "NSE:AAA-EQ", "AAA", "INE000A01001", "EQ",
                      "AAA LTD", 1, 0.05, False)]
    r1 = upsert_instruments(conn, ins)
    assert r1["inserted"] == 1 and r1["total_in_db"] == 1
    first = conn.execute("SELECT first_seen FROM instruments").fetchone()[0]
    r2 = upsert_instruments(conn, ins)  # same instrument again
    assert r2["inserted"] == 0 and r2["updated"] == 1
    assert conn.execute("SELECT first_seen FROM instruments").fetchone()[0] == first
