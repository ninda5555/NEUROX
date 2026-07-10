"""Configuration loading. Defaults mirror CLAUDE.md; config.yaml (gitignored)
overrides them and is the only place credentials may live (§12)."""

from __future__ import annotations

import copy
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
        "log_dir": "fyers_logs",
    },
    "paths": {
        "db": "data/app.db",
        "candles": "data/candles",
        "features": "data/features",
        "models": "models",
        "tokens": ".tokens",
        "surveillance_cache": "data/surveillance",
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
    "signals": {"confidence_threshold": 0.60, "scanner_top_n": 12},
    "risk": {
        "capital": 1_000_000,
        "intraday_risk_pct": 1.0,
        "swing_risk_pct": 1.5,
        "max_position_notional_pct": 20.0,
        "daily_loss_limit_pct": 3.0,
        "loss_limit_warn_frac": 0.75,
        "min_stop_to_cost": 5.0,
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


def load_config(config_path: str | Path | None = None) -> Config:
    root = project_root()
    p = Path(config_path) if config_path else root / "config.yaml"
    data = DEFAULTS
    if p.exists():
        with open(p) as f:
            user = yaml.safe_load(f) or {}
        if not isinstance(user, dict):
            raise ConfigError(f"{p} must contain a YAML mapping")
        data = _merge(DEFAULTS, user)
    return Config(copy.deepcopy(data), root)
