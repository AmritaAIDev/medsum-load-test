"""Google Drive audio and transcript fetching."""

from __future__ import annotations

import io
import logging
import re
import time as _time
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from medsum_testing.backend.services.config_loader import get_config

log = logging.getLogger("medsum_drive")

_test_cases_cache: dict = {
    "data": None,
    "unmatched_gt": None,
    "timestamp": 0.0,
    "ttl": 300,
}

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

AUDIO_MIME_TYPES = {
    "audio/mpeg",
    "audio/mp4",
    "audio/x-m4a",
    "audio/wav",
    "audio/ogg",
    "audio/webm",
    "audio/aac",
}

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_CASE_HEADER_RE = re.compile(r"^[Cc]ase\s*\d*\s*:")

TRANSCRIPT_MIME_TYPES = {
    GOOGLE_DOC_MIME,
    DOCX_MIME,
    "text/plain",
}


def extract_language(folder_name: str) -> str:
    """
    '01_Hindi'   -> 'Hindi'
    '09_English' -> 'English'
    'Hindi'      -> 'Hindi'
    """
    match = re.match(r"^\d+_(.+)$", folder_name)
    if match:
        lang = match.group(1)
    else:
        lang = folder_name
    return lang.strip().capitalize()


def _strip_number_prefix(name: str) -> str:
    """Remove leading number+underscore prefix. '03_hindi_11' -> 'hindi_11'."""
    name = name.lower()
    return re.sub(r"^\d+_", "", name)


