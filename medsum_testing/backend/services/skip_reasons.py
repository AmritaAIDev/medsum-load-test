"""Skip-reason copy owned by execute_test_run().

UI results/detail views must reuse these strings instead of inventing
generic placeholders that hide why scoring was skipped.
"""

from __future__ import annotations

from typing import Any

# Existing execute_test_run() messages — source of truth for user-facing copy.
MISSING_TRANSCRIPT = "No ground truth transcript found for this audio"
MISSING_SOAP_GT = "No SOAP ground truth found for this audio"
MISSING_TRANSLATION = "No translation ground truth found for this audio"
SOAP_PARSE_FAILED = "SOAP GT: not available or parse failed"
MISSING_GROUND_TRUTH = "No ground truth available"
DRIVE_AUTH_FAILED = "Drive authentication failed"
TRANSLATION_GT_UNAVAILABLE = "Translation GT: not available"


def drive_auth_message(exc: BaseException | str) -> str:
    """User-facing Drive auth failure — keeps the exception detail attached."""
    detail = str(exc).strip()
    if detail:
        return f"{DRIVE_AUTH_FAILED}: {detail}"
    return DRIVE_AUTH_FAILED


def collect_gt_skip_reasons(
    *,
    has_transcript_file: bool,
    transcript_text: str | None,
    has_soap_gt_file: bool,
    soap_ground_truth: dict | None,
    has_translation_gt_file: bool,
    translation_text: str | None,
    language_code: str = "",
    drive_auth_error: str | None = None,
) -> dict[str, str]:
    """Field skip reasons using execute_test_run() copy.

    ``accuracy`` is the overall result.accuracy_skip_reason when scoring
    cannot run. Per-field strings go on the comparison objects.
    """
    transcript = (transcript_text or "").strip()
    translation = (translation_text or "").strip()
    english = (language_code or "").lower() in {"en", "english"}
    out = {
        "transcription": "",
        "soap": "",
        "translation": "",
        "accuracy": "",
        "drive_auth": "",
    }
    if drive_auth_error:
        msg = drive_auth_message(drive_auth_error)
        out["drive_auth"] = msg
        out["accuracy"] = msg
        out["transcription"] = msg
        out["soap"] = msg
        out["translation"] = msg
        return out

    if not transcript:
        out["transcription"] = MISSING_TRANSCRIPT

    if has_soap_gt_file and not soap_ground_truth:
        out["soap"] = SOAP_PARSE_FAILED
    elif not soap_ground_truth:
        out["soap"] = MISSING_SOAP_GT

    if english and transcript:
        out["translation"] = ""
    elif has_translation_gt_file and not translation:
        out["translation"] = TRANSLATION_GT_UNAVAILABLE
    elif not translation:
        out["translation"] = MISSING_TRANSLATION

    if not transcript and not soap_ground_truth:
        if has_soap_gt_file:
            out["accuracy"] = SOAP_PARSE_FAILED
        elif not has_transcript_file:
            out["accuracy"] = MISSING_TRANSCRIPT
        else:
            out["accuracy"] = MISSING_GROUND_TRUTH
    return out


def skipped_comparison(reason: str) -> dict[str, Any]:
    """Comparison payload when a field cannot be scored."""
    return {
        "skipped": True,
        "skip_reason": reason or "",
        "similarity_score": None,
    }
