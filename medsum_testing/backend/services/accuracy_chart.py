"""Dashboard accuracy chart: last-10 cases, or one figure per selected batch.

Case-level scores are the same values Prompt 1 already plots (transcription
similarity / accuracy_score, omitting NOT_SCORED). Batch comparison is the
mean of that batch's own scored cases — not a mixed last-10 line.

Batch labels follow Prompt 9: ``25-Aug-2026 | 001``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

from medsum_testing.backend.services.batch_identity import display_batch_label
from medsum_testing.backend.services.run_summary import transcription_score

CASE_CHART_LIMIT = 10
MAX_COMPARISON_BATCHES = 12
CASE_TITLE = "Accuracy Over Time"
BATCH_TITLE = "Accuracy by Batch"

_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_BATCH_REF_RE = re.compile(r"BATCH-(\d{8})-(\d+)", re.I)
_PROMPT9_RE = re.compile(r"^(\d{2}-[A-Za-z]{3}-\d{4}) \| (\d+)$")
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_YMD_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def filter_results_by_batch_ids(
    rows: Iterable[dict] | None,
    selected_ids: Iterable[str] | None,
) -> list[dict]:
    """Keep rows whose batch_id is in the selected set. Empty set = no filter."""
    items = list(rows or [])
    wanted = [str(v) for v in (selected_ids or []) if v and str(v) != "all"]
    if not wanted:
        return items
    allowed = set(wanted)
    return [r for r in items if str(r.get("batch_id") or "") in allowed]


def result_score(row: dict | None):
    """Same fallbacks the existing last-10 line chart uses."""
    data = row or {}
    comp = data.get("comparison") if isinstance(data.get("comparison"), dict) else {}
    trans = (
        data.get("transcription_comparison")
        if isinstance(data.get("transcription_comparison"), dict)
        else {}
    )
    score = (
        (comp or {}).get("similarity_score")
        if comp
        else None
    )
    if score is None:
        score = (trans or {}).get("similarity_score")
    if score is None:
        score = data.get("accuracy_score")
    if score is None:
        score = data.get("similarity_score")
    if score is None or score == "":
        return None
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


def _is_chart_complete(row: dict) -> bool:
    status = str(row.get("status") or "").strip().lower()
    verdict = str(row.get("final_result") or "").strip().lower()
    return status == "complete" or verdict in ("pass", "complete_no_accuracy")


def case_chart_points(results: Iterable[dict] | None, *, limit: int = CASE_CHART_LIMIT) -> list[dict]:
    points = []
    for row in results or []:
        if not _is_chart_complete(row):
            continue
        score = result_score(row)
        if score is None:
            continue
        points.append({
            "name": row.get("audio_filename") or row.get("filename") or row.get("tc_ref") or "",
            "timestamp": str(row.get("timestamp") or row.get("created_at") or ""),
            "score": score,
        })
    points.sort(key=lambda p: p["timestamp"])
    return points[-limit:]


def batch_accuracy(rows: Iterable[dict] | None) -> float | None:
    """Mean of this batch's Prompt 1 case-level scores. NOT_SCORED omitted."""
    scores = [s for row in (rows or []) if (s := transcription_score(row)) is not None]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 1)


def _parse_ymd(year: int, month: int, day: int) -> tuple[int, int, int] | None:
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return year, month, day


