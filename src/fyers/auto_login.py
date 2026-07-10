"""Opt-in headless daily re-auth (CLAUDE.md §3.1, §12; deploy/ unattended VPS).

The SEBI daily-2FA requirement (§3.1) cannot be removed and this module does
not try to — it automates *completing* the same auth-code flow a human does
by hand each morning, using a TOTP secret + PIN instead of a browser.

OFF BY DEFAULT (`fyers.auto_login: false`). This trades some security (your
TOTP seed and PIN live in config.yaml on the server) for a fully unattended
box. Read the trade-off in SETUP.md before turning it on.

Ground truth caveat: unlike the rest of src/fyers/, the three endpoints below
are Fyers' *browser* login endpoints, reverse-engineered from the same
request/response shape their own web login uses (auth-code flow, §3.1) —
they are not documented in the official API v3 reference the way the REST/
WS endpoints in client.py and ws.py are. Fyers can change them without
notice; if that happens this raises AutoLoginError with the failing step
named, and the operator falls back to the manual flow in src.run/daily_auth.
"""

from __future__ import annotations

import pyotp
from urllib.parse import parse_qs, urlparse

import httpx

_BASE = "https://api-t2.fyers.in/vagator/v2"
_BASE_TOKEN = "https://api-t1.fyers.in/api/v3"
_LOGIN_APP_ID = "2"  # Fyers web-login's own client id — constant, unrelated
                     # to the caller's registered API app_id below.


class AutoLoginError(RuntimeError):
    def __init__(self, step: str, detail: str):
        self.step = step
        super().__init__(f"headless login failed at {step!r}: {detail}")


def _post(client: httpx.Client, url: str, payload: dict, step: str) -> dict:
    resp = client.post(url, json=payload, timeout=15)
    try:
        data = resp.json()
    except ValueError as e:
        raise AutoLoginError(step, f"non-JSON response ({resp.status_code})") from e
    if resp.status_code >= 400 or data.get("s") == "error":
        raise AutoLoginError(step, str(data.get("message") or data))
    return data


def _send_login_otp(client: httpx.Client, fyers_id: str) -> str:
    data = _post(client, f"{_BASE}/send_login_otp",
                {"fy_id": fyers_id, "app_id": _LOGIN_APP_ID}, "send_login_otp")
    key = data.get("request_key")
    if not key:
        raise AutoLoginError("send_login_otp", f"no request_key in {data!r}")
    return key


def _verify_totp(client: httpx.Client, request_key: str, totp_secret: str) -> str:
    code = pyotp.TOTP(totp_secret.strip().replace(" ", "")).now()
    data = _post(client, f"{_BASE}/verify_otp",
                {"request_key": request_key, "otp": code}, "verify_totp")
    key = data.get("request_key")
    if not key:
        raise AutoLoginError("verify_totp", f"no request_key in {data!r}")
    return key


def _verify_pin(client: httpx.Client, request_key: str, pin: str) -> str:
    data = _post(client, f"{_BASE}/verify_pin",
                {"request_key": request_key, "identity_type": "pin",
                 "identifier": pin}, "verify_pin")
    token = (data.get("data") or {}).get("access_token")
    if not token:
        raise AutoLoginError("verify_pin", f"no access_token in {data!r}")
    return token


def _request_auth_code(client: httpx.Client, session_token: str, *,
                       fyers_id: str, app_id: str, redirect_uri: str) -> str:
    prefix, _, app_type = app_id.partition("-")
    payload = {
        "fyers_id": fyers_id,
        "app_id": prefix,
        "redirect_uri": redirect_uri,
        "appType": app_type,
        "code_challenge": "",
        "state": "neurox_auto_login",
        "scope": "",
        "nonce": "",
        "response_type": "code",
        "create_cookie": True,
    }
    headers = {"Authorization": f"Bearer {session_token}"}
    resp = client.post(f"{_BASE_TOKEN}/token", json=payload, headers=headers, timeout=15)
    try:
        data = resp.json()
    except ValueError as e:
        raise AutoLoginError("request_auth_code", f"non-JSON response ({resp.status_code})") from e
    url = data.get("Url") or data.get("url")
    if not url:
        raise AutoLoginError("request_auth_code", str(data))
    codes = parse_qs(urlparse(url).query).get("auth_code")
    if not codes:
        raise AutoLoginError("request_auth_code", f"redirect URL had no auth_code: {url}")
    return codes[0]


def headless_auth_code(config) -> str:
    """Runs the full send_otp -> verify_totp -> verify_pin -> token flow and
    returns a fresh auth_code, ready for auth.exchange_auth_code(). Requires
    fyers.fyers_id, fyers.totp_secret, fyers.pin, fyers.app_id all set."""
    fyers_id = config["fyers.fyers_id"]
    totp_secret = config["fyers.totp_secret"]
    pin = config["fyers.pin"]
    app_id = config["fyers.app_id"]
    missing = [n for n, v in (("fyers_id", fyers_id), ("totp_secret", totp_secret),
                              ("pin", pin), ("app_id", app_id)) if not v]
    if missing:
        raise AutoLoginError("config", f"auto_login is on but missing: {', '.join(missing)}")

    with httpx.Client() as client:
        key1 = _send_login_otp(client, fyers_id)
        key2 = _verify_totp(client, key1, totp_secret)
        session_token = _verify_pin(client, key2, pin)
        return _request_auth_code(client, session_token, fyers_id=fyers_id,
                                  app_id=app_id, redirect_uri=config["fyers.redirect_uri"])
