"""POST /api/medsum-test/run and GET /api/medsum-test/drive-files"""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from medsum_testing.backend.models.test_result import TestResult
from medsum_testing.backend.services import ai_comparator, audio_utils, drive_service, medsum_api
from medsum_testing.backend.services.config_loader import get_config
from medsum_testing.backend.services.result_store import find_previous_result, save_result
from medsum_testing.backend.services.ref_generator import generate_tc_ref

bp = Blueprint("medsum_test_runner", __name__)
log = logging.getLogger("medsum_test_runner")

VALID_MODELS = ("gpt-4o-mini", "gpt-4", "deepseek")


def _update_step(result: TestResult, step: str, status: str) -> None:
    for item in result.progress_steps:
        if item["step"] == step:
            item["status"] = status
            return
    result.progress_steps.append({"step": step, "status": status})


def _comparison_to_dict(comp) -> dict | None:
    if not comp or getattr(comp, "skipped", False):
        return None
    return {
        "similarity_score": comp.similarity_score,
        "medical_differences": comp.medical_difference_details or [],
        "general_differences": comp.general_differences or [],
        "overall_severity": comp.severity,
        "summary": comp.summary,
    }


def _build_db_payload(result: TestResult) -> dict:
    tr = result.transcription_result or {}
    debug = tr.get("debug") or {}

    final_result = result.final_result
    if final_result in ("complete_no_accuracy", "review"):
        final_result = "pass" if final_result == "complete_no_accuracy" else "fail"
    elif final_result == "failed":
        final_result = "fail"

    return {
        "test_id": result.test_id,
        "batch_id": result.batch_id or None,
        "status": result.status,
        "language": result.language,
        "audio_filename": result.audio_filename,
        "folder_label": result.folder_label,
        "ai_model": result.ai_model,

        "audio_duration_seconds": result.audio_duration_seconds,
        "audio_size_bytes": result.audio_size_bytes,

        "session_id": result.session_id,
        "audio_id": str(result.job_id or ""),
        "summary_id": str((result.saved_summary or {}).get("summary_id", "")),
        "doctor_id": str(result.doctor_id or ""),
        "patient_id": str(result.patient_id or ""),

        "ground_truth": result.ground_truth_transcription,
        "ground_truth_translation": result.translation_ground_truth or "",
        "has_translation_ground_truth": result.has_translation_ground_truth,
        "translation_comparison": result.translation_comparison,

        "transcription": result.generated_transcription,
        "translation": result.translation or result.text_translation,

        "transcription_result": tr,

        "soap_ground_truth": result.soap_ground_truth,
        "has_soap_ground_truth": result.has_soap_ground_truth,
        "soap_comparison": result.soap_comparison,

        "comparison": _comparison_to_dict(result.transcription_comparison),
        "ai_model_used": result.ai_model_used or result.ai_model,

        "medication_validation": result.medication_validation,

        "total_test_time_seconds": result.total_test_time_seconds,
        "drive_download_time_seconds": result.drive_download_time_seconds,
        "audio_upload_time_seconds": result.audio_upload_time_seconds,
        "ai_comparison_time_seconds": result.ai_comparison_time_seconds,

        "previous_test_id": result.previous_test_id or None,
        "previous_similarity_score": result.previous_similarity_score,
        "regression_vs_previous": result.regression_vs_previous or "na",

        "final_result": final_result,
        "error_message": " | ".join(
            part for part in (
                "; ".join(result.errors) if result.errors else "",
                f"Flask LLM error: {result.flask_error}" if result.flask_error else "",
            )
            if part
        ),
        "flask_error": result.flask_error or "",

        "initiated_by": result.initiated_by,
        "target_environment": result.target_environment,
        "medsum_version": result.medsum_version,
        "git_commit": result.git_commit,

        "stt_model": result.stt_model,
        "translation_model": result.translation_model,
        "llm_model": result.llm_model,
        "summary_template_id": result.summary_template_id,
        "summary_template_name": result.summary_template_name,

        "django_audio_endpoint": result.django_audio_endpoint,
        "flask_transcribe_endpoint": result.flask_transcribe_endpoint,
        "django_summary_endpoint": result.django_summary_endpoint,

        "drive_audio_file_id": result.drive_audio_file_id,
        "drive_audio_filename": result.drive_audio_filename,
        "drive_folder_id": result.drive_folder_id,
        "drive_transcript_file_id": result.drive_transcript_file_id,
        "drive_soap_gt_file_id": result.drive_soap_gt_file_id,
        "drive_translation_gt_file_id": result.drive_translation_gt_file_id,

        "report_pdf_path": result.report_pdf_path,
        "report_excel_path": result.report_excel_path,
    }


