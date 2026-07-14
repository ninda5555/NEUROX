"""Champion/challenger promotion gate (CLAUDE.md §6.2, T5).

A retrained model never auto-activates: it must match the champion within
tolerance on its own CV report. Every decision is recorded with reasons.
"""

from __future__ import annotations

import json
import sqlite3

from src import db as dbm
from src.models.registry import (promote_if_better, promotion_decision,
                                 record_promotion)


def _insert_model(conn, model_id, mode="INTRADAY", mean=None, std=None,
                  active=0, flags=None):
    cv = {"precision_mean": mean, "precision_std": std, "folds": []}
    conn.execute(
        """INSERT INTO models (model_id, mode, trained_at, feature_list,
           lgbm_params, calibration, cv_report, red_flags, artifact_path,
           is_active) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (model_id, mode, f"2026-07-14T10:00:00+05:30", "[]", "{}", "{}",
         json.dumps(cv), json.dumps(flags or []), "p", active))
    conn.commit()


def _active_id(conn, mode="INTRADAY"):
    r = conn.execute("SELECT model_id FROM models WHERE mode=? AND is_active=1",
                     (mode,)).fetchone()
    return r["model_id"] if r else None


def test_first_model_auto_promotes(conn):
    _insert_model(conn, "m1", mean=0.45, std=0.08)
    d = promote_if_better(conn, "m1")
    assert d["decision"] == "promoted" and d["compared_to"] is None
    assert _active_id(conn) == "m1"


def test_better_challenger_promotes_and_replaces_champion(conn):
    _insert_model(conn, "champ", mean=0.45, std=0.08, active=1)
    _insert_model(conn, "chal", mean=0.50, std=0.06)
    d = promote_if_better(conn, "chal")
    assert d["decision"] == "promoted" and d["compared_to"] == "champ"
    assert _active_id(conn) == "chal"
    n = conn.execute("SELECT COUNT(*) n FROM models WHERE is_active=1").fetchone()["n"]
    assert n == 1  # exactly one champion, always


def test_equal_within_tolerance_promotes_fresher_model(conn):
    _insert_model(conn, "champ", mean=0.45, std=0.08, active=1)
    _insert_model(conn, "chal", mean=0.44, std=0.09)   # within 0.02 / 0.05
    assert promote_if_better(conn, "chal")["decision"] == "promoted"
    assert _active_id(conn) == "chal"


def test_materially_worse_challenger_is_held(conn):
    _insert_model(conn, "champ", mean=0.50, std=0.06, active=1)
    _insert_model(conn, "chal", mean=0.40, std=0.06)
    d = promote_if_better(conn, "chal")
    assert d["decision"] == "held"
    assert _active_id(conn) == "champ"          # champion untouched
    assert any("materially below" in r for r in d["reasons"])
    stored = json.loads(conn.execute(
        "SELECT promotion FROM models WHERE model_id='chal'").fetchone()["promotion"])
    assert stored["decision"] == "held" and stored["decided_at"]


def test_unstable_challenger_is_held(conn):
    _insert_model(conn, "champ", mean=0.48, std=0.05, active=1)
    _insert_model(conn, "chal", mean=0.49, std=0.20)   # mean fine, std blown
    d = promote_if_better(conn, "chal")
    assert d["decision"] == "held"
    assert any("less stable" in r for r in d["reasons"])


def test_challenger_with_no_signals_is_held(conn):
    _insert_model(conn, "champ", mean=0.48, std=0.05, active=1)
    _insert_model(conn, "chal", mean=None, std=None)
    d = promote_if_better(conn, "chal")
    assert d["decision"] == "held"
    assert _active_id(conn) == "champ"


def test_force_activate_promotes_and_records_the_override(conn):
    _insert_model(conn, "champ", mean=0.50, std=0.05, active=1)
    _insert_model(conn, "chal", mean=0.30, std=0.05)
    d = promote_if_better(conn, "chal", force=True)
    assert d["decision"] == "promoted"
    assert any("FORCED" in r for r in d["reasons"])
    assert _active_id(conn) == "chal"


def test_modes_are_gated_independently(conn):
    _insert_model(conn, "i1", mode="INTRADAY", mean=0.5, std=0.05, active=1)
    _insert_model(conn, "s1", mode="SWING", mean=0.4, std=0.05)
    d = promote_if_better(conn, "s1")
    assert d["decision"] == "promoted" and d["compared_to"] is None
    assert _active_id(conn, "INTRADAY") == "i1"
    assert _active_id(conn, "SWING") == "s1"


def test_migration_adds_promotion_column_to_old_db(tmp_path):
    """A live server DB predates the column; init_db must add it in place."""
    p = tmp_path / "old.db"
    raw = sqlite3.connect(p)
    raw.execute("""CREATE TABLE models (
        model_id TEXT PRIMARY KEY, mode TEXT NOT NULL, trained_at TEXT NOT NULL,
        train_start TEXT, train_end TEXT, feature_list TEXT NOT NULL,
        lgbm_params TEXT NOT NULL, calibration TEXT NOT NULL,
        cv_report TEXT NOT NULL, red_flags TEXT, artifact_path TEXT NOT NULL,
        is_active INTEGER DEFAULT 0)""")
    raw.commit(); raw.close()
    conn = dbm.connect(p)
    dbm.init_db(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(models)")}
    assert "promotion" in cols
    dbm.init_db(conn)  # idempotent — second run must not fail
