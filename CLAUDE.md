# CLAUDE.md — NSE Trading Assistant (single source of truth)

Read this file before writing any code or designing any screen. Claude Code and Claude Design both treat this document as authoritative. If a decision here conflicts with an ad-hoc instruction, ask before deviating; if it conflicts with reality (an API changed), update this file first, then the code.

## 1. What this project is — and is not

**Is:** A decision-support trading assistant for NSE (Indian) equities. It scans a liquidity-filtered universe of all NSE stocks, generates ML-scored setups in two modes (intraday and swing), explains every signal in plain English via SHAP, sizes positions by volatility, journals everything, and tracks real outcomes. V1 produces signals + paper trading only — no live order placement.

**Is not:** An institutional system. The technical ceiling is the "Advanced Independent Developer" blueprint (research PDF, Part 14). Explicitly out of scope, permanently: colocation, FPGA/kernel-bypass, Level 3 order books, nanosecond execution, market making. Do not design around these; do not imply them.

**Tier disclosure (permanent, in-app):** The UI must always display, verbatim, in the footer/about of every page:

> "This is an independent-developer analytics tool, not an institutional trading system. It estimates probabilities; it does not predict the future. Most retail intraday traders lose money (SEBI studies). Not investment advice."

**Language rules (enforced everywhere — UI, logs, docs, commit messages):**
- Never say "accuracy", "win rate guarantee", "X% accurate", "proven returns".
- The model output is a calibrated confidence score — describe it as "how often setups that looked like this worked historically", never as a promise.
- Report validation results across all CV splits, including bad ones. Inconsistency across splits is surfaced as a red flag, never averaged away.

## 2. Locked architecture decisions

| Decision | Choice | Rationale |
|---|---|---|
| Backend | Python 3.11+, FastAPI, uvicorn | LightGBM/SHAP are Python-native; async WebSocket fan-out to UI |
| Frontend | React 18 + Vite + Tailwind CSS | Premium glassmorphic dashboard; Claude Design deliverable |
| DB (relational) | SQLite (WAL mode) | Signals, journal, models, universe, config. Single user, zero ops |
| DB (time series) | Parquet files + DuckDB | Candles & features. Columnar, fast scans, no server |
| ML | LightGBM (binary classifier per mode) + SHAP + isotonic calibration | PDF Part 14: gradient boosting over deep nets for tabular finance |
| Validation | Purged walk-forward CV with embargo (simplified CPCV) | PDF Part 8, adapted to solo-dev scale |
| Broker API | Fyers API v3 (fyers-apiv3 SDK) | Existing account & app (App ID 2PAWCOT3W3-100) |
| Deployment | Local PC, or a single always-on Ubuntu 22.04 VPS (`deploy/`, any region ≥4GB RAM) for V1 predictions/paper-trading → Indian VPS with static IP only when order placement is added (V2) | SEBI static-IP rule applies to order APIs, not data; V1 has no order API so any Ubuntu box works |
| V1 scope | Signals + paper trading. Order APIs deliberately not wired | PDF Phase 5: shadow before capital |
| Timezone | Everything internal in IST (Asia/Kolkata); store ISO-8601 with offset | NSE trading hours 09:15–15:30 IST |

**Python deps (pin in requirements.txt):** fyers-apiv3, fastapi, uvicorn, lightgbm, shap, scikit-learn, pandas, numpy, polars (feature pipeline), duckdb, pyarrow, pyotp, pytz, httpx, apscheduler.

## 3. Fyers API v3 — verified ground truth

Everything below was verified against Fyers docs/support/community (July 2026). Numbers here are the ones the code must respect.

### 3.1 Auth (SEBI-mandated lifecycle)
- OAuth2 auth-code flow: login URL → user 2FA (TOTP/PIN) → redirect to https://127.0.0.1 with auth_code → exchange for access_token.
- Access tokens expire daily (~6 AM IST cutover). Daily re-auth is a SEBI requirement, not a bug. Continuous refresh-token sessions are no longer supported under the April 2026 framework.
- The old prototype's `src/auth.py` implements this correctly (TOTP helper, token cache in `.tokens/`, same-day validity check) — port its logic.

### 3.2 SEBI retail-algo framework (in force since April 1, 2026)

