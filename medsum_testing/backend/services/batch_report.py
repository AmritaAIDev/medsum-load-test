"""Batch / Total Report: overall results, accuracy, latency, per-case details.

No second format — this is the batch report. Accuracy and latency figures
reuse Prompt 5 / Prompt 6 / Prompt 7 calculators; this module only reads them.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from medsum_testing.backend.models.test_result import (
    display_row_status,
    is_counted_failure,
)
from medsum_testing.backend.services.accuracy_thresholds import AccuracyThresholds
from medsum_testing.backend.services.individual_report import as_result_dict
from medsum_testing.backend.services.latency_analysis import (
    LATENCY_ANALYSIS_HEADERS,
    UNAVAILABLE,
    latency_analysis_row,
    pick_transcribe_time,
)
from medsum_testing.backend.services.run_summary import (
    compute_run_summary,
    format_accuracy,
    format_latency,
    transcription_score,
)

BATCH_REPORT_SECTIONS = (
    "Overall Test Results",
    "Accuracy Metrics",
    "Latency Metrics",
    "Test Case Details",
)


def _status_counts(
    rows: list[dict],
    thresholds: AccuracyThresholds | None = None,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        shown = display_row_status(
            row.get("status"),
            row.get("final_result"),
            accuracy_score=row.get("accuracy_score"),
            soap_comparison=row.get("soap_comparison"),
            accuracy_skipped=bool(row.get("accuracy_skipped")),
            thresholds=thresholds,
        )
        counts[shown["execution"]["label"] or "—"] += 1
    return dict(counts)


def _evaluation_counts(
    rows: list[dict],
    thresholds: AccuracyThresholds | None = None,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        shown = display_row_status(
            row.get("status"),
            row.get("final_result"),
            accuracy_score=row.get("accuracy_score"),
            soap_comparison=row.get("soap_comparison"),
            accuracy_skipped=bool(row.get("accuracy_skipped")),
            thresholds=thresholds,
        )
        eval_chip = shown["evaluation"]
        if not eval_chip.get("show"):
            counts["—"] += 1
            continue
        counts[eval_chip.get("label") or "—"] += 1
    return dict(counts)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def case_detail(
    row: dict,
    thresholds: AccuracyThresholds | None = None,
) -> dict[str, str]:
    data = as_result_dict(row)
    tid = str(data.get("test_id") or "")
    lat = latency_analysis_row(data)
    score = transcription_score(data)
    status = display_row_status(
        data.get("status"),
        data.get("final_result"),
        accuracy_score=data.get("accuracy_score") or score,
        soap_comparison=data.get("soap_comparison"),
        accuracy_skipped=bool(data.get("accuracy_skipped")),
        thresholds=thresholds,
    )
    execution = status["execution"]
    evaluation = status["evaluation"]
    return {
        "test_case_id": str(
            data.get("tc_ref") or data.get("test_case_id") or tid or UNAVAILABLE
        ),
        "test_id": tid or UNAVAILABLE,
        "audio_file": str(data.get("audio_filename") or data.get("filename") or UNAVAILABLE),
        "execution_status": execution.get("label") or UNAVAILABLE,
        "soap_evaluation": evaluation.get("text") or UNAVAILABLE,
        "accuracy": format_accuracy(round(score, 1) if score is not None else None),
        "latency": lat["Total Time"],
        "individual_report": (
            f"/api/medsum-test/report/{tid}?format=pdf" if tid else UNAVAILABLE
        ),
        "transcription_time": lat["Transcription"],
        "translation_time": lat["Translation"],
        "soap_time": lat["SOAP"],
        "audio_length": lat["Audio Length"],
    }


def build_batch_report(
    rows: list[Any] | None,
    thresholds: AccuracyThresholds | None = None,
) -> dict:
    items = [as_result_dict(r) for r in (rows or [])]
    summary = compute_run_summary(items)
    details = [case_detail(r, thresholds=thresholds) for r in items]

    stage_avgs = {}
    for column, key in (
        ("Transcription", "transcription"),
        ("Translation", "translation"),
        ("SOAP", "soap"),
        ("Total Time", "total_time"),
    ):
        nums = []
        for row in items:
            raw = pick_transcribe_time(row, key)
            if raw is None or raw == "":
                continue
            try:
                nums.append(float(raw))
            except (TypeError, ValueError):
                continue
        stage_avgs[column] = format_latency(_mean(nums)) if nums else UNAVAILABLE

    return {
        "title": "MEDSUM Batch Report",
        "sections": list(BATCH_REPORT_SECTIONS),
        "Overall Test Results": {
            "total_test_cases": summary["total_test_cases"],
            "done_tests": summary["done_tests"],
            "passed_tests": summary["passed_tests"],
            "failed_tests": sum(
                1
                for r in items
                if is_counted_failure(r.get("status"), r.get("final_result"))
            ),
            "execution_counts": _status_counts(items, thresholds=thresholds),
            "evaluation_counts": _evaluation_counts(items, thresholds=thresholds),
            "status_counts": _status_counts(items, thresholds=thresholds),
            "selected_model": summary.get("selected_model") or UNAVAILABLE,
        },
        "Accuracy Metrics": {
            "average_accuracy": format_accuracy(summary["average_accuracy"]),
            "scored_count": summary.get("scored_count") or 0,
            "per_case": [
                {
                    "test_case_id": c["test_case_id"],
                    "accuracy": c["accuracy"],
                    "execution_status": c["execution_status"],
                    "soap_evaluation": c["soap_evaluation"],
                }
                for c in details
            ],
        },
        "Latency Metrics": {
            "average_latency": format_latency(summary["average_latency"]),
            "stage_averages": stage_avgs,
            "columns": list(LATENCY_ANALYSIS_HEADERS),
            "per_case": [
                {
                    "audio_file": c["audio_file"],
                    "audio_length": c["audio_length"],
                    "transcription": c["transcription_time"],
                    "translation": c["translation_time"],
                    "soap": c["soap_time"],
                    "total_time": c["latency"],
                }
                for c in details
            ],
        },
        "Test Case Details": details,
    }
