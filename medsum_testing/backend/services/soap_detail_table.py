"""SOAP Detail-tab fact table: Field | Ground Truth | Generated | Result.

Reads Prompt 1's evaluated facts (Correct / Incorrect / Missing / Hallucination,
plus NA). Scoring still uses those labels. Summary/Detail filter badges remap
for display only: both-empty (NA) → Missing; any non-hallucination error
(Prompt 1 Incorrect + Missing) → Incorrect.
"""

from __future__ import annotations

import re
from typing import Any

from medsum_testing.backend.services.soap_fact_scorer import (
    CORRECT,
    HALLUCINATION,
    INCORRECT,
    MISSING,
    NA,
    classify_pair,
    is_na_value,
    resolve_field_spec,
)

SOAP_DETAIL_COLUMNS = (
    "Field Name",
    "Ground Truth",
    "Generated Output",
    "Result",
)

SOAP_SECTION_ORDER = ("Subjective", "Objective", "Assessment", "Plan")

RESULT_LABELS = (CORRECT, INCORRECT, MISSING, HALLUCINATION, NA)

_RESULT_CSS = {
    CORRECT: "soap-result-correct",
    INCORRECT: "soap-result-incorrect",
    MISSING: "soap-result-missing",
    HALLUCINATION: "soap-result-hallucination",
    NA: "soap-result-na",
}

_TYPE_TO_RESULT = {
    "correct": CORRECT,
    "incorrect": INCORRECT,
    "missing": MISSING,
    "missing detail": MISSING,
    "removed in final": MISSING,
    "hallucination": HALLUCINATION,
    "extra": HALLUCINATION,
    "added in final": HALLUCINATION,
    "field changed": INCORRECT,
    "na": NA,
    "n/a": NA,
    "n a": NA,
}

_DASH = "—"


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_result_type(raw: Any) -> str:
    """Map engine / legacy types onto Prompt 1's four-way (+ NA) labels."""
    key = " ".join(_text(raw).lower().replace("_", " ").split())
    mapped = _TYPE_TO_RESULT.get(key)
    if mapped:
        return mapped
    titled = _text(raw)
    if titled in RESULT_LABELS:
        return titled
    return titled or ""


def display_fact_value(value: Any) -> str:
    """NA / empty show as an em dash, never a wall of literal N/A strings."""
    if is_na_value(value):
        return _DASH
    text = _text(value)
    return text if text else _DASH


def display_field_name(fact: dict | None) -> str:
    data = _as_dict(fact)
    name = _text(data.get("field") or data.get("base_field"))
    if not name:
        return "Unknown"
    if "_" in name and " " not in name:
        return name.replace("_", " ").title()
    return name


def fact_section_label(fact: dict | None) -> str:
    data = _as_dict(fact)
    raw = _text(data.get("section"))
    if raw:
        return raw[:1].upper() + raw[1:] if raw.lower() == raw else raw
    spec = resolve_field_spec(data.get("base_field") or data.get("field") or "")
    section = _text(spec.get("section"))
    if section:
        return section[:1].upper() + section[1:]
    return "Other"


def generated_soap_from_result(result: dict | None) -> dict[str, Any]:
    data = _as_dict(result)
    if data.get("soap_generated"):
        return _as_dict(data.get("soap_generated"))
    tr = _as_dict(data.get("transcription_result"))
    if any(tr.get(key) for key in ("subjective", "objective", "assessment", "plan", "summary")):
        return {
            "subjective": tr.get("subjective"),
            "objective": tr.get("objective"),
            "assessment": tr.get("assessment"),
            "plan": tr.get("plan"),
            "summary": tr.get("summary"),
        }
    return _as_dict(data.get("soap_raw"))


def stored_soap_facts(result: dict | None) -> list[dict[str, Any]]:
    """Facts already scored onto the result. Empty means 'not stored', not NA."""
    data = _as_dict(result)
    soap = _as_dict(data.get("soap_comparison"))
    pair = soap.get("gt_vs_generated")
    block = pair if isinstance(pair, dict) else soap
    facts = _as_dict(block).get("facts")
    if isinstance(facts, list):
        return [row for row in facts if isinstance(row, dict)]
    if isinstance(soap.get("facts"), list):
        return [row for row in soap["facts"] if isinstance(row, dict)]
    return []


_MED_LEAF_KEYS = ("drug_name", "dose", "schedule", "duration", "instructions")


