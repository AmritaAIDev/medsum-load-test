"""Calls MedSum Django + Flask APIs."""

from __future__ import annotations

import base64
import copy
import json
import logging
from typing import Any

import requests

from medsum_testing.backend.services.config_loader import clear_runtime, get_runtime

log = logging.getLogger("medsum")

KNOWN_META_KEYS = frozenset({
    "transcription", "translation", "total-time", "transcription-time",
    "translation-time", "llm-time", "audio_length", "status", "job_id",
})

LANGUAGE_CODE_MAP = {
    "hindi": "hi",
    "english": "en",
    "malayalam": "ml",
    "tamil": "ta",
    "telugu": "te",
    "kannada": "kn",
    "bengali": "bn",
    "marathi": "mr",
    "gujarati": "gu",
    "punjabi": "pa",
    "odia": "or",
    "urdu": "ur",
}

SOAP_CONSULT_TEMPLATE: dict[str, Any] = {
    "subjective": {
        "chief_complaint": "",
        "history_of_present_illness": "",
        "past_medical_history": "",
        "medications": "",
        "allergies": "",
        "social_history": "",
        "family_history": "",
        "blood_group": "",
    },
    "objective": {
        "vitals": {
            "blood_pressure": "",
            "heart_rate": "",
            "respiratory_rate": "",
            "temperature": "",
            "spo2": "",
        },
        "physical_exam": {
            "heart": "",
            "height": "",
            "weight": "",
        },
    },
    "assessment": {
        "diagnosis": "",
        "type": "",
        "status": "",
        "reasoning": "",
    },
    "plan": {
        "medications": "",
        "activity": "",
        "investigations": "",
        "education": "",
        "follow_up": "",
    },
    "summary": "",
}


def normalize_language(language: str) -> str:
    """Convert 'Malayalam' → 'ml', 'Hindi' → 'hi', etc."""
    key = language.lower().strip()
    return LANGUAGE_CODE_MAP.get(key, key)


class AuthError(RuntimeError):
    """Authentication failed — do not retry."""


class PatientNotFoundError(RuntimeError):
    """Configured patient does not exist."""


def _django_base(config: dict) -> str:
    return config["backends"]["django_base_url"].rstrip("/")


def _flask_transcribe_url(config: dict) -> str:
    url = config["backends"]["flask_transcribe_url"].rstrip("/")
    if not url.endswith("/transcribe"):
        url += "/transcribe"
    return url


def _auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _extract_summary(body: dict) -> str:
    text = body.get("summary", "")
    if not text:
        for key in (
            "medical_summary",
            "subjective",
            "Diagnosis",
            "Course_in_the_Hospital_and_Discussion",
            "Medicines",
        ):
            value = body.get(key)
            if value:
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                break
    if not text:
        content = {k: v for k, v in body.items() if k not in KNOWN_META_KEYS}
        text = json.dumps(content, ensure_ascii=False)
    return text


