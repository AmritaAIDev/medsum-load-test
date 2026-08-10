"""Calls MedSum Django + Flask APIs."""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any, Optional

import requests

SOAP_TEMPLATE = {
    "subjective": {
        "chief_complaint": "",
        "history_of_present_illness": "",
        "past_medical_history": "",
        "medications": "",
        "allergies": "",
        "social_history": "",
        "family_history": "",
    },
    "objective": {
        "vitals": {
            "blood_pressure": "",
            "heart_rate": "",
            "respiratory_rate": "",
            "temperature": "",
        },
        "physical_exam": {"heart": []},
    },
    "assessment": [],
    "plan": {
        "medications": [
            {
                "drug_name": "",
                "dose": "",
                "schedule": "",
                "duration": "",
                "instructions": "",
                "snomed_ct_id": "",
            }
        ],
        "activity": "",
        "investigations": "",
        "education": "",
        "follow_up": "",
    },
    "summary": "",
}

DISCHARGE_TEMPLATE = {
    "Patient_Details": {
        "Name": "",
        "Age": "",
        "Gender": "",
        "Blood_Group": "",
        "ABHA_ID": "",
    },
    "Admission_Details": {
        "Date_of_Admission": "",
        "Date_of_Discharge": "",
        "Ward": "",
        "Bed_Number": "",
        "Consultant": "",
        "Department": "",
    },
    "Presenting_Complaints": "",
    "History_of_Presenting_Illness": "",
    "Past_Medical_History": "",
    "Personal_History": "",
    "Family_History": "",
    "Examination_Findings": {
        "General_Examination": "",
        "Systemic_Examination": "",
    },
    "Investigations": "",
    "Diagnosis": {"Primary_Diagnosis": "", "Secondary_Diagnosis": ""},
    "Course_in_the_Hospital_and_Discussion": "",
    "Operative_or_Procedure_Findings": "",
    "Prognosis_on_Discharge": "",
    "Diet_Recommendation": "",
    "Physiscal_Activity": "",
    "Discharge_Medication": {
        "Serial_Number": [],
        "Drug_Name": [],
        "Dose": [],
        "Schedule": [],
        "Duration": [],
        "Days": [],
        "Instructions": [],
    },
    "Follow_Up": "",
    "summary": "",
}

KNOWN_META_KEYS = {
    "doctor_details",
    "patient_demographics",
    "session",
    "transcription",
    "transcription-time",
    "translation-time",
    "llm-time",
    "audio_length",
    "total-time",
    "medicines_backend",
}


def _django_base(config: dict) -> str:
    return config["backends"]["django_base_url"].rstrip("/")


def _flask_url(config: dict) -> str:
    url = config["backends"]["flask_transcribe_url"].rstrip("/")
    if not url.endswith("/transcribe"):
        url += "/transcribe"
    return url


def _language_code(language: str) -> str:
    mapping = {
        "hindi": "hi",
        "english": "en",
        "tamil": "ta",
        "telugu": "te",
        "kannada": "kn",
        "malayalam": "ml",
        "bengali": "bn",
        "marathi": "mr",
    }
    return mapping.get(language.lower(), language[:2].lower() if language else "en")


def _extract_summary(body: dict) -> Any:
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


def authenticate_doctor(config: dict) -> tuple[str, str]:
    """Return (auth_token, doctor_id). Updates config doctor.auth_token if fetched."""
    doctor = config["doctor"]
    if doctor.get("auth_token"):
        return doctor["auth_token"], str(doctor["id"])

    django = _django_base(config)
    username = doctor.get("username") or doctor.get("phone_number", "")
    password = doctor["password"]

    login_payloads = [
        {"phone_number": username, "password": password},
        {"username": username, "password": password},
        {"email": username, "password": password},
    ]

    last_error = ""
    for payload in login_payloads:
        try:
            resp = requests.post(f"{django}/api/login/", json=payload, timeout=30)
            if resp.status_code == 200:
                body = resp.json()
                token = body.get("access") or body.get("token")
                doctor_id = str(body.get("user", {}).get("id") or doctor.get("id"))
                doctor["auth_token"] = token
                return token, doctor_id
            last_error = f"{resp.status_code}: {resp.text[:200]}"
        except Exception as exc:
            last_error = str(exc)

    raise RuntimeError(f"Doctor authentication failed: {last_error}")


