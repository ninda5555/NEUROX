import datetime as dt

import pytest

from src import db as dbm
from src.universe.surveillance import (SurveillanceUnavailable, get_surveillance,
                                       parse_reg_ind)

REG_IND_FIXTURE = b"""\
Securities under various Surveillance Measures (Consolidated) as on 03-Jul-2026
SYMBOL,SECURITY NAME,GSM STAGE,ASM LT STAGE,ASM ST STAGE
GSMCO,GSM Company Ltd,2,,
ASMCO,ASM Company Ltd,,Stage II,
ASMST1,ASM ST One Ltd,,,1
CLEANCO,Clean Company Ltd,,,
"""


def test_parse_reg_ind_measures():
    lst = parse_reg_ind(REG_IND_FIXTURE, src_date=dt.date(2026, 7, 3))
    assert [(m.kind, m.stage) for m in lst.for_symbol("GSMCO")] == [("GSM", 2)]
    assert [(m.kind, m.stage) for m in lst.for_symbol("ASMCO")] == [("ASM_LT", 2)]
    assert [(m.kind, m.stage) for m in lst.for_symbol("ASMST1")] == [("ASM_ST", 1)]
    assert lst.for_symbol("CLEANCO") == []
    assert lst.for_symbol("NOTLISTED") == []


def test_parse_rejects_unrecognizable_file():
    with pytest.raises(SurveillanceUnavailable, match="format changed"):
        parse_reg_ind(b"totally,unrelated\n1,2", src_date=dt.date(2026, 7, 3))


def test_st_in_stage_word_not_confused_with_asm_st():
    # 'STAGE' contains the letters 'ST'; the LT column must not be
    # double-classified as ST (token-based matching).
    lst = parse_reg_ind(REG_IND_FIXTURE, src_date=dt.date(2026, 7, 3))
    kinds = {m.kind for m in lst.for_symbol("ASMCO")}
    assert kinds == {"ASM_LT"}


def test_get_surveillance_uses_cache_and_tracks_date(conn, tmp_path):
    cache = tmp_path / "surv"
    cache.mkdir()
    today = dt.date(2026, 7, 3)
    (cache / f"REG_IND_{today.isoformat()}.csv").write_bytes(REG_IND_FIXTURE)
    lst = get_surveillance(conn, cache, lookback_days=2, today=today)
    assert lst is not None and not lst.stale
    assert dbm.get_state(conn, "surveillance_file_date") == "2026-07-03"
    assert dbm.get_state(conn, "surveillance_fetch_error") == ""


def test_get_surveillance_stale_fallback(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("src.universe.surveillance._download_for_day",
                        lambda day: None)  # network dead
    cache = tmp_path / "surv"
    cache.mkdir()
    old_day = dt.date(2026, 6, 20)  # older than the lookback window
    (cache / f"REG_IND_{old_day.isoformat()}.csv").write_bytes(REG_IND_FIXTURE)
    lst = get_surveillance(conn, cache, lookback_days=3, today=dt.date(2026, 7, 3))
    assert lst is not None and lst.stale
    assert lst.src_date == old_day
    assert "reusing cached file" in dbm.get_state(conn, "surveillance_fetch_error")


def test_get_surveillance_never_fetched_is_loud(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("src.universe.surveillance._download_for_day",
                        lambda day: None)
    lst = get_surveillance(conn, tmp_path / "empty", lookback_days=2,
                           today=dt.date(2026, 7, 3))
    assert lst is None
    assert "UNSCREENED" in dbm.get_state(conn, "surveillance_fetch_error")