def authenticate_doctor(config: dict, *, force: bool = False) -> tuple[str, str]:
    """
    POST /api/login/
    Request:  { "phone_number": "...", "password": "..." }
    Response: { "access": "...", "refresh": "...", "user": {...} }
    Returns:  (access_token, doctor_id)
    """
    runtime = get_runtime()
    if not force and runtime.get("access_token"):
        doctor_id = runtime.get("doctor_id") or str(config["doctor"].get("id", ""))
        log.info("AUTH ✓ using cached token — doctor_id: %s", doctor_id)
        return runtime["access_token"], str(doctor_id)

    if force:
        clear_runtime()
        runtime = get_runtime()

    base_url = config["backends"]["django_base_url"].rstrip("/")
    login_path = config["backends"].get("login_path", "/api/login/")
    url = f"{base_url}{login_path}"

    doctor = config["doctor"]
    phone_number = doctor.get("phone_number") or doctor.get("username", "")
    password = doctor["password"]

    log.info("AUTH → POST %s", url)
    log.info("AUTH   phone_number: %s", phone_number)

    payload = {
        "phone_number": phone_number,
        "password": password,
    }

    try:
        resp = requests.post(url, json=payload, timeout=30)
    except requests.exceptions.ConnectionError as exc:
        raise AuthError(f"AUTH_FAILED: Cannot connect to {url} — {exc}") from exc
    except requests.exceptions.Timeout as exc:
        raise AuthError(f"AUTH_FAILED: Request to {url} timed out after 30s") from exc

    log.info("AUTH ← %s", resp.status_code)
    log.info("AUTH   response: %s", resp.text[:300])

    if resp.status_code == 401:
        raise AuthError(f"AUTH_FAILED: Bad credentials (401) for phone_number={phone_number}")
    if resp.status_code == 404:
        raise AuthError(
            f"AUTH_FAILED: Endpoint not found (404) — check django_base_url and login_path in config\n"
            f"URL tried: {url}"
        )
    if resp.status_code != 200:
        raise AuthError(
            f"AUTH_FAILED: Unexpected status {resp.status_code}\n"
            f"URL: {url}\n"
            f"Response: {resp.text[:300]}"
        )

    data = resp.json()

    token = data.get("access")
    if not token:
        raise AuthError(
            f"AUTH_FAILED: No 'access' field in response. "
            f"Got keys: {list(data.keys())}"
        )

    user = data.get("user", {})
    doctor_id = (
        user.get("doctor_id")
        or user.get("id")
        or user.get("user_id")
        or data.get("doctor_id")
        or doctor.get("id")
    )

    runtime["access_token"] = token
    runtime["refresh_token"] = data.get("refresh")
    runtime["doctor_id"] = str(doctor_id)

    log.info("AUTH ✓ success — doctor_id: %s, token: %s...", doctor_id, token[:20])
    return token, str(doctor_id)


def refresh_access_token(config: dict) -> str:
    """
    POST /api/token/refresh/
    Request:  { "refresh": "..." }
    Response: { "access": "...", "refresh": "..." }
    """
    runtime = get_runtime()
    base_url = config["backends"]["django_base_url"].rstrip("/")
    url = f"{base_url}/api/token/refresh/"
    refresh_token = runtime.get("refresh_token")

    if not refresh_token:
        raise AuthError("No refresh token available — re-authenticate")

    resp = requests.post(url, json={"refresh": refresh_token}, timeout=30)

    if resp.status_code != 200:
        raise AuthError(f"Token refresh failed: {resp.status_code} {resp.text[:200]}")

    data = resp.json()
    runtime["access_token"] = data["access"]
    if "refresh" in data:
        runtime["refresh_token"] = data["refresh"]

    log.info("AUTH ✓ token refreshed")
    return data["access"]


def fetch_doctor_profile(doctor_id: str, token: str, config: dict) -> dict[str, str]:
    """GET /api/user/update/{id}/ — doctor name, department, hospital."""
    doctor_cfg = config.get("doctor", {})
    fallback = {
        "name": doctor_cfg.get("name", ""),
        "department": doctor_cfg.get("department", ""),
        "hospital_name": doctor_cfg.get("hospital_name", ""),
    }

    url = f"{_django_base(config)}/api/user/update/{doctor_id}/"
    try:
        resp = requests.get(url, headers=_auth_headers(token), timeout=15)
        if resp.status_code != 200:
            log.warning("DOCTOR_PROFILE ← %s — using config fallback", resp.status_code)
            return fallback

        prof = resp.json()
        name = f"Dr {prof.get('firstname', '')} {prof.get('lastname', '')}".strip()
        if not name or name == "Dr":
            name = fallback["name"]
        return {
            "name": name or fallback["name"],
            "department": prof.get("department") or fallback["department"],
            "hospital_name": prof.get("hospital_name") or fallback["hospital_name"],
        }
    except Exception as exc:
        log.warning("DOCTOR_PROFILE failed: %s — using config fallback", exc)
        return fallback


