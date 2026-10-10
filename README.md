# NEUROX

NSE swing-stock factor screener, market-data dashboard, and research/paper journal.
The Analyse screen ranks stocks using seven measured technical factors. A score
is a relative factor score out of 100, not a probability of making money.

## Current condition

The original intraday and swing ML strategies failed validation. They remain
research code, and a fresh installation has no active model. No live-order
placement is implemented. The detailed measurements are preserved in CLAUDE.md,
section 0. Passing software tests does not establish a profitable strategy.

The runnable product is the swing screener. It uses stored, completed daily bars
from one session, skips incomplete or invalid names, displays the data date and
staleness, and explains each factor relative to peers. Entry, stop, target and
quantity are individual hypothetical levels; the shortlist is not a combined
portfolio or a recommendation to buy every name.

## Start on Windows

Use Python 3.11 or 3.12. Run setup.bat once, edit the generated config.yaml with
your Fyers app credentials, then run bootstrap.bat for the first data download.
Run start.bat on subsequent days and open Analyse in the dashboard.

For macOS/Linux, use setup.sh, bootstrap.sh, and start.sh. See SETUP.md for commands.
The launcher starts the dashboard and scheduled data maintenance. Keep it running
if you want evening refresh and backups. Data collection requires a valid Fyers
login; credentials and tokens stay in ignored local files.

## Development and verification

```bash
python -m pip install --require-hashes -r requirements.lock
python -m pytest -q
cd src/ui
npm ci
npm audit --audit-level=high
npm run build
```

Frontend development requires Node 20.19+ or 22.12+; Node 22 LTS is the CI target.
The prebuilt dashboard is included, so end users do not need Node. CI tests Python
3.11 and 3.12 on Windows and Linux, and installs/audits/builds the frontend.

## Research controls

training.retrain_schedule defaults to off. Enabling weekly or daily schedules
creates research artifacts without activating them. Manual retraining also
registers only by default. --activate explicitly invokes the historical promotion
gate; --force-activate records an override. Neither option demonstrates profitability.
Do not activate the rejected strategy for trading based on its test-suite status.

The research library includes purged cross-validation, IC analysis, calibration,
barrier outcome accounting, risk sizing, drift monitoring, and paper settlement.
Historical architecture and measured limitations are in CLAUDE.md. Current setup
instructions are in SETUP.md. Contributor instructions are in AGENTS.md.
