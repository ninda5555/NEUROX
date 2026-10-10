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
