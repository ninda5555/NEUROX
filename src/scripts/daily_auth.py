"""Morning auth CLI (CLAUDE.md §3.1, §12).

    python -m src.scripts.daily_auth            # interactive flow
    python -m src.scripts.daily_auth --status   # just report token state
    python -m src.scripts.daily_auth --auth-code <code-or-redirect-url>
"""

from __future__ import annotations

import argparse
import sys

from src import db as dbm
from src.config import load_config
from src.fyers import auth
from src.fyers.client import FyersClient


def _mask(tok: str) -> str:
    return tok[:6] + "…" + tok[-4:] if len(tok) > 12 else "…"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fyers daily re-authentication")
    ap.add_argument("--status", action="store_true", help="report token state and exit")
    ap.add_argument("--auth-code", help="auth_code or full redirect URL (non-interactive)")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the live profile() check after token exchange")
    args = ap.parse_args(argv)

    cfg = load_config()
    if not cfg["fyers.app_id"] or not cfg["fyers.secret_key"]:
        print("config.yaml is missing fyers.app_id / fyers.secret_key — "
              "copy config.example.yaml to config.yaml and fill them in.")
        return 2

    status = auth.token_status(cfg)
    if status["valid"]:
        print(f"Token valid for auth day {status['auth_day']} "
              f"(issued {status['issued_at']}); {status['reauth_note']}.")
        if args.status or not args.auth_code:
            return 0
    elif args.status:
        print(f"Token NOT valid ({status['reauth_note']}); "
              f"cached auth day: {status['auth_day'] or '—'}")
        return 1

    if args.auth_code:
        code = auth.parse_auth_code(args.auth_code)
    else:
        print("\nDaily re-auth (SEBI-mandated). Open this login URL in your "
              "browser and complete 2FA:\n")
        print("  " + auth.login_url(cfg))
        totp = auth.current_totp(cfg)
        if totp:
            print(f"\nCurrent TOTP code: {totp}")
        print("\nAfter login you land on the redirect URL "
              "(https://127.0.0.1/?...auth_code=...). Paste it (or just the "
              "auth_code) here:\n")
        code = auth.parse_auth_code(input("auth_code> "))

    token = auth.exchange_auth_code(cfg, code)
    path = auth.save_token(cfg, token)
    print(f"Access token saved to {path} (0600), masked: {_mask(token)}")

    if not args.no_verify:
        conn = dbm.connect(cfg.path("paths.db"))
        dbm.init_db(conn)
        client = FyersClient(cfg, conn, access_token=token)
        prof = client.profile()
        name = (prof.get("data") or {}).get("name", "?")
        print(f"Verified with profile(): logged in as {name}")

        # Re-run the dashboard's upstream probes right now, on this fresh
        # token, instead of leaving the banner showing whatever the last
        # (pre-refresh) hourly heartbeat saw — that gap was a real incident
        # (2026-07-28: banner kept showing a stale pre-reauth failure for
        # ~40 min after a successful morning login).
        from src.jobs.health import run_probes
        health = run_probes(conn, client)
        print("Upstream check: " + " | ".join(
            f"{k} {'OK' if health[k]['ok'] else 'FAIL(' + str(health[k]['message'])[:70] + ')'}"
            for k in ("profile", "quotes", "history")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
