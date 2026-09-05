"""Result persistence helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from medsum_testing.backend.models.test_result import TestResult, attach_row_display
from medsum_testing.backend.services.batch_identity import canonical_batch_id
from medsum_testing.backend.services.config_loader import get_results_dir


def save_result(result: TestResult) -> Path:
    path = get_results_dir() / f"{result.test_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    return path


def load_result(test_id: str) -> Optional[TestResult]:
    path = get_results_dir() / f"{test_id}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return TestResult.from_dict(json.load(f))


def _identity_fields(data: dict) -> dict:
    """Doctor / patient fields for list views — including nested transcribe payload."""
    tr = data.get("transcription_result")
    details = tr.get("doctor_details") if isinstance(tr, dict) else None
    demo = tr.get("patient_demographics") if isinstance(tr, dict) else None
    if not isinstance(details, dict):
        details = {}
    if not isinstance(demo, dict):
        demo = {}
    doctor_name = (
        data.get("doctor_name")
        or details.get("doctor_name")
        or ""
    )
    patient_id = (
        data.get("patient_id")
        or demo.get("abha_id")
        or demo.get("patient_id")
        or ""
    )
    return {
        "patient_id": str(patient_id or ""),
        "phone": data.get("phone") or "",
        "doctor_id": str(data.get("doctor_id") or ""),
        "doctor_name": str(doctor_name or "").strip(),
    }


def _comparison_snippet(comp: Any) -> dict | None:
    if not isinstance(comp, dict):
        return None
    details = comp.get("medical_difference_details") or []
    types: list[str] = []
    seen: set[str] = set()
    for item in details:
        if isinstance(item, dict):
            kind = str(item.get("type") or "").strip().replace("_", " ")
            if kind and kind not in seen:
                seen.add(kind)
                types.append(kind)
    return {
        "similarity_score": comp.get("similarity_score"),
        "summary": (comp.get("summary") or "")[:240],
        "skipped": comp.get("skipped", False),
        "skip_reason": comp.get("skip_reason") or "",
        "error": comp.get("error") or "",
        "severity": comp.get("severity") or comp.get("overall_severity") or "",
        "medical_difference_details": [
            {"type": t} for t in types
        ],
        "medical_differences": types,
    }


def _soap_snippet(soap: Any) -> dict | None:
    if not isinstance(soap, dict):
        return None
    scores = soap.get("scores") if isinstance(soap.get("scores"), dict) else {}
    gt_vs = soap.get("gt_vs_generated") if isinstance(soap.get("gt_vs_generated"), dict) else {}
    metrics = gt_vs.get("metrics") if isinstance(gt_vs.get("metrics"), dict) else {}
    if not metrics and isinstance(soap.get("metrics"), dict):
        metrics = soap.get("metrics") or {}
    snippet_metrics = {}
    for key in (
        "overall_weighted_clinical_score",
        "correct_count",
        "incorrect_count",
        "missing_count",
        "hallucination_count",
        "captured_count",
        "applicable_count",
        "critical_error_count",
    ):
        if metrics.get(key) is not None:
            snippet_metrics[key] = metrics[key]
    pair = {
        "similarity_score": scores.get("gt_vs_generated")
        or gt_vs.get("similarity_score")
        or gt_vs.get("overall_weighted_clinical_score"),
        "overall_weighted_clinical_score": gt_vs.get("overall_weighted_clinical_score")
        or scores.get("gt_vs_generated"),
        "summary": (gt_vs.get("summary") or "")[:240],
        "overall_severity": gt_vs.get("overall_severity") or soap.get("overall_severity") or "",
    }
    if snippet_metrics:
        pair["metrics"] = snippet_metrics
    return {
        "scores": {
            "gt_vs_generated": pair["similarity_score"],
        },
        "gt_vs_generated": pair,
        "skipped": bool(soap.get("skipped")),
        "skip_reason": soap.get("skip_reason") or "",
    }


def _timing_snippet(data: dict) -> dict:
    tr = data.get("transcription_result")
    if not isinstance(tr, dict):
        return {}
    return {
        "audio_length": tr.get("audio_length"),
        "translation-time": tr.get("translation-time"),
        "transcription-time": tr.get("transcription-time"),
        "llm-time": tr.get("llm-time"),
        "total-time": tr.get("total-time"),
    }


def list_results() -> list[dict]:
    return [summary for _, summary in _iter_result_files()]


def load_all_results_raw() -> list[dict]:
    """Full result dicts for dashboard and batch views."""
    return [data for data, _ in _iter_result_files(full=True)]


def list_results_by_batch(batch_id: str) -> list[dict]:
    return [r for r in load_all_results_raw() if r.get("batch_id") == batch_id]


def _iter_result_files(full: bool = False):
    results_dir = get_results_dir()
    for path in sorted(results_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if full:
            yield attach_row_display(data), path
        else:
            yield path, attach_row_display({
                "id": data.get("test_id", path.stem),
                "test_id": data.get("test_id", path.stem),
                "tc_ref": data.get("tc_ref", ""),
                "run_ref": data.get("run_ref", ""),
                "filename": data.get("audio_filename", ""),
                "audio_filename": data.get("audio_filename", ""),
                "language": data.get("language", ""),
                "timestamp": data.get("timestamp", ""),
                "final_result": data.get("final_result", ""),
                "accuracy_score": data.get("accuracy_score"),
                "similarity_score": (
                    (data.get("comparison") or {}).get("similarity_score")
                    or data.get("accuracy_score")
                    or (data.get("transcription_comparison") or {}).get("similarity_score")
                ),
                "status": data.get("status", ""),
                "batch_id": canonical_batch_id(
                    data.get("batch_id") or "",
                    data.get("batch_ref") or "",
                ),
                "created_at": data.get("timestamp", ""),
                "completed_at": data.get("timestamp", ""),
                **_identity_fields(data),
                "total_test_time_seconds": data.get("total_test_time_seconds"),
                "audio_duration_seconds": data.get("audio_duration_seconds") or 0,
                "transcription_result": _timing_snippet(data),
                "comparison": _comparison_snippet(
                    data.get("comparison") or data.get("transcription_comparison")
                ) or data.get("comparison") or data.get("transcription_comparison"),
                "transcription_comparison": _comparison_snippet(
                    data.get("transcription_comparison") or data.get("comparison")
                ),
                "translation_comparison": _comparison_snippet(
                    data.get("translation_comparison")
                ),
                "soap_comparison": _soap_snippet(data.get("soap_comparison")),
                "has_ground_truth": data.get("has_ground_truth"),
                "has_translation_ground_truth": data.get("has_translation_ground_truth"),
                "has_soap_ground_truth": data.get("has_soap_ground_truth"),
                "ground_truth_transcription": data.get("ground_truth_transcription", ""),
                "accuracy_skipped": data.get("accuracy_skipped", False),
                "accuracy_skip_reason": data.get("accuracy_skip_reason", ""),
                "ai_model": data.get("ai_model", ""),
                "ai_model_used": data.get("ai_model_used") or data.get("ai_model", ""),
                "llm_model": data.get("llm_model", ""),
                "stt_model": data.get("stt_model") or data.get("asr_model") or "",
            })


def find_previous_result(
    language: str, audio_filename: str, exclude_id: str
) -> Optional[TestResult]:
    """Most recent completed result for the same language and audio file."""
    results_dir = get_results_dir()
    matches: list[tuple[float, TestResult]] = []
    for path in results_dir.glob("*.json"):
        if path.stem == exclude_id:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if (
                data.get("audio_filename") == audio_filename
                and data.get("language") == language
                and data.get("status") == "complete"
            ):
                matches.append((path.stat().st_mtime, TestResult.from_dict(data)))
        except (json.JSONDecodeError, OSError):
            continue
    if not matches:
        return None
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def has_recent_result(audio_filename: str, within_seconds: int = 60) -> bool:
    """True if a result for this audio was saved within the last N seconds."""
    results_dir = get_results_dir()
    now = datetime.now(timezone.utc)
    for path in results_dir.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("audio_filename") != audio_filename:
                continue
            ts = data.get("timestamp", "")
            if not ts:
                continue
            saved = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if saved.tzinfo is None:
                saved = saved.replace(tzinfo=timezone.utc)
            age = (now - saved).total_seconds()
            if 0 <= age <= within_seconds:
                return True
        except (json.JSONDecodeError, OSError, ValueError):
            continue
    return False
