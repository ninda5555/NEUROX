"""SQLite (WAL) database. Schema is CLAUDE.md §9 verbatim, wrapped in
IF NOT EXISTS so init is idempotent. Candles/features live in Parquet, not
here — nothing time-series goes into SQLite."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.timeutil import ist_iso, now_ist

SCHEMA = """
-- Instruments & universe -------------------------------------------------
CREATE TABLE IF NOT EXISTS instruments (
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
CREATE TABLE IF NOT EXISTS universe_snapshot (
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
CREATE TABLE IF NOT EXISTS models (
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
  is_active  INTEGER DEFAULT 0,      -- exactly one active per mode
  promotion  TEXT                    -- JSON champion/challenger decision (§6.2)
);
CREATE TABLE IF NOT EXISTS cv_folds (
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
CREATE TABLE IF NOT EXISTS signals (
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
CREATE TABLE IF NOT EXISTS signal_outcomes (
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
CREATE TABLE IF NOT EXISTS paper_trades (
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
CREATE TABLE IF NOT EXISTS market_regime (
  regime_date TEXT PRIMARY KEY,
  vix        REAL, vix_trend TEXT,
  nifty_vs_50dma REAL, nifty_vs_200dma REAL,
  breadth_adv_dec REAL,
  label      TEXT                    -- 'calm_uptrend'|'high_vol'|...
);
CREATE TABLE IF NOT EXISTS app_state (             -- auth + operational state
  key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
); -- token_day, ws_status, surveillance_file_date, loss_limit_state ...
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for DBs created before a column existed.
    CREATE TABLE IF NOT EXISTS never alters an existing table, so live
    deployments (git pull + restart, never a teardown) pick up new columns
    here. Additive-only: never drop, never rewrite rows."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(models)")}
    if "promotion" not in cols:
        # champion/challenger decision JSON: {decision, reasons, compared_to,
        # decided_at} — why a retrained model was or wasn't activated (§6.2)
        conn.execute("ALTER TABLE models ADD COLUMN promotion TEXT")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (key, value, ist_iso(now_ist())),
    )
    conn.commit()