| Rule | Impact on this project |
|---|---|
| Orders accepted only from registered App ID mapped to a whitelisted static IP | V2 (order placement) requires the VPS/static IP. V1 data-only workflows are unaffected |
| Daily 2FA re-authentication | Already our auth design |
| Max 10 orders/second | V2 order engine must rate-limit at ≤10/s |
| Market orders auto-converted to MPP (Market Price Protection) | V2: treat "market" orders as MPP; display accordingly |
| Third-party platforms must be broker-empanelled | We are a personal-use API app, not a platform — keep it single-user |

### 3.3 Market data capabilities

| Capability | Verified fact | Consequence |
|---|---|---|
| Symbol master | Public CSVs, no auth: `https://public.fyers.in/sym_details/NSE_CM.csv` (JSON variant also exists). No header row; columns have changed before — parse defensively by position, validate row width, fail loudly | Universe builder ingests this daily |
| Symbol format | `NSE:RELIANCE-EQ`; series is embedded in suffix (-EQ, -BE, -BZ…). Indices: `NSE:NIFTY50-INDEX`, `NSE:NIFTYBANK-INDEX`; India VIX expected as `NSE:INDIAVIX-INDEX` (verify at runtime on first fetch) | T2T filtering falls out of the suffix for free |
| Data WebSocket | Up to 5,000 symbols per connection (current SDK). SymbolUpdate = tick/quote stream (ltp, cumulative volume, OHLC) | Full filtered universe streams on one socket |
| Depth | DepthUpdate on the same socket = 5-level bid/ask depth. A 50-level tick-by-tick feed exists as a separate Fyers product — not part of standard API v3; do not plan around it | OFI is computed from 5-level depth, top-of-book approximation, and only for the scanner's top-N shortlist (~50 symbols), not the whole universe |
| History API | 5-min candles: max 100 days/request; daily candles: max 366 days/request; intraday history available back to ~2017 | Backfill loops with date-chunking; every call counts against rate limits |
| Quotes API | Batched quotes, ~50 symbols/request | Snapshot fallback when WS is down |
| Rate limits | 10/second, 200/minute, 100,000/day per app | Central token-bucket rate limiter in the Fyers client wrapper; all REST calls go through it |

### 3.4 What Fyers does NOT give us (design accordingly)
- No Level 3 / full order book. No order-by-order data.
- No ASM/GSM/surveillance flags in the symbol master → sourced from NSE (§4).
- No fundamentals; not needed for V1.

## 4. Universe construction (Requirement 1)

Nightly job (and on-demand refresh) building `universe_snapshot`:
1. Ingest Fyers NSE_CM symbol master → upsert instruments.
2. **Series filter:** keep only -EQ series. This drops Trade-to-Trade (-BE, -BZ) mechanically — T2T stocks cannot be intraday-traded at all.
3. **Surveillance filter:** source NSE's ASM/GSM lists. *Reality update (verified 04-Jul-2026):* the consolidated `REG_INDddmmyy.csv` is no longer retrievable from the nsearchives content paths; the operative source is NSE's JSON APIs — `nseindia.com/api/reportASM` (`longterm`/`shortterm`, rows keyed by **ISIN**, `asmSurvIndicator` = "Stage N") and `nseindia.com/api/reportGSM` (rows keyed by symbol+ISIN; parse the true GSM stage from `survDesc` — the `gsmStage` field mixes combined surveillance codes). Both need browser-like headers + cookie warm-up. Normalize to a consolidated per-day CSV cached locally; match on symbol AND ISIN; exclude every GSM stage (incl. 0) and ASM (ST & LT) stage ≥ 1 by default (configurable). If the fetch fails, reuse the newest cached list and flag staleness in the UI — never silently trade an unscreened universe.
4. **Liquidity filter (from daily history, rolling 20 sessions):**
   - median daily turnover ≥ ₹5 crore (config `universe.min_turnover_cr`)
   - price ≥ ₹20 (penny-stock guard), listed ≥ 60 sessions
   - non-zero volume in ≥ 18 of last 20 sessions
5. **Result:** ~800–1,500 symbols typically. Persist with per-symbol exclusion reasons so the UI can answer "why isn't stock X shown?".

