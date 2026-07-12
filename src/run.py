"""One-command daily launcher — the whole morning routine in a single step.

    python -m src.run

What it does, in order:
  1. Checks the Fyers token. If today's login is missing/expired, prints the
     login link, opens it in your browser, and waits for you to paste the
     redirect URL back — the only manual step (SEBI daily-auth mandate).
  2. Starts the dashboard at http://localhost:8000 and opens it.
  3. Starts the all-day live scanner (rest-poll shadow runner).

After that you just watch the dashboard. Predictions appear as they qualify;
an empty scanner means "no trade" — that is the system working.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import webbrowser

from src import db as dbm
from src.config import load_config
from src.fyers import auth


def ensure_token(cfg) -> str:
    try:
        return auth.get_valid_token(cfg)
    except auth.NeedsReauth:
        pass
    if cfg["fyers.auto_login"]:
        # Unattended path (deploy/, opt-in): try the headless TOTP+PIN flow
        # before ever falling to input() below, which would hang forever
        # with no stdin (e.g. bootstrap.sh run right after server_setup.sh,
        # before the scheduler's 06:00 IST job has logged in for the day).
        try:
            print("\nauto_login is on — attempting headless daily re-auth…")
            token = auth.headless_login(cfg)
            print("✓ Logged in headlessly. Token valid until ~6 AM tomorrow.\n")
            return token
        except Exception as e:
            print(f"Headless login failed ({e}); falling back to manual login.\n")
    print("\n" + "=" * 64)
    print("  DAILY LOGIN (takes ~1 minute — required every morning by SEBI)")
    print("=" * 64)
    url = auth.login_url(cfg)
    print("\n1. Opening the Fyers login in your browser (or copy this link):\n")
    print("   " + url)
    totp = auth.current_totp(cfg)
    if totp:
        print(f"\n   Your current 2FA code: {totp}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("\n2. Log in. You land on a 'site can't be reached' page — that's fine.")
    print("3. Copy the FULL address bar (starts https://127.0.0.1/?...auth_code=...)")
    pasted = input("\n   Paste it here and press Enter:\n   > ").strip()
    token = auth.exchange_auth_code(cfg, auth.parse_auth_code(pasted))
    auth.save_token(cfg, token)
    print("\n   ✓ Logged in. Token valid until ~6 AM tomorrow.\n")
    return token


def main() -> int:
    cfg = load_config()
    if not cfg["fyers.app_id"]:
        print("config.yaml missing — copy config.example.yaml and fill Fyers keys.")
        return 2
    conn = dbm.connect(cfg.path("paths.db")); dbm.init_db(conn)

    ensure_token(cfg)

    # dashboard
    print("Starting dashboard at http://localhost:8000 …")
    dash = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api.app:app",
         "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning"])
    time.sleep(3)
    try:
        webbrowser.open("http://localhost:8000")
    except Exception:
        pass

    # all-day live scanner
    print("Starting the live scanner (runs until 15:30 IST)…\n")
    runner = subprocess.Popen([sys.executable, "-m", "src.scripts.day_runner"])
    try:
        runner.wait()
    except KeyboardInterrupt:
        pass
    finally:
        runner.terminate()
        print("\nScanner stopped. Dashboard still running at http://localhost:8000 "
              "(Ctrl+C to stop it).")
        try:
            dash.wait()
        except KeyboardInterrupt:
            dash.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
