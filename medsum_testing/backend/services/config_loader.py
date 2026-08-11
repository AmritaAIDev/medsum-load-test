"""Load and expose medsum_config.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_CONFIG: dict[str, Any] | None = None
_RUNTIME: dict[str, Any] = {"auth_token": None, "doctor_id": None}
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_PATH = _REPO_ROOT / "config" / "medsum_config.yaml"
_EXAMPLE_PATH = _REPO_ROOT / "config" / "medsum_config.example.yaml"


def get_repo_root() -> Path:
    return _REPO_ROOT


def get_results_dir() -> Path:
    results = _REPO_ROOT / "results"
    results.mkdir(parents=True, exist_ok=True)
    return results


def get_config_path() -> Path:
    return _CONFIG_PATH


def get_runtime() -> dict[str, Any]:
    return _RUNTIME


def clear_runtime() -> None:
    _RUNTIME["auth_token"] = None
    _RUNTIME["doctor_id"] = None


def load_config(force_reload: bool = False) -> dict[str, Any]:
    global _CONFIG
    if _CONFIG is not None and not force_reload:
        return _CONFIG

    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            "medsum_config.yaml not found - copy medsum_config.example.yaml "
            "and fill in your values"
        )

    with open(_CONFIG_PATH, encoding="utf-8") as f:
        _CONFIG = yaml.safe_load(f) or {}

    _resolve_paths(_CONFIG)
    return _CONFIG


def get_config() -> dict[str, Any]:
    return load_config()


def update_schedule_config(enabled: bool, cron: str, ai_model: str) -> None:
    """Persist scheduler settings to medsum_config.yaml."""
    config_path = _CONFIG_PATH
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    existing = config.get("scheduler") or {}
    config["scheduler"] = {
        "enabled": enabled,
        "cron": cron,
        "ai_model": ai_model,
        "notify_on_regression": existing.get("notify_on_regression", False),
        "max_parallel_tests": existing.get("max_parallel_tests", 2),
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    global _CONFIG
    _CONFIG = config
    _resolve_paths(_CONFIG)


def _resolve_paths(config: dict[str, Any]) -> None:
    gd = config.get("google_drive") or {}
    sa_path = gd.get("service_account_json")
    if sa_path and not os.path.isabs(sa_path):
        gd["service_account_json"] = str(_REPO_ROOT / sa_path)
        config["google_drive"] = gd
