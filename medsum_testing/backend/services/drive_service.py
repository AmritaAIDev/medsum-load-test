"""Google Drive audio and transcript fetching."""

from __future__ import annotations

import io
import logging
import re
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from medsum_testing.backend.services.config_loader import get_config

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


def _match_key(filename: str) -> str:
    """
    Normalised match key for pairing audio <-> transcript.
    Strips number prefix, lowercases, strips _script suffix.
    Also normalises missing underscore before duration: english03 -> english_03.
    """
    base = re.sub(r"\.\w+$", "", filename)
    base = _strip_number_prefix(base)
    base = _strip_script_suffix(base)
    duration_match = re.match(r"^([a-z]+)(\d+)$", base)
    if duration_match:
        base = f"{duration_match.group(1)}_{duration_match.group(2)}"
    return base


def get_drive_service(config: dict | None = None):
    config = config or get_config()
    gd = config["google_drive"]
    creds = service_account.Credentials.from_service_account_file(
        gd["service_account_json"], scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_test_cases(config: dict | None = None) -> list[dict]:
    logging.getLogger("medsum_drive").info(
        "list_test_cases() v8 called — new matching logic active"
    )
    config = config or get_config()
    service = get_drive_service(config)
    root_id = config["google_drive"]["root_folder_id"]

    folders_resp = service.files().list(
        q=f"'{root_id}' in parents and trashed = false",
        fields="files(id, name, mimeType)",
        pageSize=200,
    ).execute()

    folders = folders_resp.get("files", [])
    logging.getLogger("medsum_drive").info(
        "Found %d subfolders: %s", len(folders), [f["name"] for f in folders]
    )
    test_cases: list[dict] = []

    for folder in folders:
        if folder["mimeType"] != "application/vnd.google-apps.folder":
            continue

        language = extract_language(folder["name"])

        files_resp = service.files().list(
            q=f"'{folder['id']}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType, fileExtension)",
            pageSize=200,
        ).execute()

        files = files_resp.get("files", [])
        logging.getLogger("medsum_drive").info(
            "Folder %s: %d files — %s",
            folder["name"],
            len(files),
            [(f["name"], f["mimeType"]) for f in files],
        )

        audio_files = [f for f in files if f["mimeType"] in AUDIO_MIME_TYPES]
        transcript_files = [f for f in files if f["mimeType"] in TRANSCRIPT_MIME_TYPES]

        if not audio_files:
            continue

        transcript_map: dict[str, dict] = {}
        for tf in transcript_files:
            key = _match_key(tf["name"])
            transcript_map[key] = tf

        for af in audio_files:
            audio_key = _match_key(af["name"])
            transcript = transcript_map.get(audio_key)

            test_cases.append(
                {
                    "language": language,
                    "folder_label": folder["name"],
                    "audio_filename": af["name"],
                    "audio_file_id": af["id"],
                    "audio_mime_type": af["mimeType"],
                    "transcript_filename": transcript["name"] if transcript else None,
                    "transcript_file_id": transcript["id"] if transcript else None,
                    "transcript_mime_type": transcript["mimeType"] if transcript else None,
                    "has_transcript": transcript is not None,
                    "status": "ready",
                    "ground_truth_flag": "" if transcript else "no_ground_truth",
                }
            )

    return test_cases


def list_drive_files_response(config: dict | None = None) -> dict[str, Any]:
    cases = list_test_cases(config)
    ready = [c for c in cases if c.get("status") == "ready"]
    languages = sorted({c["language"] for c in ready})
    return {
        "languages": languages,
        "files": [
            {
                "language": c["language"],
                "folder_label": c.get("folder_label", c["language"]),
                "audio": c["audio_filename"],
                "has_transcript": c.get("has_transcript", False),
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


def download_transcript(
    file_id: str, mime_type: str | None = None, service=None
) -> str:
    """Download transcript content as plain text (Google Doc, .docx, or .txt)."""
    service = service or get_drive_service()

    if mime_type == GOOGLE_DOC_MIME:
        content = service.files().export(fileId=file_id, mimeType="text/plain").execute()
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")
        return str(content)

    if mime_type == DOCX_MIME:
        return _extract_docx_text(_download_raw(file_id, service))

    raw_bytes = _download_raw(file_id, service)
    return raw_bytes.decode("utf-8", errors="replace")


def download_file(file_id: str, service=None) -> bytes:
    service = service or get_drive_service()
    return _download_raw(file_id, service)


def download_audio(file_id: str) -> bytes:
    return download_file(file_id)
