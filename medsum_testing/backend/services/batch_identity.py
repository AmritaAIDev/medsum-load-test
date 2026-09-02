"""Harness-owned batch identifiers.

`batch_id` is the human-readable value `25-Aug-2026 | 001` — not a UUID and
not a second field. Sequence numbers are allocated under a process lock plus
an inter-process file lock so two concurrent Run-All starts cannot collide.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from medsum_testing.backend.services.config_loader import get_results_dir

COUNTER_FILENAME = ".batch_seq.json"
LOCK_FILENAME = ".batch_seq.lock"
BATCH_ID_SEP = " | "
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_BATCH_ID_RE = re.compile(
    r"^(\d{2}-[A-Za-z]{3}-\d{4})" + re.escape(BATCH_ID_SEP) + r"(\d+)$"
)
_LOCK_TIMEOUT_SECONDS = 30.0
_SEQ_THREAD_LOCK = threading.Lock()


@dataclass(frozen=True)
class BatchIdentity:
    batch_id: str
    sequence: int
    date_label: str


def format_batch_date(day: date) -> str:
    """Locale-stable `25-Aug-2026`."""
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def format_batch_id(day: date, sequence: int) -> str:
    return f"{format_batch_date(day)}{BATCH_ID_SEP}{int(sequence):03d}"


def is_human_batch_id(value: str) -> bool:
    return bool(_BATCH_ID_RE.match(str(value or "").strip()))


def parse_batch_id_sequence(batch_id: str, date_label: str = "") -> int | None:
    match = _BATCH_ID_RE.match(str(batch_id or "").strip())
    if not match:
        return None
    if date_label and match.group(1) != date_label:
        return None
    return int(match.group(2))


def canonical_batch_id(batch_id: str = "", legacy_ref: str = "") -> str:
    """Prefer an already-human id. Old JSON may still have UUID + batch_ref."""
    for candidate in (batch_id, legacy_ref):
        text = str(candidate or "").strip()
        if is_human_batch_id(text):
            return text
    return str(batch_id or legacy_ref or "").strip()


def display_batch_label(batch_id: str = "", legacy_ref: str = "") -> str:
    return canonical_batch_id(batch_id, legacy_ref) or "—"


def allocate_batch_identity(
    *,
    now: datetime | None = None,
    results_dir: Path | None = None,
) -> BatchIdentity:
    """Today's next `25-Aug-2026 | 001`, safe under concurrent starts."""
    when = now or datetime.now()
    day = when.date() if isinstance(when, datetime) else when
    directory = results_dir or get_results_dir()
    directory.mkdir(parents=True, exist_ok=True)
    date_label = format_batch_date(day)
    iso_key = day.isoformat()

    with _SEQ_THREAD_LOCK:
        with _exclusive_file_lock(directory / LOCK_FILENAME):
            counters = _read_counters(directory / COUNTER_FILENAME)
            scanned = _max_sequence_on_disk(directory, date_label)
            next_seq = max(int(counters.get(iso_key) or 0), scanned) + 1
            counters[iso_key] = next_seq
            _write_counters(directory / COUNTER_FILENAME, counters)

    return BatchIdentity(
        batch_id=format_batch_id(day, next_seq),
        sequence=next_seq,
        date_label=date_label,
    )


def _max_sequence_on_disk(results_dir: Path, date_label: str) -> int:
    highest = 0
    for path in results_dir.glob("*.json"):
        if path.name.startswith("."):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        human = canonical_batch_id(
            str(data.get("batch_id") or ""),
            str(data.get("batch_ref") or ""),
        )
        seq = parse_batch_id_sequence(human, date_label)
        if seq is not None and seq > highest:
            highest = seq
    return highest


def _read_counters(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def _write_counters(path: Path, counters: dict[str, int]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(counters, indent=2), encoding="utf-8")
    tmp.replace(path)


@contextmanager
def _exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
    try:
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        _acquire_fd_lock(fh)
        try:
            yield
        finally:
            _release_fd_lock(fh)
    finally:
        fh.close()


def _acquire_fd_lock(fh) -> None:
    if os.name == "nt":
        import msvcrt

        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("batch sequence lock timed out")
                time.sleep(0.02)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)


def _release_fd_lock(fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
