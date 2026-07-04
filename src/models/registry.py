"""Model registry (CLAUDE.md §6.2, §9): every trained model is a NEW row in
`models` with its full CV report; artifacts are never overwritten; exactly
one active model per mode."""

from __future__ import annotations

import json
import pickle
import sqlite3
import uuid
from pathlib import Path

from src.timeutil import ist_date, ist_iso, now_ist


def new_model_id(mode: str) -> str:
    return f"{mode.lower()}_{ist_date().isoformat()}_{uuid.uuid4().hex[:4]}"


def save_model(conn: sqlite3.Connection, models_dir: Path, *, mode: str,
               booster, calibrator, feature_list: list[str], lgbm_params: dict,
               calibration_curve: list[list[float]], cv_report: dict,
               red_flags: list[dict], train_start: str, train_end: str,
               activate: bool = True) -> str:
    model_id = new_model_id(mode)
    models_dir.mkdir(parents=True, exist_ok=True)
    txt = models_dir / f"{model_id}.txt"
    pkl = models_dir / f"{model_id}.pkl"
    if txt.exists() or pkl.exists():
        raise RuntimeError(f"artifact collision for {model_id} — never overwrite")
    booster.save_model(str(txt))
    with open(pkl, "wb") as fh:
        pickle.dump({"calibrator": calibrator, "feature_list": feature_list}, fh)

    conn.execute(
        """INSERT INTO models (model_id, mode, trained_at, train_start, train_end,
           feature_list, lgbm_params, calibration, cv_report, red_flags,
           artifact_path, is_active) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)""",
        (model_id, mode, ist_iso(now_ist()), train_start, train_end,
         json.dumps(feature_list), json.dumps(lgbm_params),
         json.dumps({"method": "isotonic", "curve": calibration_curve}),
         json.dumps(cv_report), json.dumps(red_flags), str(txt)),
    )
    for f in cv_report["folds"]:
        conn.execute(
            """INSERT INTO cv_folds (model_id, fold, train_start, train_end,
               test_start, test_end, n_signals, precision_at_thr,
               calibration_err, avg_r_multiple, max_drawdown_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (model_id, f["fold"], f["train_start"], f["train_end"],
             f["test_start"], f["test_end"], f["n_signals"],
             f["precision_at_thr"], f["calibration_err"],
             f["avg_r_multiple"], f["max_drawdown_pct"]))
    conn.commit()
    if activate:
        set_active(conn, model_id)
    return model_id


def set_active(conn: sqlite3.Connection, model_id: str) -> None:
    row = conn.execute("SELECT mode FROM models WHERE model_id=?", (model_id,)).fetchone()
    if row is None:
        raise KeyError(model_id)
    conn.execute("UPDATE models SET is_active=0 WHERE mode=?", (row["mode"],))
    conn.execute("UPDATE models SET is_active=1 WHERE model_id=?", (model_id,))
    conn.commit()


def load_active(conn: sqlite3.Connection, mode: str):
    """Returns (model_id, booster, calibrator, feature_list) for the active
    model of a mode."""
    import lightgbm as lgb
    row = conn.execute("SELECT * FROM models WHERE mode=? AND is_active=1",
                       (mode,)).fetchone()
    if row is None:
        raise LookupError(f"no active model for {mode}")
    booster = lgb.Booster(model_file=row["artifact_path"])
    with open(Path(row["artifact_path"]).with_suffix(".pkl"), "rb") as fh:
        blob = pickle.load(fh)
    return row["model_id"], booster, blob["calibrator"], blob["feature_list"]
