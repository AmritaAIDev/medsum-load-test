"""Result persistence helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from medsum_testing.backend.models.test_result import TestResult
from medsum_testing.backend.services.config_loader import get_results_dir


def save_result(result: TestResult) -> Path:
    path = get_results_dir() / f"{result.test_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    return path


def load_result(test_id: str) -> Optional[TestResult]:
    path = get_results_dir() / f"{test_id}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return TestResult.from_dict(json.load(f))


def list_results() -> list[dict]:
    return [summary for _, summary in _iter_result_files()]


def load_all_results_raw() -> list[dict]:
    """Full result dicts for dashboard and batch views."""
    return [data for data, _ in _iter_result_files(full=True)]


def list_results_by_batch(batch_id: str) -> list[dict]:
    return [r for r in load_all_results_raw() if r.get("batch_id") == batch_id]


def _iter_result_files(full: bool = False):
    results_dir = get_results_dir()
    for path in sorted(results_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if full:
            yield data, path
        else:
            yield path, {
                "id": data.get("test_id", path.stem),
                "test_id": data.get("test_id", path.stem),
                "tc_ref": data.get("tc_ref", ""),
                "run_ref": data.get("run_ref", ""),
                "filename": data.get("audio_filename", ""),
                "audio_filename": data.get("audio_filename", ""),
                "language": data.get("language", ""),
                "timestamp": data.get("timestamp", ""),
                "final_result": data.get("final_result", ""),
                "accuracy_score": data.get("accuracy_score"),
                "similarity_score": (
                    (data.get("comparison") or {}).get("similarity_score")
                    or data.get("accuracy_score")
                    or (data.get("transcription_comparison") or {}).get("similarity_score")
                ),
                "status": data.get("status", ""),
                "batch_id": data.get("batch_id", ""),
                "total_test_time_seconds": data.get("total_test_time_seconds"),
                "comparison": data.get("comparison") or data.get("transcription_comparison"),
                "has_ground_truth": data.get("has_ground_truth", True),
                "ground_truth_transcription": data.get("ground_truth_transcription", ""),
            }


def find_previous_result(
    language: str, audio_filename: str, exclude_id: str
) -> Optional[TestResult]:
    """Most recent completed result for the same audio file."""
    results_dir = get_results_dir()
    matches: list[tuple[float, TestResult]] = []
    for path in results_dir.glob("*.json"):
        if path.stem == exclude_id:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if (
                data.get("audio_filename") == audio_filename
                and data.get("status") == "complete"
            ):
                matches.append((path.stat().st_mtime, TestResult.from_dict(data)))
        except (json.JSONDecodeError, OSError):
            continue
    if not matches:
        return None
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1]


def has_recent_result(audio_filename: str, within_seconds: int = 60) -> bool:
    """True if a result for this audio was saved within the last N seconds."""
    results_dir = get_results_dir()
    now = datetime.now(timezone.utc)
    for path in results_dir.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("audio_filename") != audio_filename:
                continue
            ts = data.get("timestamp", "")
            if not ts:
                continue
            saved = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if saved.tzinfo is None:
                saved = saved.replace(tzinfo=timezone.utc)
            age = (now - saved).total_seconds()
            if 0 <= age <= within_seconds:
                return True
        except (json.JSONDecodeError, OSError, ValueError):
            continue
    return False
