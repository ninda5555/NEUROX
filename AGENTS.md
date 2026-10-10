# Contributor instructions

NEUROX's current end-user feature is a swing factor screener. Preserve the failed
ML research record; never turn scores into invented probabilities or guarantee
returns. There is no live-order placement. Do not add it as a maintenance fix.

README.md and SETUP.md describe current operation. CLAUDE.md preserves historical
architecture and research evidence. Read relevant code and tests before editing.

Use Python 3.11/3.12 and install requirements.lock with --require-hashes. Run the
full pytest suite for behavioral changes. For dashboard changes, use npm ci and
npm run build in src/ui, check npm audit, and update the committed dist assets.
Frontend Node engines are recorded in package.json.

Add behavioral regression coverage for real bugs, particularly data-date
comparability, malformed data, risk controls and API/security behavior. Do not
lower validation gates to make a test pass. Never commit config.yaml, tokens,
credentials, data, model artifacts, .venv, or node_modules. Keep datetimes aware
and convert to Asia/Kolkata. Parameterize SQL and close DB connections at request
and scheduled-job boundaries.

Normal bootstrap must not retrain or activate rejected models. Research retraining
registers only by default; scheduled retraining is off by default. Explain
limitations honestly in UI and documentation.