def _apply_django_refs(result: TestResult, django_resp: dict | None) -> None:
    if not django_resp:
        return
    if django_resp.get("tc_ref"):
        result.tc_ref = django_resp["tc_ref"]
    if django_resp.get("run_ref"):
        result.run_ref = django_resp["run_ref"]


def _ensure_local_refs(result: TestResult) -> None:
    if not result.tc_ref and result.language:
        result.tc_ref = generate_tc_ref(result.language)


def _classify_regression(previous: TestResult | None, result: TestResult) -> str:
    """better / worse / same from accuracy-score delta; na if either score is missing."""
    if previous is None:
        return "na"
    prev_score = previous.accuracy_score
    curr_score = result.accuracy_score
    if prev_score is None or curr_score is None:
        return "na"
    delta = curr_score - prev_score
    if delta > 2:
        return "better"
    if delta < -2:
        return "worse"
    return "same"


def execute_test_run(
    language: str,
    audio_filename: str,
    ai_model: str,
    test_id: str | None = None,
    batch_id: str | None = None,
    folder_label: str = "",
    initiated_by: str | None = None,
    token: str | None = None,
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
        batch_id=batch_id or "",
        folder_label=folder_label,
    )
    _ensure_local_refs(result)
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

        run_context = config.get("run_context", {})
        llm_settings = config.get("llm_settings", {})
        backends = config["backends"]
        django_base = backends["django_base_url"].rstrip("/")
        flask_base = backends["flask_transcribe_url"].rstrip("/")
        if not flask_base.endswith("/transcribe"):
            flask_base = f"{flask_base}/transcribe"

        result.initiated_by = initiated_by or run_context.get("initiated_by", "manual")
        result.target_environment = run_context.get("target_environment", "")
        result.medsum_version = run_context.get("medsum_version", "")
        result.git_commit = run_context.get("git_commit", "")
        result.stt_model = llm_settings.get("stt_model", "")
        result.translation_model = llm_settings.get("translation_type", "")
        result.llm_model = llm_settings.get("llm_model", "")
        result.summary_template_id = str(llm_settings.get("template_id", ""))
        result.summary_template_name = llm_settings.get("template_name", "")
        result.django_audio_endpoint = f"{django_base}/api/audio-data/"
        result.flask_transcribe_endpoint = flask_base
        result.django_summary_endpoint = f"{django_base}/api/summary-data/"

        run_start = time.time()
        timings: dict[str, float] = {}

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
        result.folder_label = case.get("folder_label", "")
        result.ground_truth_flag = case.get("ground_truth_flag", "")
        result.has_ground_truth = case.get("has_transcript", False)
        result.has_soap_ground_truth = case.get("has_soap_ground_truth", False)
        result.drive_audio_file_id = case.get("audio_file_id", "")
        result.drive_audio_filename = case.get("audio_filename", "")
        result.drive_folder_id = case.get("folder_id", "")
        result.drive_transcript_file_id = case.get("transcript_file_id") or ""
        result.drive_soap_gt_file_id = case.get("soap_gt_file_id") or ""
        result.drive_translation_gt_file_id = case.get("translation_gt_file_id") or ""

        drive_svc = drive_service.get_drive_service(config)

        log.info("[%s] STEP 2: Downloading audio and ground truth...", test_id)
        t0 = time.time()
        audio_bytes = drive_service.download_file(case["audio_file_id"], drive_svc)
        result.audio_size_bytes = len(audio_bytes)
        log.info("[%s] Audio: %d bytes", test_id, len(audio_bytes))

        ground_truth = ""
        soap_ground_truth = None
        translation_ground_truth = None

        if case.get("has_transcript") and case.get("transcript_file_id"):
            log.info("[%s] Downloading transcript...", test_id)
            ground_truth = drive_service.download_transcript(
                case["transcript_file_id"],
                mime_type=case.get("transcript_mime_type"),
                service=drive_svc,
            )
            log.info("[%s] Transcript: %d chars", test_id, len(ground_truth))
        elif not case.get("has_transcript"):
            log.info("[%s] No ground truth — accuracy scoring will be skipped", test_id)
            result.accuracy_skipped = True
            result.accuracy_skip_reason = "No ground truth transcript found for this audio"

        language_code = medsum_api.normalize_language(language)

        if language_code == "en" and ground_truth:
            log.info("[%s] English audio — ground truth IS the translation", test_id)
            translation_ground_truth = ground_truth
        elif case.get("has_translation_ground_truth") and case.get("translation_gt_file_id"):
            log.info("[%s] Downloading translation ground truth...", test_id)
            translation_ground_truth = drive_service.download_translation_ground_truth(
                case["translation_gt_file_id"],
                case.get("translation_gt_mime_type"),
                drive_svc,
            )
            if translation_ground_truth:
                log.info(
                    "[%s] Translation GT: %d chars",
                    test_id,
                    len(translation_ground_truth),
                )
            else:
                log.info("[%s] Translation GT: not available", test_id)

        if case.get("has_soap_ground_truth") and case.get("soap_gt_file_id"):
            log.info("[%s] Downloading SOAP ground truth...", test_id)
            soap_ground_truth = drive_service.download_soap_ground_truth(
                case["soap_gt_file_id"],
                case.get("soap_gt_mime_type"),
                drive_svc,
            )
            if soap_ground_truth:
                log.info("[%s] SOAP GT: keys=%s", test_id, list(soap_ground_truth.keys()))
            else:
                log.info("[%s] SOAP GT: not available or parse failed", test_id)
        else:
            log.info("[%s] has_soap_ground_truth: false — skipping SOAP GT download", test_id)

        timings["drive_download_time_seconds"] = round(time.time() - t0, 3)
        log.info("[%s] drive_download_time_seconds=%s", test_id, timings["drive_download_time_seconds"])

        result.ground_truth_transcription = ground_truth
        result.soap_ground_truth = soap_ground_truth
        result.has_soap_ground_truth = soap_ground_truth is not None
        result.translation_ground_truth = translation_ground_truth or ""
        result.has_translation_ground_truth = bool(translation_ground_truth)

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

        if not token:
            raise RuntimeError("No token provided to execute_test_run")

        runtime = medsum_api.get_runtime_state()
        doctor_id = runtime.get("doctor_id") or str(config["doctor"].get("id", ""))
        runtime["access_token"] = token
        if doctor_id:
            runtime["doctor_id"] = doctor_id
        log.info("[%s] Using existing token, doctor_id=%s", test_id, doctor_id)
        result.doctor_id = str(doctor_id)

        patient_id = str(config["patient"]["id"])
        log.info("[%s] STEP 6: Verifying patient %s...", test_id, patient_id)
        patient_data = medsum_api.verify_patient(patient_id, token, config)
        resolved_patient_id = str(patient_data.get("patient_id") or patient_id)
        result.patient_id = resolved_patient_id
        log.info("[%s] Patient OK: %s", test_id, patient_data.get("patient_name"))

        log.info("[%s] STEP 7: Uploading audio to Django...", test_id)
        t0 = time.time()
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
        timings["audio_upload_time_seconds"] = round(time.time() - t0, 3)
        log.info("[%s] audio_upload_time_seconds=%s", test_id, timings["audio_upload_time_seconds"])
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
        debug_translation = transcription_result.get("debug", {}).get("translation", "")
        result.text_translation = (
            debug_translation
            or transcription_result.get("translation", "")
            or ""
        )
        result.translation = result.text_translation
        result.transcription_result = transcription_result
        log.info("[%s] Transcription OK: %d chars", test_id, len(transcription))

        # Check if Flask returned an LLM error alongside the transcription
        flask_error = transcription_result.get("error")
        result.flask_error = str(flask_error) if flask_error else ""
        if flask_error:
            log.warning(
                "[%s] Flask returned an error (LLM may have failed): %s",
                test_id,
                str(flask_error)[:300],
            )
            # Transcription still usable — just SOAP may be missing

        log.info("[%s] Flask response top-level keys: %s", test_id, list(transcription_result.keys()))
        for key in ai_comparator.SOAP_KEYS:
            val = transcription_result.get(key)
            log.info(
                "[%s]   %s: %s (%s)",
                test_id,
                key,
                "FOUND" if val else "MISSING",
                type(val).__name__,
            )
        debug_block = transcription_result.get("debug") or {}
        raw_soap_dbg = debug_block.get("raw_soap") or debug_block.get("raw soap") or {}
        log.info("[%s] debug keys: %s", test_id, list(debug_block.keys()))
        log.info(
            "[%s] raw_soap keys: %s",
            test_id,
            list(raw_soap_dbg.keys()) if raw_soap_dbg else "EMPTY",
        )

        log.info("[%s] Validating medications...", test_id)
        medication_validation = ai_comparator.validate_medications(transcription_result)
        result.medication_validation = medication_validation
        log.info(
            "[%s] Medications: raw=%s, final=%s, differences=%s",
            test_id,
            medication_validation["raw_count"],
            medication_validation["final_count"],
            medication_validation["difference_count"],
        )

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
        result.saved_summary = saved_summary
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
            result.previous_test_id = previous.test_id
            result.previous_similarity_score = previous.accuracy_score
        else:
            log.info("[%s] Previous result: none", test_id)

        log.info("[%s] STEP 12: AI comparison with %s...", test_id, ai_model)
        t0 = time.time()
        if result.has_ground_truth and ground_truth.strip():
            if language_code == "en":
                log.info("[%s] English: running transcription comparison (GT vs Generated)", test_id)
            result.transcription_comparison = ai_comparator.compare_transcriptions(
                ground_truth, transcription, ai_model, config
            )
            result.accuracy_score = result.transcription_comparison.similarity_score
            result.ai_model_used = ai_model
            log.info("[%s] Comparison score: %s", test_id, result.accuracy_score)
        else:
            log.info("[%s] Skipping comparison — no ground truth", test_id)
            result.transcription_comparison = ai_comparator.compare_transcriptions(
                "", transcription, ai_model, config
            )
            if not result.accuracy_skip_reason:
                result.accuracy_skipped = True
                result.accuracy_skip_reason = "No ground truth available"
        timings["ai_comparison_time_seconds"] = round(time.time() - t0, 3)
        log.info("[%s] ai_comparison_time_seconds=%s", test_id, timings["ai_comparison_time_seconds"])

        soap_comparison = None
        # Log what we're passing to extract_soap_from_result
        _soap_debug = {
            k: type(transcription_result.get(k)).__name__
            for k in ai_comparator.SOAP_KEYS
        }
        log.info(
            "[%s] SOAP key types in transcription_result: %s",
            test_id,
            _soap_debug,
        )

        soap_generated = ai_comparator.extract_soap_from_result(transcription_result)
        log.info(
            "[%s] soap_generated: %s",
            test_id,
            list(soap_generated.keys()) if soap_generated else None,
        )

        if soap_ground_truth and soap_generated:
            log.info("[%s] Running SOAP comparison...", test_id)
            soap_comparison = ai_comparator.compare_soap(
                soap_ground_truth, soap_generated, ai_model, config
            )
            log.info(
                "[%s] SOAP score: %s",
                test_id,
                soap_comparison.get("similarity_score"),
            )
        elif soap_ground_truth and not soap_generated:
            log.warning(
                "[%s] SOAP GT available but no generated SOAP — "
                "check extract_soap_from_result()",
                test_id,
            )
        else:
            log.info("[%s] No SOAP GT — skipping SOAP comparison", test_id)
        result.soap_comparison = soap_comparison

        generated_translation = (
            transcription_result.get("debug", {}).get("translation", "")
            or result.translation
            or ""
        )
        translation_comparison = None
        if language_code != "en" and translation_ground_truth and generated_translation:
            log.info("[%s] Running translation comparison...", test_id)
            t0 = time.time()
            translation_comparison = ai_comparator.compare_translations(
                translation_ground_truth,
                generated_translation,
                ai_model,
                config,
            )
            log.info(
                "[%s] Translation comparison score: %s",
                test_id,
                translation_comparison.get("similarity_score"),
            )
        elif language_code == "en":
            log.info("[%s] English audio — skipping separate translation comparison", test_id)
        else:
            log.info(
                "[%s] Skipping translation comparison — GT available: %s, Generated: %s",
                test_id,
                bool(translation_ground_truth),
                bool(generated_translation),
            )
        result.translation_comparison = translation_comparison

        if previous and previous.generated_summary is not None:
            result.summary_comparison = ai_comparator.compare_summaries(
                previous.generated_summary, result.generated_summary, ai_model
            )

        if previous and (previous.generated_transcription or "").strip():
            result.regression_comparison = ai_comparator.compare_regression(
                previous.generated_transcription,
                result.generated_transcription,
                ai_model,
            )

        result.regression_vs_previous = _classify_regression(previous, result)

        result.medication_comparison = ai_comparator.compare_medication_lists(
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

        timings["total_test_time_seconds"] = round(time.time() - run_start, 3)
        result.total_test_time_seconds = timings["total_test_time_seconds"]
        result.drive_download_time_seconds = timings.get("drive_download_time_seconds")
        result.audio_upload_time_seconds = timings.get("audio_upload_time_seconds")
        result.ai_comparison_time_seconds = timings.get("ai_comparison_time_seconds")
        log.info(
            "[%s] total_test_time_seconds=%s",
            test_id,
            result.total_test_time_seconds,
        )

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


def _run_and_store(
    test_id: str,
    language: str,
    audio_filename: str,
    ai_model: str,
    batch_id: str = "",
    folder_label: str = "",
    initiated_by: str = "manual",
    token: str | None = None,
) -> None:
    log.info("[%s] Background thread started", test_id)
    config = get_config()

    if not token:
        try:
            token, doctor_id = medsum_api.authenticate_doctor(config)
            log.info("[%s] Auth OK doctor_id=%s", test_id, doctor_id)
        except Exception as auth_exc:
            log.error("[%s] Auth failed: %s", test_id, auth_exc)
            result = TestResult(
                test_id=test_id,
                status="failed",
                language=language,
                audio_filename=audio_filename,
                ai_model=ai_model,
                final_result="failed",
                batch_id=batch_id or "",
                folder_label=folder_label,
            )
            result.errors.append(f"Auth failed: {auth_exc}")
            save_result(result)
            return

    try:
        medsum_api.save_test_run(
            {
                "test_id": test_id,
                "batch_id": batch_id or None,
                "status": "running",
                "language": language,
                "audio_filename": audio_filename,
                "folder_label": folder_label,
                "ai_model": ai_model,
                "initiated_by": initiated_by,
            },
            token,
            config,
        )
    except Exception as auth_exc:
        log.warning("[%s] Could not save running status to Django: %s", test_id, auth_exc)

    try:
        log.info("[%s] Calling execute_test_run...", test_id)
        result = execute_test_run(
            language,
            audio_filename,
            ai_model,
            test_id=test_id,
            batch_id=batch_id,
            folder_label=folder_label,
            initiated_by=initiated_by,
            token=token,
        )
        log.info("[%s] Background thread finished — status=%s", test_id, result.status)

        if token:
            django_resp = medsum_api.save_test_run(
                _build_db_payload(result), token, config
            )
            _apply_django_refs(result, django_resp)
            save_result(result)
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
                batch_id=batch_id or "",
                folder_label=folder_label,
            )
            _ensure_local_refs(result)
            result.errors.append(str(exc))
            result.errors.append(tb)
            save_result(result)
            log.info("[%s] Failure result saved", test_id)
            if token:
                django_resp = medsum_api.save_test_run(
                    _build_db_payload(result), token, config
                )
                _apply_django_refs(result, django_resp)
                save_result(result)
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
    ai_model = body.get("ai_model", "gpt-4o-mini").strip()

    if not language or not audio_filename:
        return jsonify({"error": "language and audio_filename are required"}), 400

    if ai_model not in VALID_MODELS:
        return jsonify({"error": f"ai_model must be one of {VALID_MODELS}"}), 400

    test_id = str(uuid.uuid4())
    log.info(
        "[%s] Starting test run: language=%s, audio=%s, model=%s",
        test_id,
        language,
        audio_filename,
        ai_model,
    )
    config = get_config()
    try:
        token, _ = medsum_api.authenticate_doctor(config)
    except Exception as exc:
        return jsonify({"error": f"Auth failed: {exc}"}), 500

    thread = threading.Thread(
        target=_run_and_store,
        args=(test_id, language, audio_filename, ai_model, "", "", "manual", token),
        daemon=True,
    )
    thread.start()

    return jsonify({"test_id": test_id, "status": "running"}), 202


