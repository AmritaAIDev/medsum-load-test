"""POST /api/medsum-test/run and GET /api/medsum-test/drive-files"""

from __future__ import annotations

import logging
import threading
import traceback
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from medsum_testing.backend.models.test_result import TestResult
from medsum_testing.backend.services import ai_comparator, audio_utils, drive_service, medsum_api
from medsum_testing.backend.services.config_loader import get_config
from medsum_testing.backend.services.result_store import find_previous_result, save_result

bp = Blueprint("medsum_test_runner", __name__)
log = logging.getLogger("medsum_test_runner")


def _update_step(result: TestResult, step: str, status: str) -> None:
    for item in result.progress_steps:
        if item["step"] == step:
            item["status"] = status
            return
    result.progress_steps.append({"step": step, "status": status})


def execute_test_run(
    language: str,
    audio_filename: str,
    ai_model: str,
    test_id: str | None = None,
) -> TestResult:
    """
    Core test execution — used by HTTP route and scheduler.
    Returns the full TestResult (status complete or failed).
    """
    test_id = test_id or str(uuid.uuid4())
    log.info("[%s] execute_test_run() entered", test_id)

    result = TestResult(
        test_id=test_id,
        status="running",
        language=language,
        audio_filename=audio_filename,
        ai_model=ai_model,
    )
    result.progress_steps = [
        {"step": "Fetching audio from Drive", "status": "active"},
        {"step": "Uploading audio to Django", "status": "pending"},
        {"step": "Transcribing via Flask", "status": "pending"},
        {"step": "Running AI comparison", "status": "pending"},
    ]
    save_result(result)

    try:
        config = get_config()
        log.info("[%s] Config loaded", test_id)

        log.info("[%s] STEP 1: Looking up test case...", test_id)
        cases = drive_service.list_test_cases(config)
        case = next(
            (
                c
                for c in cases
                if c.get("status") == "ready"
                and c["language"] == language
                and c["audio_filename"] == audio_filename
            ),
            None,
        )
        if not case:
            case = next(
                (
                    c
                    for c in cases
                    if c.get("status") == "ready"
                    and c["audio_filename"] == audio_filename
                ),
                None,
            )
            if case:
                log.warning(
                    "[%s] Matched by audio filename only (requested=%s, found=%s)",
                    test_id,
                    language,
                    case["language"],
                )

        if not case:
            available = [
                (c["language"], c["audio_filename"])
                for c in cases
                if c.get("status") == "ready"
            ]
            raise ValueError(
                f"No test case found for language='{language}', "
                f"audio_filename='{audio_filename}'. Available: {available}"
            )

        log.info("[%s] Test case found: %s", test_id, case.get("folder_label", case["language"]))
        result.ground_truth_flag = case.get("ground_truth_flag", "")
        result.has_ground_truth = case.get("has_transcript", False)

        log.info("[%s] STEP 2: Downloading audio...", test_id)
        audio_bytes = drive_service.download_audio(case["audio_file_id"])
        log.info("[%s] Audio: %d bytes", test_id, len(audio_bytes))

        ground_truth = ""
        if case.get("transcript_file_id"):
            log.info("[%s] STEP 3: Downloading ground truth...", test_id)
            ground_truth = drive_service.download_transcript(
                case["transcript_file_id"],
                mime_type=case.get("transcript_mime_type"),
            )
            log.info("[%s] Ground truth: %d chars", test_id, len(ground_truth))
        elif not case.get("has_transcript"):
            log.info("[%s] STEP 3: No ground truth — accuracy scoring will be skipped", test_id)
            result.accuracy_skipped = True
            result.accuracy_skip_reason = "No ground truth transcript found for this audio"

        result.ground_truth_transcription = ground_truth

        duration_seconds = 0
        try:
            duration_seconds = audio_utils.get_duration_seconds(audio_bytes, audio_filename)
            log.info("[%s] STEP 4: Duration=%ss", test_id, duration_seconds)
        except Exception as dur_exc:
            log.warning("[%s] Duration detection failed: %s", test_id, dur_exc)

        result.audio_duration_seconds = duration_seconds
        _update_step(result, "Fetching audio from Drive", "done")
        _update_step(result, "Uploading audio to Django", "active")
        save_result(result)

        log.info("[%s] STEP 5: Authenticating...", test_id)
        token, doctor_id = medsum_api.authenticate_doctor(config)
        log.info("[%s] Auth OK doctor_id=%s", test_id, doctor_id)
        result.doctor_id = doctor_id

        patient_id = str(config["patient"]["id"])
        log.info("[%s] STEP 6: Verifying patient %s...", test_id, patient_id)
        patient_data = medsum_api.verify_patient(patient_id, token, config)
        resolved_patient_id = str(patient_data.get("patient_id") or patient_id)
        result.patient_id = resolved_patient_id
        log.info("[%s] Patient OK: %s", test_id, patient_data.get("patient_name"))

        log.info("[%s] STEP 7: Uploading audio to Django...", test_id)
        session_id, audio_id = medsum_api.upload_audio(
            audio_bytes=audio_bytes,
            audio_filename=audio_filename,
            patient_id=resolved_patient_id,
            language=language,
            token=token,
            config=config,
            user_id=doctor_id,
            file_duration=str(duration_seconds) if duration_seconds else None,
        )
        result.session_id = session_id
        result.job_id = audio_id
        result.session_datetime = datetime.now(timezone.utc).isoformat()
        log.info("[%s] Upload OK session_id=%s audio_id=%s", test_id, session_id, audio_id)

        _update_step(result, "Uploading audio to Django", "done")
        _update_step(result, "Transcribing via Flask", "active")
        save_result(result)

        log.info("[%s] STEP 8: Transcribing via Flask (may take 1-5 min)...", test_id)
        doctor_data = medsum_api.fetch_doctor_profile(doctor_id, token, config)
        transcription_result = medsum_api.transcribe_audio(
            audio_bytes=audio_bytes,
            patient_data=patient_data,
            doctor_data=doctor_data,
            language=language,
            config=config,
        )
        transcription = transcription_result.get("transcription", "")
        result.generated_transcription = transcription
        result.text_translation = transcription_result.get("translation") or ""
        log.info("[%s] Transcription OK: %d chars", test_id, len(transcription))

        log.info("[%s] STEP 9: Saving summary to Django...", test_id)
        saved_summary = medsum_api.save_summary(
            session_id=session_id,
            audio_id=audio_id,
            patient_id=resolved_patient_id,
            user_id=doctor_id,
            transcription_result=transcription_result,
            token=token,
            config=config,
        )
        log.info("[%s] Summary saved: summary_id=%s", test_id, saved_summary.get("summary_id"))

        fetched_summary = medsum_api.fetch_summary(session_id, token, config)
        result.generated_summary = (
            fetched_summary.get("summary")
            or saved_summary.get("summary")
            or transcription_result.get("summary")
            or transcription_result.get("medical_summary")
        )

        log.info("[%s] STEP 10: Fetching audio/medication data...", test_id)
        audio_data = medsum_api.fetch_audio_data(session_id, token, config)
        if audio_data:
            log.info("[%s] Audio data keys: %s", test_id, list(audio_data.keys()))
            result.medications_generated = audio_data.get("medications") or audio_data.get("medicines")

        _update_step(result, "Transcribing via Flask", "done")
        _update_step(result, "Running AI comparison", "active")
        save_result(result)

        log.info("[%s] STEP 11: Checking for previous result...", test_id)
        previous = find_previous_result(language, audio_filename, test_id)
        if previous:
            log.info("[%s] Previous result found: %s", test_id, previous.test_id)
            result.previous_transcription = previous.generated_transcription
            result.previous_summary = previous.generated_summary
        else:
            log.info("[%s] Previous result: none", test_id)

        log.info("[%s] STEP 12: AI comparison with %s...", test_id, ai_model)
        if result.has_ground_truth and ground_truth.strip():
            result.transcription_comparison = ai_comparator.compare_transcriptions(
                ground_truth, transcription, ai_model
            )
            result.accuracy_score = result.transcription_comparison.similarity_score
            log.info("[%s] Comparison score: %s", test_id, result.accuracy_score)
        else:
            log.info("[%s] Skipping comparison — no ground truth", test_id)
            result.transcription_comparison = ai_comparator.compare_transcriptions(
                "", transcription, ai_model
            )
            if not result.accuracy_skip_reason:
                result.accuracy_skipped = True
                result.accuracy_skip_reason = "No ground truth available"

        if previous and previous.generated_summary is not None:
            result.summary_comparison = ai_comparator.compare_summaries(
                previous.generated_summary, result.generated_summary, ai_model
            )
            result.regression_comparison = ai_comparator.compare_summaries(
                previous.generated_summary, result.generated_summary, ai_model
            )

        result.medication_comparison = ai_comparator.compare_medications(
            result.medications_before,
            result.medications_after_normalization,
            result.medications_generated,
            ai_model,
        )

        _update_step(result, "Running AI comparison", "done")

        if result.accuracy_skipped:
            result.final_result = "complete_no_accuracy"
        elif result.transcription_comparison and result.transcription_comparison.severity in (
            "high",
            "critical",
        ):
            result.final_result = "fail"
        elif result.transcription_comparison and (result.accuracy_score or 0) >= 80:
            result.final_result = "pass"
        else:
            result.final_result = "review"

        result.status = "complete"
        log.info("[%s] Test run COMPLETE — final_result=%s", test_id, result.final_result)

    except Exception as exc:
        tb = traceback.format_exc()
        result.status = "failed"
        result.final_result = "failed"
        result.errors.append(str(exc))
        result.errors.append(tb)
        for step in result.progress_steps:
            if step["status"] == "active":
                step["status"] = "failed"
        log.error("[%s] Test run FAILED: %s", test_id, exc)
        log.error("[%s] Traceback:\n%s", test_id, tb)
        save_result(result)
        return result

    save_result(result)
    return result