def _emit_nested_fact(
    section: str,
    field: str,
    gt_val: Any,
    gen_val: Any,
    out: list[dict],
    *,
    index: int | None = None,
) -> None:
    classified = classify_pair(gt_val, gen_val)
    if classified["result"] == NA:
        return
    spec = resolve_field_spec(field)
    section_label = _text(spec.get("section")) or (
        section[:1].upper() + section[1:] if section else "Other"
    )
    display = _text(spec.get("field")) or display_field_name({"field": field})
    if index is not None and not re.search(r"\[\d+\]$", display):
        display = f"{display} [{index + 1}]"
    out.append({
        "section": section_label,
        "field": display,
        "base_field": field,
        "ground_truth": gt_val,
        "generated": gen_val,
        "result": classified["result"],
        "criticality": spec.get("criticality") or "Normal",
        "categories": list(spec.get("categories") or []),
        "index": index,
    })


def _walk_nested_pair(
    gt_node: Any,
    gen_node: Any,
    section: str,
    prefix: str,
    out: list[dict],
) -> None:
    gt_dict = gt_node if isinstance(gt_node, dict) else {}
    gen_dict = gen_node if isinstance(gen_node, dict) else {}
    if isinstance(gt_node, dict) or isinstance(gen_node, dict):
        keys = list(dict.fromkeys(list(gt_dict) + list(gen_dict)))
        for key in keys:
            path = f"{prefix}.{key}" if prefix else str(key)
            _walk_nested_pair(gt_dict.get(key), gen_dict.get(key), section, path, out)
        return
    if isinstance(gt_node, list) or isinstance(gen_node, list):
        gt_list = gt_node if isinstance(gt_node, list) else []
        gen_list = gen_node if isinstance(gen_node, list) else []
        n = max(len(gt_list), len(gen_list))
        for i in range(n):
            left = gt_list[i] if i < len(gt_list) else None
            right = gen_list[i] if i < len(gen_list) else None
            if isinstance(left, dict) or isinstance(right, dict):
                ld = left if isinstance(left, dict) else {}
                rd = right if isinstance(right, dict) else {}
                for med_key in _MED_LEAF_KEYS:
                    if med_key not in ld and med_key not in rd:
                        continue
                    _emit_nested_fact(
                        section, med_key, ld.get(med_key), rd.get(med_key), out, index=i
                    )
            else:
                leaf = prefix.rsplit(".", 1)[-1] if prefix else section
                _emit_nested_fact(section, leaf, left, right, out, index=i)
        return
    leaf = prefix.rsplit(".", 1)[-1] if prefix else section
    _emit_nested_fact(section, leaf, gt_node, gen_node, out)


def facts_from_nested_soap(result: dict | None) -> list[dict[str, Any]]:
    """Leaf facts from nested GT/Gen SOAP using classify_pair (same as getCellClasses)."""
    data = _as_dict(result)
    gt = _as_dict(data.get("soap_ground_truth"))
    gen = generated_soap_from_result(data)
    if not gt and not gen:
        return []
    out: list[dict[str, Any]] = []
    for key in ("subjective", "objective", "assessment", "plan"):
        if gt.get(key) is None and gen.get(key) is None:
            continue
        _walk_nested_pair(gt.get(key), gen.get(key), key, "", out)
    return out


def soap_facts_from_result(result: dict | None) -> list[dict[str, Any]]:
    """Prompt 1 facts when stored; otherwise nested SOAP leaves classified the same way.

    Root cause of empty Summary S/O/A/P: the navigator only read stored
    ``gt_vs_generated.facts``. Detail could still list nested SOAP keys via
    soapFieldTable. Both tabs now share this function.
    """
    stored = stored_soap_facts(result)
    if stored:
        return stored
    return facts_from_nested_soap(result)


COUNTED_RESULTS = (CORRECT, INCORRECT, MISSING, HALLUCINATION)


def fact_classification(fact: dict | None) -> str:
    """Same label the field lists use: result or type, NA distinct, implicit Correct."""
    data = _as_dict(fact)
    label = normalize_result_type(data.get("result") or data.get("type"))
    if label == NA:
        return NA
    if label in COUNTED_RESULTS:
        return label
    # Prompt 1 made Correct explicit; older payloads omit it (no flagged diff).
    return CORRECT if not label else label


