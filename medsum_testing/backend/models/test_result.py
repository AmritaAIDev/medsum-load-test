"""Test result dataclass and serialization helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

from medsum_testing.backend.services.accuracy_thresholds import (
    AccuracyThresholds,
    displayed_accuracy_band,
    get_accuracy_thresholds,
)
from medsum_testing.backend.services.batch_identity import canonical_batch_id
from medsum_testing.backend.services.soap_detail_table import count_classified_facts

PASSED_FINAL_RESULTS = frozenset({"pass", "complete_no_accuracy"})

# Execution Status is TestResult.status, except a finished run with no
# accuracy score shows NOT_SCORED instead of Completed.
_EXECUTION_DISPLAY = {
    "pending": ("Not evaluated", "not-evaluated"),
    "running": ("Not evaluated", "not-evaluated"),
    "complete": ("Completed", "completed"),
    "failed": ("Error", "error"),
    "skipped": ("Not evaluated", "not-evaluated"),
}

# SOAP Evaluation bands from final_result. fail is Low accuracy — never
# "Fail", which operators read as a broken run.
_EVALUATION_BANDS = {
    "pass": ("High accuracy", "high-accuracy"),
    "review": ("Needs review", "needs-review"),
    "fail": ("Low accuracy", "low-accuracy"),
    "complete_no_accuracy": ("NOT_SCORED", "not-scored"),
}

_SOAP_FACT_KEYS = ("Correct", "Incorrect", "Missing", "Hallucination")

RESULTS_TABLE_HEADERS = (
    "Test Case ID",
    "Audio File",
    "Language",
    "SOAP accuracy",
    "Clinical Quality",
    "Execution Status",
)

_CLINICAL_QUALITY = {
    "acceptable": ("Clinically Acceptable", "acceptable"),
    "minor": ("Minor Deviation", "minor"),
    "moderate": ("Moderate Deviation", "moderate"),
    "major": ("Major Deviation", "major"),
}


def is_passed_final_result(value: str | None) -> bool:
    """True for high-accuracy completions, including runs with no ground-truth scoring."""
    return (value or "") in PASSED_FINAL_RESULTS


def is_done_status(status: str | None) -> bool:
    """True when execution finished and produced an output (status=complete)."""
    return (status or "").strip().lower() == "complete"


def is_execution_error(status: str | None, final_result: str | None = None) -> bool:
    """True for a real execution failure — not a low SOAP accuracy band."""
    s = (status or "").strip().lower()
    v = (final_result or "").strip().lower()
    return s == "failed" or v == "failed"


def is_counted_failure(status: str | None, final_result: str | None) -> bool:
    """Dashboard / run-total execution errors. Accuracy bands are not failures."""
    return is_execution_error(status, final_result)


def display_execution_status(
    status: str | None,
    final_result: str | None = None,
    *,
    accuracy_skipped: bool = False,
) -> dict[str, str]:
    """Execution Status chip: Completed, Error, Not evaluated, or NOT_SCORED."""
    s = (status or "").strip().lower()
    v = (final_result or "").strip().lower()
    if s == "failed" or v == "failed":
        return {"label": "Error", "css": "error"}
    if s == "complete" and (accuracy_skipped or v == "complete_no_accuracy"):
        return {"label": "NOT_SCORED", "css": "not-scored"}
    if s in _EXECUTION_DISPLAY:
        label, css = _EXECUTION_DISPLAY[s]
        return {"label": label, "css": css}
    return {"label": "Not evaluated", "css": "not-evaluated"}


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _soap_pair(soap_comparison: Any) -> dict:
    if not isinstance(soap_comparison, dict):
        return {}
    gt = soap_comparison.get("gt_vs_generated")
    if isinstance(gt, dict):
        return gt
    if soap_comparison.get("metrics") or soap_comparison.get("facts"):
        return soap_comparison
    return {}


def soap_fact_counts(soap_comparison: Any) -> dict[str, int] | None:
    """Correct / Incorrect / Missing / Hallucination counts from Prompt 1 facts."""
    pair = _soap_pair(soap_comparison)
    facts = pair.get("facts") if pair else None
    if not (isinstance(facts, list) and facts) and isinstance(soap_comparison, dict):
        facts = soap_comparison.get("facts")
    if isinstance(facts, list) and facts:
        counted = count_classified_facts(facts)
        if counted is not None:
            return counted
    metrics = pair.get("metrics") if pair else None
    if not isinstance(metrics, dict) or not metrics:
        return None
    correct = int(metrics.get("correct_count") or 0)
    missing = int(metrics.get("missing_count") or 0)
    hallucination = int(metrics.get("hallucination_count") or 0)
    incorrect = metrics.get("incorrect_count")
    if incorrect is None:
        captured = metrics.get("captured_count")
        incorrect = max(0, int(captured) - correct) if captured is not None else 0
    return {
        "Correct": correct,
        "Incorrect": int(incorrect),
        "Missing": missing,
        "Hallucination": hallucination,
    }


def _soap_percent(soap_comparison: Any) -> float | None:
    pair = _soap_pair(soap_comparison)
    if pair:
        score = _as_float(
            pair.get("overall_weighted_clinical_score")
            or pair.get("similarity_score")
        )
        if score is not None:
            return score
        metrics = pair.get("metrics") if isinstance(pair.get("metrics"), dict) else {}
        score = _as_float(metrics.get("overall_weighted_clinical_score"))
        if score is not None:
            return score
    if isinstance(soap_comparison, dict):
        scores = soap_comparison.get("scores")
        if isinstance(scores, dict):
            score = _as_float(scores.get("gt_vs_generated"))
            if score is not None:
                return score
    return None


def _format_percent(value: float | None) -> str:
    if value is None:
        return ""
    return f"{int(round(value))}%"


def display_soap_accuracy(soap_comparison: Any = None) -> dict[str, Any]:
    """SOAP accuracy cell: 'N% accuracy' from SOAP score, else skip reason or —."""
    percent_value = _soap_percent(soap_comparison)
    if percent_value is None:
        reason = ""
        if isinstance(soap_comparison, dict):
            reason = str(soap_comparison.get("skip_reason") or "").strip()
        if reason:
            return {
                "label": reason,
                "percent": "",
                "percent_value": None,
                "show": True,
                "skipped": True,
            }
        return {"label": "—", "percent": "", "percent_value": None, "show": False}
    rounded = int(round(percent_value))
    label = f"{rounded}% accuracy"
    return {
        "label": label,
        "percent": f"{rounded}%",
        "percent_value": percent_value,
        "show": True,
        "skipped": False,
    }


def _format_facts(counts: dict[str, int] | None) -> str:
    if not counts:
        return ""
    return " · ".join(f"{key} {counts[key]}" for key in _SOAP_FACT_KEYS)


def display_soap_evaluation(
    status: str | None,
    final_result: str | None,
    *,
    accuracy_score: Any = None,
    soap_comparison: Any = None,
    accuracy_skipped: bool = False,
    thresholds: AccuracyThresholds | None = None,
) -> dict[str, Any]:
    """SOAP Evaluation chip: percentage, accuracy-band, fact classifications.

    Execution errors and scheduler skips never get an accuracy judgment.
    Pass / review bands re-apply test_settings.accuracy_pass_score.
    """
    s = (status or "").strip().lower()
    v = (final_result or "").strip().lower()
    empty = {
        "label": "—",
        "css": "no-eval",
        "percent": "",
        "facts": "",
        "text": "—",
        "show": False,
    }
    if s == "failed" or v == "failed":
        return empty
    if s == "skipped" or v == "skipped":
        return empty
    if s in {"pending", "running"} or v == "pending":
        return {
            "label": "—",
            "css": "muted",
            "percent": "",
            "facts": "",
            "text": "—",
            "show": True,
        }

    percent_value = None
    if not accuracy_skipped and v != "complete_no_accuracy":
        percent_value = _soap_percent(soap_comparison)
        if percent_value is None:
            percent_value = _as_float(accuracy_score)

    band_key = displayed_accuracy_band(
        status=status,
        final_result=final_result,
        score=percent_value,
        accuracy_skipped=accuracy_skipped,
        thresholds=thresholds,
    )
    if band_key is None:
        return empty
    if band_key == "pending":
        return {
            "label": "—",
            "css": "muted",
            "percent": "",
            "facts": "",
            "text": "—",
            "show": True,
        }

    band = _EVALUATION_BANDS.get(band_key)
    if band is None:
        band = ("NOT_SCORED", "not-scored")
    label, css = band

    if css == "not-scored" or accuracy_skipped:
        percent_value = None
    percent = _format_percent(percent_value)
    facts = _format_facts(soap_fact_counts(soap_comparison)) if css != "not-scored" else ""
    parts = [p for p in (percent, label) if p and p != "—"]
    text = " · ".join(parts) if parts else label
    if facts:
        text = f"{text} · {facts}"
    return {
        "label": label,
        "css": css,
        "percent": percent,
        "facts": facts,
        "text": text,
        "show": True,
        "percent_value": percent_value,
    }


def display_clinical_quality(
    status: str | None,
    final_result: str | None,
    *,
    accuracy_score: Any = None,
    soap_comparison: Any = None,
    accuracy_skipped: bool = False,
    evaluation: dict | None = None,
    thresholds: AccuracyThresholds | None = None,
) -> dict[str, Any]:
    """Clinical Quality chip: Clinically Acceptable / Minor / Moderate / Major Deviation."""
    empty = {"label": "—", "css": "empty", "show": False}
    t = thresholds or get_accuracy_thresholds()
    evaluation = evaluation or display_soap_evaluation(
        status,
        final_result,
        accuracy_score=accuracy_score,
        soap_comparison=soap_comparison,
        accuracy_skipped=accuracy_skipped,
        thresholds=t,
    )
    if not evaluation.get("show") or evaluation.get("css") in {"not-scored", "muted", "no-eval"}:
        return empty
    score = evaluation.get("percent_value")
    if score is None:
        return empty
    if score >= t.quality_acceptable_score:
        key = "acceptable"
    elif score >= t.quality_minor_score:
        key = "minor"
    elif score >= t.quality_moderate_score:
        key = "moderate"
    else:
        key = "major"
    label, css = _CLINICAL_QUALITY[key]
    return {"label": label, "css": css, "show": True}


def display_row_status(
    status: str | None,
    final_result: str | None,
    *,
    accuracy_score: Any = None,
    soap_comparison: Any = None,
    accuracy_skipped: bool = False,
    thresholds: AccuracyThresholds | None = None,
) -> dict[str, Any]:
    """Both chips for one row. Never collapses execution and evaluation."""
    t = thresholds or get_accuracy_thresholds()
    execution = display_execution_status(
        status, final_result, accuracy_skipped=accuracy_skipped
    )
    evaluation = display_soap_evaluation(
        status,
        final_result,
        accuracy_score=accuracy_score,
        soap_comparison=soap_comparison,
        accuracy_skipped=accuracy_skipped,
        thresholds=t,
    )
    return {
        "execution": execution,
        "evaluation": evaluation,
        "quality": display_clinical_quality(
            status,
            final_result,
            accuracy_score=accuracy_score,
            soap_comparison=soap_comparison,
            accuracy_skipped=accuracy_skipped,
            evaluation=evaluation,
            thresholds=t,
        ),
    }


def attach_row_display(
    row: dict | None,
    thresholds: AccuracyThresholds | None = None,
) -> dict:
    """Add server-computed chip payloads so the UI can render without remapping."""
    data = row or {}
    t = thresholds or get_accuracy_thresholds()
    display = display_row_status(
        data.get("status"),
        data.get("final_result"),
        accuracy_score=data.get("accuracy_score") or data.get("similarity_score"),
        soap_comparison=data.get("soap_comparison"),
        accuracy_skipped=bool(data.get("accuracy_skipped")),
        thresholds=t,
    )
    data["execution_display"] = display["execution"]
    data["soap_evaluation_display"] = display["evaluation"]
    data["clinical_quality_display"] = display["quality"]
    data["soap_accuracy_display"] = display_soap_accuracy(data.get("soap_comparison"))
    return data


def count_failures(rows: list[dict]) -> int:
    """Count execution errors from each row's status / final_result fields."""
    return sum(
        1
        for r in rows
        if is_counted_failure(r.get("status"), r.get("final_result"))
    )