def parse_batch_date_parts(raw: Any) -> tuple[int, int, int] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    m = _ISO_RE.match(text)
    if m:
        return _parse_ymd(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    compact = text.replace("-", "")[:8]
    m = _YMD_RE.match(compact)
    if m:
        return _parse_ymd(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.year, dt.month, dt.day
    except ValueError:
        return None


def format_batch_date(raw: Any) -> str:
    parts = parse_batch_date_parts(raw)
    if not parts:
        return ""
    year, month, day = parts
    return f"{day:02d}-{_MONTHS[month - 1]}-{year}"


def parsed_batch_seq(batch: dict | None) -> str | None:
    data = batch or {}
    explicit = data.get("batch_seq")
    if explicit is not None and str(explicit).strip() != "":
        try:
            return f"{int(str(explicit).strip()):03d}"
        except ValueError:
            digits = re.sub(r"\D", "", str(explicit))
            return digits.zfill(3) if digits else None
    ref = str(
        data.get("batch_id") or data.get("batch_ref") or data.get("run_ref") or ""
    )
    prompt9 = _PROMPT9_RE.match(ref.strip())
    if prompt9:
        return f"{int(prompt9.group(2)):03d}"
    m = _BATCH_REF_RE.search(ref)
    if m:
        return f"{int(m.group(2)):03d}"
    return None


def collect_batches(rows: Iterable[dict] | None) -> list[dict]:
    seen: dict[str, dict] = {}
    for row in rows or []:
        batch_id = str(row.get("batch_id") or "").strip()
        if not batch_id or batch_id in seen:
            continue
        seen[batch_id] = {
            "batch_id": batch_id,
            "batch_ref": row.get("batch_ref") or row.get("run_ref") or "",
            "batch_display_label": row.get("batch_display_label") or "",
            "batch_seq": row.get("batch_seq"),
            "timestamp": str(row.get("created_at") or row.get("timestamp") or ""),
        }
    return list(seen.values())


def assign_batch_labels(batches: Iterable[dict] | None) -> dict[str, str]:
    """Prompt 9 labels: persisted ``batch_id`` (``25-Aug-2026 | 001``)."""
    items = [dict(b) for b in (batches or []) if b and b.get("batch_id")]
    items.sort(key=lambda b: str(b.get("timestamp") or ""))
    per_day: dict[tuple[int, int, int], int] = defaultdict(int)
    labels: dict[str, str] = {}
    for batch in items:
        bid = str(batch["batch_id"])
        stored = display_batch_label(bid, str(batch.get("batch_ref") or ""))
        prompt9 = _PROMPT9_RE.match(stored.strip()) or _PROMPT9_RE.match(bid.strip())
        if prompt9:
            labels[bid] = f"{prompt9.group(1)} | {int(prompt9.group(2)):03d}"
            continue
        if stored and stored not in ("—", bid) and not _looks_like_uuid_prefix(stored, bid):
            labels[bid] = stored
            continue
        parts = parse_batch_date_parts(batch.get("timestamp"))
        date_text = format_batch_date(batch.get("timestamp"))
        seq = parsed_batch_seq(batch)
        if seq is None and parts:
            per_day[parts] += 1
            seq = f"{per_day[parts]:03d}"
        elif seq is None:
            seq = "001"
        if date_text and seq:
            labels[bid] = f"{date_text} | {seq}"
        else:
            labels[bid] = stored if stored and stored != "—" else (seq or bid[:8])
    return labels


def _looks_like_uuid_prefix(label: str, batch_id: str) -> bool:
    text = str(label or "")
    ident = str(batch_id or "")
    return bool(ident) and text == ident[:8]


def batch_display_label(batch: dict | None, labels: dict[str, str] | None = None) -> str:
    data = batch or {}
    batch_id = str(data.get("batch_id") or "")
    if labels and batch_id in labels:
        return labels[batch_id]
    assigned = assign_batch_labels([data])
    return assigned.get(batch_id, batch_id[:8] or "Batch")


def _batch_sort_key(batch: dict) -> tuple:
    parts = parse_batch_date_parts(batch.get("timestamp")) or (0, 0, 0)
    seq = parsed_batch_seq(batch) or "000"
    return (parts, seq, str(batch.get("timestamp") or ""))


def build_accuracy_chart(
    results: Iterable[dict] | None,
    selected_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return a render model: last-10 cases, or one bar per selected batch."""
    rows = list(results or [])
    wanted = [str(v) for v in (selected_ids or []) if v and str(v) != "all"]
    if len(wanted) < 2:
        points = case_chart_points(rows)
        return {
            "mode": "cases",
            "title": CASE_TITLE,
            "labels": [(p["name"] or "run")[:12] or "run" for p in points],
            "values": [p["score"] for p in points],
            "truncated": False,
            "shown": len(points),
            "selected": len(wanted),
            "note": "",
            "legend": [],
        }

    grouped: dict[str, list[dict]] = {bid: [] for bid in wanted}
    for row in rows:
        bid = str(row.get("batch_id") or "")
        if bid in grouped:
            grouped[bid].append(row)

    meta = {b["batch_id"]: b for b in collect_batches(rows)}
    for bid in wanted:
        meta.setdefault(bid, {"batch_id": bid, "timestamp": "", "batch_ref": ""})
        if grouped[bid] and not meta[bid].get("timestamp"):
            meta[bid]["timestamp"] = str(
                grouped[bid][0].get("created_at") or grouped[bid][0].get("timestamp") or ""
            )

    ordered = sorted((meta[bid] for bid in wanted), key=_batch_sort_key)
    truncated = len(ordered) > MAX_COMPARISON_BATCHES
    if truncated:
        ordered = ordered[-MAX_COMPARISON_BATCHES:]
    labels_map = assign_batch_labels(ordered)

    legend = []
    labels = []
    values = []
    for batch in ordered:
        bid = batch["batch_id"]
        label = labels_map.get(bid) or batch_display_label(batch)
        accuracy = batch_accuracy(grouped.get(bid) or [])
        labels.append(label)
        values.append(accuracy)
        legend.append({
            "batch_id": bid,
            "label": label,
            "value": accuracy,
        })

    note = ""
    if truncated:
        note = (
            f"Showing {len(ordered)} of {len(wanted)} selected batches "
            f"(most recent {MAX_COMPARISON_BATCHES})"
        )

    return {
        "mode": "batches",
        "title": BATCH_TITLE,
        "labels": labels,
        "values": values,
        "truncated": truncated,
        "shown": len(ordered),
        "selected": len(wanted),
        "note": note,
        "legend": legend,
    }
