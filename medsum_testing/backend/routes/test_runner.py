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


def _parse_doctors(raw, fallback_patient_id: str = "") -> list[dict]:
    """Normalize request doctors into {phone, password, patients: [str, ...]}."""
    doctors = []
    fallback = str(fallback_patient_id or "").strip()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        phone = str(item.get("phone") or "").strip()
        password = str(item.get("password") or "")
        patients = item.get("patients") or []
        if isinstance(patients, str):
            patients = [p.strip() for p in patients.split(",") if p.strip()]
        else:
            patients = [str(p).strip() for p in patients if str(p).strip()]
        if not patients and fallback:
            patients = [fallback]
        if not phone or not password or not patients:
            continue
        doctors.append({
            "phone": phone,
            "password": password,
            "patients": patients,
        })
    return doctors


def _resolve_patient_id(requested: str | None, config: dict | None = None) -> str:
    """Prefer request-body patient_id; fall back to config patient.id."""
    pid = str(requested or "").strip()
    if pid:
        return pid
    cfg = config if config is not None else get_config()
    return str((cfg.get("patient") or {}).get("id") or "").strip()


def _update_step(result: TestResult, step: str, status: str) -> None:
    for item in result.progress_steps:
        if item["step"] == step:
            item["status"] = status
            return
    result.progress_steps.append({"step": step, "status": status})


def _comparison_to_dict(comp) -> dict | None:
    if not comp or getattr(comp, "skipped", False):
        return None
    if isinstance(comp, dict):
        if comp.get("skipped"):
            return None
        return {
            "similarity_score": comp.get("similarity_score"),
            "medical_differences": (
                comp.get("medical_difference_details")
                or comp.get("medical_differences")
                or []
            ),
            "general_differences": comp.get("general_differences") or [],
            "overall_severity": comp.get("overall_severity") or comp.get("severity") or "low",
            "summary": comp.get("summary") or "",
            "error": comp.get("error") or "",
        }
    return {
        "similarity_score": comp.similarity_score,
        "medical_differences": comp.medical_difference_details or [],
        "general_differences": comp.general_differences or [],
        "overall_severity": comp.severity,
        "summary": comp.summary,
        "error": getattr(comp, "error", "") or "",
    }


