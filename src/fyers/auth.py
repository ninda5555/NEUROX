"""Daily OAuth2 auth-code flow with token cache (CLAUDE.md §3.1, §12).

Flow: login URL -> user completes 2FA (TOTP/PIN) in a browser -> redirect to
https://127.0.0.1 with auth_code -> exchange for access_token. Tokens expire
daily at the ~06:00 IST cutover; daily re-auth is a SEBI requirement, not a
bug. Token cached in .tokens/ with 0600 perms, gitignored.

NOTE: CLAUDE.md §15 says to port the old prototype's src/auth.py nearly
as-is. The prototype folder was not attached to this session, so this module
implements the documented behavior (OAuth+TOTP helper+daily cache) from
§3.1/§12 directly. When the prototype is attached, reconcile this against it.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.timeutil import auth_day, ist_iso, now_ist


class NeedsReauth(RuntimeError):
    pass


TOKEN_FILE = "access_token.json"


def _token_path(config) -> Path:
    return config.path("paths.tokens") / TOKEN_FILE


def build_session(config, for_token_exchange: bool = False):
    from fyers_apiv3 import fyersModel  # only src/fyers/ may import this
    kwargs = dict(
        client_id=config["fyers.app_id"],
        secret_key=config["fyers.secret_key"],
        redirect_uri=config["fyers.redirect_uri"],
        response_type="code",
    )
    if for_token_exchange:
        kwargs["grant_type"] = "authorization_code"
    return fyersModel.SessionModel(**kwargs)


def login_url(config) -> str:
    return build_session(config).generate_authcode()


def current_totp(config) -> str | None:
    """TOTP helper: prints the current 2FA code so the morning login is
    one paste. Requires fyers.totp_secret in config.yaml."""
    secret = (config["fyers.totp_secret"] or "").strip().replace(" ", "")
    if not secret:
        return None
    import pyotp
    return pyotp.TOTP(secret).now()


def parse_auth_code(pasted: str) -> str:
    """Accept either the raw auth_code or the full redirect URL
    (https://127.0.0.1/?...&auth_code=...&state=...)."""
    pasted = pasted.strip()
    if pasted.lower().startswith(("http://", "https://")):
        qs = parse_qs(urlparse(pasted).query)
        codes = qs.get("auth_code")
        if not codes:
            raise ValueError("redirect URL has no auth_code parameter")
        return codes[0]
    return pasted


def exchange_auth_code(config, auth_code: str) -> str:
    session = build_session(config, for_token_exchange=True)
    session.set_token(auth_code)
    resp = session.generate_token()
    if not isinstance(resp, dict) or "access_token" not in resp:
        raise RuntimeError(f"token exchange failed: {resp!r}")
    return resp["access_token"]


def save_token(config, access_token: str) -> Path:
    path = _token_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": access_token,
        "app_id": config["fyers.app_id"],
        "issued_at": ist_iso(now_ist()),
        "auth_day": auth_day().isoformat(),
    }
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    tmp.replace(path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def load_cached(config) -> dict | None:
    path = _token_path(config)
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def get_valid_token(config) -> str:
    """Return the cached token if it belongs to the current auth day
    (same-day validity check with the 06:00 IST cutover), else raise."""
    cached = load_cached(config)
    if cached is None:
        raise NeedsReauth("no cached token — run: python -m src.scripts.daily_auth")
    if cached.get("app_id") != config["fyers.app_id"]:
        raise NeedsReauth("cached token belongs to a different app_id — re-auth")
    if cached.get("auth_day") != auth_day().isoformat():
        raise NeedsReauth(
            f"cached token is from auth day {cached.get('auth_day')} "
            f"(today is {auth_day().isoformat()}, ~06:00 IST cutover) — re-auth"
        )
    return cached["access_token"]


def headless_login(config) -> str:
    """Opt-in unattended re-auth (fyers.auto_login: true only — see
    src.fyers.auto_login). Completes the same daily auth-code flow as the
    manual paste-the-URL path, then exchanges + caches the token exactly
    like save_token()/exchange_auth_code() do for a human login."""
    from src.fyers.auto_login import headless_auth_code
    auth_code = headless_auth_code(config)
    token = exchange_auth_code(config, auth_code)
    save_token(config, token)
    return token


def token_status(config) -> dict:
    """For CLI/UI status: valid / issued_at / re-auth deadline."""
    cached = load_cached(config)
    ok = False
    try:
        get_valid_token(config)
        ok = True
    except NeedsReauth:
        pass
    return {
        "valid": ok,
        "issued_at": (cached or {}).get("issued_at"),
        "auth_day": (cached or {}).get("auth_day"),
        "reauth_note": "re-auth by ~06:00 IST tomorrow" if ok else "re-auth needed now",
    }
