import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEUROX_DB", str(tmp_path / "api.db"))
    import src.api.app as appmod
    importlib.reload(appmod)
    return TestClient(appmod.app)


def test_status_carries_disclosure_and_loss_state(client):
    r = client.get("/api/status").json()
    assert "not an institutional trading system" in r["disclosure"]
    assert r["loss_limit"]["state"] in ("ok", "warning", "halted")
    assert r["token"]["valid"] in (True, False)


def test_scanner_empty_state_is_first_class(client):
    r = client.get("/api/scanner", params={"mode": "INTRADAY"}).json()
    assert r["cards"] == []
    assert "no trade" in r["empty_reason"]


def test_journal_and_model_endpoints_shape(client):
    j = client.get("/api/journal", params={"mode": "SWING"}).json()
    assert j["horizons"] == ["1d", "5d", "10d"]
    assert j["signals"] == [] and j["equity_r"] == []
    m = client.get("/api/model", params={"mode": "INTRADAY"}).json()
    assert m["active"] is None  # fresh DB: no model rows

    u = client.get("/api/universe").json()
    assert u["snap_date"] is None and u["rows"] == []

    s = client.get("/api/search", params={"q": "REL"}).json()
    assert s["rows"] == []


def test_no_accuracy_language_in_api_responses(client):
    for path in ("/api/status", "/api/scanner", "/api/journal", "/api/universe"):
        body = client.get(path).text.lower()
        assert "accura" not in body
