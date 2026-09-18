"""Client session persistence policy for the MedSum testing UI.

A running batch is server-side truth: only ``currentBatchId`` is stored, then
GET /results/batch/{id} re-syncs Pending/Running/Done (Prompt 2) and re-arms
the poller while work remains.

Other categories, applied consistently:
- Doctor/patient form: persist serializable fields (phone, password, patients).
- File selection: reset on refresh. File blobs cannot round-trip; restoring
  Drive keys without the matching upload files would look selected but would
  not be what Run All submits.
- Dashboard selected batch IDs: persist, dropping IDs that no longer exist.
- Results/Latency table tab: persist per table source.
"""

from __future__ import annotations

from typing import Any

STORAGE_KEY_BATCH = "medsum.currentBatchId"
STORAGE_KEY_DOCTORS = "medsum.doctors"
STORAGE_KEY_LT_ROWS = "medsum.ltRows"
STORAGE_KEY_BATCH_FILTER = "medsum.selectedBatchIds"
STORAGE_KEY_TABLE_TAB = "medsum.resultsTableTab"

FILE_SELECTION_RESETS_ON_REFRESH = True

PHASE_PENDING = "pending"
PHASE_RUNNING = "running"
PHASE_DONE = "done"

VALID_TABLE_TABS = ("results", "latency")


def batch_watch_phase(payload: dict | None) -> str:
    """Prompt 2 batch phase from GET /results/batch/{id}.

    Running wins over Pending. Empty payloads (rows not written yet) stay
    Pending so a refresh immediately after Run All still re-arms the poller.
    Done only when the server reports no pending work and at least one row
    or a positive total.
    """
    data = payload or {}
    results = list(data.get("results") or [])
    statuses = [(row.get("status") or "").strip().lower() for row in results]
    if any(status == "running" for status in statuses):
        return PHASE_RUNNING
    try:
        pending_n = int(data.get("pending") if data.get("pending") is not None else 0)
    except (TypeError, ValueError):
        pending_n = 0
    if pending_n > 0 or any(status == "pending" for status in statuses):
        return PHASE_PENDING
    try:
        total_n = int(data["total"]) if data.get("total") is not None else len(results)
    except (TypeError, ValueError):
        total_n = len(results)
    if not results and total_n <= 0:
        return PHASE_PENDING
    return PHASE_DONE


def should_resume_polling(payload: dict | None) -> bool:
    """True while the stored batch is still Pending or Running."""
    return batch_watch_phase(payload) != PHASE_DONE


def should_clear_stored_batch(payload: dict | None, *, http_status: int | None = None) -> bool:
    """Clear localStorage currentBatchId only when the batch is Done.

    404/5xx are not Done — the first result JSON may not exist yet.
    """
    if http_status is not None and http_status != 200:
        return False
    return batch_watch_phase(payload) == PHASE_DONE


def doctor_form_snapshot(doctors: list | None) -> list[dict[str, Any]]:
    """Serializable doctor rows. Blank rows are omitted so restore stays clean."""
    out: list[dict[str, Any]] = []
    for row in doctors or []:
        if not isinstance(row, dict):
            continue
        phone = str(row.get("phone") or "").strip()
        password = str(row.get("password") or "")
        raw_patients = row.get("patients") or []
        if not isinstance(raw_patients, list):
            raw_patients = [raw_patients]
        patients = [str(item).strip() for item in raw_patients if str(item).strip()]
        if not phone and not password and not patients:
            continue
        out.append({"phone": phone, "password": password, "patients": patients})
    return out


def should_add_blank_doctor_row(snapshot: list | None) -> bool:
    """initPage adds a blank row only when there is nothing to restore."""
    return not doctor_form_snapshot(snapshot if snapshot is not None else [])


def lt_form_snapshot(rows: list | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        phone = str(row.get("phone") or "").strip()
        password = str(row.get("password") or "")
        patient_id = str(row.get("patientId") or row.get("patient_id") or "").strip()
        if not phone and not password and not patient_id:
            continue
        out.append({"phone": phone, "password": password, "patientId": patient_id})
    return out


def should_add_blank_lt_row(snapshot: list | None) -> bool:
    return not lt_form_snapshot(snapshot if snapshot is not None else [])


def restore_selected_batch_ids(stored: list | None, available: list | None) -> list[str]:
    """Keep only IDs that still exist. Stale IDs drop to All Batches (empty)."""
    avail = {str(item) for item in (available or []) if str(item)}
    kept: list[str] = []
    seen: set[str] = set()
    for item in stored or []:
        key = str(item or "").strip()
        if not key or key not in avail or key in seen:
            continue
        seen.add(key)
        kept.append(key)
    return kept


def normalize_table_tab(tab: str | None) -> str:
    value = str(tab or "").strip().lower()
    return "latency" if value == "latency" else "results"
