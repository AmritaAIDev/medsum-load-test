"""Structured Generated-vs-Ground-Truth rows for the test-case detail panel.

Presents SOAP section_details and medication_validation already computed by
the accuracy engine. Does not call the comparator or invent a second severity
scale — engine values (none/low/medium/high/critical) are mapped to the
panel labels Critical / Major / Minor.
"""

from __future__ import annotations

import re
from typing import Any

from medsum_testing.backend.services.soap_detail_table import (
    HALLUCINATION,
    MISSING,
    error_facts,
    facts_for_section,
    normalize_result_type,
    stored_soap_facts,
)

SOAP_SECTION_KEYS = ("subjective", "objective", "assessment", "plan")
SOAP_SECTION_LABELS = {
    "subjective": "Subjective",
    "objective": "Objective",
    "assessment": "Assessment",
    "plan": "Plan",
}
MEDICATION_ROW_ID = "medication"
MEDICATION_ROW_LABEL = "Medication (From Raw LLM)"
ROW_IDS = SOAP_SECTION_KEYS + (MEDICATION_ROW_ID,)

TABLE_COLUMNS = (
    "Section",
    "Ground Truth (GT)",
    "Generated Output (Gen)",
    "Raw LLM (Med Only)",
    "Difference & Notes",
)

# Filter: which comparison a row must participate in. "All" shows every row.
# Generated vs GT — gt_vs_generated / Gen-column diffs (incl. formatting-only).
# Raw LLM vs GT — gt_vs_raw / Raw-LLM-column diffs (medication row, or SOAP
# rows that have gt_vs_raw section_details even though the cell shows "—").
DIFF_SCOPE_ALL = "all"
DIFF_SCOPE_GEN_VS_GT = "gen_vs_gt"
DIFF_SCOPE_RAW_VS_GT = "raw_vs_gt"
DIFF_SCOPE_OPTIONS = (
    (DIFF_SCOPE_ALL, "All"),
    (DIFF_SCOPE_GEN_VS_GT, "Generated vs GT"),
    (DIFF_SCOPE_RAW_VS_GT, "Raw LLM vs GT"),
)

DISPLAY_CRITICAL = "Critical"
DISPLAY_MAJOR = "Major"
DISPLAY_MINOR = "Minor"

_ENGINE_RANK = {
    "critical": 4,
    "high": 4,
    "medium": 3,
    "low": 2,
    "none": 1,
    "unknown": 1,
    "": 1,
}

_DASH = "—"
_BLANK_MARKERS = frozenset({"", "—", "-"})
_NA_MARKERS = frozenset({
    "NA", "na", "n/a", "N/A", "Not applicable", "not applicable",
    "Nothing to report", "not applicable/established",
})
_EMPTY_MARKERS = _BLANK_MARKERS | {m.lower() for m in _NA_MARKERS} | _NA_MARKERS
_DOSE_RE = re.compile(r"\d+(?:\.\d+)?\s*mg\b", re.I)
_NORM_RE = re.compile(r"[.,\-–—\s]")


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_empty(value: Any) -> bool:
    text = _text(value)
    if text in _BLANK_MARKERS or not text:
        return True
    return text in _NA_MARKERS or text.lower() in {m.lower() for m in _NA_MARKERS}


def _is_na_marker(value: Any) -> bool:
    """Prompt 1 NA (not established) — not the same as Missing."""
    text = _text(value)
    if not text or text in _BLANK_MARKERS:
        return True
    return text in _NA_MARKERS or text.lower() in {m.lower() for m in _NA_MARKERS}


def _norm(value: Any) -> str:
    return _NORM_RE.sub("", _text(value).lower())


def display_cell(value: Any) -> str:
    text = _text(value)
    return text if text else _DASH


def display_severity(engine_severity: str | None) -> str:
    """Map the existing engine scale onto the mockup's three labels."""
    key = _text(engine_severity).lower()
    if key in ("critical", "high"):
        return DISPLAY_CRITICAL
    if key == "medium":
        return DISPLAY_MAJOR
    return DISPLAY_MINOR


