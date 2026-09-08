"""User-facing error copy for failed test runs.

Technical exceptions stay in logs. Dashboard / detail Errors panels
should show these short messages instead of tracebacks or API dumps.
"""

from __future__ import annotations

import json
import re
from typing import Any

_TRACEBACK_RE = re.compile(
    r"traceback \(most recent call last\)",
    re.IGNORECASE,
)
_FILE_LINE_RE = re.compile(r'^\s*File ".*", line \d+', re.MULTILINE)
_DJANGO_FIELD_RE = re.compile(
    r'\{\s*"(?P<field>[^"]+)"\s*:\s*\[\s*"(?P<msg>[^"]+)"\s*\]\s*\}',
    re.IGNORECASE,
)

_FIELD_MESSAGES = {
    "language": (
        "Language is required. Choose a language for this audio file, "
        "then run the test again."
    ),
    "patient_id": (
        "Patient ID is required. Add a patient in Doctor & Patient Setup."
    ),
    "user_id": "Doctor ID is missing. Sign in again and retry the test.",
    "audio": "Audio file is missing or invalid. Re-select the file and try again.",
}


def _looks_like_traceback(text: str) -> bool:
    if _TRACEBACK_RE.search(text):
        return True
    if _FILE_LINE_RE.search(text) and "Error" in text:
        return True
    return False


def _django_field_message(payload: Any) -> str | None:
    if isinstance(payload, str):
        match = _DJANGO_FIELD_RE.search(payload)
        if match:
            field = match.group("field").strip().lower()
            if field in _FIELD_MESSAGES:
                return _FIELD_MESSAGES[field]
            msg = match.group("msg").strip()
            label = field.replace("_", " ")
            return f"{label.capitalize()} is invalid: {msg}"
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(payload, dict):
        return None
    for field, messages in payload.items():
        key = str(field or "").strip().lower()
        if key in _FIELD_MESSAGES:
            return _FIELD_MESSAGES[key]
        if isinstance(messages, list) and messages:
            label = key.replace("_", " ") or "Field"
            return f"{label.capitalize()} is invalid: {messages[0]}"
        if isinstance(messages, str) and messages.strip():
            label = key.replace("_", " ") or "Field"
            return f"{label.capitalize()} is invalid: {messages.strip()}"
    return None


def user_facing_error(exc: BaseException | str | None) -> str:
    """Map a technical exception/string to a short message for the UI."""
    if exc is None:
        return "The test failed. Please try again."
    text = str(exc).strip()
    if not text:
        return "The test failed. Please try again."
    if _looks_like_traceback(text):
        # Prefer the final exception line when a traceback was stored in errors[].
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in reversed(lines):
            if line.startswith("File ") or line.startswith("Traceback"):
                continue
            if "Error" in line or "Exception" in line or "failed" in line.lower():
                text = re.sub(r"^[A-Za-z0-9_.]+:\s*", "", line).strip() or line
                break
        else:
            return "The test failed. Please try again."

    lower = text.lower()
    django_msg = _django_field_message(text)
    if django_msg:
        return django_msg

    if "language" in lower and (
        "may not be blank" in lower
        or "required" in lower
        or "not be empty" in lower
    ):
        return _FIELD_MESSAGES["language"]

    if "audio_upload timeout" in lower:
        return "Audio upload timed out. Please try again."

    if "audio_upload failed" in lower:
        return (
            django_msg
            or "Audio upload failed. Check the audio file and try again."
        )

    if "audio_upload response missing" in lower:
        return "Audio upload did not return a session. Please try again."

    if "no patient_id" in lower or "patient_id is required" in lower:
        return _FIELD_MESSAGES["patient_id"]

    if "auth failed" in lower or "authentication failed" in lower:
        detail = re.sub(
            r"^(auth failed|authentication failed)\s*:?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        if detail and detail.lower() != text.lower():
            return f"Doctor login failed: {detail}"
        return "Doctor login failed. Check the phone number and password."

    if "no token provided" in lower:
        return "Doctor session expired. Sign in again and retry the test."

    if "no test case found" in lower:
        return (
            "No matching test case was found for this audio. "
            "Check the language and file selection."
        )

    if "drive authentication failed" in lower:
        return text  # already user-facing from skip_reasons

    # Strip noisy RuntimeError / HTTP dumps when nothing else matched.
    cleaned = re.sub(r"^RuntimeError:\s*", "", text).strip()
    cleaned = re.sub(
        r"^AUDIO_UPLOAD failed\s+\d+\s*:\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    if cleaned != text:
        nested = _django_field_message(cleaned)
        if nested:
            return nested
        if len(cleaned) <= 180 and not _looks_like_traceback(cleaned):
            return cleaned

    if len(text) > 220:
        return "The test failed. Please try again."
    return text


def user_facing_errors(errors: list[Any] | None) -> list[str]:
    """Deduped user messages from a stored result.errors list."""
    out: list[str] = []
    seen: set[str] = set()
    for item in errors or []:
        text = str(item or "").strip()
        if not text or _looks_like_traceback(text):
            continue
        message = user_facing_error(text)
        key = message.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(message)
    return out or (["The test failed. Please try again."] if errors else [])
