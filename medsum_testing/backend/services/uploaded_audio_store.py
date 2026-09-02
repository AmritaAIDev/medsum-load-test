"""Session-scoped storage for Test Run 'Upload Manually' audio bytes.

Design (choice a): files are POSTed to /api/medsum-test/upload-audio as soon as
they are dropped/selected. Run Batch Test stays JSON — the same selected_audios
shape the Drive path already uses — with an upload_id on source=upload rows.
That avoids switching run-all to multipart (which would disrupt Drive) while
still giving execute_test_run real bytes. Load Testing's request.files pattern
is the reference for receiving bytes; this store is the session hold.

Bytes stay on disk until the process ends or a record is unreferenced and past
TTL. A run marks the upload in-use so a batch/report can still re-read it
(View / replay) while that run is relevant. Drive discovery is never touched.
"""

from __future__ import annotations

import atexit
import logging
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger("medsum_uploaded_audio")

_LOCK = threading.Lock()
_RECORDS: dict[str, dict[str, Any]] = {}
_DIR: Path | None = None

# Keep unused uploads for a working session; sweep only after this + no refs.
_TTL_SECONDS = 6 * 60 * 60


def _store_dir() -> Path:
    global _DIR
    if _DIR is None:
        _DIR = Path(tempfile.mkdtemp(prefix="medsum_uploaded_audio_"))
        log.info("uploaded audio store dir=%s", _DIR)
    return _DIR


def reset_store() -> None:
    """Drop every record and temp file. Tests only."""
    global _DIR
    with _LOCK:
        _RECORDS.clear()
        if _DIR is not None and _DIR.exists():
            shutil.rmtree(_DIR, ignore_errors=True)
        _DIR = None


def store_upload(
    filename: str,
    data: bytes,
    *,
    language: str = "",
    content_type: str = "",
) -> dict[str, Any]:
    name = str(filename or "").strip()
    if not name:
        raise ValueError("filename is required")
    if not data:
        raise ValueError("audio file is empty")

    upload_id = str(uuid.uuid4())
    dest = _store_dir() / upload_id
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / Path(name).name
    path.write_bytes(data)
    now = time.time()
    record = {
        "upload_id": upload_id,
        "filename": name,
        "language": str(language or "").strip(),
        "path": str(path),
        "size_bytes": len(data),
        "content_type": str(content_type or ""),
        "created_at": now,
        "last_used": now,
        "ref_count": 0,
    }
    with _LOCK:
        _sweep_locked(now)
        _RECORDS[upload_id] = record
    log.info("stored upload %s name=%r bytes=%d", upload_id, name, len(data))
    return dict(record)


def get_upload(upload_id: str | None) -> dict[str, Any] | None:
    key = str(upload_id or "").strip()
    if not key:
        return None
    with _LOCK:
        rec = _RECORDS.get(key)
        return dict(rec) if rec else None


def find_upload(
    *,
    language: str = "",
    audio_filename: str = "",
    upload_id: str | None = None,
) -> dict[str, Any] | None:
    if upload_id:
        found = get_upload(upload_id)
        if found:
            return found
    want_name = str(audio_filename or "").strip().lower()
    want_lang = str(language or "").strip().lower()
    if not want_name:
        return None
    with _LOCK:
        for rec in _RECORDS.values():
            if str(rec.get("filename") or "").strip().lower() != want_name:
                continue
            rec_lang = str(rec.get("language") or "").strip().lower()
            if rec_lang == want_lang or not want_lang:
                return dict(rec)
    return None


def read_bytes(upload_id: str) -> bytes:
    rec = get_upload(upload_id)
    if not rec:
        raise FileNotFoundError(f"uploaded audio {upload_id} is not in the session store")
    path = Path(rec["path"])
    if not path.is_file():
        raise FileNotFoundError(f"uploaded audio {upload_id} file is missing")
    with _LOCK:
        stored = _RECORDS.get(str(upload_id))
        if stored:
            stored["last_used"] = time.time()
    return path.read_bytes()


def acquire(upload_id: str | None) -> dict[str, Any] | None:
    """Pin a record so cleanup will not delete it during an in-flight run."""
    key = str(upload_id or "").strip()
    if not key:
        return None
    with _LOCK:
        rec = _RECORDS.get(key)
        if not rec:
            return None
        rec["ref_count"] = int(rec.get("ref_count") or 0) + 1
        rec["last_used"] = time.time()
        return dict(rec)


def release(upload_id: str | None) -> None:
    key = str(upload_id or "").strip()
    if not key:
        return
    with _LOCK:
        rec = _RECORDS.get(key)
        if not rec:
            return
        rec["ref_count"] = max(0, int(rec.get("ref_count") or 0) - 1)
        rec["last_used"] = time.time()


def list_uploaded_cases() -> list[dict[str, Any]]:
    """Runnable case entries for session uploads. Status is always ready."""
    with _LOCK:
        records = [dict(rec) for rec in _RECORDS.values()]
    return [case_from_record(rec) for rec in records]


def case_from_record(record: dict[str, Any] | None, language: str | None = None) -> dict[str, Any]:
    rec = record or {}
    filename = str(rec.get("filename") or "").strip()
    lang = str(language if language is not None else rec.get("language") or "").strip()
    return {
        "language": lang,
        "folder_label": lang,
        "audio_filename": filename,
        "audio": filename,
        "source": "upload",
        "status": "ready",
        "upload_id": rec.get("upload_id") or "",
        "has_transcript": False,
        "has_soap_ground_truth": False,
        "has_translation_ground_truth": False,
        "is_english": lang.lower() in ("english", "en"),
        "ground_truth_flag": "no_ground_truth",
    }


def cases_for_selection(selected: list[dict] | None) -> list[dict[str, Any]]:
    """One ready case per source=upload row that still has stored bytes."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in selected or []:
        if str(item.get("source") or "drive") != "upload":
            continue
        audio = str(
            item.get("audio") or item.get("audio_filename") or item.get("filename") or ""
        ).strip()
        language = str(item.get("language") or item.get("folder_label") or "").strip()
        rec = find_upload(
            language=language,
            audio_filename=audio,
            upload_id=str(item.get("upload_id") or ""),
        )
        if not rec:
            continue
        uid = str(rec.get("upload_id") or "")
        if uid in seen:
            continue
        seen.add(uid)
        out.append(case_from_record(rec, language=language))
    return out


def _sweep_locked(now: float) -> None:
    expired = []
    for uid, rec in _RECORDS.items():
        if int(rec.get("ref_count") or 0) > 0:
            continue
        last = float(rec.get("last_used") or rec.get("created_at") or 0)
        if now - last >= _TTL_SECONDS:
            expired.append(uid)
    for uid in expired:
        _delete_locked(uid)


def _delete_locked(upload_id: str) -> None:
    rec = _RECORDS.pop(upload_id, None)
    if not rec:
        return
    dest = Path(rec["path"]).parent
    shutil.rmtree(dest, ignore_errors=True)
    log.info("swept uploaded audio %s", upload_id)


def _atexit_cleanup() -> None:
    reset_store()


atexit.register(_atexit_cleanup)
