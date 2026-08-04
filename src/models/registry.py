"""Model registry (CLAUDE.md §6.2, §9): every trained model is a NEW row in
`models` with its full CV report; artifacts are never overwritten; exactly
one active model per mode."""

from __future__ import annotations

import json
import pickle
import re
import sqlite3
import uuid
from pathlib import Path

from src.timeutil import ist_date, ist_iso, now_ist


# Feature space every model trained from 2026-08-01 on expects: features
# percentile-ranked within their bar, per-bar constants excluded
# (features/xsection.py). Legacy artifacts carry no marker and are refused
# by the scan path, because they were fitted on raw values.
FEATURE_SPACE = "xsection_rank_v1"


class FeatureSpaceMismatch(RuntimeError):
    """Active model predates the cross-sectional fix. Serving it would feed
    it rank-normalised inputs it was never fitted on — a silent skew, so we
    stop loudly instead. Fix by retraining:
        python -m src.scripts.build_features && python -m src.scripts.retrain
    """


def new_model_id(mode: str) -> str:
    return f"{mode.lower()}_{ist_date().isoformat()}_{uuid.uuid4().hex[:4]}"


def save_model(conn: sqlite3.Connection, models_dir: Path, *, mode: str,
               booster, calibrator, feature_list: list[str], lgbm_params: dict,
               calibration_curve: list[list[float]], cv_report: dict,
               red_flags: list[dict], train_start: str, train_end: str,
               activate: bool = True, featstats: dict | None = None) -> str:
    from src.models.ensemble import BaggedBooster, calibrator_to_json
    from src.models.meta import MetaPipeline
    model_id = new_model_id(mode)
    models_dir.mkdir(parents=True, exist_ok=True)
    txt = models_dir / f"{model_id}.txt"
    if txt.exists():
        raise RuntimeError(f"artifact collision for {model_id} — never overwrite")
    meta = booster if isinstance(booster, MetaPipeline) else None
    primary = meta.primary if meta else booster
    members = primary.members if isinstance(primary, BaggedBooster) else [primary]
    members[0].save_model(str(txt))          # member 0 = the classic artifact
    for i, m in enumerate(members[1:], start=1):
        m.save_model(str(models_dir / f"{model_id}.m{i}.txt"))
    if meta:
        meta.meta.save_model(str(models_dir / f"{model_id}.meta.txt"))
    # calibrator as JSON (T10): new artifacts carry no pickle at all —
    # load_active keeps a .pkl fallback for models trained before this.
    (models_dir / f"{model_id}.calib.json").write_text(json.dumps(
        {"calibrator": calibrator_to_json(calibrator),
         "feature_list": feature_list, "n_members": len(members),
         # which feature SPACE this model expects (added 2026-08-01). Models
         # trained before the cross-sectional fix saw raw values; serving
         # them rank-normalised inputs is silent train/serve skew, so the
         # scan path refuses rather than scoring nonsense. Absent == legacy.
         "feature_space": FEATURE_SPACE,
         **({"meta_features": meta.ctx_cols} if meta else {})}))
    if featstats:
        # training-time feature distributions — the PSI drift baseline (T7)
        (models_dir / f"{model_id}.featstats.json").write_text(
            json.dumps(featstats))

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


# Promotion gate (champion/challenger, §6.2). A retrained model no longer
# auto-activates: it must be at least as good as the current champion on its
# own CV report. Tolerances exist because fresher training data has value —
# a challenger within noise of the champion should win, one that is
# materially worse should not silently take over an unattended deployment.
PROMOTE_MEAN_TOLERANCE = 0.02   # challenger precision_mean >= champ - this
PROMOTE_STD_TOLERANCE = 0.05    # challenger precision_std  <= champ + this


def promotion_decision(conn: sqlite3.Connection, model_id: str) -> dict:
    """Compare the challenger `model_id` against the active champion of the
    same mode. Pure decision — activates nothing, writes nothing. Every
    reason is recorded so the Model page can show WHY (§17.2 spirit: the
    decision is displayed, never silent)."""
    row = conn.execute("SELECT * FROM models WHERE model_id=?", (model_id,)).fetchone()
    if row is None:
        raise KeyError(model_id)
    champ = conn.execute(
        "SELECT * FROM models WHERE mode=? AND is_active=1 AND model_id != ?",
        (row["mode"], model_id)).fetchone()
    if champ is None:
        return {"decision": "promoted", "compared_to": None,
                "reasons": ["first model for this mode — no champion to beat"]}

    new_r = json.loads(row["cv_report"])
    old_r = json.loads(champ["cv_report"])
    new_mean, old_mean = new_r.get("precision_mean"), old_r.get("precision_mean")
    new_std, old_std = new_r.get("precision_std"), old_r.get("precision_std")

    reasons, promote = [], True
    if new_mean is None:
        promote = False
        reasons.append("challenger produced no signals in CV — nothing demonstrated")
    elif old_mean is None:
        reasons.append("champion had no CV signals; challenger does — promote")
    else:
        if new_mean >= old_mean - PROMOTE_MEAN_TOLERANCE:
            reasons.append(f"precision mean {new_mean:.3f} vs champion "
                           f"{old_mean:.3f} (tolerance {PROMOTE_MEAN_TOLERANCE}) — ok")
        else:
            promote = False
            reasons.append(f"precision mean {new_mean:.3f} materially below "
                           f"champion {old_mean:.3f} — hold")
        if new_std is not None and old_std is not None:
            if new_std <= old_std + PROMOTE_STD_TOLERANCE:
                reasons.append(f"fold-to-fold std {new_std:.3f} vs champion "
                               f"{old_std:.3f} — stability ok")
            else:
                promote = False
                reasons.append(f"fold-to-fold std {new_std:.3f} materially above "
                               f"champion {old_std:.3f} — less stable across time, hold")
    n_flags_new = len(json.loads(row["red_flags"] or "[]"))
    n_flags_old = len(json.loads(champ["red_flags"] or "[]"))
    reasons.append(f"red flags: challenger {n_flags_new}, champion {n_flags_old} "
                   "(reported, not gated — §17.2)")
    return {"decision": "promoted" if promote else "held",
            "compared_to": champ["model_id"], "reasons": reasons}