def _build_db_payload(result: TestResult) -> dict:
    tr = result.transcription_result or {}
    debug = tr.get("debug") or {}

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
        "doctor_name": result.doctor_name or "",
        "phone": result.phone or "",
        "patient_id": str(result.patient_id or ""),

        "ground_truth": result.ground_truth_transcription,
        "ground_truth_translation": result.translation_ground_truth or "",
        "has_translation_ground_truth": result.has_translation_ground_truth,
        "translation_comparison": result.translation_comparison,

        "transcription": result.transcription or result.generated_transcription,
        "translation": (
            result.generated_translation
            or result.translation
            or result.text_translation
        ),

        "transcription_result": tr,

        "soap_ground_truth": result.soap_ground_truth,
        "soap_generated": result.soap_generated,
        "soap_raw": result.soap_raw,
        "has_soap_ground_truth": result.has_soap_ground_truth,
        "soap_comparison": result.soap_comparison,

        "comparison": result.comparison or _comparison_to_dict(result.transcription_comparison),
        "final_result": result.final_result,
        "ai_model_used": result.ai_model_used or result.ai_model,

        "medication_validation": result.medication_validation,

        "total_test_time_seconds": result.total_test_time_seconds,
        "drive_download_time_seconds": result.drive_download_time_seconds,
        "audio_upload_time_seconds": result.audio_upload_time_seconds,
        "ai_comparison_time_seconds": result.ai_comparison_time_seconds,

        "previous_test_id": result.previous_test_id or None,
        "previous_similarity_score": result.previous_similarity_score,
        "regression_vs_previous": result.regression_vs_previous or "na",

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
    patient_id: str | None = None,
    doctor_id: str | None = None,
    phone: str | None = None,
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
        phone=phone or "",
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
            available = [
                (c["language"], c["audio_filename"])
                for c in cases
                if c.get("status") == "ready"
            ]
            raise ValueError(
                f"No test case found for language='{language}', "
                f"audio_filename='{audio_filename}'. Available: {available}"
            )

        language = case["language"]
        audio_filename = case["audio_filename"]
        result.language = language
        result.audio_filename = audio_filename

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
            ground_truth = drive_service.strip_case_header(
                drive_service.download_transcript(
                    case["transcript_file_id"],
                    mime_type=case.get("transcript_mime_type"),
                    service=drive_svc,
                )
                or ""
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
                translation_ground_truth = drive_service.strip_case_header(
                    translation_ground_truth
                )
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

        ground_truth = drive_service.strip_case_header(ground_truth or "")
        translation_ground_truth = drive_service.strip_case_header(
            translation_ground_truth or ""
        ) or None

        result.ground_truth_transcription = ground_truth
        result.ground_truth = ground_truth
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
        resolved_doctor_id = str(
            doctor_id
            or runtime.get("doctor_id")
            or (config.get("doctor") or {}).get("id", "")
            or ""
        )
        runtime["access_token"] = token
        if resolved_doctor_id:
            runtime["doctor_id"] = resolved_doctor_id
        log.info("[%s] Using existing token, doctor_id=%s", test_id, resolved_doctor_id)
        result.doctor_id = resolved_doctor_id

        resolved_patient_id = _resolve_patient_id(patient_id, config)
        if not resolved_patient_id:
            raise RuntimeError("No patient_id provided")
        log.info("[%s] STEP 6: Verifying patient %s...", test_id, resolved_patient_id)
        patient_data = medsum_api.verify_patient(resolved_patient_id, token, config)
        resolved_patient_id = str(patient_data.get("patient_id") or resolved_patient_id)
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
            user_id=resolved_doctor_id,
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
        doctor_data = medsum_api.fetch_doctor_profile(resolved_doctor_id, token, config)
        result.doctor_name = (doctor_data.get("name") or "").strip()
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

        log.info("[%s] STEP 9: Saving summary to Django...", test_id)
        saved_summary = medsum_api.save_summary(
            session_id=session_id,
            audio_id=audio_id,
            patient_id=resolved_patient_id,
            user_id=resolved_doctor_id,
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
            prev_comp = previous.comparison or {}
            if not isinstance(prev_comp, dict):
                prev_comp = {}
            result.previous_similarity_score = (
                prev_comp.get("similarity_score")
                or (
                    previous.transcription_comparison.similarity_score
                    if previous.transcription_comparison
                    else None
                )
                or previous.accuracy_score
            )
            log.info(
                "[%s] Previous result: score=%s",
                test_id,
                result.previous_similarity_score,
            )
        else:
            log.info("[%s] Previous result: none", test_id)

        # ─────────────────────────────────────────────────────────
        # STEP: Extract all data from Flask response
        # ─────────────────────────────────────────────────────────
        transcription = transcription_result.get("transcription", "") or transcription
        debug = transcription_result.get("debug") or {}
        gen_translation = debug.get("translation", "") or result.translation or ""

        # Raw soap — handle both key variants
        raw_soap_block = debug.get("raw_soap") or debug.get("raw soap") or {}
        # Only use if it contains actual SOAP keys, not just {"error": "..."}
        soap_raw = None
        if isinstance(raw_soap_block, dict) and any(
            k in raw_soap_block
            for k in ["subjective", "objective", "assessment", "plan", "summary"]
        ):
            soap_raw = raw_soap_block

        # Generated SOAP from top level
        soap_generated = ai_comparator.extract_soap_from_result(
            transcription_result, allow_raw_fallback=False
        )

        log.info(
            "[%s] Data available — transcription: %d chars, "
            "gen_translation: %d chars, soap_generated: %s, soap_raw: %s",
            test_id,
            len(transcription),
            len(gen_translation),
            bool(soap_generated),
            bool(soap_raw),
        )

        t_ai_start = time.time()

        # ─────────────────────────────────────────────────────────
        # COMPARISON 1 — Transcription: Ground Truth vs Generated
        # ─────────────────────────────────────────────────────────
        transcription_comparison = None
        if ground_truth and transcription:
            log.info("[%s] Running transcription comparison...", test_id)
            t0 = time.time()
            transcription_comparison = ai_comparator.compare_transcriptions(
                ground_truth, transcription, ai_model, config
            )
            timings["ai_transcription_comparison_time"] = round(time.time() - t0, 3)
            log.info(
                "[%s] Transcription comparison score: %s severity: %s",
                test_id,
                transcription_comparison.similarity_score,
                transcription_comparison.severity,
            )
        else:
            log.info(
                "[%s] Skipping transcription comparison — GT: %s, Generated: %s",
                test_id, bool(ground_truth), bool(transcription),
            )
            if not result.accuracy_skip_reason:
                result.accuracy_skipped = True
                result.accuracy_skip_reason = "No ground truth available"

        # ─────────────────────────────────────────────────────────
        # COMPARISON 2 — Translation: Ground Truth vs Generated
        # ─────────────────────────────────────────────────────────
        translation_comparison = None
        gt_translation = translation_ground_truth or ""

        # For English: _script = ground truth transcription = ground truth translation
        # Compare it against debug.translation from Flask
        if language_code == "en":
            gt_translation = ground_truth  # always use script as translation GT for English

        if gt_translation and gen_translation:
            log.info("[%s] Running translation comparison...", test_id)
            t0 = time.time()
            translation_comparison = ai_comparator.compare_translations(
                gt_translation,      # English _script content
                gen_translation,     # debug.translation from Flask
                ai_model,
                config,
            )
            timings["ai_translation_comparison_time"] = round(time.time() - t0, 3)
            log.info(
                "[%s] Translation comparison score: %s",
                test_id,
                translation_comparison.get("similarity_score"),
            )
        else:
            log.info(
                "[%s] Skipping translation comparison — GT: %s, Generated: %s",
                test_id, bool(gt_translation), bool(gen_translation),
            )

        # ─────────────────────────────────────────────────────────
        # COMPARISON 3 — SOAP (three-way when GT exists)
        # ─────────────────────────────────────────────────────────
        soap_comparison = None

        if soap_ground_truth:
            log.info("[%s] Running three-way SOAP comparison...", test_id)
            t0 = time.time()
            soap_comparison = ai_comparator.compare_soap_three_way(
                soap_ground_truth=soap_ground_truth,
                soap_generated=soap_generated,
                soap_raw=soap_raw,
                model=ai_model,
                config=config,
            )
            timings["ai_soap_comparison_time"] = round(time.time() - t0, 3)
            log.info(
                "[%s] SOAP scores — GT/Gen: %s, GT/Raw: %s, Raw/Gen: %s",
                test_id,
                soap_comparison["scores"].get("gt_vs_generated"),
                soap_comparison["scores"].get("gt_vs_raw"),
                soap_comparison["scores"].get("raw_vs_generated"),
            )
        elif soap_generated and soap_raw:
            # No GT but both generated and raw exist — compare them
            log.info("[%s] No SOAP GT — comparing Raw vs Generated only...", test_id)
            t0 = time.time()
            raw_vs_gen = ai_comparator.compare_soap(
                soap_raw, soap_generated, ai_model, config
            )
            timings["ai_soap_comparison_time"] = round(time.time() - t0, 3)
            soap_comparison = {
                "gt_vs_generated":  None,
                "gt_vs_raw":        None,
                "raw_vs_generated": raw_vs_gen,
                "scores": {
                    "gt_vs_generated":  None,
                    "gt_vs_raw":        None,
                    "raw_vs_generated": raw_vs_gen.get("similarity_score"),
                }
            }
            log.info(
                "[%s] Raw vs Generated SOAP score: %s",
                test_id,
                raw_vs_gen.get("similarity_score"),
            )
        else:
            log.info(
                "[%s] Skipping SOAP comparison — GT: %s, Generated: %s, Raw: %s",
                test_id, bool(soap_ground_truth),
                bool(soap_generated), bool(soap_raw),
            )

        # ─────────────────────────────────────────────────────────
        # COMPARISON 4 — Medication validation (raw vs generated)
        # ─────────────────────────────────────────────────────────
        medication_validation = ai_comparator.compare_medications(transcription_result)
        log.info(
            "[%s] Medication validation — raw: %d, final: %d, differences: %d",
            test_id,
            medication_validation.get("raw_count", 0),
            medication_validation.get("final_count", 0),
            medication_validation.get("difference_count", 0),
        )

        timings["ai_comparison_time_seconds"] = round(time.time() - t_ai_start, 3)
        log.info("[%s] ai_comparison_time_seconds=%s", test_id, timings["ai_comparison_time_seconds"])

        # ─────────────────────────────────────────────────────────
        # Save all comparison results to result object
        # ─────────────────────────────────────────────────────────
        result.transcription         = transcription
        result.generated_transcription = transcription
        result.generated_translation = gen_translation
        result.translation           = gen_translation
        result.text_translation      = gen_translation
        result.soap_generated        = soap_generated
        result.soap_raw              = soap_raw
        result.ground_truth          = ground_truth

        # Primary comparison (transcription) — used for main accuracy score
        result.transcription_comparison = transcription_comparison
        result.comparison              = _comparison_to_dict(transcription_comparison)
        result.translation_comparison  = translation_comparison
        result.soap_comparison         = soap_comparison
        result.medication_validation   = medication_validation
        if transcription_comparison and not getattr(transcription_comparison, "skipped", False):
            result.accuracy_score = transcription_comparison.similarity_score
            result.ai_model_used = ai_model

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
    patient_id: str | None = None,
    doctor_id: str | None = None,
    phone: str | None = None,
) -> TestResult:
    log.info("[%s] Background thread started", test_id)
    config = get_config()

    resolved_patient_id = _resolve_patient_id(patient_id, config)
    if not resolved_patient_id:
        log.error("[%s] No patient_id available", test_id)
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
        result.errors.append("No patient_id provided")
        save_result(result)
        return result

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
            return result

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
            patient_id=resolved_patient_id,
            doctor_id=doctor_id,
            phone=phone,
        )
        log.info("[%s] Background thread finished — status=%s", test_id, result.status)

        if token:
            django_resp = medsum_api.save_test_run(
                _build_db_payload(result), token, config
            )
            _apply_django_refs(result, django_resp)
            save_result(result)
        return result
    except Exception as exc:
        tb = traceback.format_exc()
        log.error("[%s] Unhandled exception in background thread: %s", test_id, exc)
        log.error("[%s] Traceback:\n%s", test_id, tb)
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
        try:
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
        return result


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
    patient_id = str(body.get("patient_id") or "").strip()

    if not language or not audio_filename:
        return jsonify({"error": "language and audio_filename are required"}), 400

    if ai_model not in VALID_MODELS:
        return jsonify({"error": f"ai_model must be one of {VALID_MODELS}"}), 400

    if not patient_id:
        patient_id = _resolve_patient_id("", get_config())
    if not patient_id:
        return jsonify({"error": "patient_id is required"}), 400

    test_id = str(uuid.uuid4())
    log.info(
        "[%s] Starting test run: language=%s, audio=%s, model=%s, patient_id=%s",
        test_id,
        language,
        audio_filename,
        ai_model,
        patient_id,
    )
    config = get_config()
    try:
        token, _ = medsum_api.authenticate_doctor(config)
    except Exception as exc:
        return jsonify({"error": f"Auth failed: {exc}"}), 500

    thread = threading.Thread(
        target=_run_and_store,
        args=(
            test_id,
            language,
            audio_filename,
            ai_model,
            "",
            "",
            "manual",
            token,
            patient_id,
        ),
        daemon=True,
    )
    thread.start()

    return jsonify({"test_id": test_id, "status": "running"}), 202


