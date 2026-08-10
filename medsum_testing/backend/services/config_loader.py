"""Load and expose medsum_config.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_CONFIG: dict[str, Any] | None = None
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


def _resolve_paths(config: dict[str, Any]) -> None:
    gd = config.get("google_drive") or {}
    sa_path = gd.get("service_account_json")
    if sa_path and not os.path.isabs(sa_path):
        gd["service_account_json"] = str(_REPO_ROOT / sa_path)
        config["google_drive"] = gd
