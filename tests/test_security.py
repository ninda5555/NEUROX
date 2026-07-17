"""T13: always-on hardening headers + optional Tailscale-identity gate."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch, cfg_patch=None):
    monkeypatch.setenv("NEUROX_DB", str(tmp_path / "sec.db"))
    import src.api.app as appmod
    importlib.reload(appmod)
    if cfg_patch:
        cfg_patch(appmod.cfg._data)
    return appmod, TestClient(appmod.app)


def test_security_headers_on_every_response(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)
    r = client.get("/api/status")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_auth_off_by_default_allows_everything(tmp_path, monkeypatch):
    _, client = _client(tmp_path, monkeypatch)
    assert client.get("/api/status").status_code == 200      # no identity header


def test_auth_on_rejects_without_identity(tmp_path, monkeypatch):
    def on(d): d["security"]["require_tailscale_identity"] = True
    _, client = _client(tmp_path, monkeypatch, on)
    assert client.get("/api/status").status_code == 403
    r = client.get("/api/status", headers={"Tailscale-User-Login": "me@x.com"})
    assert r.status_code == 200


def test_auth_allowlist_enforced(tmp_path, monkeypatch):
    def on(d):
        d["security"]["require_tailscale_identity"] = True
        d["security"]["allowed_logins"] = ["owner@x.com"]
    _, client = _client(tmp_path, monkeypatch, on)
    assert client.get("/api/status",
                      headers={"Tailscale-User-Login": "stranger@x.com"}).status_code == 403
    assert client.get("/api/status",
                      headers={"Tailscale-User-Login": "owner@x.com"}).status_code == 200


def test_ws_respects_identity_gate(tmp_path, monkeypatch):
    def on(d): d["security"]["require_tailscale_identity"] = True
    _, client = _client(tmp_path, monkeypatch, on)
    with pytest.raises(Exception):        # server closes with 1008 before accept
        with client.websocket_connect("/ws"):
            pass
    # with identity, the socket opens and heartbeats
    with client.websocket_connect(
            "/ws", headers={"Tailscale-User-Login": "me@x.com"}) as ws:
        msg = ws.receive_json()
        assert msg["type"] == "heartbeat"
