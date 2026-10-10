"""Configuration loading. Defaults mirror CLAUDE.md; config.yaml (gitignored)
overrides them and is the only place credentials may live (§12)."""

from __future__ import annotations

import copy
import math
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "fyers": {
        "app_id": "",
        "secret_key": "",
        "redirect_uri": "https://127.0.0.1",
        "fyers_id": "",
        "totp_secret": "",
        "pin": "",
        "auto_login": False,
        "log_dir": "fyers_logs",
    },
    "paths": {
        "db": "data/app.db",
        "candles": "data/candles",
        "features": "data/features",
        "models": "models",
        "tokens": ".tokens",
        "surveillance_cache": "data/surveillance",
        "scores": "data/scores",
    },
    "universe": {
        "min_turnover_cr": 5.0,
        "min_price": 20.0,
        "min_listed_sessions": 60,
        "min_nonzero_vol_sessions": 18,
        "exclude_gsm_all": True,
        "exclude_asm_min_stage": 1,
        "surveillance_lookback_days": 7,
    },
    "backfill": {"daily_days": 730, "fivemin_days": 120},
    "labels": {"min_barrier_atr_pct": 0.30},
    "training": {
        "retrain_schedule": "off",  # research only: off | weekly | daily
        "time_decay_half_life_days": 0,     # T9: >0 halves sample weight per N days of age (0 = off)
        "swing_uniqueness_weights": False,  # T9: 1/overlap weights for swing labels (off)
        "ensemble_size": 1,                 # T10: >1 = seed-bagged members (1 = off)
        "meta_labeling": False,             # T11: meta model filters the primary (off)
    },
    "observability": {
        "score_log": True,          # parquet log of ALL scored candidates (T6)
        "psi_window_days": 5,       # live window compared against training (T7)
        "psi_flag_threshold": 0.25, # PSI above this => feature-drift red flag
    },
    "backup": {
        "enabled": True,            # T16: nightly gzip snapshot of the journal DB
        "dir": "data/backups",
        "keep_days": 14,
    },
    "security": {
        # T13, OFF by default (perimeter-only trust model, CLAUDE.md §12).
        # When true, every request must carry a Tailscale-User-Login header —
        # only `tailscale serve` on 127.0.0.1 can set it, so it is unspoofable
        # from off-tailnet. allowed_logins [] = any tailnet identity; non-empty
        # = an allowlist (the multi-user story).
        "require_tailscale_identity": False,
        "allowed_logins": [],
    },
    "signals": {
        "confidence_threshold": 0.60,
        "scanner_top_n": 12,
        # T12 (all-zero = off): additive per-regime-bucket threshold bumps —
        # e.g. hv_bn 0.05 demands 0.65 in a high-VIX/negative-breadth tape
        "regime_threshold_bump": {
            "hv_bn": 0.0, "hv_bp": 0.0, "lv_bn": 0.0, "lv_bp": 0.0,
            "unknown": 0.0,
        },
    },
    "risk": {
        "capital": 1_000_000,
        "intraday_risk_pct": 1.0,
        "swing_risk_pct": 1.5,
        "max_position_notional_pct": 20.0,
        "daily_loss_limit_pct": 3.0,
        "loss_limit_warn_frac": 0.75,
        "min_stop_to_cost": 5.0,
        "min_expected_r": 0.0,   # post-cost E[R] floor; only ever rejects more
                                 # than the confidence gate (see signals/engine)
    },
    "costs": {"per_side_pct": 0.05},
}


class ConfigError(RuntimeError):
    pass


def _merge(base: dict, override: dict, path: str = "") -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        here = f"{path}.{k}" if path else k
        if k not in base:
            raise ConfigError(
                f"unknown config key {here!r} — config keys are defined by "
                "CLAUDE.md; fix the typo or update DEFAULTS alongside CLAUDE.md"
            )
        if isinstance(base[k], dict):
            if not isinstance(v, dict):
                raise ConfigError(f"config key {here!r} must be a mapping")
            out[k] = _merge(base[k], v, here)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data: dict[str, Any], root: Path):
        self._data = data
        self.root = root

    def __getitem__(self, dotted: str) -> Any:
        cur: Any = self._data
        for part in dotted.split("."):
            try:
                cur = cur[part]
            except (KeyError, TypeError):
                raise ConfigError(f"missing config key {dotted!r}") from None
        return cur

    def path(self, dotted: str) -> Path:
        """Resolve a paths.* entry relative to the project root."""
        p = Path(self[dotted])
        return p if p.is_absolute() else self.root / p

    @property
    def data(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)