def verify_patient(patient_id: str, token: str, config: dict) -> dict:
    base_url = config["backends"]["django_base_url"].rstrip("/")
    patient_path = config["backends"].get("patient_path", "/api/patient-data/")
    url = f"{base_url}{patient_path}{patient_id}/"

    log.info("PATIENT → GET %s", url)

    resp = requests.get(url, headers=_auth_headers(token), timeout=30)

    log.info("PATIENT ← %s", resp.status_code)
    log.info("PATIENT   response: %s", resp.text[:300])

    if resp.status_code == 404:
        raise PatientNotFoundError(
            f"PATIENT_NOT_FOUND: Patient ID '{patient_id}' not found.\n"
            f"URL tried: {url}\n"
            f"Check patient.id in medsum_config.yaml"
        )
    if resp.status_code == 401:
        raise PatientNotFoundError(
            f"PATIENT_UNAUTHORIZED: Token rejected for patient lookup.\n"
            f"URL: {url}\n"
            f"Check that Bearer token is being sent correctly."
        )
    if resp.status_code != 200:
        raise PatientNotFoundError(
            f"PATIENT_ERROR: {resp.status_code} for {url}\n"
            f"Response: {resp.text[:200]}"
        )

    data = resp.json()
    log.info(
        "PATIENT ✓ found — patient_id=%s, name=%s, hospital_id=%s",
        data.get("patient_id"),
        data.get("patient_name"),
        data.get("hospital_id"),
    )
    return data


AUDIO_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".mpeg": "audio/mpeg",
    ".wav": "audio/wav",
    ".wave": "audio/wav",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
    ".aac": "audio/aac",
}


def _audio_upload_meta(audio_filename: str) -> tuple[str, str]:
    """Keep the original extension and MIME type; do not force .mp3 / audio/mpeg."""
    name = audio_filename or "audio.mp3"
    dot = name.rfind(".")
    ext = name[dot:].lower() if dot >= 0 else ""
    if ext in AUDIO_CONTENT_TYPES:
        return name, AUDIO_CONTENT_TYPES[ext]
    if ext:
        return name, "application/octet-stream"
    return f"{name}.mp3", "audio/mpeg"


def upload_audio(
    audio_bytes: bytes,
    audio_filename: str,
    patient_id: str,
    language: str,
    token: str,
    config: dict,
    user_id: str,
    file_duration: str | None = None,
) -> tuple[str, str]:
    """
    Upload audio to Django.
    Returns: (session_id, audio_id)

    POST /api/audio-data/
    """
    url = f"{_django_base(config)}/api/audio-data/"
    lang = normalize_language(language)

    log.info("AUDIO_UPLOAD → POST %s", url)
    log.info(
        "  patient_id=%s, user_id=%s, language=%s, filename=%s, size=%d bytes",
        patient_id,
        user_id,
        lang,
        audio_filename,
        len(audio_bytes),
    )

    upload_name, content_type = _audio_upload_meta(audio_filename)
    files = {
        "audio": (upload_name, audio_bytes, content_type),
    }
    data: dict[str, str] = {
        "patient_id": str(patient_id),
        "user_id": str(user_id),
        "language": lang,
    }
    if file_duration:
        data["file_duration"] = str(file_duration)

    headers = {"Authorization": f"Bearer {token}"}

    try:
        resp = requests.post(url, files=files, data=data, headers=headers, timeout=120)
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(f"AUDIO_UPLOAD timeout after 120s for {url}") from exc

    log.info("AUDIO_UPLOAD ← %s", resp.status_code)
    log.info("AUDIO_UPLOAD   response: %s", resp.text[:500])

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"AUDIO_UPLOAD failed {resp.status_code}: {resp.text[:300]}"
        )

    data_resp = resp.json()
    session_id = str(data_resp.get("session_id", ""))
    audio_id = str(data_resp.get("audio_id", ""))

    if not session_id or not audio_id:
        raise RuntimeError(
            f"AUDIO_UPLOAD response missing session_id/audio_id: {data_resp}"
        )

    log.info("AUDIO_UPLOAD ✓ session_id=%s, audio_id=%s", session_id, audio_id)
    return session_id, audio_id


