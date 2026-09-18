"""Stable test-case identity for the detail view.

View must open the clicked case by its own test_id — never by table index,
Django integer PK (`id`), or a shared mutable row reference.

Django batch payloads often expose `id` as a numeric PK. Using that as the
local `results/{id}.json` key opens the wrong file (or 404s). Local JSON is
named by the harness UUID in `test_id`.
"""

from __future__ import annotations

import re
from typing import Any

from medsum_testing.backend.models.test_result import (
    display_row_status,
    display_soap_accuracy,
    soap_fact_counts,
)
from medsum_testing.backend.services.accuracy_thresholds import AccuracyThresholds
from medsum_testing.backend.services.batch_identity import display_batch_label
from medsum_testing.backend.services.result_store import load_result, load_all_results_raw
from medsum_testing.backend.services.soap_detail_table import (
    count_classified_facts,
    soap_facts_from_result,
)

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
HEX32_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)

SOURCE_DRIVE = "google_drive"
SOURCE_UPLOAD = "upload"

REQUIRED_DETAIL_FIELDS = (
    "test_id",
    "audio_filename",
    "audio_player",
    "audio_length",
    "transcription",
    "translation",
    "soap_output",
    "ground_truth",
    "comparison",
    "accuracy",
    "soap_facts",
    "latency",
    "med_diffs",
    "info_fields",
)

# Prompt 13 Part A four-way tiles. Counts come from Prompt 1 classifications
# (the same list Summary/Detail field lists render), not from a diff-only walk.
SOAP_FACT_TILES = (
    {
        "key": "Correct",
        "label": "Match (Correct)",
        "legend": "Generated matches ground truth.",
    },
    {
        "key": "Incorrect",
        "label": "Incorrect",
        "legend": "Generated contradicts ground truth.",
    },
    {
        "key": "Missing",
        "label": "Missing",
        "legend": "Ground-truth fact not captured in generated output.",
    },
    {
        "key": "Hallucination",
        "label": "Hallucinated",
        "legend": "Generated content not supported by ground truth.",
    },
)

# Fields on this page that have no Patient ID / Template analogue.
DETAIL_UNMAPPED_REFERENCE_FIELDS = (
    "Patient ID — not a test-case detail field in this app; not shown.",
    "Template — summary_template_id exists on some runs but is not shown on this page.",
)


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def looks_like_integer_pk(value: str) -> bool:
    """Django accuracy-run primary keys are small integers, not harness UUIDs."""
    raw = _text(value)
    return bool(raw) and raw.isdigit() and len(raw) <= 12


def looks_like_stable_id(value: str) -> bool:
    raw = _text(value)
    if not raw or looks_like_integer_pk(raw):
        return False
    return bool(UUID_RE.match(raw) or HEX32_RE.match(raw) or ("-" in raw) or (len(raw) >= 8))


def stable_test_id(row: dict | None) -> str:
    """Canonical id for View / GET /results/<id>. Never a table index or int PK."""
    data = row or {}
    for key in ("test_id",):
        val = _text(data.get(key))
        if val and not looks_like_integer_pk(val):
            return val
    fallback = _text(data.get("id"))
    if looks_like_stable_id(fallback) and not looks_like_integer_pk(fallback):
        return fallback
    return ""


def find_result_by_test_id(rows: list[dict] | None, test_id: str) -> dict | None:
    """Exact test_id match only. Does not fall back to list index."""
    wanted = _text(test_id)
    if not wanted:
        return None
    for row in rows or []:
        if stable_test_id(row) == wanted:
            return row
    return None


def is_stale_open(opened_id: str, payload: dict | None) -> bool:
    """True when a slower fetch for an earlier click should be discarded."""
    wanted = _text(opened_id)
    got = stable_test_id(payload)
    return bool(wanted) and bool(got) and wanted != got


def last_clicked_id(click_ids: list[str]) -> str:
    """Rapid / double-click: the last click is the case that must be shown."""
    for value in reversed(click_ids or []):
        val = _text(value)
        if val:
            return val
    return ""


DETAIL_HOST_IDS = frozenset({
    "detail-view",
    "back-btn",
    "pdf-btn",
    "excel-btn",
    "export-btn",
    "export-menu",
})


def click_should_open_detail(closest_host_id: str | None, test_id: str | None) -> bool:
    """Row View clicks open a case. Clicks on the detail page host / Back do not.

    `#detail-view` stores data-open-test-id for Prompt 4 identity. Using that
    same attribute as a document-wide opener re-opens the case when Back is
    clicked (the click bubbles through the host).
    """
    tid = _text(test_id)
    if not tid:
        return False
    host = _text(closest_host_id)
    return host not in DETAIL_HOST_IDS