def get_doctor_profile(config: dict, doctor_id: str, token: str) -> dict[str, str]:
    django = _django_base(config)
    name = department = hospital = ""
    try:
        resp = requests.get(
            f"{django}/api/user/update/{doctor_id}/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            prof = resp.json()
            name = f"Dr {prof.get('firstname', '')} {prof.get('lastname', '')}".strip()
            department = prof.get("department", "")
            hospital = prof.get("hospital_name", "")
    except Exception:
        pass
    return {"doctor_name": name, "department": department, "hospital": hospital}


def get_patient_details(config: dict, patient_id: str, token: str) -> dict[str, str]:
    django = _django_base(config)
    try:
        resp = requests.get(
            f"{django}/api/patient-data/{patient_id}/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            pd = resp.json()
            return {
                "patient_name": pd.get("patient_name", ""),
                "age": str(pd.get("age", "")),
                "gender": pd.get("gender", ""),
            }
    except Exception:
        pass
    return {"patient_name": "", "age": "", "gender": ""}


def get_or_create_session(
    doctor_id: str, patient_id: str, token: str, config: dict
) -> str:
    """Return session_id — uses client-generated ID if no dedicated endpoint exists."""
    return f"MEDSUMTEST_{uuid.uuid4()}"


def submit_audio(
    audio_bytes: bytes,
    audio_filename: str,
    session_id: str,
    config: dict,
    token: str,
    language: str,
    duration_seconds: int,
    doctor_id: str,
    patient_id: str,
) -> dict[str, Any]:
    """
    Submit audio through Flask transcribe pipeline and archive via Django.
    Returns dict with job_id, transcription, summary, translation, medications, flask_body.
    """
    django = _django_base(config)
    flask_url = _flask_url(config)
    llm_cfg = config.get("llm_settings", {})

    profile = get_doctor_profile(config, doctor_id, token)
    patient = get_patient_details(config, patient_id, token)

    template_type = llm_cfg.get("template_type", "soap")
    template = SOAP_TEMPLATE if template_type == "soap" else DISCHARGE_TEMPLATE
    lang_code = _language_code(language)

    resp = requests.post(
        flask_url,
        json={
            "audio_base64": base64.b64encode(audio_bytes).decode(),
            "doctor_name": profile["doctor_name"],
            "doctor_department": profile["department"],
            "hospital_name": profile["hospital"],
            "patient_id": str(patient_id),
            "patient_name": patient["patient_name"],
            "age": patient["age"],
            "gender": patient["gender"],
            "template": json.dumps(template),
            "template_id": str(llm_cfg.get("template_id", "1")),
            "language": lang_code,
            "llm": llm_cfg.get("llm", "OpenAI"),
            "stt_model": llm_cfg.get("stt_model", "Bhasini"),
            "translate_model": llm_cfg.get("translate_model", "Bhasini"),
        },
        timeout=300,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Transcribe failed {resp.status_code}: {resp.text[:300]}")

    flask_body = resp.json()
    transcription = flask_body.get("transcription", "")
    summary = _extract_summary(flask_body)
    translation = flask_body.get("translation") or flask_body.get("translated_text") or ""

    sess = requests.Session()
    sess.headers["Authorization"] = f"Bearer {token}"
    upload = sess.post(
        f"{django}/api/audio-data/",
        data={
            "user_id": str(doctor_id),
            "patient_id": str(patient_id),
            "language": lang_code,
            "file_duration": str(duration_seconds),
            "session_id": session_id,
        },
        files={"audio": (audio_filename, audio_bytes, "audio/mpeg")},
        timeout=60,
    )
    if upload.status_code != 201:
        raise RuntimeError(f"Audio upload failed {upload.status_code}: {upload.text[:300]}")

    upload_body = upload.json()
    audio_id = upload_body.get("audio_id")
    session_id = upload_body.get("session_id") or session_id
    summary_text = summary if isinstance(summary, str) else json.dumps(summary, ensure_ascii=False)

    store = sess.post(
        f"{django}/api/summary-data/",
        json={
            "user_id": doctor_id,
            "patient_id": int(patient_id) if str(patient_id).isdigit() else patient_id,
            "audio_id": audio_id,
            "session_id": session_id,
            "summary": summary_text,
            "summary_length": len(summary_text),
            "transcription": transcription,
            "template_id": str(llm_cfg.get("template_id", "1")),
            "original_summary": summary_text,
            "is_approved": "No",
            "is_update": "False",
            "google_doc_link": None,
        },
        timeout=30,
    )
    if store.status_code != 201:
        raise RuntimeError(f"Summary store failed {store.status_code}: {store.text[:300]}")

    summary_id = store.json().get("summary_id")
    job_id = str(summary_id or audio_id or session_id)

    return {
        "job_id": job_id,
        "session_id": session_id,
        "audio_id": audio_id,
        "summary_id": summary_id,
        "transcription": transcription,
        "summary": summary,
        "translation": translation,
        "flask_body": flask_body,
    }


def fetch_transcription(
    session_id: str,
    token: str,
    config: dict,
    retries: Optional[int] = None,
    buffer: Optional[int] = None,
    cached: str = "",
) -> tuple[str, int]:
    """Wait buffer seconds then retry fetching transcription. Returns (text, retry_count)."""
    settings = config.get("test_settings", {})
    max_retries = retries if retries is not None else settings.get("max_retries", 3)
    retry_interval = settings.get("retry_interval_seconds", 5)
    buffer_seconds = buffer if buffer is not None else settings.get("buffer_seconds", 10)

    if cached:
        time.sleep(buffer_seconds)
        return cached, 0

    time.sleep(buffer_seconds)
    django = _django_base(config)
    last_text = ""
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(
                f"{django}/api/session/{session_id}/transcription/",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            if resp.status_code == 200:
                body = resp.json()
                last_text = body.get("transcription") or body.get("text") or ""
                if last_text.strip():
                    return last_text, attempt
        except Exception:
            pass
        if attempt < max_retries:
            time.sleep(retry_interval)

    return last_text, max_retries


def fetch_summary(session_id: str, token: str, config: dict, cached: Any = None) -> dict:
    if cached is not None:
        return {"summary": cached}

    django = _django_base(config)
    try:
        resp = requests.get(
            f"{django}/api/session/{session_id}/summary/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {"summary": cached or ""}


def fetch_medications(
    session_id: str, token: str, config: dict, flask_body: Optional[dict] = None
) -> dict:
    meds: dict[str, Any] = {
        "before": None,
        "after_normalization": None,
        "generated": None,
    }

    if flask_body:
        backend_meds = flask_body.get("medicines_backend") or flask_body.get("medications")
        if backend_meds:
            meds["generated"] = backend_meds

    django = _django_base(config)
    try:
        resp = requests.get(
            f"{django}/api/session/{session_id}/medications/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code == 200:
            body = resp.json()
            meds["before"] = body.get("before") or body.get("medications_before")
            meds["after_normalization"] = body.get("after") or body.get(
                "medications_after_normalization"
            )
            if not meds["generated"]:
                meds["generated"] = body.get("generated") or body.get("medications_generated")
    except Exception:
        pass

    return meds