def _run_and_store(test_id: str, language: str, audio_filename: str, ai_model: str) -> None:
    log.info("[%s] Background thread started", test_id)
    try:
        log.info("[%s] Calling execute_test_run...", test_id)
        result = execute_test_run(language, audio_filename, ai_model, test_id=test_id)
        log.info("[%s] Background thread finished — status=%s", test_id, result.status)
    except Exception as exc:
        tb = traceback.format_exc()
        log.error("[%s] Unhandled exception in background thread: %s", test_id, exc)
        log.error("[%s] Traceback:\n%s", test_id, tb)
        try:
            result = TestResult(
                test_id=test_id,
                status="failed",
                language=language,
                audio_filename=audio_filename,
                ai_model=ai_model,
                final_result="failed",
            )
            result.errors.append(str(exc))
            result.errors.append(tb)
            save_result(result)
            log.info("[%s] Failure result saved", test_id)
        except Exception as save_exc:
            log.error("[%s] Failed to save result: %s", test_id, save_exc)


@bp.route("/drive-files", methods=["GET"])
def drive_files():
    try:
        config = get_config()
        return jsonify(drive_service.list_drive_files_response(config))
    except Exception as exc:
        return jsonify({"error": str(exc), "traceback": traceback.format_exc()}), 500


@bp.route("/run", methods=["POST"])
def run_test():
    body = request.get_json(silent=True) or {}
    language = body.get("language", "").strip()
    audio_filename = body.get("audio_filename", "").strip()
    ai_model = body.get("ai_model", "gpt-4").strip()

    if not language or not audio_filename:
        return jsonify({"error": "language and audio_filename are required"}), 400

    if ai_model not in ("gpt-4", "deepseek"):
        return jsonify({"error": "ai_model must be 'gpt-4' or 'deepseek'"}), 400

    test_id = str(uuid.uuid4())
    log.info(
        "[%s] Starting test run: language=%s, audio=%s, model=%s",
        test_id,
        language,
        audio_filename,
        ai_model,
    )
    thread = threading.Thread(
        target=_run_and_store,
        args=(test_id, language, audio_filename, ai_model),
        daemon=True,
    )
    thread.start()

    return jsonify({"test_id": test_id, "status": "running"}), 202
