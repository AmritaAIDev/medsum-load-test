"""Latency Analysis rows from Flask /transcribe timings.

Column order is fixed. SOAP generation is Flask `llm-time` (SOAP/summary LLM) —
the API does not return a separate SOAP timer. Missing values are
"unavailable", never a fabricated 0.
"""

from __future__ import annotations

from typing import Any

LATENCY_ANALYSIS_HEADERS = (
    "Audio File",
    "Audio Length",
    "Transcription",
    "Translation",
    "SOAP",
    "Total Time",
)

UNAVAILABLE = "unavailable"

# /transcribe fields → Latency Analysis columns
# SOAP ← llm-time (LLM summarisation / SOAP generation in this pipeline)
TRANSCRIBE_FIELD_MAP = {
    "transcription": "transcription-time",
    "translation": "translation-time",
    "soap": "llm-time",
    "total_time": "total-time",
    "audio_length": "audio_length",
}


def _tr(result: dict | None) -> dict:
    data = result or {}
    tr = data.get("transcription_result")
    return tr if isinstance(tr, dict) else {}


def _present(container: dict, key: str):
    if key not in container:
        return None
    val = container.get(key)
    if val is None or val == "":
        return None
    return val


def pick_transcribe_time(result: dict | None, column: str):
    """Raw numeric (or None) for a Latency Analysis timing column."""
    tr = _tr(result)
    nested = tr.get("time") if isinstance(tr.get("time"), dict) else {}
    key = TRANSCRIBE_FIELD_MAP.get(column)
    if not key:
        return None
    val = _present(tr, key)
    if val is not None:
        return val
    aliases = {
        "transcription-time": ("ASR", "transcription"),
        "translation-time": ("Translation", "translation"),
        "llm-time": ("llm", "LLM"),
        "total-time": ("total",),
        "audio_length": ("audio_length",),
    }
    for alias in aliases.get(key, ()):
        val = _present(nested, alias)
        if val is not None:
            return val
    if column == "audio_length":
        dur = (result or {}).get("audio_duration_seconds")
        if dur not in (None, "", 0, "0"):
            return dur
    return None


def format_seconds(value: Any, *, audio: bool = False) -> str:
    if value is None or value == "":
        return UNAVAILABLE
    try:
        n = float(value)
    except (TypeError, ValueError):
        return UNAVAILABLE
    if audio:
        if n >= 60:
            return f"{int(n // 60)}m {int(round(n % 60))}s"
        return f"{int(round(n))}s"
    return f"{n:.2f}s"


def latency_analysis_row(result: dict | None) -> dict[str, str]:
    """Display cells in LATENCY_ANALYSIS_HEADERS order (as a dict)."""
    data = result or {}
    audio = (
        str(data.get("audio_filename") or data.get("filename") or "").strip()
        or UNAVAILABLE
    )
    return {
        "Audio File": audio,
        "Audio Length": format_seconds(
            pick_transcribe_time(data, "audio_length"), audio=True
        ),
        "Transcription": format_seconds(pick_transcribe_time(data, "transcription")),
        "Translation": format_seconds(pick_transcribe_time(data, "translation")),
        "SOAP": format_seconds(pick_transcribe_time(data, "soap")),
        "Total Time": format_seconds(pick_transcribe_time(data, "total_time")),
    }


def latency_analysis_values(result: dict | None) -> list[str]:
    row = latency_analysis_row(result)
    return [row[h] for h in LATENCY_ANALYSIS_HEADERS]