def worst_engine_severity(diffs: list | None) -> str:
    worst = "none"
    worst_rank = _ENGINE_RANK["none"]
    for diff in diffs or []:
        if not isinstance(diff, dict):
            continue
        sev = _text(diff.get("severity")).lower() or "low"
        rank = _ENGINE_RANK.get(sev, 1)
        if rank > worst_rank:
            worst = sev
            worst_rank = rank
    return worst


def is_formatting_only(left: Any, right: Any) -> bool:
    """Same classifier Prompt 5 uses: punctuation/spacing are not medical diffs."""
    if _is_empty(left) and _is_empty(right):
        return False
    if _text(left) == _text(right):
        return False
    if _is_empty(left) or _is_empty(right):
        return False
    return _norm(left) == _norm(right)


def texts_differ(left: Any, right: Any) -> bool:
    if _is_empty(left) and _is_empty(right):
        return False
    return _norm(left) != _norm(right)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if value is None or _is_empty(value):
        return []
    return [value]


def generated_soap(result: dict | None) -> dict:
    data = _as_dict(result)
    if data.get("soap_generated"):
        return _as_dict(data.get("soap_generated"))
    tr = _as_dict(data.get("transcription_result"))
    if any(tr.get(k) for k in ("subjective", "objective", "assessment", "plan", "summary")):
        return {
            "subjective": tr.get("subjective"),
            "objective": tr.get("objective"),
            "assessment": tr.get("assessment"),
            "plan": tr.get("plan"),
            "summary": tr.get("summary"),
        }
    return _as_dict(data.get("soap_raw"))


def raw_soap(result: dict | None) -> dict:
    data = _as_dict(result)
    if data.get("soap_raw"):
        return _as_dict(data.get("soap_raw"))
    tr = _as_dict(data.get("transcription_result"))
    debug = _as_dict(tr.get("debug"))
    return _as_dict(debug.get("raw_soap") or debug.get("raw soap"))


def _format_one_med(med: Any) -> str:
    if not isinstance(med, dict):
        return _text(med)
    parts = [
        _text(med.get("drug_name") or med.get("name")),
        _text(med.get("dose")),
        _text(med.get("schedule") or med.get("frequency")),
        _text(med.get("duration")),
    ]
    return " ".join(p for p in parts if p and p.lower() not in ("na", "n/a"))


def format_medications(meds: Any) -> str:
    items = _as_list(meds)
    if not items:
        return ""
    lines = [_format_one_med(m) for m in items]
    return "; ".join(p for p in lines if p)