def prefer_local_batch_runs(
    local_runs: list[dict] | None,
    django_runs: list[dict] | None,
) -> list[dict]:
    """Local files carry the harness test_id. Django rows may only have PK `id`."""
    if local_runs:
        return list(local_runs)
    return list(django_runs or [])


def infer_ground_truth_source(result: dict | None) -> str:
    """Provenance for this case's GT — not a global hardcoded source."""
    data = result or {}
    explicit = _text(data.get("ground_truth_source")).lower()
    if explicit in {SOURCE_DRIVE, SOURCE_UPLOAD}:
        return explicit
    if data.get("drive_transcript_file_id") or data.get("drive_soap_gt_file_id"):
        return SOURCE_DRIVE
    if data.get("uploaded_ground_truth_filename"):
        return SOURCE_UPLOAD
    has_gt = bool(
        _text(data.get("ground_truth"))
        or _text(data.get("ground_truth_transcription"))
        or data.get("soap_ground_truth")
    )
    if has_gt and not data.get("drive_audio_file_id"):
        return SOURCE_UPLOAD
    if data.get("drive_audio_file_id") or data.get("drive_transcript_file_id"):
        return SOURCE_DRIVE
    return ""


def infer_audio_source(result: dict | None) -> str:
    data = result or {}
    explicit = _text(data.get("audio_source")).lower()
    if explicit in {SOURCE_DRIVE, SOURCE_UPLOAD}:
        return explicit
    if data.get("drive_audio_file_id"):
        return SOURCE_DRIVE
    if data.get("uploaded_audio_filename") or (
        _text(data.get("audio_filename")) and not data.get("drive_audio_file_id")
    ):
        if _text(data.get("audio_filename")):
            return SOURCE_UPLOAD if not data.get("drive_audio_file_id") else SOURCE_DRIVE
    return ""


def format_pct(value: Any) -> str:
    """Single display format for accuracy percentages on the detail header."""
    if value is None or value == "":
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{int(round(n))}%"


def format_audio_length(seconds: Any) -> str:
    """Single display format for audio length. Shared by info bar and case materials."""
    if seconds is None or seconds == "":
        return "—"
    try:
        n = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    if n >= 60:
        return f"{int(n // 60)}m {int(round(n % 60))}s"
    return f"{int(round(n))}s"


