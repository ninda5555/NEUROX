"""T16: nightly SQLite backup — restorable, integrity-checked, pruned."""

from __future__ import annotations

import datetime as dt
import gzip
import shutil
import sqlite3

from src import db as dbm
from src.jobs.backup import backup_sqlite
from src.timeutil import ist_date


def _make_db(tmp_path):
    p = tmp_path / "app.db"
    c = dbm.connect(p)
    dbm.init_db(c)
    c.execute("INSERT INTO app_state (key, value, updated_at) "
              "VALUES ('probe', 'survives-restore', 't')")
    c.commit()
    return p, c


def test_backup_is_restorable(tmp_path):
    db, live = _make_db(tmp_path)
    res = backup_sqlite(db, tmp_path / "bk")
    assert res["status"] == "ok"
    gz = tmp_path / "bk" / f"app-{ist_date().isoformat()}.db.gz"
    assert gz.exists()
    # restore it and read the probe row back
    restored = tmp_path / "restored.db"
    with gzip.open(gz, "rb") as fin, open(restored, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    rc = sqlite3.connect(restored)
    row = rc.execute("SELECT value FROM app_state WHERE key='probe'").fetchone()
    assert row[0] == "survives-restore"
    assert rc.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    live.close()


def test_backup_safe_while_connection_open_and_idempotent(tmp_path):
    db, live = _make_db(tmp_path)
    # live connection stays open (scheduler/dashboard hold one in production)
    assert backup_sqlite(db, tmp_path / "bk")["status"] == "ok"
    assert backup_sqlite(db, tmp_path / "bk")["status"] == "ok"  # same-day rerun
    assert len(list((tmp_path / "bk").glob("*.gz"))) == 1        # overwrites, no dupes
    live.close()


def test_prune_respects_retention(tmp_path):
    db, live = _make_db(tmp_path)
    bk = tmp_path / "bk"
    bk.mkdir()
    old = ist_date() - dt.timedelta(days=20)
    recent = ist_date() - dt.timedelta(days=3)
    (bk / f"app-{old.isoformat()}.db.gz").write_bytes(b"x")
    (bk / f"app-{recent.isoformat()}.db.gz").write_bytes(b"x")
    (bk / "unrelated.txt").write_bytes(b"x")
    res = backup_sqlite(db, bk, keep_days=14)
    assert res["status"] == "ok" and res["pruned"] == 1
    assert not (bk / f"app-{old.isoformat()}.db.gz").exists()
    assert (bk / f"app-{recent.isoformat()}.db.gz").exists()
    assert (bk / "unrelated.txt").exists()   # never touches non-backup files
    live.close()


def test_backup_never_raises(tmp_path):
    res = backup_sqlite(tmp_path / "missing" / "nope.db",
                        tmp_path / "unwritable-parent" / "bk")
    assert res["status"] in ("ok", "error")   # a fresh empty DB may be created;
    # the contract under test: no exception escapes


def test_scheduler_job_respects_kill_switch(tmp_path, monkeypatch):
    import copy
    from src.config import Config, DEFAULTS
    from src.jobs import scheduler as sched
    data = copy.deepcopy(DEFAULTS)
    data["backup"]["enabled"] = False
    monkeypatch.setattr(sched, "load_config", lambda: Config(data, root=tmp_path))
    calls = []
    monkeypatch.setattr("src.jobs.backup.backup_sqlite",
                        lambda *a, **k: calls.append(1))
    sched.job_nightly_backup()
    assert calls == []                        # disabled -> untouched