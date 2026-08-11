"""Calls MedSum Django + Flask APIs."""

from __future__ import annotations

import time
from datetime import date
from typing import Any, Optional

import requests

from medsum_testing.backend.services.config_loader import clear_runtime, get_runtime


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
    return {"Authorization": f"Token {token}"}


def authenticate_doctor(config: dict) -> tuple[str, str]:
    """Authenticate once per test run. Raises AuthError on failure (no retry)."""
    clear_runtime()
    runtime = get_runtime()

    django = _django_base(config)
    doctor = config["doctor"]
    phone = doctor.get("username") or doctor.get("phone", "")
    password = doctor["password"]

    resp = requests.post(
        f"{django}/api/auth/login/",
        json={"phone": phone, "password": password},
        timeout=30,
    )
    if resp.status_code != 200:
        raise AuthError(f"AUTH_FAILED: login returned {resp.status_code}: {resp.text[:200]}")

    body = resp.json()
    token = body.get("token")
    if not token:
        raise AuthError("AUTH_FAILED: no token in login response")

    doctor_id = str(body.get("doctor_id") or body.get("user_id") or doctor.get("id"))
    runtime["auth_token"] = token
    runtime["doctor_id"] = doctor_id
    return token, doctor_id


def verify_patient(patient_id: str, token: str, config: dict) -> dict:
    django = _django_base(config)
    resp = requests.get(
        f"{django}/api/patients/{patient_id}/",
        headers=_auth_headers(token),
        timeout=30,
    )
    if resp.status_code == 404:
        raise PatientNotFoundError("PATIENT_NOT_FOUND")
    if resp.status_code != 200:
        raise RuntimeError(f"Patient lookup failed {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def get_or_create_session(
    doctor_id: str, patient_id: str, token: str, config: dict
) -> str:
    """Create a new consultation session tagged as an automated test."""
    verify_patient(patient_id, token, config)

    django = _django_base(config)
    resp = requests.post(
        f"{django}/api/consultations/",
        headers=_auth_headers(token),
        json={
            "patient": patient_id,
            "doctor": doctor_id,
            "date": date.today().isoformat(),
            "notes": "MEDSUM_AUTO_TEST",
        },
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Session creation failed {resp.status_code}: {resp.text[:200]}"
        )

    body = resp.json()
    session_id = body.get("id")
    if session_id is None:
        raise RuntimeError("Session creation response missing id")
    return str(session_id)


def submit_audio(
    audio_bytes: bytes,
    audio_filename: str,
    session_id: str,
    config: dict,
    language: str,
    doctor_id: str,
    patient_id: str,
) -> dict[str, Any]:
    """Upload audio to Flask transcribe endpoint. Returns job_id."""
    flask_url = _flask_transcribe_url(config)
    llm_cfg = config.get("llm_settings", {})

    resp = requests.post(
        flask_url,
        files={"audio_file": (audio_filename, audio_bytes, "audio/mpeg")},
        data={
            "session_id": session_id,
            "doctor_id": doctor_id,
            "patient_id": patient_id,
            "language": language,
            "stt_model": llm_cfg.get("stt_model", "whisper-1"),
            "translation_type": llm_cfg.get("translation_type", "auto"),
        },
        timeout=120,
    )
    if resp.status_code not in (200, 201, 202):
        raise RuntimeError(f"Audio upload failed {resp.status_code}: {resp.text[:300]}")

    body = resp.json()
    job_id = body.get("job_id")
    if not job_id:
        raise RuntimeError(f"Flask upload response missing job_id: {body}")
    return {"job_id": job_id, "status": body.get("status", "processing")}


def fetch_transcription(
    job_id: str,
    config: dict,
    retries: Optional[int] = None,
    buffer: Optional[int] = None,
) -> tuple[str, Optional[str], int]:
    """
    Poll Flask for transcription completion.
    Returns (transcription, translation, retries_used).
    """
    settings = config.get("test_settings", {})
    max_retries = retries if retries is not None else settings.get("max_retries", 3)
    retry_interval = settings.get("retry_interval_seconds", 5)
    buffer_seconds = buffer if buffer is not None else settings.get("buffer_seconds", 10)

    flask_url = _flask_transcribe_url(config)
    status_url = f"{flask_url}/status/{job_id}"

    time.sleep(buffer_seconds)

    for attempt in range(max_retries):
        resp = requests.get(status_url, timeout=30)
        if resp.status_code != 200:
            if attempt < max_retries - 1:
                time.sleep(retry_interval)
                continue
            raise RuntimeError(
                f"Transcription status failed {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        if data.get("status") == "complete":
            return (
                data.get("transcription") or "",
                data.get("translation"),
                attempt + 1,
            )
        time.sleep(retry_interval)

    raise TimeoutError(f"Transcription not ready after {max_retries} retries")


def store_transcription(
    session_id: str,
    transcription: str,
    translation: Optional[str],
    language: str,
    token: str,
    config: dict,
) -> dict:
    """POST transcription to Django — triggers LLM summary generation."""
    django = _django_base(config)
    llm_cfg = config.get("llm_settings", {})

    resp = requests.post(
        f"{django}/api/consultations/{session_id}/transcription/",
        headers=_auth_headers(token),
        json={
            "transcription": transcription,
            "translation": translation,
            "stt_model": llm_cfg.get("stt_model", "whisper-1"),
            "language": language,
        },
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Store transcription failed {resp.status_code}: {resp.text[:200]}"
        )
    return resp.json()


def fetch_summary(
    session_id: str,
    token: str,
    config: dict,
    retries: Optional[int] = None,
    buffer: Optional[int] = None,
) -> dict:
    """Poll Django for summary completion."""
    settings = config.get("test_settings", {})
    max_retries = retries if retries is not None else settings.get("max_retries", 3)
    retry_interval = settings.get("retry_interval_seconds", 5)
    buffer_seconds = buffer if buffer is not None else settings.get("buffer_seconds", 10)

    django = _django_base(config)
    url = f"{django}/api/consultations/{session_id}/summary/"

    time.sleep(buffer_seconds)

    last_body: dict = {}
    for attempt in range(max_retries):
        resp = requests.get(url, headers=_auth_headers(token), timeout=30)
        if resp.status_code == 200:
            last_body = resp.json()
            if last_body.get("status") == "complete":
                return last_body
        time.sleep(retry_interval)

    if last_body:
        return last_body
    raise TimeoutError(f"Summary not ready after {max_retries} retries")


def fetch_medications(session_id: str, token: str, config: dict) -> dict:
    django = _django_base(config)
    resp = requests.get(
        f"{django}/api/consultations/{session_id}/medications/",
        headers=_auth_headers(token),
        timeout=30,
    )
    if resp.status_code != 200:
        return {
            "before": None,
            "after_normalization": None,
            "generated": None,
        }

    body = resp.json()
    return {
        "before": body.get("medications_before"),
        "after_normalization": body.get("medications_normalized"),
        "generated": body.get("medications_generated"),
    }