def format_latency_seconds(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    if n < 0:
        return "—"
    return f"{n:.2f}s"


def format_end_to_end(seconds: Any) -> str:
    """Wall-clock total_test_time_seconds — the old status-line 'Latency: 1m 38s'."""
    if seconds is None or seconds == "":
        return "—"
    try:
        n = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if n < 0:
        return "—"
    if n < 60:
        return f"{int(round(n))}s"
    return f"{int(n // 60)}m {int(round(n % 60))}s"


def source_label(source: str) -> str:
    if source == SOURCE_DRIVE:
        return "Google Drive"
    if source == SOURCE_UPLOAD:
        return "Frontend upload"
    return source or "Unknown source"


def _accuracy_details(result: dict) -> dict[str, Any]:
    """One accuracy object for the whole detail header. Do not re-extract elsewhere."""
    trans = result.get("comparison") or result.get("transcription_comparison") or {}
    translation = result.get("translation_comparison") or {}
    soap = result.get("soap_comparison") or {}
    soap_scores = soap.get("scores") or {}
    soap_shown = display_soap_accuracy(soap)
    soap_score = soap_shown.get("percent_value")
    if soap_score is None:
        soap_score = soap_scores.get("gt_vs_generated") or (
            (soap.get("gt_vs_generated") or {}).get("similarity_score")
        )
    return {
        "accuracy_score": result.get("accuracy_score"),
        "transcription_score": trans.get("similarity_score"),
        "translation_score": translation.get("similarity_score"),
        "soap_score": soap_score,
        "summary": trans.get("summary") or "",
        "skipped": bool(result.get("accuracy_skipped")),
        "skip_reason": result.get("accuracy_skip_reason") or "",
        "metrics": (soap.get("gt_vs_generated") or {}).get("metrics") or {},
    }


def display_outcome_counts(counts: dict | None) -> dict[str, int]:
    """Four Prompt 1 buckets for the stat-card row. NA is already excluded."""
    data = counts or {}
    return {
        "Correct": int(data.get("Correct") or 0),
        "Incorrect": int(data.get("Incorrect") or 0),
        "Missing": int(data.get("Missing") or 0),
        "Hallucination": int(data.get("Hallucination") or 0),
    }


def soap_fact_tiles(result: dict | None) -> dict[str, Any]:
    """Four outcome cards: Match (Correct), Incorrect, Missing, Hallucinated.

    Tallies ``soap_facts_from_result`` — the same Prompt 1 list the field
    views already render — not a diff-only walk of ``row.result``.
    """
    data = result or {}
    raw = count_classified_facts(soap_facts_from_result(data))
    if raw is None:
        raw = soap_fact_counts(data.get("soap_comparison"))
    counts = display_outcome_counts(raw)
    total = sum(int(counts.get(tile["key"]) or 0) for tile in SOAP_FACT_TILES)
    tiles = []
    for tile in SOAP_FACT_TILES:
        count = int(counts.get(tile["key"]) or 0)
        percent = int(round(100 * count / total)) if total else 0
        tiles.append({
            "key": tile["key"],
            "label": tile["label"],
            "legend": tile["legend"],
            "count": count,
            "percent": percent,
            "percent_display": format_pct(percent) if total else "—",
        })
    return {"tiles": tiles, "total": total, "has_counts": bool(raw)}


def latency_figures(result: dict | None) -> dict[str, Any]:
    """Flask stage times + end-to-end. Audio length is not a latency figure."""
    data = result or {}
    tr = data.get("transcription_result") or {}
    time_map = tr.get("time") if isinstance(tr.get("time"), dict) else {}
    items = [
        {
            "key": "translation_time",
            "label": "Translation time",
            "value": tr.get("translation-time") if tr.get("translation-time") is not None
            else time_map.get("Translation"),
            "display": "",
        },
        {
            "key": "transcription_time",
            "label": "Transcription time",
            "value": tr.get("transcription-time") if tr.get("transcription-time") is not None
            else time_map.get("ASR"),
            "display": "",
        },
        {
            "key": "llm_time",
            "label": "LLM pre-processing time",
            "value": tr.get("llm-time") if tr.get("llm-time") is not None
            else time_map.get("llm"),
            "display": "",
        },
        {
            "key": "total_time",
            "label": "Total processing time",
            "value": tr.get("total-time"),
            "display": "",
        },
        {
            "key": "end_to_end",
            "label": "End-to-end",
            "value": data.get("total_test_time_seconds"),
            "display": "",
        },
    ]
    for item in items:
        if item["key"] == "end_to_end":
            item["display"] = format_end_to_end(item["value"])
        else:
            item["display"] = format_latency_seconds(item["value"])
    visible = [
        item for item in items
        if item["value"] is not None and item["display"] != "—"
    ]
    return {"items": items, "visible": visible, "has_any": bool(visible)}


def med_diff_summary(result: dict | None) -> dict[str, Any]:
    data = result or {}
    med_val = data.get("medication_validation") or {}
    count = int(med_val.get("difference_count") or 0)
    has_critical = bool(med_val.get("has_critical_differences"))
    if count == 0:
        label = "✓ Meds"
    else:
        label = f"{count} Med Diff{'s' if count != 1 else ''}"
    tone = "high" if count == 0 else ("low" if has_critical else "warn")
    return {
        "count": count,
        "has_critical": has_critical,
        "label": label,
        "tone": tone,
    }


def _audio_length_value(data: dict) -> Any:
    return (
        data.get("audio_duration_seconds")
        or (data.get("transcription_result") or {}).get("audio_length")
        or data.get("audio_length")
    )


def info_field_list(
    *,
    tc_ref: str,
    batch_label: str,
    audio_length_display: str,
    model_name: str,
) -> list[dict[str, str]]:
    """Info-bar pairs that are not already on the header cards.

    Audio file and audio source live on Case materials. Run and language
    are not shown here — they duplicate card / filename context.
    """
    fields = [
        {"key": "tc-ref", "label": "Test Case ID", "value": tc_ref or "—", "id": "detail-tc-ref"},
        {"key": "batch", "label": "Batch", "value": batch_label or "—", "id": "detail-batch-ref"},
        {"key": "audio-length", "label": "Audio length", "value": audio_length_display},
    ]
    if model_name:
        fields.append({"key": "model", "label": "Model", "value": model_name})
    return fields


def header_metric_sites(model: dict | None) -> dict[str, list[Any]]:
    """Every header display of a deduplicated metric, all reading from `model`.

    Changing a model field once is what updates every site in this map.
    """
    data = model or {}
    acc = data.get("accuracy") or {}
    overall = acc.get("accuracy_score")
    trans = acc.get("transcription_score")
    transl = acc.get("translation_score")
    soap = acc.get("soap_score")
    length = data.get("audio_length")
    length_display = data.get("audio_length_display") or format_audio_length(length)
    overall_display = format_pct(overall)
    sites: dict[str, list[Any]] = {
        "overall_accuracy": [overall, overall_display],
        "transcription_score": [trans, format_pct(trans)],
        "translation_score": [transl, format_pct(transl)],
        "soap_score": [soap, format_pct(soap)],
        "audio_length": [length, length_display],
    }
    for field in data.get("info_fields") or []:
        if field.get("key") == "audio-length":
            sites["audio_length"].append(field.get("value"))
    return sites


def _soap_output(result: dict) -> Any:
    if result.get("soap_generated"):
        return result.get("soap_generated")
    tr = result.get("transcription_result") or {}
    if isinstance(tr, dict) and any(
        tr.get(k) for k in ("subjective", "objective", "assessment", "plan", "summary")
    ):
        return {
            "subjective": tr.get("subjective"),
            "objective": tr.get("objective"),
            "assessment": tr.get("assessment"),
            "plan": tr.get("plan"),
            "summary": tr.get("summary"),
        }
    return result.get("soap_raw") or {}


def detail_view_model(
    result: dict | None,
    thresholds: AccuracyThresholds | None = None,
) -> dict[str, Any]:
    """Case-scoped fields the detail view must render. No run-level fallbacks."""
    data = dict(result or {})
    test_id = stable_test_id(data)
    audio_source = infer_audio_source(data)
    gt_source = infer_ground_truth_source(data)
    audio_name = _text(data.get("audio_filename") or data.get("drive_audio_filename"))
    length = _audio_length_value(data)
    length_display = format_audio_length(length)
    model_name = _text(
        data.get("ai_model_used") or data.get("ai_model") or data.get("llm_model")
    )
    run_ref = _text(data.get("run_ref"))
    accuracy = _accuracy_details(data)
    transcription = _text(
        data.get("transcription") or data.get("generated_transcription")
    )
    translation = _text(
        data.get("generated_translation")
        or data.get("translation")
        or data.get("text_translation")
        or ((data.get("transcription_result") or {}).get("debug") or {}).get(
            "translation"
        )
    )
    ground_truth = _text(
        data.get("ground_truth") or data.get("ground_truth_transcription")
    )
    comparison = data.get("comparison") or data.get("transcription_comparison") or {}
    audio_url = f"/api/medsum-test/results/{test_id}/audio" if test_id else ""
    can_play = audio_source == SOURCE_DRIVE and bool(data.get("drive_audio_file_id"))
    shown = display_row_status(
        data.get("status"),
        data.get("final_result"),
        accuracy_score=data.get("accuracy_score") or data.get("similarity_score"),
        soap_comparison=data.get("soap_comparison"),
        accuracy_skipped=bool(data.get("accuracy_skipped")),
        thresholds=thresholds,
    )
    return {
        "test_id": test_id,
        "tc_ref": _text(data.get("tc_ref")),
        "audio_filename": audio_name,
        "audio_source": audio_source,
        "audio_url": audio_url if can_play else "",
        "audio_player": bool(can_play),
        "audio_length": length,
        "audio_length_display": length_display,
        "language": _text(data.get("language")),
        "run_ref": run_ref,
        "model_name": model_name,
        "transcription": transcription,
        "translation": translation,
        "soap_output": _soap_output(data),
        "ground_truth": ground_truth,
        "ground_truth_source": gt_source,
        "comparison": comparison,
        "accuracy": accuracy,
        "soap_facts": soap_fact_tiles(data),
        "latency": latency_figures(data),
        "med_diffs": med_diff_summary(data),
        "info_fields": info_field_list(
            tc_ref=_text(data.get("tc_ref")),
            batch_label=display_batch_label(data.get("batch_id"), data.get("batch_ref")),
            audio_length_display=length_display,
            model_name=model_name,
        ),
        "execution_display": shown["execution"],
        "soap_evaluation_display": shown["evaluation"],
        "clinical_quality_display": shown["quality"],
    }


def load_result_by_stable_id(test_id: str):
    """Load exactly this test_id. Filename first, then scan `test_id` field."""
    wanted = _text(test_id)
    if not wanted or looks_like_integer_pk(wanted):
        return None
    result = load_result(wanted)
    if result and _text(result.test_id) == wanted:
        return result
    for data in load_all_results_raw():
        if stable_test_id(data) == wanted:
            loaded = load_result(stable_test_id(data))
            if loaded and _text(loaded.test_id) == wanted:
                return loaded
    return None