def transcribe_audio(
    audio_bytes: bytes,
    patient_data: dict,
    doctor_data: dict,
    language: str,
    config: dict,
) -> dict[str, Any]:
    """
    Send audio to Flask transcription service.
    POST {flask_transcribe_url}/transcribe
    """
    url = _flask_transcribe_url(config)
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    llm_settings = config.get("llm_settings", {})
    lang = normalize_language(language)

    payload = {
        "doctor_name": doctor_data.get("name", ""),
        "doctor_department": doctor_data.get("department", ""),
        "hospital_name": doctor_data.get("hospital_name", ""),
        "patient_id": str(patient_data.get("patient_id", "")),
        "patient_name": patient_data.get("patient_name", ""),
        "age": str(patient_data.get("age", "")),
        "gender": patient_data.get("gender", ""),
        "template": copy.deepcopy(SOAP_CONSULT_TEMPLATE),
        "template_id": int(llm_settings.get("template_id", 4)),
        "audio_base64": audio_b64,
        "isaudio": True,
        "language": lang,
        "llm": llm_settings.get("llm_model", "OpenAI"),
        "stt_model": llm_settings.get("stt_model", "Bhasini"),
        "translate_model": llm_settings.get("translation_type", "Bhasini"),
    }

    log.info("TRANSCRIBE → POST %s", url)
    log_payload = {k: v for k, v in payload.items() if k != "audio_base64"}
    log_payload["audio_base64"] = f"<{len(audio_b64)} chars>"
    log.info("TRANSCRIBE   payload: %s", log_payload)

    try:
        resp = requests.post(url, json=payload, timeout=300)
    except requests.exceptions.Timeout as exc:
        raise RuntimeError("TRANSCRIBE timeout after 300s — audio may be too large") from exc

    log.info("TRANSCRIBE ← %s", resp.status_code)

    if resp.status_code != 200:
        raise RuntimeError(
            f"TRANSCRIBE failed {resp.status_code}: {resp.text[:300]}"
        )

    result = resp.json()
    transcription = result.get("transcription", "")
    log.info(
        "TRANSCRIBE ✓ transcription=%d chars, keys=%s",
        len(transcription),
        list(result.keys()),
    )
    return result


def save_summary(
    session_id: str,
    audio_id: str,
    patient_id: str,
    user_id: str,
    transcription_result: dict,
    token: str,
    config: dict,
) -> dict:
    """
    Save the transcription result as a summary in Django.
    POST /api/summary-data/
    """
    url = f"{_django_base(config)}/api/summary-data/"
    llm_settings = config.get("llm_settings", {})
    transcription = transcription_result.get("transcription", "")
    summary_text = _extract_summary(transcription_result)
    template_id = llm_settings.get("template_id", 4)

    payload = {
        "session_id": session_id,
        "user_id": int(user_id) if str(user_id).isdigit() else user_id,
        "patient_id": int(patient_id) if str(patient_id).isdigit() else patient_id,
        "audio_id": audio_id,
        "transcription": transcription,
        "summary": summary_text,
        "summary_length": len(summary_text),
        "template_id": template_id,
        "original_summary": summary_text,
        "is_approved": "No",
        "is_update": "False",
        "google_doc_link": None,
    }

    log.info("SAVE_SUMMARY → POST %s", url)
    log.info("  session_id=%s, audio_id=%s", session_id, audio_id)

    resp = requests.post(url, json=payload, headers=_auth_headers(token), timeout=60)

    log.info("SAVE_SUMMARY ← %s", resp.status_code)
    log.info("SAVE_SUMMARY   response: %s", resp.text[:300])

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"SAVE_SUMMARY failed {resp.status_code}: {resp.text[:200]}"
        )

    result = resp.json()
    log.info("SAVE_SUMMARY ✓ summary_id=%s", result.get("summary_id"))
    return result


def fetch_summary(session_id: str, token: str, config: dict) -> dict:
    """
    Fetch saved summary by session_id.
    GET /api/summary-data/?session_id=<id>
    """
    url = f"{_django_base(config)}/api/summary-data/"

    log.info("FETCH_SUMMARY → GET %s?session_id=%s", url, session_id)

    resp = requests.get(
        url,
        params={"session_id": session_id},
        headers=_auth_headers(token),
        timeout=30,
    )

    log.info("FETCH_SUMMARY ← %s", resp.status_code)

    if resp.status_code != 200:
        log.warning("FETCH_SUMMARY failed %s: %s", resp.status_code, resp.text[:200])
        return {}

    data = resp.json()
    summaries = data.get("summaries", [])
    result = summaries[0] if summaries else {}
    log.info("FETCH_SUMMARY ✓ found %d summaries", len(summaries))
    return result


