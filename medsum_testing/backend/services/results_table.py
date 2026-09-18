"""One results table; Results vs Latency is a column tab, not a second table.

Row set, order, sort, filter, and page live on the view state. Switching
tabs only changes which columns render. Test Case ID is the leading column
on both tabs so identity stays visible (and View can key off test_id).
"""

from __future__ import annotations

import math
from typing import Any

from medsum_testing.backend.models.test_result import RESULTS_TABLE_HEADERS
from medsum_testing.backend.services.latency_analysis import LATENCY_ANALYSIS_HEADERS
from medsum_testing.backend.services.test_case_view import stable_test_id

TAB_RESULTS = "results"
TAB_LATENCY = "latency"

RESULTS_TAB_HEADERS = RESULTS_TABLE_HEADERS
LATENCY_TAB_HEADERS = ("Test Case ID",) + LATENCY_ANALYSIS_HEADERS

DEFAULT_PAGE_SIZE = 50

_SORT_FIELDS = {
    "Test Case ID": ("tc_ref", "test_case_id", "test_id"),
    "test_id": ("test_id", "tc_ref"),
    "tc_ref": ("tc_ref", "test_id"),
    "Audio File": ("audio_filename", "filename"),
    "audio_filename": ("audio_filename", "filename"),
    "Language": ("language",),
    "language": ("language",),
}


def default_table_state(**overrides) -> dict[str, Any]:
    state = {
        "tab": TAB_RESULTS,
        "page": 1,
        "page_size": DEFAULT_PAGE_SIZE,
        "sort_key": "",
        "sort_dir": "asc",
        "filter": "",
    }
    state.update(overrides)
    return state


def visible_headers(tab: str | None) -> tuple[str, ...]:
    if (tab or "").strip().lower() == TAB_LATENCY:
        return LATENCY_TAB_HEADERS
    return RESULTS_TAB_HEADERS


def display_test_case_id(row: dict | None) -> str:
    """Visible Test Case ID. Never a batch run_ref and never a table index."""
    data = row or {}
    for key in ("tc_ref", "test_case_id"):
        val = str(data.get(key) or "").strip()
        if val:
            return val
    return stable_test_id(data) or "—"


def row_identities(rows: list[dict] | None) -> list[str]:
    return [stable_test_id(row) for row in (rows or [])]


def _haystack(row: dict) -> str:
    data = row or {}
    parts = [
        display_test_case_id(data),
        stable_test_id(data),
        str(data.get("audio_filename") or data.get("filename") or ""),
        str(data.get("language") or ""),
    ]
    return " ".join(parts).lower()


def _sort_value(row: dict, sort_key: str) -> str:
    fields = _SORT_FIELDS.get(sort_key) or (sort_key,)
    for field in fields:
        val = str((row or {}).get(field) or "").strip()
        if val:
            return val.lower()
    if sort_key in {"Test Case ID", "test_id", "tc_ref"}:
        return display_test_case_id(row).lower()
    return ""


def apply_table_view(rows: list[dict] | None, state: dict | None = None) -> dict[str, Any]:
    """Filter → sort → paginate. `tab` selects headers only — not the row set."""
    cfg = default_table_state(**(state or {}))
    tab = cfg["tab"] if cfg.get("tab") == TAB_LATENCY else TAB_RESULTS
    items = list(rows or [])
    query = str(cfg.get("filter") or "").strip().lower()
    if query:
        items = [row for row in items if query in _haystack(row)]
    sort_key = str(cfg.get("sort_key") or "").strip()
    if sort_key:
        reverse = str(cfg.get("sort_dir") or "asc").lower() == "desc"
        items = sorted(items, key=lambda row: _sort_value(row, sort_key), reverse=reverse)
    page_size = max(1, int(cfg.get("page_size") or DEFAULT_PAGE_SIZE))
    total = len(items)
    total_pages = max(1, math.ceil(total / page_size) if total else 1)
    page = max(1, int(cfg.get("page") or 1))
    page = min(page, total_pages)
    start = (page - 1) * page_size
    page_rows = items[start : start + page_size]
    return {
        "tab": tab,
        "headers": visible_headers(tab),
        "rows": page_rows,
        "identities": row_identities(page_rows),
        "display_ids": [display_test_case_id(row) for row in page_rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "sort_key": sort_key,
        "sort_dir": cfg.get("sort_dir") or "asc",
        "filter": query,
    }


def switch_tab(state: dict | None, tab: str) -> dict[str, Any]:
    """Change only the column tab. Sort / filter / page stay put."""
    next_state = default_table_state(**(state or {}))
    next_state["tab"] = TAB_LATENCY if tab == TAB_LATENCY else TAB_RESULTS
    return next_state
