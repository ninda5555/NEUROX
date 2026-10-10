"""Local-only credential prompt. No account creation or payments."""
from __future__ import annotations

from getpass import getpass
import os

import yaml

from src.config import load_config


def main():
    cfg = load_config()
    print("Create your own Fyers account/API app first:")
    print("https://fyers.in/web/api-dashboard/user-apps")
    print("Use Historical Data and Quotes & Market Data permissions.")
    print("Enter credentials only here. They are saved locally, never sent to GitHub.")
    app_id = getpass("App ID (hidden): ").strip()
    secret = getpass("App secret (hidden): ").strip()
    redirect = input("Exact redirect URL registered in Fyers [https://127.0.0.1]: ").strip() or "https://127.0.0.1"
    if not app_id or not secret or not redirect.startswith(("https://", "http://127.0.0.1")):
        raise SystemExit("Incomplete app settings; nothing saved.")
    data = cfg.data
    data["fyers"].update(app_id=app_id, secret_key=secret, redirect_uri=redirect)
    path = cfg.root / "config.yaml"
    tmp = cfg.root / "config.yaml.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(data, stream, sort_keys=False)
        load_config(tmp)  # validate before replacing any existing settings
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()
    print("Saved. Restart monitoring, then run bootstrap.bat and complete broker login.")
    print("Future daily login: .venv\\Scripts\\python.exe -m src.scripts.daily_auth")


if __name__ == "__main__":
    main()
