"""Google Drive audio and transcript fetching."""

from __future__ import annotations

import io
import re
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from medsum_testing.backend.services.config_loader import get_config

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
TRANSCRIPT_EXTENSION = ".txt"


def _get_drive_service():
    config = get_config()
    gd = config["google_drive"]
    creds = service_account.Credentials.from_service_account_file(
        gd["service_account_json"], scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_children(service, folder_id: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed = false"
    while True:
        response = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
                pageSize=200,
            )
            .execute()
        )
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def _stem(filename: str) -> str:
    for ext in AUDIO_EXTENSIONS | {TRANSCRIPT_EXTENSION}:
        if filename.lower().endswith(ext):
            return filename[: -len(ext)]
    return re.sub(r"\.[^.]+$", "", filename)


def list_test_cases() -> list[dict[str, Any]]:
    """Scan language subfolders under root_folder_id."""
    config = get_config()
    root_id = config["google_drive"]["root_folder_id"]
    service = _get_drive_service()

    test_cases: list[dict[str, Any]] = []
    language_folders = _list_children(service, root_id)

    for folder in language_folders:
        if folder.get("mimeType") != "application/vnd.google-apps.folder":
            continue
        language = folder["name"]
        folder_id = folder["id"]
        children = _list_children(service, folder_id)

        audio_files = [
            f
            for f in children
            if any(f["name"].lower().endswith(ext) for ext in AUDIO_EXTENSIONS)
        ]
        transcript_files = [
            f for f in children if f["name"].lower().endswith(TRANSCRIPT_EXTENSION)
        ]

        transcript_by_stem: dict[str, list[dict]] = {}
        for tf in transcript_files:
            transcript_by_stem.setdefault(_stem(tf["name"]), []).append(tf)

        for audio in audio_files:
            stem = _stem(audio["name"])
            matches = transcript_by_stem.get(stem, [])
            flag = ""
            transcript_id = None
            has_transcript = False

            if len(matches) == 0:
                flag = "no_ground_truth"
            elif len(matches) > 1:
                flag = "ambiguous_match"
            else:
                has_transcript = True
                transcript_id = matches[0]["id"]

            if flag == "ambiguous_match":
                continue

            test_cases.append(
                {
                    "language": language,
                    "audio_filename": audio["name"],
                    "audio_file_id": audio["id"],
                    "transcript_file_id": transcript_id,
                    "has_transcript": has_transcript,
                    "ground_truth_flag": flag,
                }
            )

    return test_cases


def list_drive_files_response() -> dict[str, Any]:
    cases = list_test_cases()
    languages = sorted({c["language"] for c in cases})
    files = [
        {
            "language": c["language"],
            "audio": c["audio_filename"],
            "has_transcript": c["has_transcript"],
        }
        for c in cases
    ]
    return {"languages": languages, "files": files}


def download_audio(file_id: str) -> bytes:
    service = _get_drive_service()
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def download_transcript(file_id: str) -> str:
    service = _get_drive_service()
    request = service.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue().decode("utf-8", errors="replace")
