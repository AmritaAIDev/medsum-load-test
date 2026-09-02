"""Doctor → patient assignment for an accuracy test run.

Rule: each doctor may be linked to at most one patient in a single run.

Legacy configs that already list multiple patients for one doctor are not
truncated here. The UI must show every stored ID with a note, and the run
API rejects the payload until extras are removed by the user.
"""

from __future__ import annotations

MAX_PATIENTS_PER_DOCTOR = 1

TOO_MANY_PATIENTS_MSG = (
    "Each doctor can be linked to only one patient in a test run"
)

# Early GET /api/patient-data/{id}/ on Add Patient is skipped: that endpoint
# requires a doctor JWT, so each Add would extra-authenticate and still could
# not run when Phone/Password are empty. A bad ID stays a Run All concern
# rather than an extra request per Add.
LOOKUP_PATIENT_ON_ADD = False

PATIENT_REQUIRED_MSG = "Patient ID is required"
PATIENT_NUMERIC_MSG = "Patient ID must be numeric"
CREDENTIALS_SAVED_LABEL = "Saved"


class DoctorPatientError(ValueError):
    """Invalid doctor–patient assignment for a test run."""


def normalize_patient_ids(raw) -> list[str]:
    """Preserve order; drop blanks and duplicates. Never invent IDs."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [p.strip() for p in raw.split(",")]
    else:
        items = [str(p).strip() for p in raw]
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def add_patient_controls_visible(patient_count: int, changing: bool = False) -> bool:
    """Add Patient is reachable only when the slot is empty or replacing."""
    return int(patient_count or 0) == 0 or bool(changing)


def is_numeric_patient_id(raw) -> bool:
    val = str(raw if raw is not None else "").strip()
    return bool(val) and val.isdigit()


def patient_saved_message(patient_id) -> str:
    return f"Patient {str(patient_id or '').strip()} saved"


def patient_add_validation_error(raw, existing, *, replace: bool = False) -> str | None:
    """Client-side Add Patient errors. Does not call GET /api/patient-data/."""
    val = str(raw if raw is not None else "").strip()
    if not val:
        return PATIENT_REQUIRED_MSG
    if not is_numeric_patient_id(val):
        return PATIENT_NUMERIC_MSG
    try:
        assign_patient(existing, val, replace=replace)
    except DoctorPatientError as exc:
        return str(exc)
    return None


def credentials_look_saved(phone, password) -> bool:
    """Phone + Password are populated enough to show a row-level Saved state."""
    p = str(phone or "").strip()
    w = str(password or "")
    return bool(p) and bool(w) and any(ch.isdigit() for ch in p)


def assign_patient(
    existing,
    new_id: str,
    *,
    replace: bool = False,
) -> list[str]:
    """Attach one patient ID to a doctor. Rejects a second assignment."""
    ids = normalize_patient_ids(existing)
    val = str(new_id or "").strip()
    if not val:
        raise DoctorPatientError("Patient ID is required")
    if replace:
        return [val]
    if val in ids:
        raise DoctorPatientError("Patient ID already assigned to this doctor")
    if len(ids) >= MAX_PATIENTS_PER_DOCTOR:
        raise DoctorPatientError(TOO_MANY_PATIENTS_MSG)
    return ids + [val]


def validate_doctors_one_patient(doctors: list[dict]) -> None:
    """Reject a run payload where any doctor has more than one patient."""
    for doctor in doctors or []:
        patients = normalize_patient_ids((doctor or {}).get("patients"))
        if len(patients) > MAX_PATIENTS_PER_DOCTOR:
            phone = str((doctor or {}).get("phone") or "").strip() or "this doctor"
            raise DoctorPatientError(f"{TOO_MANY_PATIENTS_MSG} ({phone})")
