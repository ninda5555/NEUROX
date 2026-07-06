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