def fetch_audio_data(session_id: str, token: str, config: dict) -> dict:
    """
    Fetch audio/medication data by session_id.
    GET /api/audio-data/?session_id=<id>
    """
    url = f"{_django_base(config)}/api/audio-data/"

    log.info("FETCH_AUDIO_DATA → GET %s?session_id=%s", url, session_id)

    resp = requests.get(
        url,
        params={"session_id": session_id},
        headers=_auth_headers(token),
        timeout=30,
    )

    log.info("FETCH_AUDIO_DATA ← %s", resp.status_code)

    if resp.status_code != 200:
        log.warning("FETCH_AUDIO_DATA failed %s: %s", resp.status_code, resp.text[:200])
        return {}

    data = resp.json()
    records = data if isinstance(data, list) else data.get("results", data.get("audio_data", []))
    if isinstance(records, list) and records:
        log.info("FETCH_AUDIO_DATA ✓ found %d records", len(records))
        return records[0] if isinstance(records[0], dict) else {"records": records}

    log.info("FETCH_AUDIO_DATA ✓ response keys=%s", list(data.keys()) if isinstance(data, dict) else type(data))
    return data if isinstance(data, dict) else {}


def get_runtime_state() -> dict:
    """Return the current auth runtime state."""
    return get_runtime()


def create_batch(
    batch_id: str,
    ai_model: str,
    config: dict,
    token: str,
    total_files: int = 0,
) -> str:
    """
    POST /api/accuracy-testing/batches/create/
    Returns batch_ref (e.g. BATCH-20260813-00001)
    """
    base_url = config["backends"]["django_base_url"].rstrip("/")
    path = config["backends"].get(
        "at_batch_create", "/api/accuracy-testing/batches/create/"
    )
    url = f"{base_url}{path}"

    payload = {
        "batch_id": batch_id,
        "ai_model": ai_model,
        "total_files": total_files,
    }

    log.info("CREATE_BATCH → POST %s batch_id=%s", url, batch_id)
    try:
        resp = requests.post(
            url,
            json=payload,
            headers=_auth_headers(token),
            timeout=30,
        )
        log.info("CREATE_BATCH ← %s", resp.status_code)
        if resp.status_code in (200, 201):
            data = resp.json()
            log.info("CREATE_BATCH ✓ batch_ref=%s", data.get("batch_ref"))
            return data.get("batch_ref", "")
        log.warning(
            "CREATE_BATCH failed %s: %s",
            resp.status_code,
            resp.text[:200],
        )
    except Exception as exc:
        log.warning("CREATE_BATCH error: %s", exc)
    return ""


def save_test_run(payload: dict, token: str, config: dict) -> dict:
    """
    POST /api/accuracy-testing/runs/create/
    Upsert test run in Django DB.
    Non-fatal — logs warning on failure, never raises.
    """
    if not config.get("features", {}).get("save_to_django_db", True):
        log.info("SAVE_TEST_RUN: skipped (save_to_django_db=false in config)")
        return payload

    base_url = config["backends"]["django_base_url"].rstrip("/")
    run_path = config["backends"].get(
        "at_run_create", "/api/accuracy-testing/runs/create/"
    )
    url = f"{base_url}{run_path}"

    log.info(
        "SAVE_TEST_RUN → POST %s status=%s test_id=%s",
        url,
        payload.get("status"),
        payload.get("test_id"),
    )

    try:
        resp = requests.post(
            url,
            json=payload,
            headers=_auth_headers(token),
            timeout=30,
        )
        log.info("SAVE_TEST_RUN ← %s", resp.status_code)
        if resp.status_code in (200, 201):
            log.info("SAVE_TEST_RUN ✓ saved to Django DB")
            return resp.json()
        log.warning(
            "SAVE_TEST_RUN failed %s: %s",
            resp.status_code,
            resp.text[:200],
        )
    except Exception as exc:
        log.warning("SAVE_TEST_RUN error (non-fatal): %s", exc)

    return payload