def count_done(rows: list[dict]) -> int:
    """Count executions that finished (status=complete)."""
    return sum(1 for r in rows if is_done_status(r.get("status")))


@dataclass
class ComparisonResult:
    similarity_score: Optional[float] = None
    medical_differences: list[str] = field(default_factory=list)
    medical_difference_details: list[dict] = field(default_factory=list)
    general_differences: list[str] = field(default_factory=list)
    severity: str = "low"
    summary: str = ""
    error: str = ""
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> ComparisonResult:
        if not data:
            return cls()
        return cls(
            similarity_score=data.get("similarity_score"),
            medical_differences=data.get("medical_differences") or [],
            medical_difference_details=data.get("medical_difference_details") or [],
            general_differences=data.get("general_differences") or [],
            severity=data.get("severity") or "low",
            summary=data.get("summary") or "",
            error=data.get("error") or "",
            skipped=data.get("skipped", False),
            skip_reason=data.get("skip_reason") or "",
        )


@dataclass
class MedComparisonResult:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    similarity_score: Optional[float] = None
    medical_differences: list[str] = field(default_factory=list)
    general_differences: list[str] = field(default_factory=list)
    severity: str = "low"
    summary: str = ""
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> MedComparisonResult:
        if not data:
            return cls()
        return cls(
            added=data.get("added") or [],
            removed=data.get("removed") or [],
            changed=data.get("changed") or [],
            similarity_score=data.get("similarity_score"),
            medical_differences=data.get("medical_differences") or [],
            general_differences=data.get("general_differences") or [],
            severity=data.get("severity") or "low",
            summary=data.get("summary") or "",
            skipped=data.get("skipped", False),
            skip_reason=data.get("skip_reason") or "",
        )