@bp.route("/run-all", methods=["POST"])
def run_all_tests():
    """Run every ready audio file from Drive for each doctor × patient."""
    body = request.get_json(silent=True) or {}
    config = get_config()
    default_model = config.get("ai_comparison", {}).get("default_model", "gpt-4o-mini")
    ai_model = body.get("ai_model", default_model)
    if ai_model not in VALID_MODELS:
        return jsonify({"error": f"ai_model must be one of {VALID_MODELS}"}), 400

    patient_id = str(body.get("patient_id") or "").strip()
    if not patient_id:
        patient_id = _resolve_patient_id("", config)

    doctors = _parse_doctors(body.get("doctors"), fallback_patient_id=patient_id)
    if not doctors:
        return jsonify({"error": "No doctors provided"}), 400

    if not patient_id and not any(d["patients"] for d in doctors):
        return jsonify({
            "error": "patient_id is required. "
                     "Provide it in the request body or set patient.id in medsum_config.yaml"
        }), 400

    log.info("run_all_tests: patient_id=%s ai_model=%s", patient_id, ai_model)

    batch_id = str(uuid.uuid4())

    authed = []
    for doctor in doctors:
        try:
            token, doctor_id = medsum_api.authenticate_doctor(
                config,
                phone=doctor["phone"],
                password=doctor["password"],
            )
            authed.append({**doctor, "token": token, "doctor_id": str(doctor_id)})
        except Exception as exc:
            return jsonify({
                "error": f"Auth failed for {doctor['phone']}: {exc}"
            }), 500

    test_cases = [
        tc for tc in drive_service.list_test_cases(config) if tc.get("status") == "ready"
    ]

    jobs = []
    for doctor in authed:
        for patient_id in doctor["patients"]:
            for tc in test_cases:
                jobs.append((doctor, patient_id, tc))

    batch_ref = medsum_api.create_batch(
        batch_id, ai_model, config, authed[0]["token"], total_files=len(jobs)
    )
    log.info("Batch created: batch_id=%s batch_ref=%s", batch_id, batch_ref)

    stagger = config.get("test_settings", {}).get("run_all_stagger_seconds", 3)
    test_ids = []
    for i, (doctor, patient_id, tc) in enumerate(jobs):
        test_id = str(uuid.uuid4())
        test_ids.append({
            "test_id": test_id,
            "language": tc["language"],
            "audio_filename": tc["audio_filename"],
            "folder_label": tc.get("folder_label", ""),
            "patient_id": patient_id,
            "doctor_id": doctor["doctor_id"],
        })
        pending = TestResult(
            test_id=test_id,
            status="pending",
            language=tc["language"],
            audio_filename=tc["audio_filename"],
            folder_label=tc.get("folder_label", ""),
            ai_model=ai_model,
            batch_id=batch_id,
            patient_id=str(patient_id),
            doctor_id=str(doctor["doctor_id"]),
            phone=doctor["phone"],
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
                doctor["token"],
                str(patient_id),
                str(doctor["doctor_id"]),
                doctor["phone"],
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