def _strip_script_suffix(name: str) -> str:
    """Remove transcript suffixes from a lowercased base name."""
    for suffix in ("_script", "_transcript", "_ground_truth", "_gt"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


SOAP_GT_SUFFIXES = ("_soap", "_soap.txt", "_soap.json")


def _is_soap_filename(filename: str) -> bool:
    """True when filename is a SOAP ground truth file (_soap, _soap.txt, etc.)."""
    return is_soap_gt(filename)


def is_soap_gt(filename: str) -> bool:
    """True when filename is a SOAP ground truth file."""
    lower = filename.lower()
    if any(lower.endswith(s) for s in SOAP_GT_SUFFIXES):
        return True
    for ext in (".txt", ".json", ".docx"):
        if lower.endswith(ext) and lower[: -len(ext)].endswith("_soap"):
            return True
    return False


TRANSLATION_GT_SUFFIXES = (
    "_translation.txt",
    "_translation",
    "_trans.txt",
    "_trans",
    "_english.txt",
    "_english",
)


def is_translation_gt(filename: str) -> bool:
    """Check if a file is a translation ground truth file."""
    name = filename.lower()
    return any(
        name.endswith(s) or name.endswith(s + ".txt")
        for s in ("_translation", "_trans", "_english")
    )


def _strip_known_ext(name: str) -> str:
    """Strip one trailing .txt/.json/.docx (after lowercasing)."""
    for ext in (".txt", ".json", ".docx"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def _normalize_duration_underscore(base: str) -> str:
    """english03 -> english_03. No-op when an underscore is already present."""
    stem = (base or "").strip()
    duration_match = re.match(r"^([a-z]+)(\d+)$", stem)
    if duration_match:
        return f"{duration_match.group(1)}_{duration_match.group(2)}"
    return stem


def get_translation_base(filename: str) -> str:
    """
    Strip translation suffix to get matching audio base name.
    '02_english_05_translation.txt' → 'english_05'
    Same duration/whitespace normalisation as _match_key.
    """
    name = _strip_known_ext((filename or "").strip().lower()).strip()
    for suffix in ("_translation", "_trans", "_english"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return _normalize_duration_underscore(_strip_number_prefix(name.strip()))


def get_soap_base(filename: str) -> str:
    """
    Strip soap suffix to get matching audio base name.
    '02_english_05_soap.txt' -> 'english_05'
    '02_english_05_soap'     -> 'english_05'
    'english03_soap.txt'     -> 'english_03'  (same duration rule as audio)
    """
    name = _strip_known_ext((filename or "").strip().lower()).strip()
    if name.endswith("_soap"):
        name = name[: -5]
    return _normalize_duration_underscore(_strip_number_prefix(name.strip()))


def _match_key(filename: str) -> str:
    """
    Normalised match key for pairing audio <-> transcript.
    Strips surrounding whitespace, one extension, number prefix, lowercases,
    strips _script/_transcript/_ground_truth/_gt suffix.
    Also normalises missing underscore before duration: english03 -> english_03.
    """
    base = re.sub(r"\.\w+$", "", (filename or "").strip()).strip()
    base = _strip_number_prefix(base)
    base = _strip_script_suffix(base)
    return _normalize_duration_underscore(base)


def get_drive_service(config: dict | None = None):
    config = config or get_config()
    gd = config["google_drive"]
    creds = service_account.Credentials.from_service_account_file(
        gd["service_account_json"], scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_drive_children(
    service, parent_id: str, fields: str = "files(id, name, mimeType)"
) -> list[dict]:
    """Return all non-trashed children of a Drive folder, following pagination."""
    return _list_drive_files(
        service,
        query=f"'{parent_id}' in parents and trashed = false",
        fields=fields,
    )


def _list_drive_files(service, query: str, fields: str, page_size: int = 200) -> list[dict]:
    files: list[dict] = []
    page_token = None
    if "nextPageToken" not in fields:
        fields = f"nextPageToken, {fields}"
    while True:
        kwargs = {
            "q": query,
            "fields": fields,
            "pageSize": page_size,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = service.files().list(**kwargs).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def _set_unique_map_entry(
    mapping: dict[str, dict],
    key: str,
    file: dict,
    kind: str,
    folder_name: str,
) -> None:
    existing = mapping.get(key)
    if existing is not None and existing.get("id") != file.get("id"):
        log.warning(
            "Duplicate %s match key %r in folder %s: keeping %r, ignoring %r",
            kind,
            key,
            folder_name,
            existing.get("name"),
            file.get("name"),
        )
        return
    mapping[key] = file


def invalidate_test_cases_cache() -> None:
    """Call when Drive files may have changed."""
    _test_cases_cache["data"] = None
    _test_cases_cache["unmatched_gt"] = None
    _test_cases_cache["timestamp"] = 0.0


def unmatched_gt_notice(unmatched: list[dict] | None) -> dict[str, Any]:
    """Upload-UI copy for Drive GT files that did not pair with any audio."""
    rows = list(unmatched or [])
    count = len(rows)
    noun = "file" if count == 1 else "files"
    return {
        "count": count,
        "heading": (
            f"{count} Ground Truth {noun} in Drive didn't match any audio file"
            if count
            else ""
        ),
        "files": rows,
    }


def _unmatched_gt_entry(
    kind: str,
    file: dict,
    key: str,
    folder: dict,
    language: str,
) -> dict[str, Any]:
    return {
        "filename": file.get("name") or "",
        "kind": kind,
        "folder_label": folder.get("name") or "",
        "language": language,
        "match_key": key,
        "reason": f"match key {key!r} did not match any audio file",
    }


def _pair_folder_files(folder: dict, files: list[dict]) -> tuple[list[dict], list[dict]]:
    """Pair audio with GT sidecars in one language folder.

    Returns (test_cases, unmatched_gt). Unmatched GT files are listed instead
    of being dropped. Scanning/mime filters are unchanged.
    """
    language = extract_language(folder.get("name") or "")
    folder_name = folder.get("name") or ""

    audio_files = [f for f in files if f.get("mimeType") in AUDIO_MIME_TYPES]
    transcript_files = [
        f
        for f in files
        if f.get("mimeType") in TRANSCRIPT_MIME_TYPES
        and not is_soap_gt(f.get("name") or "")
        and not is_translation_gt(f.get("name") or "")
    ]
    soap_gt_files = [
        f
        for f in files
        if f.get("mimeType") in TRANSCRIPT_MIME_TYPES and is_soap_gt(f.get("name") or "")
    ]
    translation_gt_files = [
        f
        for f in files
        if f.get("mimeType") in TRANSCRIPT_MIME_TYPES
        and is_translation_gt(f.get("name") or "")
    ]

    log.info(
        "Drive GT discovery: folder=%s scanned=%d files=%s audio=%d "
        "transcript=%d soap=%d translation=%d",
        folder_name,
        len(files),
        [f.get("name") for f in files],
        len(audio_files),
        len(transcript_files),
        len(soap_gt_files),
        len(translation_gt_files),
    )

    transcript_map: dict[str, dict] = {}
    for tf in transcript_files:
        _set_unique_map_entry(
            transcript_map, _match_key(tf["name"]), tf, "transcript", folder_name
        )

    soap_map: dict[str, dict] = {}
    for sf in soap_gt_files:
        _set_unique_map_entry(
            soap_map, get_soap_base(sf["name"]), sf, "SOAP GT", folder_name
        )

    translation_map: dict[str, dict] = {}
    for tf in translation_gt_files:
        _set_unique_map_entry(
            translation_map,
            get_translation_base(tf["name"]),
            tf,
            "translation GT",
            folder_name,
        )

    audio_keys = {_match_key(af["name"]) for af in audio_files}
    test_cases: list[dict] = []

    for af in audio_files:
        audio_key = _match_key(af["name"])
        transcript = transcript_map.get(audio_key)
        soap_gt_file = soap_map.get(audio_key)
        translation_gt_file = translation_map.get(audio_key)
        if transcript:
            log.info(
                "Drive GT matched: kind=transcript file=%r audio=%r key=%r folder=%s",
                transcript.get("name"),
                af.get("name"),
                audio_key,
                folder_name,
            )
        else:
            log.info(
                "Drive GT no transcript: audio=%r key=%r folder=%s reason=%s",
                af.get("name"),
                audio_key,
                folder_name,
                "no transcript with this match key",
            )
        if soap_gt_file:
            log.info(
                "Drive GT matched: kind=soap file=%r audio=%r key=%r folder=%s",
                soap_gt_file.get("name"),
                af.get("name"),
                audio_key,
                folder_name,
            )
        else:
            log.info(
                "Drive GT no SOAP: audio=%r key=%r folder=%s reason=%s",
                af.get("name"),
                audio_key,
                folder_name,
                "no SOAP GT with this match key",
            )
        if translation_gt_file:
            log.info(
                "Drive GT matched: kind=translation file=%r audio=%r key=%r folder=%s",
                translation_gt_file.get("name"),
                af.get("name"),
                audio_key,
                folder_name,
            )
        else:
            log.info(
                "Drive GT no translation: audio=%r key=%r folder=%s reason=%s",
                af.get("name"),
                audio_key,
                folder_name,
                "no translation GT with this match key",
            )

        test_cases.append(
            {
                "language": language,
                "folder_label": folder_name,
                "folder_id": folder.get("id"),
                "audio_filename": af["name"],
                "audio_file_id": af["id"],
                "audio_mime_type": af["mimeType"],
                "transcript_filename": transcript["name"] if transcript else None,
                "transcript_file_id": transcript["id"] if transcript else None,
                "transcript_mime_type": transcript["mimeType"] if transcript else None,
                "has_transcript": transcript is not None,
                "soap_gt_filename": soap_gt_file["name"] if soap_gt_file else None,
                "soap_gt_file_id": soap_gt_file["id"] if soap_gt_file else None,
                "soap_gt_mime_type": soap_gt_file["mimeType"] if soap_gt_file else None,
                "has_soap_ground_truth": soap_gt_file is not None,
                "translation_gt_filename": (
                    translation_gt_file["name"] if translation_gt_file else None
                ),
                "translation_gt_file_id": (
                    translation_gt_file["id"] if translation_gt_file else None
                ),
                "translation_gt_mime_type": (
                    translation_gt_file["mimeType"] if translation_gt_file else None
                ),
                "has_translation_ground_truth": translation_gt_file is not None,
                "is_english": language.lower() in ("english", "en"),
                "status": "ready",
                "ground_truth_flag": "" if transcript else "no_ground_truth",
            }
        )

    unmatched: list[dict] = []
    for key, tf in transcript_map.items():
        if key not in audio_keys:
            entry = _unmatched_gt_entry("transcript", tf, key, folder, language)
            unmatched.append(entry)
            log.info(
                "Drive GT unmatched: kind=transcript file=%r key=%r folder=%s reason=%s",
                tf.get("name"),
                key,
                folder_name,
                entry["reason"],
            )
    for key, sf in soap_map.items():
        if key not in audio_keys:
            entry = _unmatched_gt_entry("soap", sf, key, folder, language)
            unmatched.append(entry)
            log.info(
                "Drive GT unmatched: kind=soap file=%r key=%r folder=%s reason=%s",
                sf.get("name"),
                key,
                folder_name,
                entry["reason"],
            )
    for key, tf in translation_map.items():
        if key not in audio_keys:
            entry = _unmatched_gt_entry("translation", tf, key, folder, language)
            unmatched.append(entry)
            log.info(
                "Drive GT unmatched: kind=translation file=%r key=%r folder=%s reason=%s",
                tf.get("name"),
                key,
                folder_name,
                entry["reason"],
            )

    log.info(
        "Drive GT pairing summary: folder=%s cases=%d unmatched_gt=%d",
        folder_name,
        len(test_cases),
        len(unmatched),
    )
    return test_cases, unmatched


def list_test_cases(config: dict | None = None) -> list[dict]:
    now = _time.time()
    if (
        _test_cases_cache["data"] is not None
        and now - _test_cases_cache["timestamp"] < _test_cases_cache["ttl"]
    ):
        log.info(
            "list_test_cases() — returning cached result (%d cases)",
            len(_test_cases_cache["data"]),
        )
        return _test_cases_cache["data"]

    log.info("list_test_cases() v8 called — new matching logic active")
    config = config or get_config()
    service = get_drive_service(config)
    root_id = config["google_drive"]["root_folder_id"]

    folders = list_drive_children(service, root_id, fields="files(id, name, mimeType)")
    logging.getLogger("medsum_drive").info(
        "Found %d subfolders: %s", len(folders), [f["name"] for f in folders]
    )
    test_cases: list[dict] = []
    unmatched_gt: list[dict] = []

    for folder in folders:
        if folder["mimeType"] != "application/vnd.google-apps.folder":
            continue

        files = list_drive_children(
            service,
            folder["id"],
            fields="files(id, name, mimeType, fileExtension)",
        )
        logging.getLogger("medsum_drive").info(
            "Folder %s: %d files — %s",
            folder["name"],
            len(files),
            [(f["name"], f["mimeType"]) for f in files],
        )

        cases, orphans = _pair_folder_files(folder, files)
        test_cases.extend(cases)
        unmatched_gt.extend(orphans)

    _test_cases_cache["data"] = test_cases
    _test_cases_cache["unmatched_gt"] = unmatched_gt
    _test_cases_cache["timestamp"] = _time.time()
    log.info(
        "Drive GT discovery complete: cases=%d unmatched_gt=%d",
        len(test_cases),
        len(unmatched_gt),
    )
    return test_cases


def list_drive_files_response(config: dict | None = None) -> dict[str, Any]:
    cases = list_test_cases(config)
    ready = [c for c in cases if c.get("status") == "ready"]
    languages = sorted({c["language"] for c in ready})
    notice = unmatched_gt_notice(_test_cases_cache.get("unmatched_gt") or [])
    return {
        "languages": languages,
        "unmatched_ground_truth": notice["files"],
        "unmatched_ground_truth_count": notice["count"],
        "unmatched_ground_truth_heading": notice["heading"],
        "files": [
            {
                "language": c["language"],
                "folder_label": c.get("folder_label", c["language"]),
                "audio": c["audio_filename"],
                "has_transcript": c.get("has_transcript", False),
                "has_transcript_ground_truth": c.get("has_transcript", False),
                "has_soap_ground_truth": c.get("has_soap_ground_truth", False),
                "has_summary_ground_truth": c.get("has_soap_ground_truth", False),
                "has_translation_ground_truth": c.get("has_translation_ground_truth", False),
                "has_json_ground_truth": str(c.get("soap_gt_filename") or "").lower().endswith(".json")
                or str(c.get("transcript_filename") or "").lower().endswith(".json"),
                "transcript_filename": c.get("transcript_filename"),
                "soap_gt_filename": c.get("soap_gt_filename"),
                "translation_gt_filename": c.get("translation_gt_filename"),
                "is_english": c.get("is_english", False),
                "status": c["status"],
            }
            for c in ready
        ],
    }


def _download_raw(file_id: str, service) -> bytes:
    buf = io.BytesIO()
    request = service.files().get_media(fileId=file_id)
    dl = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


def _extract_docx_text(raw_bytes: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError(
            "python-docx is required to read .docx transcripts. "
            "Run: pip install python-docx"
        ) from exc

    doc = Document(io.BytesIO(raw_bytes))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def strip_case_header(text: str) -> str:
    """
    Remove case metadata lines from ground truth files.
    Handles patterns like:
      "Case: Urinary Tract Infection (UTI) (Urologist / General Physician)"
      "Case 1: Diabetes (Endocrinologist)"
      "Case:UTI"
    """
    if not text:
        return text

    lines = text.strip().splitlines()
    cleaned: list[str] = []
    skip_next_blank = False

    for line in lines:
        stripped = line.strip()
        if _CASE_HEADER_RE.match(stripped):
            skip_next_blank = True
            continue
        if skip_next_blank and stripped == "":
            skip_next_blank = False
            continue
        skip_next_blank = False
        cleaned.append(line)

    return "\n".join(cleaned).strip()


def download_transcript(
    file_id: str, mime_type: str | None = None, service=None
) -> str:
    """Download transcript content as plain text (Google Doc, .docx, or .txt)."""
    service = service or get_drive_service()

    if mime_type == GOOGLE_DOC_MIME:
        content = service.files().export(fileId=file_id, mimeType="text/plain").execute()
        if isinstance(content, bytes):
            text = content.decode("utf-8", errors="replace")
        else:
            text = str(content)
        return strip_case_header(text)

    if mime_type == DOCX_MIME:
        return strip_case_header(_extract_docx_text(_download_raw(file_id, service)))

    raw_bytes = _download_raw(file_id, service)
    return strip_case_header(raw_bytes.decode("utf-8", errors="replace"))


def download_file(file_id: str, service=None) -> bytes:
    service = service or get_drive_service()
    return _download_raw(file_id, service)


def download_audio(file_id: str) -> bytes:
    return download_file(file_id)


def download_soap_ground_truth(
    file_id: str, mime_type: str, service=None
) -> dict | None:
    """
    Download _soap.txt and parse as JSON.
    Returns parsed dict or None if parsing fails.
    """
    import json

    log = logging.getLogger("medsum_drive")
    service = service or get_drive_service()
    try:
        raw_text = download_transcript(file_id, mime_type, service)
        clean = raw_text.strip()
        if clean.startswith("\ufeff"):
            clean = clean[1:]
        parsed = json.loads(clean)
        log.info("SOAP_GT: parsed successfully, keys=%s", list(parsed.keys()))
        return parsed
    except json.JSONDecodeError as exc:
        log.warning("SOAP_GT: JSON parse failed: %s — treating as None", exc)
        return None
    except Exception as exc:
        log.warning("SOAP_GT: download failed: %s — treating as None", exc)
        return None


def download_translation_ground_truth(
    file_id: str, mime_type: str, service=None
) -> str | None:
    """
    Download translation ground truth as plain text.
    Returns text string or None if download fails.
    """
    log = logging.getLogger("medsum_drive")
    service = service or get_drive_service()
    try:
        text = strip_case_header(download_transcript(file_id, mime_type, service))
        clean = text.strip()
        if clean:
            log.info("TRANSLATION_GT: downloaded %d chars", len(clean))
            return clean
        return None
    except Exception as exc:
        log.warning("TRANSLATION_GT: download failed: %s", exc)
        return None
