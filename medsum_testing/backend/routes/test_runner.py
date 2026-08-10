"""POST /api/medsum-test/run and GET /api/medsum-test/drive-files"""

from __future__ import annotations

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


def _update_step(result: TestResult, step: str, status: str) -> None:
    for item in result.progress_steps:
        if item["step"] == step:
            item["status"] = status
            return
    result.progress_steps.append({"step": step, "status": status})


def _run_test_flow(test_id: str, language: str, audio_filename: str, ai_model: str) -> None:
    result = TestResult(
        test_id=test_id,
        status="running",
        language=language,
        audio_filename=audio_filename,
        ai_model=ai_model,
    )
    result.progress_steps = [
        {"step": "Fetching audio from Drive", "status": "active"},
        {"step": "Submitting to MedSum API", "status": "pending"},
        {"step": "Waiting for transcription", "status": "pending"},
        {"step": "Running AI comparison", "status": "pending"},
    ]
    save_result(result)

    try:
        config = get_config()
        cases = drive_service.list_test_cases()
        case = next(
            (
                c
                for c in cases
                if c["language"] == language and c["audio_filename"] == audio_filename
            ),
            None,
        )
        if not case:
            raise ValueError(f"Test case not found: {language}/{audio_filename}")

        result.ground_truth_flag = case.get("ground_truth_flag", "")
        result.has_ground_truth = case.get("has_transcript", False)

        audio_bytes = drive_service.download_audio(case["audio_file_id"])
        ground_truth = ""
        if case.get("transcript_file_id"):
            ground_truth = drive_service.download_transcript(case["transcript_file_id"])
        elif result.ground_truth_flag == "no_ground_truth":
            result.accuracy_skipped = True
            result.accuracy_skip_reason = "No ground truth .txt file found for this audio"

        result.ground_truth_transcription = ground_truth
        result.audio_duration_seconds = audio_utils.get_duration_seconds(
            audio_bytes, audio_filename
        )
        _update_step(result, "Fetching audio from Drive", "done")
        _update_step(result, "Submitting to MedSum API", "active")
        save_result(result)

        token, doctor_id = medsum_api.authenticate_doctor(config)
        patient_id = str(config["patient"]["id"])
        result.doctor_id = doctor_id
        result.patient_id = patient_id

        session_id = medsum_api.get_or_create_session(doctor_id, patient_id, token, config)
        result.session_id = session_id
        result.session_datetime = datetime.now(timezone.utc).isoformat()

        submit = medsum_api.submit_audio(
            audio_bytes=audio_bytes,
            audio_filename=audio_filename,
            session_id=session_id,
            config=config,
            token=token,
            language=language,
            duration_seconds=result.audio_duration_seconds,
            doctor_id=doctor_id,
            patient_id=patient_id,
        )

        result.job_id = submit.get("job_id", "")
        result.session_id = submit.get("session_id", session_id)
        cached_transcription = submit.get("transcription", "")
        cached_summary = submit.get("summary")
        result.text_translation = submit.get("translation", "")
        flask_body = submit.get("flask_body", {})

        _update_step(result, "Submitting to MedSum API", "done")
        _update_step(result, "Waiting for transcription", "active")
        save_result(result)

        generated, retry_count = medsum_api.fetch_transcription(
            result.session_id,
            token,
            config,
            cached=cached_transcription,
        )
        result.retry_count = retry_count
        result.generated_transcription = generated or cached_transcription

        summary_data = medsum_api.fetch_summary(
            result.session_id, token, config, cached=cached_summary
        )
        result.generated_summary = summary_data.get("summary", cached_summary)

        meds = medsum_api.fetch_medications(result.session_id, token, config, flask_body)
        result.medications_before = meds.get("before")
        result.medications_after_normalization = meds.get("after_normalization")
        result.medications_generated = meds.get("generated")

        _update_step(result, "Waiting for transcription", "done")
        _update_step(result, "Running AI comparison", "active")
        save_result(result)

        previous = find_previous_result(language, audio_filename, test_id)
        if previous:
            result.previous_transcription = previous.generated_transcription
            result.previous_summary = previous.generated_summary

        if result.has_ground_truth and ground_truth.strip():
            result.transcription_comparison = ai_comparator.compare_transcriptions(
                ground_truth, result.generated_transcription, ai_model
            )
            result.accuracy_score = result.transcription_comparison.similarity_score
        else:
            result.transcription_comparison = ai_comparator.compare_transcriptions(
                "", result.generated_transcription, ai_model
            )
            if not result.accuracy_skip_reason:
                result.accuracy_skipped = True
                result.accuracy_skip_reason = "No ground truth available"

        if previous and result.previous_summary is not None:
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
        elif result.transcription_comparison and result.transcription_comparison.severity == "high":
            result.final_result = "fail"
        elif result.transcription_comparison and (result.accuracy_score or 0) >= 80:
            result.final_result = "pass"
        else:
            result.final_result = "review"

        result.status = "complete"

    except Exception as exc:
        result.status = "failed"
        result.final_result = "failed"
        result.errors.append(str(exc))
        result.errors.append(traceback.format_exc())
        for step in result.progress_steps:
            if step["status"] == "active":
                step["status"] = "failed"

    save_result(result)


@bp.route("/drive-files", methods=["GET"])
def drive_files():
    try:
        return jsonify(drive_service.list_drive_files_response())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


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
    thread = threading.Thread(
        target=_run_test_flow,
        args=(test_id, language, audio_filename, ai_model),
        daemon=True,
    )
    thread.start()

    return jsonify({"test_id": test_id, "status": "running"}), 202
