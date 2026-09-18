"""Accuracy-category thresholds from test_settings.

Pass / review / fail (shown as High accuracy / Needs review / Low accuracy)
use these values. Execution Status (Pending / Running / Done) never reads them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from medsum_testing.backend.services.config_loader import load_config

DEFAULT_ACCURACY_PASS_SCORE = 80.0
DEFAULT_ACCURACY_REVIEW_SCORE = 65.0
DEFAULT_ACCURACY_MODERATE_SCORE = 50.0
DEFAULT_QUALITY_ACCEPTABLE_SCORE = 90.0
DEFAULT_QUALITY_MINOR_SCORE = 80.0
DEFAULT_QUALITY_MODERATE_SCORE = 70.0

_SEVERITY_FAIL = frozenset({"high", "critical"})
_ACCURACY_BANDS = frozenset({"pass", "review", "fail", "complete_no_accuracy"})


@dataclass(frozen=True)
class AccuracyThresholds:
    pass_score: float = DEFAULT_ACCURACY_PASS_SCORE
    review_score: float = DEFAULT_ACCURACY_REVIEW_SCORE
    moderate_score: float = DEFAULT_ACCURACY_MODERATE_SCORE
    quality_acceptable_score: float = DEFAULT_QUALITY_ACCEPTABLE_SCORE
    quality_minor_score: float = DEFAULT_QUALITY_MINOR_SCORE
    quality_moderate_score: float = DEFAULT_QUALITY_MODERATE_SCORE

    def with_pass_score(self, pass_score: float) -> AccuracyThresholds:
        return AccuracyThresholds(
            pass_score=float(pass_score),
            review_score=self.review_score,
            moderate_score=self.moderate_score,
            quality_acceptable_score=self.quality_acceptable_score,
            quality_minor_score=self.quality_minor_score,
            quality_moderate_score=self.quality_moderate_score,
        )


def _as_score(value: Any, default: float) -> float:
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def get_accuracy_thresholds(config: dict[str, Any] | None = None) -> AccuracyThresholds:
    """Read named test_settings keys. One edit updates every accuracy category."""
    if config is None:
        try:
            config = load_config()
        except FileNotFoundError:
            config = {}
    settings = (config or {}).get("test_settings") or {}
    return AccuracyThresholds(
        pass_score=_as_score(
            settings.get("accuracy_pass_score"), DEFAULT_ACCURACY_PASS_SCORE
        ),
        review_score=_as_score(
            settings.get("accuracy_review_score"), DEFAULT_ACCURACY_REVIEW_SCORE
        ),
        moderate_score=_as_score(
            settings.get("accuracy_moderate_score"), DEFAULT_ACCURACY_MODERATE_SCORE
        ),
        quality_acceptable_score=_as_score(
            settings.get("accuracy_quality_acceptable_score"),
            DEFAULT_QUALITY_ACCEPTABLE_SCORE,
        ),
        quality_minor_score=_as_score(
            settings.get("accuracy_quality_minor_score"),
            DEFAULT_QUALITY_MINOR_SCORE,
        ),
        quality_moderate_score=_as_score(
            settings.get("accuracy_quality_moderate_score"),
            DEFAULT_QUALITY_MODERATE_SCORE,
        ),
    )


def accuracy_band_from_score(
    score: float | None,
    *,
    severity: str | None = None,
    thresholds: AccuracyThresholds | None = None,
) -> str:
    """pass / review / fail from a score. Never pending, running, or done."""
    t = thresholds or get_accuracy_thresholds()
    if (severity or "").strip().lower() in _SEVERITY_FAIL:
        return "fail"
    if score is None:
        return "complete_no_accuracy"
    if float(score) >= t.pass_score:
        return "pass"
    return "review"


def displayed_accuracy_band(
    *,
    status: str | None,
    final_result: str | None,
    score: float | None,
    accuracy_skipped: bool = False,
    thresholds: AccuracyThresholds | None = None,
) -> str | None:
    """Band shown on every accuracy-category surface.

    None: execution error or scheduler skip — no accuracy judgment.
    pending: run not finished — still no judgment.
    fail stored as a severity fail stays Low accuracy.
    pass / review are re-applied from the current pass_score so one config
    edit changes the category everywhere it is displayed.
    """
    s = (status or "").strip().lower()
    v = (final_result or "").strip().lower()
    if s == "failed" or v == "failed":
        return None
    if s == "skipped" or v == "skipped":
        return None
    if s in {"pending", "running"} or v == "pending":
        return "pending"
    if accuracy_skipped or v == "complete_no_accuracy":
        return "complete_no_accuracy"
    if v == "fail":
        return "fail"
    if score is not None:
        return accuracy_band_from_score(score, thresholds=thresholds)
    if v in _ACCURACY_BANDS:
        return v
    return "complete_no_accuracy"