def display_filter_result(raw: Any) -> str:
    """Summary/Detail filter buckets. Does not change Prompt 1 scoring.

    Missing = both Ground Truth and Generated empty (Prompt 1 NA).
    Incorrect = any error other than Hallucination (Prompt 1 Incorrect + Missing).
    """
    label = normalize_result_type(raw)
    if label == NA:
        return MISSING
    if label == MISSING:
        return INCORRECT
    if label in COUNTED_RESULTS:
        return label
    return CORRECT if not label else label


def count_classified_facts(facts: list | None) -> dict[str, int] | None:
    """Four-way tallies from Prompt 1 facts. NA is excluded from every bucket."""
    rows = [row for row in (facts or []) if isinstance(row, dict)]
    if not rows:
        return None
    counts = {key: 0 for key in COUNTED_RESULTS}
    for row in rows:
        label = fact_classification(row)
        if label == NA:
            continue
        if label in counts:
            counts[label] += 1
    return counts


def is_na_fact(fact: dict | None) -> bool:
    data = _as_dict(fact)
    return normalize_result_type(data.get("result") or data.get("type")) == NA


def error_facts(facts: list[dict] | None) -> list[dict]:
    out = []
    for fact in facts or []:
        label = normalize_result_type(fact.get("result") or fact.get("type"))
        if label in (INCORRECT, MISSING, HALLUCINATION):
            out.append(fact)
    return out


def facts_for_section(facts: list[dict] | None, section_key: str) -> list[dict]:
    wanted = _text(section_key).lower()
    rows = []
    for fact in facts or []:
        label = fact_section_label(fact).lower()
        if label == wanted:
            rows.append(fact)
    return rows


def cell_classes_for(gt_value: Any, gen_value: Any) -> dict[str, str]:
    """Display classes that follow classify_pair (NA ≠ Missing ≠ Hallucination)."""
    label = classify_pair(gt_value, gen_value)["result"]
    if label == NA:
        return {"result": NA, "gtClass": "cell-na", "genClass": "cell-na"}
    if label == CORRECT:
        return {"result": CORRECT, "gtClass": "", "genClass": ""}
    if label == MISSING:
        return {
            "result": MISSING,
            "gtClass": "cell-missing-gt",
            "genClass": "cell-missing-gen",
        }
    if label == HALLUCINATION:
        return {
            "result": HALLUCINATION,
            "gtClass": "cell-hallucination-gt",
            "genClass": "cell-hallucination-gen",
        }
    return {
        "result": INCORRECT,
        "gtClass": "cell-correct-gt",
        "genClass": "cell-incorrect-gen",
    }


def cell_classes_for_result(result_label: Any, gt_value: Any = None, gen_value: Any = None) -> dict[str, str]:
    """Cell classes from Prompt 1's result when present — not a second classifier."""
    label = normalize_result_type(result_label)
    if label == NA:
        return {"result": NA, "gtClass": "cell-na", "genClass": "cell-na"}
    if label == CORRECT:
        return {"result": CORRECT, "gtClass": "", "genClass": ""}
    if label == MISSING:
        return {
            "result": MISSING,
            "gtClass": "cell-missing-gt",
            "genClass": "cell-missing-gen",
        }
    if label == HALLUCINATION:
        return {
            "result": HALLUCINATION,
            "gtClass": "cell-hallucination-gt",
            "genClass": "cell-hallucination-gen",
        }
    if label == INCORRECT:
        return {
            "result": INCORRECT,
            "gtClass": "cell-correct-gt",
            "genClass": "cell-incorrect-gen",
        }
    return cell_classes_for(gt_value, gen_value)


def encoded_cells(result_label: Any, gt_value: Any, gen_value: Any) -> dict[str, Any]:
    """Populate vs empty encoding: Missing hides Generated; Hallucination hides GT."""
    label = normalize_result_type(result_label) or CORRECT
    gt_text = display_fact_value(gt_value)
    gen_text = display_fact_value(gen_value)
    if label == MISSING:
        return {
            "ground_truth": gt_text,
            "generated": _DASH,
            "gt_empty": False,
            "gen_empty": True,
            "result": label,
        }
    if label == HALLUCINATION:
        return {
            "ground_truth": _DASH,
            "generated": gen_text,
            "gt_empty": True,
            "gen_empty": False,
            "result": label,
        }
    return {
        "ground_truth": gt_text,
        "generated": gen_text,
        "gt_empty": False,
        "gen_empty": False,
        "result": label,
    }