def flatten_section(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if _is_na_marker(text):
            return ""
        return text
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [flatten_section(item) for item in value]
        return "; ".join(p for p in parts if p)
    if isinstance(value, dict):
        if "drug_name" in value or "dose" in value:
            return _format_one_med(value)
        parts = []
        for nested in value.values():
            text = flatten_section(nested)
            if text:
                parts.append(text)
        return " ".join(parts)
    return _text(value)


def _section_details(comparison: Any, section_key: str) -> dict:
    block = _as_dict(comparison)
    details = _as_dict(block.get("section_details"))
    return _as_dict(details.get(section_key))


def _section_diffs(comparison: Any, section_key: str) -> list[dict]:
    raw = _section_details(comparison, section_key).get("differences") or []
    out = []
    for d in raw:
        if not isinstance(d, dict):
            continue
        label = normalize_result_type(d.get("type") or d.get("result"))
        if label in ("NA", "N/A", "Correct"):
            continue
        out.append(d)
    return out


def _fact_as_diff(fact: dict) -> dict[str, Any]:
    return {
        "field": fact.get("field"),
        "ground_truth": fact.get("ground_truth"),
        "generated": fact.get("generated"),
        "type": str(normalize_result_type(fact.get("result") or fact.get("type")) or "").lower(),
        "result": normalize_result_type(fact.get("result") or fact.get("type")),
        "severity": str(fact.get("criticality") or "Normal").lower(),
    }


def _badge_for_type(diff: dict | None) -> str:
    if not diff:
        return ""
    kind = normalize_result_type(diff.get("type") or diff.get("result"))
    field = _text(diff.get("field")).lower()
    if kind == MISSING:
        return "Missing"
    if kind == HALLUCINATION:
        return "Hallucination"
    if kind == "Incorrect" or kind.lower() == "incorrect":
        if field == "dose" or "dose" in _text(diff.get("type")).lower():
            return "Dose difference"
        return "Incorrect"
    raw_kind = _text(diff.get("type")).lower()
    if raw_kind in ("extra", "added_in_final"):
        return "Hallucination"
    if raw_kind in ("missing", "removed_in_final"):
        return "Missing"
    if field == "dose" or "dose" in raw_kind:
        return "Dose difference"
    if raw_kind == "field_changed" and field == "drug_name":
        return "Name difference"
    if raw_kind in ("name_normalized", "name_difference"):
        return "Name difference"
    if raw_kind == "incorrect":
        return "Incorrect"
    if raw_kind == "field_changed":
        return field.replace("_", " ").title() if field else "Changed"
    if kind in ("Correct", "N/A", "NA"):
        return ""
    return _text(diff.get("type")).replace("_", " ").title()


def _excerpt(value: Any, limit: int = 80) -> str:
    text = _text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _notes_from_diff(diff: dict | None, fallback: str = "") -> str:
    if not diff:
        return fallback
    kind = _text(diff.get("type") or diff.get("result")).lower()
    field = _text(diff.get("field")).lower()
    gt = _text(diff.get("ground_truth") or diff.get("raw_value"))
    gen = _text(diff.get("generated") or diff.get("final_value"))
    detail = _text(diff.get("detail"))
    if kind == "missing" or normalize_result_type(kind) == MISSING:
        excerpt = _excerpt(gt)
        return f"Missing: '{excerpt}'" if excerpt else "Missing"
    if normalize_result_type(kind) == HALLUCINATION or kind in ("extra", "added_in_final"):
        excerpt = _excerpt(gen)
        return f"Hallucination: '{excerpt}'" if excerpt else "Hallucination"
    if field == "dose" or "dose" in kind:
        left = gt or _text(diff.get("raw_value"))
        right = gen or _text(diff.get("final_value"))
        if left and right:
            return f"Dose difference ({left} vs {right})"
        return "Dose difference"
    if detail:
        return detail
    badge = _badge_for_type(diff)
    if gt and gen:
        return f"{badge}: '{_excerpt(gt)}' vs '{_excerpt(gen)}'" if badge else fallback
    return badge or fallback


def _primary_diff(diffs: list[dict] | None) -> dict | None:
    if not diffs:
        return None
    return max(
        diffs,
        key=lambda d: _ENGINE_RANK.get(_text(d.get("severity")).lower(), 1),
    )


def _dose_token(text: str) -> str:
    match = _DOSE_RE.search(text or "")
    return match.group(0) if match else ""


def _unique_token(focus: str, other: str) -> str:
    dose = _dose_token(focus)
    other_dose = _dose_token(other)
    if dose and other_dose and _norm(dose) != _norm(other_dose):
        return dose
    focus_words = [w for w in re.split(r"\s+", _text(focus)) if w]
    other_norm = {_norm(w) for w in re.split(r"\s+", _text(other)) if w}
    for word in focus_words:
        n = _norm(word)
        if n and n not in other_norm:
            return word
    return ""


def _highlights(gt: str, gen: str, raw: str, diff: dict | None) -> dict[str, str]:
    gt_h = _text((diff or {}).get("ground_truth"))
    gen_h = _text((diff or {}).get("generated"))
    raw_h = _text((diff or {}).get("raw_value"))
    final_h = _text((diff or {}).get("final_value"))
    field = _text((diff or {}).get("field")).lower()
    if field == "dose" or (gt_h and gen_h and _dose_token(gt_h) and _dose_token(gen_h)):
        return {
            "gt": _dose_token(gt) or gt_h or _unique_token(gt, raw or gen),
            "gen": _dose_token(gen) or final_h or gen_h or _unique_token(gen, raw or gt),
            "raw": _dose_token(raw) or raw_h or _unique_token(raw, gt),
        }
    return {
        "gt": gt_h if gt_h and gt_h in gt else _unique_token(gt, gen or raw),
        "gen": gen_h if gen_h and gen_h in gen else _unique_token(gen, gt),
        "raw": raw_h if raw_h and raw_h in raw else _unique_token(raw, gt),
    }


def _explanation(
    *,
    has_diff: bool,
    formatting: bool,
    badge: str,
    notes: str,
    gt: str,
    gen: str,
    raw: str,
    diff: dict | None,
) -> str:
    if not has_diff and not formatting:
        return "No difference. Generated output matches Ground Truth."
    if formatting and not has_diff:
        return "Minor formatting difference only; medical meaning matches Ground Truth."
    gt_eq_gen = not texts_differ(gt, gen)
    raw_vs_gt = (not _is_empty(raw)) and texts_differ(raw, gt)
    if badge == "Dose difference" or _text((diff or {}).get("field")).lower() == "dose":
        if raw_vs_gt and gt_eq_gen:
            return (
                "Dose differs between Raw LLM and Ground Truth. "
                "Generated output matches GT."
            )
        if not gt_eq_gen:
            return "Dose differs between Generated output and Ground Truth."
    if normalize_result_type((diff or {}).get("type") or (diff or {}).get("result")) == MISSING:
        excerpt = _excerpt(_text((diff or {}).get("ground_truth")))
        if excerpt:
            return (
                f"Generated output is missing detail present in Ground Truth: "
                f"'{excerpt}'."
            )
        return "Generated output is missing detail present in Ground Truth."
    if normalize_result_type((diff or {}).get("type") or (diff or {}).get("result")) == HALLUCINATION:
        excerpt = _excerpt(_text((diff or {}).get("generated")))
        if excerpt:
            return f"Generated output includes a hallucination not in Ground Truth: '{excerpt}'."
        return "Generated output includes a hallucination not in Ground Truth."
    if raw_vs_gt and gt_eq_gen:
        return (
            "Raw LLM differs from Ground Truth. Generated output matches GT."
        )
    if notes and notes != "No difference":
        return notes
    return "Generated output differs from Ground Truth."


def _soap_row(result: dict, section_key: str) -> dict[str, Any]:
    soap_comp = _as_dict(result.get("soap_comparison"))
    gt_vs_gen = soap_comp.get("gt_vs_generated")
    gt_vs_raw = soap_comp.get("gt_vs_raw")
    gen_diffs = _section_diffs(gt_vs_gen, section_key)
    raw_diffs = _section_diffs(gt_vs_raw, section_key)
    all_facts = stored_soap_facts(result)
    if all_facts:
        gen_diffs = [
            _fact_as_diff(f)
            for f in error_facts(facts_for_section(all_facts, section_key))
        ]
    gt_soap = _as_dict(result.get("soap_ground_truth"))
    gen_soap = generated_soap(result)
    gt_text = flatten_section(gt_soap.get(section_key))
    gen_text = flatten_section(gen_soap.get(section_key))
    formatting = is_formatting_only(gt_text, gen_text)
    primary = _primary_diff(gen_diffs) or _primary_diff(raw_diffs)
    engine_sev = worst_engine_severity(gen_diffs) or "none"
    if engine_sev == "none" and formatting:
        engine_sev = "low"
    has_gen = bool(gen_diffs) or formatting
    has_raw = bool(raw_diffs)
    has_diff = bool(gen_diffs or raw_diffs)
    badge = ""
    notes = "No difference"
    if formatting and not gen_diffs:
        badge = "Formatting"
        notes = "Minor formatting difference (space)"
        if _text(gt_text).replace(" ", "") != _text(gen_text).replace(" ", ""):
            notes = "Minor formatting difference"
    if gen_diffs:
        gen_primary = _primary_diff(gen_diffs)
        badge = _badge_for_type(gen_primary)
        notes = _notes_from_diff(gen_primary, notes)
    elif raw_diffs and not formatting:
        notes = _notes_from_diff(_primary_diff(raw_diffs), notes)
    highlights = _highlights(gt_text, gen_text, "", primary)
    return {
        "id": section_key,
        "section_key": section_key,
        "section_label": SOAP_SECTION_LABELS[section_key],
        "severity_engine": engine_sev,
        "severity": display_severity(engine_sev),
        "gt_text": gt_text,
        "gen_text": gen_text,
        "raw_text": "",
        "raw_display": _DASH,
        "gen_badge": badge if has_gen and badge else "",
        "raw_badge": "",
        "notes": notes,
        "has_difference": has_diff or formatting,
        "has_gen_gt_diff": has_gen,
        "has_raw_gt_diff": has_raw,
        "explanation": _explanation(
            has_diff=has_diff,
            formatting=formatting,
            badge=badge,
            notes=notes,
            gt=gt_text,
            gen=gen_text,
            raw="",
            diff=primary,
        ),
        "gt_highlight": highlights["gt"] if (has_diff or formatting) else "",
        "gen_highlight": highlights["gen"] if (has_diff or formatting) else "",
        "raw_highlight": "",
    }


def _medication_row(result: dict) -> dict[str, Any]:
    med_val = _as_dict(result.get("medication_validation"))
    diffs = [d for d in (med_val.get("differences") or []) if isinstance(d, dict)]
    gt_soap = _as_dict(result.get("soap_ground_truth"))
    gen_soap = generated_soap(result)
    raw = raw_soap(result)
    gt_text = format_medications(
        (_as_dict(gt_soap.get("plan"))).get("medications")
    )
    gen_text = format_medications(
        med_val.get("final_medications")
        or (_as_dict(gen_soap.get("plan"))).get("medications")
    )
    raw_text = format_medications(
        med_val.get("raw_medications")
        or (_as_dict(raw.get("plan"))).get("medications")
    )
    formatting_gen = is_formatting_only(gt_text, gen_text)
    formatting_raw = is_formatting_only(gt_text, raw_text)
    primary = _primary_diff(diffs)
    engine_sev = worst_engine_severity(diffs)
    gen_vs_gt = texts_differ(gt_text, gen_text) or formatting_gen
    raw_vs_gt = (not _is_empty(raw_text)) and (
        texts_differ(gt_text, raw_text) or formatting_raw
    )
    # Med Diffs pill uses medication_validation (raw vs generated). If those
    # diffs exist and gen matches GT, they are raw-vs-GT for this table.
    if diffs and not gen_vs_gt:
        raw_vs_gt = True
    if engine_sev == "none" and (formatting_gen or formatting_raw):
        engine_sev = "low"
    badge = ""
    raw_badge = ""
    notes = "No difference"
    if formatting_gen and not diffs:
        badge = "Formatting"
        notes = "Minor formatting difference (space)"
    if primary:
        kind_badge = _badge_for_type(primary)
        notes = _notes_from_diff(primary, notes)
        if gen_vs_gt and not formatting_gen:
            badge = kind_badge
        elif formatting_gen:
            badge = "Formatting"
        if raw_vs_gt:
            raw_badge = kind_badge or ("Formatting" if formatting_raw else "")
        if kind_badge == "Dose difference" or _text(primary.get("field")).lower() == "dose":
            left = _text(primary.get("raw_value")) or _dose_token(raw_text)
            right = (
                _text(primary.get("ground_truth"))
                or _dose_token(gt_text)
                or _text(primary.get("final_value"))
            )
            if left and right and left != right:
                notes = f"Dose difference ({right} vs {left})" if not gen_vs_gt else (
                    f"Dose difference ({_dose_token(gt_text) or right} vs "
                    f"{_dose_token(gen_text) or left})"
                )
                if not gen_vs_gt:
                    notes = f"Dose difference ({_dose_token(gt_text) or right} vs {_dose_token(raw_text) or left})"
    elif formatting_raw and not gen_vs_gt:
        raw_badge = "Formatting"
        notes = "Minor formatting difference"
    has_diff = bool(diffs) or gen_vs_gt or raw_vs_gt
    highlights = _highlights(gt_text, gen_text, raw_text, primary)
    return {
        "id": MEDICATION_ROW_ID,
        "section_key": MEDICATION_ROW_ID,
        "section_label": MEDICATION_ROW_LABEL,
        "severity_engine": engine_sev,
        "severity": display_severity(engine_sev),
        "gt_text": gt_text,
        "gen_text": gen_text,
        "raw_text": raw_text,
        "raw_display": display_cell(raw_text),
        "gen_badge": badge,
        "raw_badge": raw_badge,
        "notes": notes,
        "has_difference": has_diff,
        "has_gen_gt_diff": gen_vs_gt,
        "has_raw_gt_diff": raw_vs_gt,
        "explanation": _explanation(
            has_diff=bool(diffs) or gen_vs_gt or raw_vs_gt,
            formatting=formatting_gen or formatting_raw,
            badge=badge or raw_badge,
            notes=notes,
            gt=gt_text,
            gen=gen_text,
            raw=raw_text,
            diff=primary,
        ),
        "gt_highlight": highlights["gt"] if has_diff else "",
        "gen_highlight": highlights["gen"] if has_diff else "",
        "raw_highlight": highlights["raw"] if has_diff else "",
    }


def build_comparison_rows(result: dict | None) -> list[dict[str, Any]]:
    data = _as_dict(result)
    rows = [_soap_row(data, key) for key in SOAP_SECTION_KEYS]
    rows.append(_medication_row(data))
    return rows


def filter_comparison_rows(
    rows: list[dict[str, Any]] | None,
    *,
    section: str = "all",
    severity: str = "all",
    show_for: str = DIFF_SCOPE_ALL,
    query: str = "",
) -> list[dict[str, Any]]:
    """AND-combine section, severity, comparison-scope, and keyword search."""
    wanted_section = _text(section).lower() or "all"
    wanted_sev = _text(severity).lower() or "all"
    scope = _text(show_for).lower() or DIFF_SCOPE_ALL
    needle = _text(query).lower()
    out = []
    for row in rows or []:
        if wanted_section not in ("all", "all sections"):
            if _text(row.get("id")).lower() != wanted_section:
                continue
        if wanted_sev not in ("all", ""):
            if _text(row.get("severity")).lower() != wanted_sev:
                continue
        if scope == DIFF_SCOPE_GEN_VS_GT and not row.get("has_gen_gt_diff"):
            continue
        if scope == DIFF_SCOPE_RAW_VS_GT and not row.get("has_raw_gt_diff"):
            continue
        if needle:
            hay = " ".join(
                [
                    _text(row.get("section_label")),
                    _text(row.get("gt_text")),
                    _text(row.get("gen_text")),
                ]
            ).lower()
            if needle not in hay:
                continue
        out.append(row)
    return out


def details_for_row(row: dict | None) -> dict[str, Any]:
    """Difference Details payload — this row's values, not another row's."""
    data = _as_dict(row)
    return {
        "id": data.get("id"),
        "gt_text": data.get("gt_text") or "",
        "gen_text": data.get("gen_text") or "",
        "raw_text": data.get("raw_text") or "",
        "raw_display": data.get("raw_display") or _DASH,
        "explanation": data.get("explanation") or "",
        "gt_highlight": data.get("gt_highlight") or "",
        "gen_highlight": data.get("gen_highlight") or "",
        "raw_highlight": data.get("raw_highlight") or "",
    }