def record_promotion(conn: sqlite3.Connection, model_id: str, decision: dict) -> None:
    decision = {**decision, "decided_at": ist_iso(now_ist())}
    conn.execute("UPDATE models SET promotion=? WHERE model_id=?",
                 (json.dumps(decision), model_id))
    conn.commit()


def promote_if_better(conn: sqlite3.Connection, model_id: str, *,
                      force: bool = False) -> dict:
    """The gate: decide, record the decision on the challenger row, and
    activate only on 'promoted' (or force=True, which is itself recorded)."""
    decision = promotion_decision(conn, model_id)
    if force and decision["decision"] != "promoted":
        decision = {**decision, "decision": "promoted",
                    "reasons": decision["reasons"] + ["FORCED by operator (--force-activate)"]}
    record_promotion(conn, model_id, decision)
    if decision["decision"] == "promoted":
        set_active(conn, model_id)
    return decision


def load_active(conn: sqlite3.Connection, mode: str, *, require_current_space: bool = True):
    """Returns (model_id, booster, calibrator, feature_list) for the active
    model of a mode. booster is a BaggedBooster when member artifacts exist
    ({model_id}.m1.txt …), else a plain Booster — the scan path duck-types.

    Models saved from T10 on carry a JSON calibrator ({model_id}.calib.json,
    no pickle anywhere). The .pkl branch below exists only for artifacts
    trained before that. Security note on that legacy branch: unpickling is
    unsafe for untrusted input in general; it's safe here specifically
    because the path always comes from `artifact_path` on a DB row that only
    save_model() ever writes (the trusted training pipeline) — never from
    request/user input. Do not add an API endpoint or CLI flag that lets a
    caller choose an arbitrary artifact_path/model_id to load without
    re-checking this."""
    import lightgbm as lgb
    from src.models.ensemble import BaggedBooster, calibrator_from_json
    require = require_current_space
    row = conn.execute("SELECT * FROM models WHERE mode=? AND is_active=1",
                       (mode,)).fetchone()
    if row is None:
        raise LookupError(f"no active model for {mode}")
    artifact = Path(row["artifact_path"])
    booster = lgb.Booster(model_file=str(artifact))
    member_rx = re.compile(re.escape(row["model_id"]) + r"\.m(\d+)\.txt$")
    extras = sorted((p for p in artifact.parent.glob(f"{row['model_id']}.m*.txt")
                     if member_rx.search(p.name)),
                    key=lambda p: int(member_rx.search(p.name).group(1)))
    if extras:
        booster = BaggedBooster([booster] + [lgb.Booster(model_file=str(p))
                                             for p in extras])
    calib_json = artifact.with_suffix(".calib.json")
    if calib_json.exists():
        blob = json.loads(calib_json.read_text())
        _check_feature_space(row["model_id"], blob.get("feature_space"), require)
        if blob.get("meta_features"):        # T11: wrap the meta filter
            from src.models.meta import MetaPipeline
            meta_b = lgb.Booster(
                model_file=str(artifact.with_suffix(".meta.txt")))
            booster = MetaPipeline(booster, meta_b, blob["meta_features"])
        return (row["model_id"], booster,
                calibrator_from_json(blob["calibrator"]), blob["feature_list"])
    with open(artifact.with_suffix(".pkl"), "rb") as fh:   # pre-T10 artifacts
        blob = pickle.load(fh)
    _check_feature_space(row["model_id"], None, require)
    return row["model_id"], booster, blob["calibrator"], blob["feature_list"]


def _check_feature_space(model_id: str, space: str | None, require: bool) -> None:
    if not require or space == FEATURE_SPACE:
        return
    raise FeatureSpaceMismatch(
        f"active model {model_id} was trained on feature space "
        f"{space or 'legacy (raw values, per-bar constants included)'}, but "
        f"the scan path now produces {FEATURE_SPACE}. Scoring it would feed "
        "the model inputs it was never fitted on. Rebuild features and "
        "retrain before serving:\n"
        "  python -m src.scripts.build_features --mode both\n"
        "  python -m src.scripts.retrain --mode both")
