"""Test result dataclass and serialization helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional


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
    session_id: str = ""
    session_datetime: str = ""
    audio_duration_seconds: int = 0
    ground_truth_transcription: str = ""
    generated_transcription: str = ""
    previous_transcription: str = ""
    generated_summary: Any = None
    previous_summary: Any = None
    text_translation: str = ""
    medications_before: Any = None
    medications_after_normalization: Any = None
    medications_generated: Any = None
    transcription_comparison: Optional[ComparisonResult] = None
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
    has_soap_ground_truth: bool = False
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
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestResult:
        tc = data.pop("transcription_comparison", None)
        sc = data.pop("summary_comparison", None)
        mc = data.pop("medication_comparison", None)
        rc = data.pop("regression_comparison", None)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        result = cls(**filtered)
        result.transcription_comparison = ComparisonResult.from_dict(tc)
        result.summary_comparison = ComparisonResult.from_dict(sc)
        result.medication_comparison = MedComparisonResult.from_dict(mc)
        result.regression_comparison = ComparisonResult.from_dict(rc)
        return result
