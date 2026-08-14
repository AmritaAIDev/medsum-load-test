"""Human-readable reference ID generators for test runs."""

from __future__ import annotations

import json
from pathlib import Path

from medsum_testing.backend.services.config_loader import get_results_dir

LANGUAGE_CODE_MAP = {
    "hindi": "HI",
    "english": "EN",
    "malayalam": "ML",
    "tamil": "TA",
    "telugu": "TE",
    "kannada": "KN",
    "bengali": "BN",
    "marathi": "MR",
    "gujarati": "GU",
    "punjabi": "PA",
    "odia": "OD",
    "urdu": "UR",
}


def _iter_saved_tc_refs() -> list[str]:
    refs: list[str] = []
    results_dir = get_results_dir()
    for path in results_dir.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            tc_ref = data.get("tc_ref")
            if tc_ref:
                refs.append(tc_ref)
        except (json.JSONDecodeError, OSError):
            continue
    return refs


def generate_tc_ref(language: str) -> str:
    """Format: TC-{LANG_CODE}-{4-digit counter}, per language."""
    lang_code = LANGUAGE_CODE_MAP.get(language.lower().strip(), "XX")
    prefix = f"TC-{lang_code}-"
    existing_count = sum(1 for ref in _iter_saved_tc_refs() if ref.startswith(prefix))
    return f"{prefix}{str(existing_count + 1).zfill(4)}"