Index membership (NIFTY50/sector indices) is metadata, not a filter — the prototype's hardcoded NIFTY50 list survives only as the relative-strength benchmark mapping and a "core" badge.

## 5. Trading modes (Requirement 2)

One mode enum threads through everything: `INTRADAY | SWING`. Signal logic is separate per mode; risk, journal, UI infra are shared.

| | INTRADAY | SWING |
|---|---|---|
| Bars | 5-min (aggregated from ticks/1-min) | Daily (+ weekly context) |
| Label horizon | Rest-of-day (square-off 15:15) | 5–10 sessions |
| Label definition | Triple-barrier on 5-min bars: +1 if TP (1.5×ATR₅) hit before SL (1×ATR₅) before 15:15, else 0. **Barrier ATR is floored (added 06-Jul-2026, P5 Day-1 root-cause fix): `atr_eff = max(ATR₅, labels.min_barrier_atr_pct% × price)` so barriers can't collapse below trading-cost/noise level on calm stocks — otherwise the model learns sub-cost noise moves.** | Triple-barrier on daily bars: TP 2×ATR₁₄d vs SL 1.25×ATR₁₄d within 10 sessions |
| Entry window | 09:30–14:30 only (skip open auction noise, no fresh entries near close) | Signals generated post-close for next-day entry |
| Order type (V2) | Fyers productType: INTRADAY (= "MIS" at other brokers; auto square-off) | productType: CNC (delivery) |
| Stop style | 1.5×ATR(5-min) on the same floored `atr_eff` as the labels, snapped beyond structure (ORB level) — keeps live stops consistent with what the model was trained on | 2.5×ATR(daily) — wider, gap-aware; gaps CAN blow through it and UI must say so |
| Extra risk | Daily loss limit halts new intraday signals | Overnight/gap risk disclosure on every card; earnings-date proximity flag |

## 6. Signal engine (Requirements 3 & 6)

### 6.1 Features (engineered per mode, stored per bar)
**Shared:** RSI(14), MACD(12,26,9) histogram + slope, volume z-score vs 20-bar, Garman-Klass realized volatility (rolling), ATR%, relative strength vs NIFTY50 (rolling return differential), gap %, day-of-week/time-of-day encodings, distance from VWAP (intraday) or from 20/50-DMA (swing), regime features (§6.4).

**Intraday-only:** VWAP distance in ATR units, opening-range position, 15-min trend agreement, cumulative intraday volume vs same-time-of-day average, OFI from 5-level DepthUpdate (top-N shortlist only; feature is nullable — model handles missing natively, LightGBM strength).

**Swing-only:** 20/50/200-DMA alignment, 52-week-high distance, weekly RSI, sector-relative strength, realized-vol trend (GK 10d vs 60d).

**Feature hygiene (PDF Part 7):** compute Information Coefficient per feature at training time; drop features with |IC| below threshold or pairwise correlation > 0.9 (keep the higher-IC one). Apply tradability masks: bars where the stock was in upper/lower circuit are excluded from labels (unexecutable prices must not train the model).

### 6.2 Model
- One LightGBM binary classifier per mode. Regularized hard: shallow trees (num_leaves ≤ 31, max_depth ≤ 6), min_child_samples high, feature + bagging fraction < 1, early stopping on the validation fold.
- **Calibration:** isotonic regression fit on out-of-fold predictions. The UI only ever sees the calibrated probability. Show a calibration curve in the model page — if the model says 65% and reality is 52%, that curve is where the lie becomes visible.
- **Explainability:** SHAP TreeExplainer. Every signal stores its top-6 SHAP contributors. Plain-English templating maps feature → sentence, e.g. `vwap_dist_atr: +0.21` → "Price is holding well above VWAP (strongest factor)".
- **Retraining:** weekly (swing) / weekly on 100 days of 5-min data (intraday), scheduled job; every trained model is a new row in `models` with its full CV report. Old models are never overwritten.

### 6.3 Signal emission
A signal is emitted only when calibrated confidence ≥ mode threshold (config, default 0.60) AND risk engine approves (§7). Every emission writes the complete context to `signals` (§9) before it is shown anywhere. Scanner ranks by confidence × liquidity, surfaces top N (default 12), never dumps the universe.

