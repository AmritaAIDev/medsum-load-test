"""Individual Run Report fields (one audio / one result row).

Shape matches the LLD list: Test Case Number, Batch ID, Run Number,
date/time, input, endpoint, model config, Ground Truth availability,
expected vs actual, accuracy scores, processing time, final status.
"""

from __future__ import annotations

from typing import Any

from medsum_testing.backend.models.test_result import display_row_status
from medsum_testing.backend.services.accuracy_thresholds import AccuracyThresholds
from medsum_testing.backend.services.batch_identity import display_batch_label
from medsum_testing.backend.services.latency_analysis import (
    LATENCY_ANALYSIS_HEADERS,
    UNAVAILABLE,
    latency_analysis_row,
)
from medsum_testing.backend.services.run_summary import transcription_score

INDIVIDUAL_REPORT_REQUIRED_LABELS = (
    "Test Case Number",
    "Batch ID",
    "Run Number",
    "Date/Time",
    "Input",
    "Endpoint",
    "Model Config",
    "Ground Truth Availability",
    "Expected vs Actual Output",
    "Accuracy Scores",
    "Processing Time",
    "Execution Status",
    "SOAP Evaluation",
)


def as_result_dict(result: Any) -> dict:
    if result is None:
        return {}
    if hasattr(result, "to_dict"):
        return result.to_dict()
    return dict(result)


def _yn(flag) -> str:
    return "available" if flag else "unavailable"


def _clip(text: Any, limit: int = 2000) -> str:
    raw = "" if text is None else str(text)
    if isinstance(text, (dict, list)):
        import json
        raw = json.dumps(text, ensure_ascii=False)
    raw = raw.strip() or UNAVAILABLE
    if len(raw) > limit:
        return raw[: limit - 1] + "…"
    return raw


def _score_line(name: str, value) -> str:
    if value is None or value == "":
        return f"{name}: {UNAVAILABLE}"
    try:
        return f"{name}: {float(value):.1f}%"
    except (TypeError, ValueError):
        return f"{name}: {value}"


def individual_report_fields(
    result: Any,
    thresholds: AccuracyThresholds | None = None,
) -> list[tuple[str, str]]:
    data = as_result_dict(result)
    tr = data.get("transcription_result") if isinstance(data.get("transcription_result"), dict) else {}
    lat = latency_analysis_row(data)
    overall = transcription_score(data)
    shown = display_row_status(
        data.get("status"),
        data.get("final_result"),
        accuracy_score=data.get("accuracy_score") or overall,
        soap_comparison=data.get("soap_comparison"),
        accuracy_skipped=bool(data.get("accuracy_skipped")),
        thresholds=thresholds,
    )
    execution = shown["execution"]
    evaluation = shown["evaluation"]
    trans_comp = data.get("transcription_comparison") or data.get("comparison") or {}
    transl_comp = data.get("translation_comparison") or {}
    soap_comp = data.get("soap_comparison") or {}
    soap_score = None
    if isinstance(soap_comp, dict):
        soap_score = (soap_comp.get("scores") or {}).get("gt_vs_generated")
        if soap_score is None:
            soap_score = (soap_comp.get("gt_vs_generated") or {}).get("similarity_score")

    endpoints = [
        data.get("flask_transcribe_endpoint"),
        data.get("django_audio_endpoint"),
        data.get("django_summary_endpoint"),
    ]
    endpoint = ", ".join(str(e) for e in endpoints if e) or UNAVAILABLE

    models = [
        f"comparison={data.get('ai_model_used') or data.get('ai_model') or UNAVAILABLE}",
        f"STT={data.get('stt_model') or UNAVAILABLE}",
        f"translation={data.get('translation_model') or UNAVAILABLE}",
        f"LLM={data.get('llm_model') or UNAVAILABLE}",
    ]

    gt_lines = [
        f"transcription={_yn(data.get('has_ground_truth'))}",
        f"translation={_yn(data.get('has_translation_ground_truth'))}",
        f"SOAP={_yn(data.get('has_soap_ground_truth'))}",
    ]

    expected_actual = [
        "Transcription expected: " + _clip(data.get("ground_truth") or data.get("ground_truth_transcription")),
        "Transcription actual: " + _clip(data.get("transcription") or data.get("generated_transcription")),
        "Translation expected: " + _clip(data.get("translation_ground_truth")),
        "Translation actual: " + _clip(
            data.get("generated_translation") or data.get("translation") or data.get("text_translation")
        ),
        "SOAP expected: " + _clip(data.get("soap_ground_truth")),
        "SOAP actual: " + _clip(data.get("soap_generated") or data.get("generated_summary")),
    ]

    accuracy_lines = [
        _score_line("Overall (transcription)", overall),
        _score_line(
            "Transcription",
            trans_comp.get("similarity_score") if isinstance(trans_comp, dict) else None,
        ),
        _score_line(
            "Translation",
            transl_comp.get("similarity_score") if isinstance(transl_comp, dict) else None,
        ),
        _score_line("SOAP", soap_score),
    ]

    timing_lines = [f"{h}: {lat[h]}" for h in LATENCY_ANALYSIS_HEADERS if h != "Audio File"]

    audio = data.get("audio_filename") or data.get("filename") or UNAVAILABLE
    language = data.get("language") or UNAVAILABLE
    patient = data.get("patient_id") or UNAVAILABLE
    doctor = data.get("doctor_name") or data.get("doctor_id") or UNAVAILABLE

    return [
        ("Test Case Number", str(data.get("tc_ref") or data.get("test_case_id") or data.get("test_id") or UNAVAILABLE)),
        ("Batch ID", display_batch_label(data.get("batch_id") or "", data.get("batch_ref") or "")),
        ("Run Number", str(data.get("run_ref") or UNAVAILABLE)),
        ("Date/Time", str(data.get("timestamp") or data.get("session_datetime") or UNAVAILABLE)),
        (
            "Input",
            f"audio={audio}; language={language}; patient={patient}; doctor={doctor}",
        ),
        ("Endpoint", endpoint),
        ("Model Config", "; ".join(models)),
        ("Ground Truth Availability", "; ".join(gt_lines)),
        ("Expected vs Actual Output", "\n".join(expected_actual)),
        ("Accuracy Scores", "\n".join(accuracy_lines)),
        ("Processing Time", "\n".join(timing_lines)),
        ("Execution Status", execution.get("label") or UNAVAILABLE),
        ("SOAP Evaluation", evaluation.get("text") or UNAVAILABLE),
    ]


def extra_report_fields(result: Any) -> list[tuple[str, str]]:
    """Additional detail already produced by the existing individual report."""
    data = as_result_dict(result)
    return [
        ("Test ID", str(data.get("test_id") or UNAVAILABLE)),
        ("Audio File", str(data.get("audio_filename") or data.get("filename") or UNAVAILABLE)),
        ("Language", str(data.get("language") or UNAVAILABLE)),
        ("Errors", "; ".join(data.get("errors") or []) or "None"),
    ]
