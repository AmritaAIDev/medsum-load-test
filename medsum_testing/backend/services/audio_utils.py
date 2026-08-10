"""Audio duration detection using mutagen."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from mutagen import File as MutagenFile


def get_duration_seconds(audio_bytes: bytes, filename: str = "audio.mp3") -> int:
    suffix = Path(filename).suffix or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        audio = MutagenFile(tmp_path)
        if audio is None or audio.info is None:
            return 0
        return int(round(audio.info.length))
    finally:
        Path(tmp_path).unlink(missing_ok=True)