### 6.4 Regime context
Daily regime tags stored in `market_regime`: India VIX level & 20-day trend, NIFTY50 above/below 50/200-DMA, advance-decline breadth from universe candles. Regime is a feature, a journal field, and a UI banner ("High-VIX regime: historically fewer setups qualified — expect fewer signals").

## 7. Risk engine (Requirement 5)

Shared across modes; runs after the model, before the UI.
- **Volatility-targeted sizing:** `qty = (capital × risk_pct) / (stop_distance)`, with stop distance from the mode's ATR rule — position size shrinks automatically as volatility expands. Default risk_pct 1% intraday, 1.5% swing; hard cap: single position ≤ 20% of capital notional.
- **Portfolio flags:** sector exposure (NSE sector mapping in instruments): 2 open signals in one sector → flag; pairwise 60-day return correlation of open positions > 0.7 → flag. Flags are shown, not silently enforced (V1 is decision support).
- **Daily loss limit:** paper P&L ≤ −3% of capital (config) → no new intraday signals for the day, banner in UI. 75% of limit → warning state.
- **Cost-viability gate (added 06-Jul-2026 from a P5 Day-1 finding):** a setup whose natural (ATR) stop distance is so tight that modeled round-trip cost dominates the risk has no realistic edge after costs, so it is *not emitted*. Rule: `stop_distance ≥ risk.min_stop_to_cost × round_trip_cost_per_share`, where `round_trip_cost_per_share = 2 × costs.per_side_pct% × price` (both sides, all-in). Default `min_stop_to_cost` = 5 (round-trip cost ≤ ~20% of risk → a stop-out is ≈ −1.2R, not −2R). This is a filter, never a stop-widening (widening would distort the model's learned barriers; the deeper fix — a minimum triple-barrier width in §5 labels — is deferred to the next retrain and noted here).
- Every risk plan ships: entry, stop, target (2:1 default), qty, ₹-at-risk, % capital at risk, and the mode-appropriate holding-period statement.

## 8. Validation framework (Requirement 4)

Simplified purged walk-forward CV (honest by construction):
- ≥ 6 chronological folds. For each fold: train on data strictly before the test window, purge training samples whose label horizon overlaps the test window, embargo 1 label-horizon after the test window before training data may resume (for the next fold's perspective).
- Report per fold: calibrated hit rate vs predicted confidence (calibration error), precision at threshold, signal count, avg R-multiple, max drawdown of the paper equity curve.
- **Red-flag rules (automatic, displayed, never suppressed):**
  - any fold with precision < 0.45 → "inconsistent across time"
  - fold-to-fold precision std > 0.10 → "regime-sensitive"
  - live/paper divergence from CV expectation > 10 pts over 30 signals → "drift"
- The model page shows ALL folds as a table + strip chart. Averages are shown with the spread, never alone. A model with red flags can still be used — the point is the user sees the flags.

## 9. Database schema

SQLite (`data/app.db`, WAL). Candles/features live in Parquet (`data/candles/{mode_tf}/{symbol}/{yyyy-mm}.parquet`) queried via DuckDB; SQLite holds everything transactional.

```sql
-- Instruments & universe -------------------------------------------------
CREATE TABLE instruments (
  fytoken    TEXT PRIMARY KEY,
  symbol     TEXT NOT NULL,          -- NSE:RELIANCE-EQ
  nse_code   TEXT NOT NULL,          -- RELIANCE
  isin       TEXT,
  series     TEXT NOT NULL,          -- EQ / BE / BZ ...
  name       TEXT,
  lot_size   INTEGER DEFAULT 1,
  tick_size  REAL,
  sector     TEXT,                   -- NSE sector classification
  is_index   INTEGER DEFAULT 0,
  first_seen TEXT NOT NULL,
  last_seen  TEXT NOT NULL           -- delisting detection: stale = gone
);
CREATE TABLE universe_snapshot (
  snap_date  TEXT NOT NULL,          -- YYYY-MM-DD (IST)
  symbol     TEXT NOT NULL,
  included   INTEGER NOT NULL,
  exclude_reason TEXT,               -- 'T2T'|'ASM_LT2'|'GSM_1'|'LOW_TURNOVER'|...
  median_turnover_cr REAL,
  adv_20d    REAL,
  atr_pct    REAL,
  surveillance_src_date TEXT,        -- staleness tracking for NSE list
  PRIMARY KEY (snap_date, symbol)
);
-- Models & validation -----------------------------------------------------
CREATE TABLE models (
  model_id   TEXT PRIMARY KEY,       -- e.g. 'intraday_2026-07-06_a3f2'
  mode       TEXT NOT NULL CHECK (mode IN ('INTRADAY','SWING')),
  trained_at TEXT NOT NULL,
  train_start TEXT, train_end TEXT,
  feature_list TEXT NOT NULL,        -- JSON array
  lgbm_params TEXT NOT NULL,         -- JSON
  calibration TEXT NOT NULL,         -- JSON: method + curve points
  cv_report  TEXT NOT NULL,          -- JSON: per-fold metrics + red flags
  red_flags  TEXT,                   -- JSON array, denormalized for UI
  artifact_path TEXT NOT NULL,       -- models/{model_id}.txt (lgbm) + .pkl (calibrator)
  is_active  INTEGER DEFAULT 0       -- exactly one active per mode
);
CREATE TABLE cv_folds (
  model_id   TEXT NOT NULL REFERENCES models(model_id),
  fold       INTEGER NOT NULL,
  train_start TEXT, train_end TEXT, test_start TEXT, test_end TEXT,
  n_signals  INTEGER,
  precision_at_thr REAL,
  calibration_err REAL,
  avg_r_multiple REAL,
  max_drawdown_pct REAL,
  PRIMARY KEY (model_id, fold)
);
-- Signals & journal (Requirement 7: full context, always) ------------------
CREATE TABLE signals (
  signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,          -- ISO-8601 IST
  mode       TEXT NOT NULL,
  symbol     TEXT NOT NULL,
  direction  INTEGER NOT NULL,       -- +1 long, -1 short (intraday only)
  confidence REAL NOT NULL,          -- CALIBRATED probability
  model_id   TEXT NOT NULL REFERENCES models(model_id),
  features   TEXT NOT NULL,          -- JSON: full feature vector at emission
  shap_top   TEXT NOT NULL,          -- JSON: [{feature, value, shap, sentence}]
  regime     TEXT,                   -- JSON: {vix, vix_trend, nifty_trend, breadth}
  entry      REAL, stop_loss REAL, target REAL,
  qty        INTEGER, capital_at_risk REAL, risk_pct REAL,
  risk_flags TEXT,                   -- JSON: sector/correlation/loss-limit flags
  holding_stmt TEXT,                 -- plain-English holding-period guidance
  explanation TEXT NOT NULL          -- assembled plain-English reasoning
);
CREATE TABLE signal_outcomes (
  signal_id INTEGER NOT NULL REFERENCES signals(signal_id),
  horizon    TEXT NOT NULL,          -- '30m'|'60m'|'eod' | '1d'|'5d'|'10d'
  price      REAL,
  ret_pct    REAL,
  r_multiple REAL,                   -- vs planned stop distance
  mae_pct    REAL, mfe_pct REAL,     -- max adverse/favorable excursion
  status     TEXT NOT NULL,          -- 'pending'|'hit_target'|'hit_stop'|'expired'
  evaluated_at TEXT,
  PRIMARY KEY (signal_id, horizon)
);
CREATE TABLE paper_trades (
  trade_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id INTEGER REFERENCES signals(signal_id),
  opened_at TEXT, closed_at TEXT,
  entry_fill REAL, exit_fill REAL,   -- incl. modeled slippage + costs
  qty        INTEGER,
  costs_modeled REAL,                -- brokerage+STT+slippage estimate
  pnl        REAL, r_multiple REAL,
  exit_reason TEXT                   -- 'target'|'stop'|'squareoff'|'manual'
);
-- Context tables ----------------------------------------------------------
CREATE TABLE market_regime (
  regime_date TEXT PRIMARY KEY,
  vix        REAL, vix_trend TEXT,
  nifty_vs_50dma REAL, nifty_vs_200dma REAL,
  breadth_adv_dec REAL,
  label      TEXT                    -- 'calm_uptrend'|'high_vol'|...
);
CREATE TABLE app_state (             -- auth + operational state
  key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
); -- token_day, ws_status, surveillance_file_date, loss_limit_state ...
```

**Parquet layout:** `data/candles/5min/…`, `data/candles/1d/…`; `data/features/{mode}/{yyyy-mm}.parquet` (wide, one row per symbol-bar). DuckDB reads them in place; nothing time-series goes into SQLite.

## 10. Backend architecture

```
src/
  fyers/     client.py (REST + rate limiter), ws.py (5k-symbol socket),
             auth.py (daily OAuth+TOTP — port from prototype), symbols.py
  universe/  master.py, surveillance.py (NSE REG_IND fetch), liquidity.py
  data/      backfill.py (chunked history), candles.py (tick→1m→5m agg),
             store.py (parquet/duckdb)
  features/  shared.py, intraday.py, swing.py, ofi.py, ic_filter.py
  models/    train.py, calibrate.py, cv.py (purged walk-forward),
             explain.py (SHAP→English), registry.py
  signals/   engine.py (emission gate), scanner.py (ranking)
  risk/      sizing.py, stops.py, portfolio_flags.py, loss_limit.py
  journal/   journal.py, outcomes.py (scheduled evaluator), paper.py
  api/       FastAPI app: REST + /ws push to UI
  jobs/      scheduler.py (APScheduler): nightly universe, weekly retrain,
             outcome evaluation, 15:15 intraday square-off sweep
  ui/        React app (see §11)
  scripts/   daily_auth.py, backfill.py, retrain.py
```

**Data flow:** WS ticks → candle aggregator → on 5-min close: features → active model → calibrated confidence → risk gate → signal row → UI push + journal. Swing path runs post-close on daily bars. UI is a pure consumer of the API.

## 11. UI specification (Requirements 9 & 10)

Premium dark fintech aesthetic. Claude Design implements from this section.

- **Theme:** near-black base (#0A0E17), glassmorphic cards (translucent violet-tinted fill over the dark base, 1px inner border rendered as a soft violet ring, backdrop-blur, subtle gradient rings à la a concentric donut chart). **Accent palette is a soft purple/violet gradient:** violet/lavender (#8B5CF6 → #A78BFA → #C4B5FD) is the primary brand accent and glow — used for the logo, active navigation, mode toggle, live/connection status, confidence bars, calibration curve, and info highlights. Directional and outcome semantics stay legible within the same soft, non-candy register: a mint-green (#34D399) for long / positive outcomes (target hit, gains, included), a rose-pink (#FB7185) for short / risk / negative outcomes (stopped, losses, excluded), and periwinkle/lilac (#818CF8) for warnings and cautions. Numbers in a tabular mono font. No candy gradients on content, no fake badges — there is no "accuracy %" anywhere in the product.
- **Global chrome:** mode toggle (INTRADAY / SWING) top-center — it re-scopes every view. Status strip: WS connection, token validity ("re-auth by 6 AM"), universe size + surveillance-list date, regime banner, daily-loss-limit state. Footer: the permanent tier disclosure (§1), always visible, not dismissible.
- **Scanner (home):** top-N ranked setup cards. Card: symbol + sector chip, direction, calibrated confidence (shown as "6 of 10 similar setups worked historically" phrasing + number), sparkline, top-3 SHAP sentences, risk plan (entry/stop/target/qty/₹ at risk), holding statement, risk flags. Expand → full SHAP list, feature values, regime context.
- **Signal detail / journal:** searchable table of every emitted signal with outcome columns (30m/60m/EOD or 1d/5d/10d), R-multiples, MAE/MFE; equity curve of paper trades; calibration curve (predicted vs realized) front and center on the model page — this is the honesty dashboard.
- **Model page:** active model card, ALL CV folds table, red flags rendered as prominent rose / periwinkle banners, feature IC table, retrain history.
- **Universe page:** included/excluded counts, per-symbol exclusion reason, surveillance-list staleness warning if applicable.
- **Empty states matter:** "No qualifying setups right now" is a first-class, well-designed state — the system saying "no trade" is it working correctly.

## 12. Compliance & auth (Requirement 8)

- Daily OAuth + TOTP flow (port prototype `src/auth.py`); token cached with 0600 perms, gitignored; UI surfaces token freshness and walks the user through the morning re-auth.
- **Static IP:** required only when order placement ships (V2). Plan: Indian VPS (e.g., AWS Mumbai) with Elastic IP, whitelisted in Fyers MyAPI dashboard against App ID. Document but do not build in V1.
- **V2 order engine (design now, build later):** INTRADAY/CNC product types per mode, ≤10 orders/sec limiter, MPP-awareness for market orders, manual-confirm gate on every order, 15:15 square-off scheduler for MIS.
- Credentials only ever in `config.yaml` (gitignored) / OS keyring. Never in code, logs, or the DB.

**Unattended cloud deployment (`deploy/`, added 10-Jul-2026):** V1 (predictions/paper-trading only) can run 24/7 on a single always-on Ubuntu 22.04 VPS instead of a local machine, amd64 or arm64/aarch64 alike (verified against Oracle Cloud's free-tier Ampere A1) — `deploy/server_setup.sh` installs it (default layout: user `ubuntu`, `/home/ubuntu/NEUROX`; both overridable via `NEUROX_USER`/`NEUROX_APP_DIR` for the older dedicated-service-account layout) and ends by starting both services and self-checking them (`systemctl is-active` + a local `curl`); `deploy/oneshot.sh` is a zero-prerequisite entry point that fetches the repo and hands off to it, for a box that doesn't have anything cloned yet. `deploy/systemd/*.service` keep the dashboard and scheduler running (`Restart=always`). The dashboard binds `127.0.0.1` only and is reached over a private Tailscale tunnel (`tailscale serve`) — never a public bind, enforced by `deploy/firewall.sh` denying the port publicly and by `tests/test_deploy.py`. Self-retraining cadence is `training.retrain_schedule` (`weekly`, Sat 10:00 IST, default; or `daily`, ~16:45 IST). Every scheduled job is wrapped so one failure logs and the scheduler keeps running (§17.11). The SEBI daily-2FA requirement is never removed; `fyers.auto_login` (config, **off by default**) optionally automates completing it headlessly via TOTP+PIN at 06:00 IST — a real security trade-off (seed+PIN then live in `config.yaml` on the server), documented prominently in `SETUP.md` and left off unless explicitly opted into.

**Dashboard trust model (stated explicitly, not an oversight):** the FastAPI REST/`/ws` surface (`src/api/app.py`) has zero application-level authentication — it relies entirely on the network perimeter (Tailscale-private reachability + `ufw` denying the port publicly, or `127.0.0.1`-only on a local machine). This is a deliberate call for a single-user V1 decision-support tool with no order-placement surface: worst case of the perimeter being breached is read-only exposure of your own scanner/journal/model data, not capital loss. If a future version adds multi-user access, real money movement, or exposes this beyond a private tunnel, add real auth first — don't extend the current no-auth assumption past this trust boundary.

## 13. Trade journal & outcomes (Requirement 7)

Every emitted signal is journaled at emission time with the complete context (features, SHAP, confidence, regime, VIX, mode, risk plan) — before any outcome is known, so hindsight can't edit history. A scheduled evaluator fills `signal_outcomes` at each horizon from stored candles; paper trades model costs as a single all-in round-trip = 2 × `costs.per_side_pct`% of notional (brokerage + STT + estimated slippage, default 0.05%/side → 0.10% round trip). *Correction (06-Jul-2026):* the first cut double-counted this (adverse slippage baked into both fills **and** a separate costs line ≈ 0.20% round trip); fills are now booked at signal price with one explicit `costs_modeled` line so the figure is honest, not inflated. Weekly digest view: realized hit-rate vs stated confidence per bucket — the calibration promise, audited against reality, in the user's face.

## 14. Feasibility matrix (all 10 requirements)

| # | Requirement | Verdict | Evidence / constraint |
|---|---|---|---|
| 1 | All-liquid-NSE universe | ✅ Feasible | Fyers symbol master (public CSV) + series suffix for T2T + NSE REG_IND/ASM lists + turnover filter; 5,000-symbol WS covers the filtered set |
| 2 | Dual mode intraday/swing | ✅ Feasible | 5-min & daily history both available; Fyers order product types INTRADAY (MIS-equivalent) & CNC (V2) |
| 3 | LightGBM + calibrated confidence + SHAP | ✅ Feasible | Pure client-side ML; OFI limited to 5-level depth on shortlist only (50-level TBT is a separate Fyers product — out of scope) |
| 4 | Purged walk-forward CV | ✅ Feasible | Method is code, not API; 100-day/request chunked backfill gives years of 5-min data |
| 5 | Vol-targeted risk engine | ✅ Feasible | All inputs (ATR, GK vol, sector map, correlations) computed locally |
| 6 | Plain-English SHAP guidance | ✅ Feasible | SHAP TreeExplainer + sentence templates |
| 7 | Full-context journal | ✅ Feasible | SQLite schema §9 |
| 8 | SEBI compliance infra | ✅ Feasible (V2 for orders) | Daily 2FA + OAuth now; static IP + 10 orders/s + MPP when orders ship |
| 9 | Premium dark dashboard | ✅ Feasible | React/Tailwind, §11 |
| 10 | Permanent tier disclosure | ✅ Feasible | §1 text, non-dismissible footer |
| — | Colocation / FPGA / L3 books | ❌ Impossible for retail | Confirmed; excluded by design (PDF Part 14) |

## 15. Prototype reference map (what to port from the old zip)

The earlier prototype (`Trading-bot-claude-nse-intraday-trading-assistant-*.zip`) is a logic reference, not a structural template. Port the logic; rewrite the structure to §10.

| Prototype file | Verdict |
|---|---|
| `src/auth.py` | Port nearly as-is (OAuth+TOTP+daily cache is correct) |
| `src/feeds.py` | Port the Live/Delayed/Replay feed abstraction — replay mode is gold for development; extend Live to 5k symbols + DepthUpdate |
| `src/candles.py` | Port tick→candle aggregation |
| `src/risk.py` | Port ATR-stop + fixed-fractional sizing + DayRiskTracker as the risk-engine core |
| `src/journal.py` | Schema superseded by §9; keep the outcome-scoring concept |
| `src/signals.py` | Superseded: rule-vote engine → its five indicators become model features, not votes |
| `src/universe.py` | Superseded by §4 (dynamic universe); keep symbol-format mappers |
| `dashboard/` (Streamlit) | Superseded by React UI; its honest-language patterns carry over |

## 16. Roadmap

1. **P0 – Foundations:** repo scaffold, config, Fyers client + rate limiter, daily auth, symbol master ingest, universe pipeline, backfill (daily + 5-min).
2. **P1 – Features & data:** candle store, feature pipelines (both modes), tradability masks, regime table, IC report.
3. **P2 – Models:** LightGBM + calibration + purged walk-forward CV + SHAP sentences + model registry. Red-flag rules.
4. **P3 – Signals & risk:** emission gate, scanner ranking, risk engine, journal + outcome evaluator, paper trading with costs.
5. **P4 – UI:** full React dashboard per §11.
6. **P5 – Shadow operation:** ≥ 4 weeks paper, live-vs-CV divergence tracked.
7. **V2 (gated on P5 results):** VPS + static IP, order placement (INTRADAY/CNC, manual-confirm, ≤10/s, MPP), square-off automation.

## 17. Non-negotiables (for every future Claude session)

1. No "accuracy" language, anywhere. Calibrated confidence only, spread shown.
2. All CV folds reported; red flags surfaced, never averaged away.
3. Tier disclosure stays in the UI permanently.
4. Signals journal-first: no signal reaches the UI before it's persisted.
5. Tradability masks in every training pipeline (circuit-hit bars excluded).
6. Every Fyers REST call goes through the shared rate limiter (10/s, 200/min, 100k/day).
7. Surveillance-list staleness is visible, never silent.
8. V1 places no orders. The order module stays unwired until P5 exit criteria pass.
9. Time is IST everywhere; naive datetimes are a bug.
10. This file is the source of truth — update it when reality changes.
11. Every scheduled job (`src/jobs/scheduler.py`) is wrapped so one job's failure logs and the scheduler keeps running — a single bad day must never take down an unattended deployment.
12. The dashboard is never bound to a public interface, on a local machine or a cloud VPS — `127.0.0.1` + a private tunnel only.
