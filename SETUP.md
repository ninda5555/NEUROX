# Set up NEUROX

## Windows

1. Install Python 3.11 or 3.12 with the official Python installer and enable PATH.
2. Extract the project into a writable folder.
3. Run setup.bat. It creates .venv, installs the hash-verified dependency lock,
   and creates config.yaml. Any installation error stops setup.
4. In config.yaml, enter fyers.app_id and fyers.secret_key. Keep identifiers,
   the PIN, and other credential values quoted. Do not commit this file.
5. Run bootstrap.bat. Complete Fyers login when prompted. The first download
   may take hours; interrupted downloads resume from stored history.
6. Run start.bat. The dashboard opens at http://localhost:8000. Press Analyse.

Setup downloads daily history, builds the liquidity/surveillance-filtered
universe, and obtains sector/earnings context. It does not train or activate ML
models. The screener does not require one.

## macOS or Linux

Use Python 3.11 or 3.12:

```bash
bash setup.sh
# Edit config.yaml with your Fyers app credentials.
bash bootstrap.sh
bash start.sh
```

## Daily operation

### Laptop observation service (no paid hosting required)

Run `monitor.bat` to start the dashboard and scheduler even before broker setup.
It supervises both child processes, restarts crashes with backoff, prevents
duplicate service launches, and rotates logs in `data/logs/service.log`.
The Monitor page checks scheduler liveness separately from market-data freshness.
Keep the laptop plugged in, connected, and awake. This setup does not change your
Windows sleep or lid settings. It cannot observe markets while the laptop is off.

To start monitoring automatically when you sign into Windows, run
`powershell -ExecutionPolicy Bypass -File deploy/install-laptop-startup.ps1`.
This creates only a shortcut in your current user's Startup folder; removing
`NEUROX Monitor.lnk` from that folder disables automatic startup. Keep this
project folder in place. Do not run `start.bat` and `monitor.bat` together.

Create your own Fyers account/API app through the broker's dashboard. Run
`connect-broker.bat` to enter the App ID, secret, and exact registered redirect
URL privately on this computer, then restart the service. Complete `bootstrap.bat`
for login and initial history. Renew login through `src.scripts.daily_auth`
when required. Broker/KYC setup is a user action; no credentials belong in chat.
Data permissions needed: Historical Data and Quotes & Market Data.

Fyers documents free API data access for its clients:
https://support.fyers.in/portal/en/kb/articles/do-i-need-to-pay-for-datafeeds
Check current account/API eligibility in its dashboard; NEUROX does not create
accounts, buy services, bypass broker authentication, or place real orders.

The independent observer fetches five-minute candles every ten minutes within
09:25–15:40 IST on weekdays. It observes the 50 highest-turnover included stocks
and three indexes; before universe bootstrap, only the indexes are requested.
The weekday clock is not an exchange holiday calendar: missing fresh candles
are reported as stale/partial, never live. Outside the session, the service
remains running for health checks and maintenance. It is not a tick feed.

Forward paper reporting begins when the service first starts. Results are grouped
by model and include pending trades, target-before-stop successes after modeled
costs, net P&L, a descriptive Wilson interval, and closed-trade drawdown. Duplicate
paper records count once. Only signals after model training and service start
are included. This is an audit aid, not proof of out-of-sample validity: review
training cutoffs, data completeness, overlapping trades, liquidity, capacity,
slippage and walk-forward results separately. Earlier settled journal rows are
not rewritten by the corrected outcome evaluator.

The requested 70% target remains unverified. No model is created or approved
by enabling observation. Existing rejected research stays inactive. New model
development requires a testable hypothesis, leakage-free chronological validation,
and prospective paper observation; collecting more data alone is not learning.

The launcher starts the dashboard on 127.0.0.1:8000 and the maintenance scheduler.
Keep both running for scheduled refreshes. Complete Fyers authentication when
requested; the app does not purchase services or place real orders.

The default scheduler performs morning catch-up, upstream health probes,
evening candle refresh, regime refresh, universe rebuild, and nightly backups.
Historical signal jobs need active research models; on a fresh installation
these are unavailable. Automatic retraining is off. For research only, weekly
or daily retraining can be enabled; scheduled artifacts are never auto-activated.

Analyse reads completed daily bars stored locally. Before market close it uses
the previous completed session. All compared stocks must have the same date.
A missing-data message means bootstrap or refresh needs attention. A stale-date
message means the ranking describes older market data. A low factor score is
not a measured probability of failure, and a high score is not a success rate.

## Troubleshooting

- Setup fails: use a supported Python version and read the installation error.
- Login needed: activate .venv and run python -m src.scripts.daily_auth.
- Nothing to rank: run python -m src.scripts.bootstrap; inspect daily-data and
  surveillance errors. At least 30 comparable stocks are required.
- Dashboard fails to start: check whether another application uses port 8000.
- No active model: expected for this product. Use Analyse, not historical Scanner.
- Changed UI source: with Node 22 LTS installed, run npm ci and npm run build in src/ui.
- Configuration rejected: the error names the malformed setting; correct it
  rather than weakening validation. Unknown keys are rejected.

## Optional Ubuntu server

The existing deploy/ scripts support an unprivileged dashboard and scheduler
under systemd. Review deploy/server_setup.sh before running it on your server.
Daily Fyers login is still required unless you explicitly configure the existing
auto_login option with its local credential requirements.

Bind the dashboard to 127.0.0.1 and use Tailscale Serve for private access.
Never use tailscale funnel to publish this dashboard. The optional identity
header gate assumes the documented private reverse-proxy perimeter; it is not
safe authentication for an app directly exposed to the public Internet.
Keep config.yaml and token files private (chmod 600 on POSIX systems).

Backups use SQLite's online backup mechanism, integrity checking, and atomic gzip
replacement. They must come from an existing journal, not a silently created empty
file. Check data/backups and test restoration separately before relying on them.
No deployment has been performed by this repair; no paid server is required locally.