def detail_row(fact: dict | None) -> dict[str, Any]:
    data = _as_dict(fact)
    prompt1 = normalize_result_type(data.get("result") or data.get("type")) or CORRECT
    result = display_filter_result(prompt1)
    gt = data.get("ground_truth")
    gen = data.get("generated")
    if "ground_truth" not in data and "value" in data:
        gt = data.get("value")
    cells = encoded_cells(result, gt, gen)
    classes = cell_classes_for_result(result, gt, gen)
    return {
        "field_name": display_field_name(data),
        "ground_truth": cells["ground_truth"],
        "generated": cells["generated"],
        "gt_empty": cells["gt_empty"],
        "gen_empty": cells["gen_empty"],
        "result": result if result in RESULT_LABELS else result,
        "result_css": _RESULT_CSS.get(result, "soap-result-incorrect"),
        "gtClass": classes["gtClass"],
        "genClass": classes["genClass"],
        "section": fact_section_label(data),
        "is_na": False,
        "criticality": _text(data.get("criticality")),
        "categories": list(data.get("categories") or []),
        "confidence": data.get("confidence"),
    }


_CATEGORY_SUBTYPE = {
    "temporal": "Duration",
    "medication": "Medication",
    "numerical": "Numeric",
    "diagnosis": "Diagnosis",
}


def error_type_label(result_label: Any) -> str:
    label = normalize_result_type(result_label)
    if label == CORRECT:
        return "Match"
    if label == INCORRECT:
        return "Value Mismatch"
    if label == MISSING:
        return "Missing"
    if label == HALLUCINATION:
        return "Hallucination"
    return _text(result_label) or "—"


def subtype_label(fact: dict | None) -> str:
    data = _as_dict(fact)
    for cat in data.get("categories") or []:
        mapped = _CATEGORY_SUBTYPE.get(str(cat).lower())
        if mapped:
            return mapped
    return display_field_name(data)


def difference_line(fact: dict | None) -> str:
    data = _as_dict(fact)
    result = normalize_result_type(data.get("result") or data.get("type")) or CORRECT
    cells = encoded_cells(result, data.get("ground_truth"), data.get("generated"))
    gt = cells["ground_truth"]
    gen = cells["generated"]
    if result == CORRECT:
        return "No difference. Generated output matches Ground Truth."
    if result == NA:
        return "Field is empty in both Ground Truth and Generated output."
    if result == MISSING:
        bit = gt if gt and gt != _DASH else display_field_name(data)
        return f"Generated output is missing: '{bit}'."
    if result == HALLUCINATION:
        bit = gen if gen and gen != _DASH else display_field_name(data)
        return f"Generated output includes content not in Ground Truth: '{bit}'."
    if gt and gen and gt != _DASH and gen != _DASH:
        return f"Difference: {display_field_name(data)} changed from {gt} to {gen}."
    return "Generated output differs from Ground Truth."


def section_field_names(facts: list[dict] | None) -> dict[str, list[str]]:
    """Field names per SOAP section — Summary and Detail must return this same map."""
    table = group_detail_sections(facts, include_na=False)
    out = {name: [] for name in SOAP_SECTION_ORDER}
    for section in table["sections"]:
        out[section["section"]] = [row["field_name"] for row in section["rows"]]
    return out


def field_names_from_result(result: dict | None) -> dict[str, list[str]]:
    return section_field_names(soap_facts_from_result(result))


def group_detail_sections(
    facts: list[dict] | None,
    *,
    include_na: bool = False,
) -> dict[str, Any]:
    """Facts grouped under Subjective / Objective / Assessment / Plan."""
    buckets: dict[str, list[dict]] = {name: [] for name in SOAP_SECTION_ORDER}
    other: list[dict] = []
    na_count = 0
    for fact in facts or []:
        row = detail_row(fact)
        if row["is_na"]:
            na_count += 1
            if not include_na:
                continue
        label = row["section"]
        titled = next(
            (name for name in SOAP_SECTION_ORDER if name.lower() == label.lower()),
            None,
        )
        if titled:
            buckets[titled].append(row)
        else:
            other.append(row)
    sections = [
        {"section": name, "rows": buckets[name]}
        for name in SOAP_SECTION_ORDER
        if buckets[name]
    ]
    if other:
        sections.append({"section": "Other", "rows": other})
    return {
        "columns": list(SOAP_DETAIL_COLUMNS),
        "sections": sections,
        "na_count": na_count,
        "include_na": include_na,
    }


def detail_table_from_result(
    result: dict | None,
    *,
    include_na: bool = False,
) -> dict[str, Any]:
    return group_detail_sections(
        soap_facts_from_result(result),
        include_na=include_na,
    )
