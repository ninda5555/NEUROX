"""Nightly SQLite backup (T16).

The DB is the journal — signals, outcomes, paper trades, model registry,
the audit trail hindsight must never edit (§13). One bad disk or fat-
fingered delete on an unattended box must not erase it.

Uses sqlite3's online backup API (safe under WAL while the scheduler and
dashboard hold connections — no file-copy torn pages), quick-checks the
copy's integrity BEFORE compressing (a corrupt backup that is only
discovered on restore day is worse than an alert tonight), gzips, then
prunes backups older than the retention window. Runs from the scheduler at
21:30 IST inside _safe; never raises.
"""

from __future__ import annotations

import datetime as dt
import gzip
import logging
import shutil
import sqlite3
from pathlib import Path

from src.timeutil import ist_date

log = logging.getLogger(__name__)


def backup_sqlite(db_path: Path, backup_dir: Path, keep_days: int = 14) -> dict:
    """Returns {status, path?, pruned}. Never raises."""
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        day = ist_date().isoformat()
        raw = backup_dir / f"app-{day}.db"
        gz = backup_dir / f"app-{day}.db.gz"

        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(str(raw))
            try:
                src.backup(dst)               # online backup API, WAL-safe
                check = dst.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                dst.close()
        finally:
            src.close()
        if check != "ok":
            raw.unlink(missing_ok=True)
            log.error("backup integrity check failed: %s — backup discarded", check)
            return {"status": "integrity_failed", "pruned": 0}

        with open(raw, "rb") as fin, gzip.open(gz, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)
        raw.unlink()

        cutoff = ist_date() - dt.timedelta(days=keep_days)
        pruned = 0
        for old in sorted(backup_dir.glob("app-*.db.gz")):
            try:
                d = dt.date.fromisoformat(old.stem.replace("app-", "").replace(".db", ""))
            except ValueError:
                continue
            if d < cutoff:
                old.unlink()
                pruned += 1
        log.info("backup ok: %s (%d old pruned, keep %dd)", gz.name, pruned, keep_days)
        return {"status": "ok", "path": str(gz), "pruned": pruned}
    except Exception:
        log.exception("backup failed (journal untouched; will retry tomorrow)")
        return {"status": "error", "pruned": 0}