def project_root() -> Path:
    return Path(os.environ.get("NEUROX_ROOT", Path(__file__).resolve().parents[1]))


def _warn_if_world_readable(p: Path) -> None:
    """config.yaml holds the Fyers secret (and, if auto_login is on, the TOTP
    seed + PIN) — §12. Warn, non-fatally, if it's readable by group/other on
    a POSIX box so a misconfigured deploy is visible instead of silent. Never
    raises: a perms check must not be able to take the dashboard down."""
    if os.name != "posix":
        return
    try:
        mode = p.stat().st_mode
    except OSError:
        return
    if mode & 0o077:
        import warnings
        warnings.warn(
            f"{p} is group/other-accessible (mode {oct(mode & 0o777)}); it "
            "holds credentials (§12). Run: chmod 600 " + str(p),
            stacklevel=2)


def load_config(config_path: str | Path | None = None) -> Config:
    root = project_root()
    p = Path(config_path) if config_path else root / "config.yaml"
    data = DEFAULTS
    if p.exists():
        _warn_if_world_readable(p)
        with open(p, encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
        if not isinstance(user, dict):
            raise ConfigError(f"{p} must contain a YAML mapping")
        data = _merge(DEFAULTS, user)
    _validate(data)
    return Config(copy.deepcopy(data), root)


def _validate(data: dict) -> None:
    """Reject malformed settings before they reach jobs or position sizing."""
    def walk(values, defaults, prefix=""):
        for key, default in defaults.items():
            value = values[key]
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(default, dict):
                walk(value, default, name)
            elif isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ConfigError(f"{name} must be true or false")
            elif isinstance(default, (int, float)):
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value)):
                    raise ConfigError(f"{name} must be a finite number")
                if isinstance(default, int) and not isinstance(value, int):
                    raise ConfigError(f"{name} must be an integer")
            elif isinstance(default, str):
                if not isinstance(value, str):
                    raise ConfigError(f"{name} must be text (quote PINs and IDs)")
            elif isinstance(default, list):
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise ConfigError(f"{name} must be a list of text values")
    walk(data, DEFAULTS)
    if data["training"]["retrain_schedule"] not in {"off", "weekly", "daily"}:
        raise ConfigError("training.retrain_schedule must be off, weekly or daily")
    risk = data["risk"]
    if risk["capital"] <= 0:
        raise ConfigError("risk.capital must be positive")
    for key in ("intraday_risk_pct", "swing_risk_pct", "max_position_notional_pct", "daily_loss_limit_pct"):
        if not 0 < risk[key] <= 100:
            raise ConfigError(f"risk.{key} must be in (0, 100]")
    if not 0 < risk["loss_limit_warn_frac"] <= 1:
        raise ConfigError("risk.loss_limit_warn_frac must be in (0, 1]")
    if not 0 <= data["signals"]["confidence_threshold"] <= 1:
        raise ConfigError("signals.confidence_threshold must be in [0, 1]")
    if not 1 <= data["signals"]["scanner_top_n"] <= 100:
        raise ConfigError("signals.scanner_top_n must be in [1, 100]")
    if data["costs"]["per_side_pct"] < 0:
        raise ConfigError("costs.per_side_pct cannot be negative")
    for key in ("daily_days", "fivemin_days"):
        if data["backfill"][key] <= 0:
            raise ConfigError(f"backfill.{key} must be positive")
    if data["backup"]["keep_days"] < 1:
        raise ConfigError("backup.keep_days must be positive")
    if data["training"]["ensemble_size"] < 1:
        raise ConfigError("training.ensemble_size must be positive")
    if data["training"]["time_decay_half_life_days"] < 0:
        raise ConfigError("training.time_decay_half_life_days cannot be negative")
