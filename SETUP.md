# Run the bot on your own computer — step by step

Your dashboard and the whole engine run locally. You need **Python** (that's
it — the dashboard is pre-built, so no Node/coding required). Below: Windows
first, then Mac.

Time: ~15 min of setup + a one-time data download that runs 1–3 hours by
itself. After that, each trading morning is ~2 minutes.

---

## Windows

**1. Install Python 3.11+**
Go to <https://www.python.org/downloads/> → "Download Python". Run the
installer and **tick "Add Python to PATH"** on the first screen.

**2. Get the project**
On the GitHub page → green **Code** button → **Download ZIP**. Unzip it to a
folder you'll remember, e.g. `C:\NEUROX`.

**3. One-time setup**
Open the folder and **double-click `setup.bat`**. It builds everything and
creates `config.yaml`. Wait for "Setup done".

**4. Put in your Fyers keys**
Open `config.yaml` in Notepad. Fill:
```
fyers:
  app_id: "2PAWCOT3W3-100"
  secret_key: "YOUR_SECRET"
  redirect_uri: "https://127.0.0.1"
```
Save.

**5. One-time data download + training**
**Double-click `bootstrap.bat`.** It logs you in (paste the redirect URL like
we do here), then downloads ~2 years of history and trains the models. This
runs **1–3 hours** — leave it. Do it once.

**6. Every trading morning**
**Double-click `start.bat`** → it opens the login, you paste the redirect URL,
and your **dashboard opens at http://localhost:8000**. The bot scans live all
day. That's it.

---

## Mac

Open the **Terminal** app, then:

```bash
# 1. Python 3.11+ (if you don't have it):  brew install python@3.11
# 2. Get the project:
git clone https://github.com/ninda5555/neurox.git && cd neurox
# (or download the ZIP from GitHub and `cd` into the folder)

# 3. One-time setup:
./setup.sh

# 4. Put your Fyers keys into config.yaml (open it in any text editor)

# 5. One-time data download + training (1-3 hours):
./bootstrap.sh

# 6. Every trading morning:
./start.sh          # opens the dashboard at http://localhost:8000
```

---

---

## Run on a cloud server (always-on, phone access)

Instead of your own laptop, run NEUROX on a small always-on Ubuntu server so
it scans and retrains itself 24/7, and you check it from your phone over a
private network — no home Wi-Fi needed, works fine on 5G.

**You do the server part** (Claude Code can't create or pay for a server for
you): spin up any **Ubuntu 22.04** VPS with **≥ 4 GB RAM** (2 GB or the
cheapest "nano" tiers fail during retraining) — AWS Lightsail Mumbai,
Hetzner, Hostinger, DigitalOcean, whichever your payment method covers.
Region doesn't matter for a predictions-only tool; you don't need India's
special static-IP tier — that rule is about *order placement*, which V1
doesn't do. Note the server's IP and your SSH login.

**Then, on the server:**

```bash
# 1. One-time base install (as root):
git clone -b claude/nse-trading-assistant-setup-hly9x3 \
  https://github.com/ninda5555/NEUROX.git /opt/neurox
sudo /opt/neurox/deploy/server_setup.sh

# 2. Fill in your Fyers keys:
sudo -u neurox nano /opt/neurox/config.yaml

# 3. Join your private network (prints a one-time login URL):
tailscale up

# 4. One-time history download + training (1-3 hrs, runs on the server's
#    connection, not your phone data):
sudo -u neurox bash -lc 'cd /opt/neurox && source .venv/bin/activate && ./bootstrap.sh'

# 5. Start the services — they now run forever, restart on crash/reboot,
#    and self-retrain on schedule with zero further action from you:
sudo systemctl start neurox-dashboard neurox-scheduler

# 6. Put the dashboard on your private network only (never public):
tailscale serve --bg http://127.0.0.1:8000
```

**On your phone:** install the Tailscale app, sign into the *same* Tailscale
account, then open the URL `tailscale serve status` printed on the server
(looks like `https://<machine-name>.<your-tailnet>.ts.net`). That's your
dashboard, reachable from anywhere, reachable by no one else — the server's
public firewall never opens the dashboard port at all
(`deploy/firewall.sh`).

**From then on:** the scheduler retrains and rescans on its own
(`training.retrain_schedule` in `config.yaml` — `weekly`, Saturday morning,
by default; set it to `daily` for a retrain every trading evening instead).
Your only remaining daily task is the ~1-minute Fyers login — SEBI requires
a fresh one every day and nothing can remove that — **unless** you turn on
auto-login below.

### Optional: skip the daily login too (read the trade-off first)

`config.yaml` has an `auto_login` switch, **off by default**:

```yaml
fyers:
  auto_login: false      # true = server logs itself in every morning at 06:00 IST
  totp_secret: ""        # your Fyers TOTP seed — only read when auto_login: true
  pin: ""                # your Fyers PIN — only read when auto_login: true
```

Turning it on means your TOTP seed and PIN sit in `config.yaml` on the
server — effectively a second factor stored alongside the first. That's a
real trade-off: anyone who gets root on that server (or a copy of
`config.yaml`) could log into your Fyers account. It's off by default for
that reason. If you accept it, set `auto_login: true` and fill in
`totp_secret`/`pin`; a 06:00 IST job then completes the same login flow
headlessly, using undocumented-but-widely-used Fyers login endpoints (not
part of their official API docs — if Fyers changes them, the job fails
loudly in the logs and the manual flow above still works as a fallback).
Leave it `false` if you're not sure — the manual paste-the-link flow takes
about a minute and touches nothing extra.

`config.yaml` is never committed or shared regardless of this setting.

---

## What you'll see

- The **dashboard** (your design) opens in your browser. Scanner, Journal,
  Model, Universe, Search — all live, updating through the day.
- Each morning `start` asks for the **1-minute Fyers login** (SEBI rule — a
  fresh login every day; nobody can remove this).
- Quiet days with an empty scanner are normal and correct — the bot only
  shows setups that clear its honesty gate.

## Notes
- Keep the window open while trading — closing it stops the live scan (the
  dashboard is still viewable if you re-open `start`).
- `config.yaml` holds your keys and is **never** shared or committed.
- Weekly on Saturday the models retrain themselves if you run
  `python -m src.jobs.scheduler` (optional; keeps it sharp).
- This is **V1: signals + paper trading only.** No real orders are placed —
  that's a later, separately-gated step.