@dataclass
class TestResult:
    test_id: str
    status: str = "running"
    language: str = ""
    audio_filename: str = ""
    ai_model: str = "gpt-4o-mini"
    timestamp: str = ""
    test_case_id: str = ""
    patient_id: str = ""
    doctor_id: str = ""
    doctor_name: str = ""
    phone: str = ""
    session_id: str = ""
    session_datetime: str = ""
    audio_duration_seconds: int = 0
    ground_truth_transcription: str = ""
    ground_truth: str = ""
    generated_transcription: str = ""
    transcription: str = ""
    generated_translation: str = ""
    previous_transcription: str = ""
    generated_summary: Any = None
    previous_summary: Any = None
    text_translation: str = ""
    medications_before: Any = None
    medications_after_normalization: Any = None
    medications_generated: Any = None
    transcription_comparison: Optional[ComparisonResult] = None
    comparison: Any = None
    summary_comparison: Optional[ComparisonResult] = None
    medication_comparison: Optional[MedComparisonResult] = None
    regression_comparison: Optional[ComparisonResult] = None
    accuracy_score: Optional[float] = None
    accuracy_skipped: bool = False
    accuracy_skip_reason: str = ""
    has_ground_truth: bool = True
    ground_truth_flag: str = ""
    retry_count: int = 0
    errors: list[str] = field(default_factory=list)
    progress_steps: list[dict[str, str]] = field(default_factory=list)
    final_result: str = "pending"
    job_id: str = ""
    batch_id: str = ""
    folder_label: str = ""
    run_ref: str = ""
    tc_ref: str = ""
    transcription_result: Any = None
    flask_error: str = ""
    medication_validation: Any = None
    translation: str = ""
    soap_ground_truth: Any = None
    soap_generated: Any = None
    soap_raw: Any = None
    has_soap_ground_truth: bool = False
    has_summary_ground_truth: bool = False
    has_json_ground_truth: bool = False
    has_transcript_ground_truth: bool = False
    soap_comparison: Any = None
    translation_ground_truth: str = ""
    has_translation_ground_truth: bool = False
    translation_comparison: Any = None
    saved_summary: Any = None
    total_test_time_seconds: float | None = None
    drive_download_time_seconds: float | None = None
    audio_upload_time_seconds: float | None = None
    ai_comparison_time_seconds: float | None = None
    audio_size_bytes: int = 0
    previous_test_id: str = ""
    previous_similarity_score: float | None = None
    regression_vs_previous: str = "na"
    ai_model_used: str = ""
    initiated_by: str = "manual"
    target_environment: str = ""
    medsum_version: str = ""
    git_commit: str = ""
    stt_model: str = ""
    translation_model: str = ""
    llm_model: str = ""
    summary_template_id: str = ""
    summary_template_name: str = ""
    django_audio_endpoint: str = ""
    flask_transcribe_endpoint: str = ""
    django_summary_endpoint: str = ""
    drive_audio_file_id: str = ""
    drive_audio_filename: str = ""
    drive_folder_id: str = ""
    drive_transcript_file_id: str = ""
    drive_soap_gt_file_id: str = ""
    drive_translation_gt_file_id: str = ""
    audio_source: str = ""
    ground_truth_source: str = ""
    uploaded_audio_filename: str = ""
    uploaded_ground_truth_filename: str = ""
    report_pdf_path: str = ""
    report_excel_path: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"
        if not self.test_case_id:
            self.test_case_id = f"{self.language}_{self.audio_filename}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["transcription_comparison"] = (
            self.transcription_comparison.to_dict()
            if self.transcription_comparison
            else None
        )
        data["summary_comparison"] = (
            self.summary_comparison.to_dict() if self.summary_comparison else None
        )
        data["medication_comparison"] = (
            self.medication_comparison.to_dict()
            if self.medication_comparison
            else None
        )
        data["regression_comparison"] = (
            self.regression_comparison.to_dict()
            if self.regression_comparison
            else None
        )
        if not data.get("ground_truth"):
            data["ground_truth"] = self.ground_truth_transcription
        if not data.get("transcription"):
            data["transcription"] = self.generated_transcription
        if not data.get("generated_translation"):
            data["generated_translation"] = self.translation or self.text_translation
        if not data.get("comparison") and data.get("transcription_comparison"):
            tc = data["transcription_comparison"]
            if tc and not tc.get("skipped"):
                data["comparison"] = {
                    "similarity_score": tc.get("similarity_score"),
                    "medical_differences": (
                        tc.get("medical_difference_details")
                        or tc.get("medical_differences")
                        or []
                    ),
                    "general_differences": tc.get("general_differences") or [],
                    "overall_severity": tc.get("severity") or tc.get("overall_severity") or "low",
                    "summary": tc.get("summary") or "",
                    "error": tc.get("error") or "",
                }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestResult:
        data = dict(data)
        human = canonical_batch_id(data.get("batch_id") or "", data.get("batch_ref") or "")
        if human:
            data["batch_id"] = human
        data.pop("batch_ref", None)
        tc = data.pop("transcription_comparison", None)
        sc = data.pop("summary_comparison", None)
        mc = data.pop("medication_comparison", None)
        rc = data.pop("regression_comparison", None)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        result = cls(**filtered)
        result.transcription_comparison = ComparisonResult.from_dict(tc) if tc else None
        result.summary_comparison = ComparisonResult.from_dict(sc) if sc else None
        result.medication_comparison = MedComparisonResult.from_dict(mc) if mc else None
        result.regression_comparison = ComparisonResult.from_dict(rc) if rc else None
        return result