@bp.route("/run-all", methods=["POST"])
def run_all_tests():
    """Run every ready audio file from Drive."""
    body = request.get_json(silent=True) or {}
    config = get_config()
    default_model = config.get("ai_comparison", {}).get("default_model", "gpt-4o-mini")
    ai_model = body.get("ai_model", default_model)
    if ai_model not in VALID_MODELS:
        return jsonify({"error": f"ai_model must be one of {VALID_MODELS}"}), 400

    batch_id = str(uuid.uuid4())

    try:
        token, _ = medsum_api.authenticate_doctor(config)
    except Exception as exc:
        return jsonify({"error": f"Auth failed: {exc}"}), 500

    test_cases = [
        tc for tc in drive_service.list_test_cases(config) if tc.get("status") == "ready"
    ]

    batch_ref = medsum_api.create_batch(
        batch_id, ai_model, config, token, total_files=len(test_cases)
    )
    log.info("Batch created: batch_id=%s batch_ref=%s", batch_id, batch_ref)

    stagger = config.get("test_settings", {}).get("run_all_stagger_seconds", 3)
    test_ids = []
    for i, tc in enumerate(test_cases):
        test_id = str(uuid.uuid4())
        test_ids.append({
            "test_id": test_id,
            "language": tc["language"],
            "audio_filename": tc["audio_filename"],
            "folder_label": tc.get("folder_label", ""),
        })
        pending = TestResult(
            test_id=test_id,
            status="pending",
            language=tc["language"],
            audio_filename=tc["audio_filename"],
            folder_label=tc.get("folder_label", ""),
            ai_model=ai_model,
            batch_id=batch_id,
        )
        _ensure_local_refs(pending)
        save_result(pending)

        threading.Timer(
            i * stagger,
            _run_and_store,
            args=(
                test_id,
                tc["language"],
                tc["audio_filename"],
                ai_model,
                batch_id,
                tc.get("folder_label", ""),
                "scheduler",
                token,
            ),
        ).start()

    log.info("Batch %s started — %d tests", batch_id, len(test_ids))
    return jsonify({
        "batch_id": batch_id,
        "batch_ref": batch_ref,
        "total": len(test_ids),
        "test_ids": test_ids,
        "status": "started",
    }), 202
