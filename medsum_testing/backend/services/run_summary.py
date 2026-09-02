"""Headline run metrics for Dashboard / Test Run Summary.

Total test cases is the current results-table set (the same rows the table
shows). Prompt 3 exclusions never become result rows — execution uses
filter_cases_for_run, and an empty selection runs nothing — so excluded
files are not in this count. Historical dashboard filters (all / batch /
today) count whatever was actually run in that view, not the live catalog.

Done tests count executions that finished (status=complete). That is
independent of SOAP Evaluation: a Done row may be High accuracy, Needs
review, Low accuracy, or NOT_SCORED. is_passed_final_result still names
the high-accuracy / not-scored band; it is not the Done KPI.

Average accuracy is the mean of scored transcription similarities
(Prompt 5). NOT_SCORED / missing scores are omitted, not 0%.

Average latency is the mean of Flask total-time from Latency Analysis
(Prompt 6). Missing timings are omitted, not a fabricated 0. The headline
does not fall back to wall-clock total_test_time_seconds so it cannot
drift from the Latency Analysis Total Time column.

Selected model is ai_model_used / ai_model / llm_model on the rows, or the
UI comparison-model fallback when rows have none.
"""

from __future__ import annotations

from typing import Any

from medsum_testing.backend.models.test_result import (
    count_done,
    count_failures,
    is_passed_final_result,
)
from medsum_testing.backend.services.latency_analysis import (
    UNAVAILABLE,
    pick_transcribe_time,
)

HEADLINE_METRIC_KEYS = (
    "total_test_cases",
    "done_tests",
    "average_accuracy",
    "average_latency",
    "selected_model",
)


def transcription_score(row: dict | None):
    """Transcription LLM similarity, or None when the case was not scored."""
    data = row or {}
    verdict = (data.get("final_result") or "").strip().lower()
    if data.get("accuracy_skipped") or verdict == "complete_no_accuracy":
        return None
    comp = data.get("comparison") or data.get("transcription_comparison") or {}
    score = comp.get("similarity_score") if isinstance(comp, dict) else None
    if score is None:
        score = data.get("accuracy_score")
    if score is None:
        score = data.get("similarity_score")
    if score is None or score == "":
        return None
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


def row_model(row: dict | None) -> str:
    data = row or {}
    return str(
        data.get("ai_model_used")
        or data.get("ai_model")
        or data.get("llm_model")
        or ""
    ).strip()


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def compute_run_summary(
    rows: list[dict] | None,
    *,
    selected_model: str | None = None,
) -> dict[str, Any]:
    items = list(rows or [])
    scores = [s for row in items if (s := transcription_score(row)) is not None]
    times: list[float] = []
    for row in items:
        raw = pick_transcribe_time(row, "total_time")
        if raw is None or raw == "":
            continue
        try:
            times.append(float(raw))
        except (TypeError, ValueError):
            continue

    models = _unique_nonempty([row_model(r) for r in items])
    fallback = (selected_model or "").strip()
    if fallback and fallback not in models:
        if not models:
            models = [fallback]

    doctors = _unique_nonempty(
        [r.get("doctor_name") or r.get("phone") or "" for r in items]
    )
    patients = _unique_nonempty(
        [str(r.get("patient_id") or r.get("patientId") or "") for r in items]
    )

    return {
        "total_test_cases": len(items),
        "done_tests": count_done(items),
        "passed_tests": sum(
            1 for r in items if is_passed_final_result(r.get("final_result"))
        ),
        "execution_errors": count_failures(items),
        "average_accuracy": (
            round(sum(scores) / len(scores), 1) if scores else None
        ),
        "average_latency": (
            round(sum(times) / len(times), 2) if times else None
        ),
        "selected_model": ", ".join(models) if models else "",
        "doctor_names": doctors,
        "patient_ids": patients,
        "scored_count": len(scores),
        "latency_count": len(times),
    }


def format_accuracy(value) -> str:
    if value is None:
        return UNAVAILABLE
    return f"{value}%"


def format_latency(value) -> str:
    if value is None:
        return UNAVAILABLE
    return f"{value:.2f}s"


def format_model(value: str | None) -> str:
    text = (value or "").strip()
    return text or UNAVAILABLE


def format_meta(summary: dict | None) -> str:
    data = summary or {}
    doctors = data.get("doctor_names") or []
    patients = data.get("patient_ids") or []
    parts: list[str] = []
    if doctors:
        parts.append("Doctor: " + ", ".join(doctors))
    if patients:
        parts.append("Patient: " + ", ".join(patients))
    return " · ".join(parts)


def summary_display(rows: list[dict] | None, *, selected_model: str | None = None) -> dict[str, str]:
    raw = compute_run_summary(rows, selected_model=selected_model)
    return {
        "total_test_cases": str(raw["total_test_cases"]),
        "done_tests": str(raw["done_tests"]),
        "passed_tests": str(raw["passed_tests"]),
        "average_accuracy": format_accuracy(raw["average_accuracy"]),
        "average_latency": format_latency(raw["average_latency"]),
        "selected_model": format_model(raw["selected_model"]),
        "meta": format_meta(raw),
    }
