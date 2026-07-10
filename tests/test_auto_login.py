"""Headless daily re-auth (CLAUDE.md §3.1, opt-in fyers.auto_login).

No live Fyers server here — this exercises the request_key threading,
TOTP generation, and auth_code parsing against a mocked transport, and
proves the whole thing is a no-op unless explicitly turned on.
"""

from __future__ import annotations

import copy
import json

import httpx
import pyotp
import pytest

from src.config import Config, DEFAULTS
from src.fyers import auto_login
from src.jobs.scheduler import job_daily_auto_login

TOTP_SECRET = pyotp.random_base32()
_RealClient = httpx.Client  # captured before any monkeypatching below


def _cfg(tmp_path, **fyers_overrides):
    data = copy.deepcopy(DEFAULTS)
    data["fyers"].update(app_id="ABCDE12345-100", secret_key="s3cr3t",
                         fyers_id="XY01234", totp_secret=TOTP_SECRET, pin="1234")
    data["fyers"].update(fyers_overrides)
    return Config(data, root=tmp_path)


def _mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path.endswith("/send_login_otp"):
            assert body["fy_id"] == "XY01234"
            return httpx.Response(200, json={"s": "ok", "request_key": "rk1"})
        if request.url.path.endswith("/verify_otp"):
            assert body["request_key"] == "rk1"
            assert pyotp.TOTP(TOTP_SECRET).verify(body["otp"], valid_window=1)
            return httpx.Response(200, json={"s": "ok", "request_key": "rk2"})
        if request.url.path.endswith("/verify_pin"):
            assert body["request_key"] == "rk2"
            assert body["identifier"] == "1234"
            return httpx.Response(200, json={"s": "ok", "data": {"access_token": "sess-tok"}})
        if request.url.path.endswith("/token"):
            assert request.headers["authorization"] == "Bearer sess-tok"
            assert body["app_id"] == "ABCDE12345"
            assert body["appType"] == "100"
            return httpx.Response(200, json={
                "Url": "https://127.0.0.1/?state=x&auth_code=AC-999&other=1"})
        return httpx.Response(404, json={"s": "error", "message": "unexpected"})
    return httpx.MockTransport(handler)


def test_headless_auth_code_full_flow(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    transport = _mock_transport()
    monkeypatch.setattr(httpx, "Client",
                        lambda *a, **k: _RealClient(transport=transport))
    code = auto_login.headless_auth_code(cfg)
    assert code == "AC-999"


def test_headless_auth_code_missing_fields_raises(tmp_path):
    cfg = _cfg(tmp_path, pin="")
    with pytest.raises(auto_login.AutoLoginError, match="pin"):
        auto_login.headless_auth_code(cfg)


def test_step_failure_names_itself(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"s": "error", "message": "bad creds"})
    monkeypatch.setattr(httpx, "Client",
                        lambda *a, **k: _RealClient(transport=httpx.MockTransport(handler)))
    cfg = _cfg(tmp_path)
    with pytest.raises(auto_login.AutoLoginError) as exc:
        auto_login.headless_auth_code(cfg)
    assert exc.value.step == "send_login_otp"


def test_scheduler_job_is_noop_when_auto_login_off(tmp_path, monkeypatch):
    monkeypatch.setattr("src.jobs.scheduler.load_config", lambda: _cfg(tmp_path))
    job_daily_auto_login()  # must not attempt any network call / raise


def test_scheduler_job_invokes_headless_login_when_on(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("src.jobs.scheduler.load_config",
                        lambda: _cfg(tmp_path, auto_login=True))
    monkeypatch.setattr("src.jobs.scheduler.auth.headless_login",
                        lambda cfg: calls.append(cfg) or "tok")
    job_daily_auto_login()
    assert len(calls) == 1
