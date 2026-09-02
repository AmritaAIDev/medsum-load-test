"""Summary-tab SOAP field navigator: sections, filters, empty selection.

Built from Prompt 1 facts already on the result. Does not rescore.
Default selection is empty — the main pane is the 'Select a field' state.
"""

from __future__ import annotations

from typing import Any

from medsum_testing.backend.services.soap_detail_table import (
    difference_line,
    display_filter_result,
    encoded_cells,
    error_type_label,
    fact_section_label,
    normalize_result_type,
    soap_facts_from_result,
    subtype_label,
)
from medsum_testing.backend.services.soap_fact_scorer import (
    CORRECT,
    HALLUCINATION,
    INCORRECT,
    MISSING,
    NA,
    load_scoring_config,
)

FILTER_ALL = "all"
FILTER_MATCH = "match"
FILTER_INCORRECT = "incorrect"
FILTER_MISSING = "missing"
FILTER_HALLUCINATED = "hallucinated"
FILTER_ORDER = (
    FILTER_ALL,
    FILTER_MATCH,
    FILTER_INCORRECT,
    FILTER_MISSING,
    FILTER_HALLUCINATED,
)
FILTER_LABELS = {
    FILTER_ALL: "All",
    FILTER_MATCH: "Match",
    FILTER_INCORRECT: "Incorrect",
    FILTER_MISSING: "Missing",
    FILTER_HALLUCINATED: "Hallucinated",
}

SECTION_KEYS = ("subjective", "objective", "assessment", "plan")
SECTION_LABELS = {
    "subjective": "SUBJECTIVE",
    "objective": "OBJECTIVE",
    "assessment": "ASSESSMENT",
    "plan": "PLAN",
}

PLAN_GROUP_ORDER = (
    "medications",
    "investigations",
    "procedures",
    "follow_up",
    "other",
)
PLAN_GROUP_LABELS = {
    "medications": "Medications",
    "investigations": "Investigations",
    "procedures": "Procedures",
    "follow_up": "Follow up",
    "other": "Other",
}
ALWAYS_PLAN_GROUPS = (
    "medications",
    "investigations",
    "procedures",
    "follow_up",
)

EMPTY_HEADING = "Select a field to compare"
EMPTY_BODY = (
    "Choose any field from the SOAP sections to view Ground Truth vs AI Output "
    "comparison and field level details"
)

SEARCH_PLACEHOLDER = "Search fields, medication, diagnosis..."

DEFAULT_EXPANDED = {
    "subjective": False,
    "objective": False,
    "assessment": False,
    "plan": False,
}

_MED_FIELDS = frozenset({
    "drug name", "dose", "schedule", "duration", "instructions",
})
_MED_CATALOG_SKIP = frozenset({
    "drug_name", "dose", "schedule", "duration", "instructions",
})
_MED_ATTRS = (
    (("drug name", "drug_name"), "Medicine name"),
    (("dose",), "Dose"),
    (("schedule",), "Schedule"),
    (("duration",), "Duration"),
    (("instructions",), "Instructions"),
)
_INVEST_FIELDS = frozenset({"investigations", "investigation"})
_PROC_FIELDS = frozenset({"procedures", "procedure"})
_FOLLOW_FIELDS = frozenset({"follow-up", "follow up", "follow_up", "followup"})


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm_name(value: Any) -> str:
    return " ".join(_text(value).lower().replace("_", " ").replace("-", " ").split())


def _result_of(fact: dict | None) -> str:
    data = _as_dict(fact)
    return normalize_result_type(data.get("result") or data.get("type")) or CORRECT


def _is_na(fact: dict | None) -> bool:
    return _result_of(fact) == NA


def _section_key(fact: dict | None) -> str:
    label = _norm_name(fact_section_label(fact))
    return next((k for k in SECTION_KEYS if k == label), label or "other")


def plan_group_key(fact: dict | None) -> str | None:
    """Plan subgroup: medications / investigations / procedures / follow_up / other."""
    data = _as_dict(fact)
    if _section_key(data) != "plan":
        return None
    cats = [str(c).lower() for c in (data.get("categories") or [])]
    if "medication" in cats:
        return "medications"
    name = _norm_name(data.get("base_field") or data.get("field"))
    if name in _MED_FIELDS or "drug" in name:
        return "medications"
    if name in _INVEST_FIELDS or "investigation" in name:
        return "investigations"
    if name in _PROC_FIELDS or "procedure" in name:
        return "procedures"
    if name in _FOLLOW_FIELDS or name.startswith("follow"):
        return "follow_up"
    return "other"


def field_id(fact: dict | None, index: int | str) -> str:
    data = _as_dict(fact)
    section = _section_key(data)
    slug = _norm_name(data.get("base_field") or data.get("field")) or "field"
    slug = slug.replace(" ", "_")
    med_idx = data.get("index")
    if med_idx is not None and med_idx != "":
        return f"{section}.{slug}.{med_idx}"
    return f"{section}.{slug}.{index}"


def display_field_label(fact: dict | None) -> str:
    data = _as_dict(fact)
    name = _text(data.get("field") or data.get("base_field"))
    if not name:
        return "Unknown"
    if "_" in name and " " not in name:
        return name.replace("_", " ").title()
    return name


def nav_field(fact: dict | None, index: int | str) -> dict[str, Any]:
    data = _as_dict(fact)
    prompt1 = _result_of(data)
    result = display_filter_result(prompt1)
    section = _section_key(data)
    gt = data.get("ground_truth")
    gen = data.get("generated")
    if "ground_truth" not in data and "value" in data:
        gt = data.get("value")
    cells = encoded_cells(result, gt, gen)
    return {
        "id": field_id(data, index),
        "section": section if section in SECTION_KEYS else section,
        "group": plan_group_key(data) if section == "plan" else None,
        "label": display_field_label(data),
        "base_field": _text(data.get("base_field") or data.get("field")),
        "result": result,
        "ground_truth": cells["ground_truth"],
        "generated": cells["generated"],
        "gt_empty": cells["gt_empty"],
        "gen_empty": cells["gen_empty"],
        "raw_ground_truth": _text(gt),
        "raw_generated": _text(gen),
        "categories": list(data.get("categories") or []),
        "criticality": _text(data.get("criticality")),
        "confidence": data.get("confidence"),
        "error_type": error_type_label(result),
        "subtype": subtype_label(data),
        "difference": difference_line(data),
        "index": data.get("index"),
        "is_na": False,
        "is_match": result == CORRECT,
        "is_incorrect": result == INCORRECT,
        "is_missing": result == MISSING,
        "is_hallucinated": result == HALLUCINATION,
    }


def fields_from_facts(facts: list[dict] | None) -> list[dict[str, Any]]:
    out = []
    for i, fact in enumerate(facts or []):
        if not isinstance(fact, dict):
            continue
        out.append(nav_field(fact, i))
    return out


def catalog_nav_specs() -> list[dict[str, Any]]:
    """Every SOAP catalog field, in YAML order, for the Summary sidebar.

    Medication leaves (name/dose/schedule/instructions) collapse to one
    Medicine field. Display only — does not rescore.
    """
    cfg = load_scoring_config()
    out: list[dict[str, Any]] = []
    inserted_med = False
    medicine_spec = {
        "field": "Medicine",
        "base_field": "medicine",
        "section": "plan",
        "categories": ["medication"],
        "result": NA,
        "ground_truth": "",
        "generated": "",
    }
    for key, spec in (cfg.get("fields") or {}).items():
        if not isinstance(spec, dict):
            continue
        if str(key) in _MED_CATALOG_SKIP:
            if not inserted_med:
                out.append(dict(medicine_spec))
                inserted_med = True
            continue
        section = _norm_name(spec.get("section"))
        if section not in SECTION_KEYS:
            continue
        out.append({
            "field": spec.get("field") or key,
            "base_field": key,
            "section": section,
            "categories": list(spec.get("categories") or []),
            "result": NA,
            "ground_truth": "",
            "generated": "",
        })
    if not inserted_med:
        out.append(dict(medicine_spec))
    return out


def catalog_field_labels() -> list[str]:
    return [_text(spec.get("field")) for spec in catalog_nav_specs()]


def _field_merge_key(row: dict) -> tuple[str, str]:
    base = _norm_name(row.get("base_field") or row.get("label") or row.get("field"))
    section = _norm_name(row.get("section"))
    return (base, section)


def order_by_catalog(rows: list[dict] | None) -> list[dict[str, Any]]:
    catalog_index: dict[tuple[str, str], int] = {}
    for i, spec in enumerate(catalog_nav_specs()):
        catalog_index[_field_merge_key(spec)] = i
        catalog_index[(_norm_name(spec.get("field")), _norm_name(spec.get("section")))] = i
    section_rank = {key: i for i, key in enumerate(SECTION_KEYS)}

    def sort_key(row: dict) -> tuple:
        section = row.get("section") or ""
        merge = _field_merge_key(row)
        cat_i = catalog_index.get(merge)
        if cat_i is None:
            cat_i = catalog_index.get((_norm_name(row.get("label")), _norm_name(section)), 999)
        idx = row.get("index")
        try:
            med_i = int(idx)
        except (TypeError, ValueError):
            med_i = 0
        return (section_rank.get(section, 99), cat_i, med_i, _text(row.get("label")))

    return sorted(list(rows or []), key=sort_key)


def merge_catalog_fields(scored: list[dict] | None) -> list[dict[str, Any]]:
    """Scored facts win; catalog fills any SOAP field the result did not emit."""
    rows = list(scored or [])
    seen: set[tuple[str, str]] = set()
    for row in rows:
        seen.add(_field_merge_key(row))
        seen.add((_norm_name(row.get("label")), _norm_name(row.get("section"))))
    extras: list[dict[str, Any]] = []
    for i, spec in enumerate(catalog_nav_specs()):
        keys = (
            _field_merge_key(spec),
            (_norm_name(spec.get("field")), _norm_name(spec.get("section"))),
        )
        if any(key in seen for key in keys):
            continue
        extras.append(nav_field(spec, f"cat-{i}"))
    return order_by_catalog(rows + extras)


def fields_from_result(result: dict | None) -> list[dict[str, Any]]:
    return collapse_medicine_fields(
        merge_catalog_fields(fields_from_facts(soap_facts_from_result(result)))
    )


def _is_med_leaf(row: dict | None) -> bool:
    data = _as_dict(row)
    if data.get("is_medicine") or _norm_name(data.get("base_field")) == "medicine":
        return False
    if data.get("group") == "medications":
        return True
    name = _norm_name(data.get("base_field") or data.get("label") or data.get("field"))
    return name in _MED_FIELDS and _norm_name(data.get("section")) == "plan"


def _worst_result(results: list[str] | None) -> str:
    rank = {
        HALLUCINATION: 4,
        INCORRECT: 3,
        MISSING: 2,
        CORRECT: 1,
        NA: 0,
        "": 0,
    }
    worst = NA
    best = -1
    for raw in results or []:
        label = normalize_result_type(raw) or NA
        score = rank.get(label, 0)
        if score > best:
            best = score
            worst = label
    return worst


def _empty_medicine_rows() -> list[dict[str, Any]]:
    return [
        {
            "label": label,
            "ground_truth": "—",
            "generated": "—",
            "gt_empty": True,
            "gen_empty": True,
            "result": MISSING,
        }
        for _keys, label in _MED_ATTRS
    ]


def _medicine_table_for_leaves(leaves: list[dict], index: Any) -> dict[str, Any]:
    by_attr: dict[str, dict] = {}
    for leaf in leaves:
        key = _norm_name(leaf.get("base_field") or leaf.get("label"))
        by_attr[key] = leaf
    rows = []
    for keys, label in _MED_ATTRS:
        leaf = next((by_attr.get(k) for k in keys if by_attr.get(k)), None)
        if leaf:
            rows.append({
                "label": label,
                "ground_truth": leaf.get("ground_truth") or "—",
                "generated": leaf.get("generated") or "—",
                "gt_empty": bool(leaf.get("gt_empty")),
                "gen_empty": bool(leaf.get("gen_empty")),
                "result": leaf.get("result") or MISSING,
            })
        else:
            rows.append({
                "label": label,
                "ground_truth": "—",
                "generated": "—",
                "gt_empty": True,
                "gen_empty": True,
                "result": MISSING,
            })
    name_leaf = by_attr.get("drug name") or by_attr.get("drug_name")
    name = ""
    if name_leaf:
        name = (
            _text(name_leaf.get("raw_ground_truth"))
            or _text(name_leaf.get("raw_generated"))
        )
        if name in ("—",):
            name = ""
    try:
        n = int(index) + 1
    except (TypeError, ValueError):
        n = 1
    title = name or f"Medicine {n}"
    return {
        "title": title,
        "rows": rows,
        "result": _worst_result([r["result"] for r in rows]),
    }


def collapse_medicine_fields(rows: list[dict] | None) -> list[dict[str, Any]]:
    """Fold name/dose/schedule/instructions into one Medicine field. Display only."""
    leaves: list[dict] = []
    rest: list[dict] = []
    for row in rows or []:
        if row.get("is_medicine") or _norm_name(row.get("base_field")) == "medicine":
            continue
        if _is_med_leaf(row):
            leaves.append(row)
        else:
            rest.append(row)
    rest.append(_build_medicine_field(leaves))
    return order_by_catalog(rest)


def _build_medicine_field(leaves: list[dict]) -> dict[str, Any]:
    buckets: dict[Any, list[dict]] = {}
    for leaf in leaves:
        idx = leaf.get("index")
        key = 0 if idx is None or idx == "" else idx
        buckets.setdefault(key, []).append(leaf)
    keys = sorted(buckets, key=lambda x: int(x) if str(x).isdigit() else 0)
    if not keys:
        tables = [{"title": "Medicine", "rows": _empty_medicine_rows(), "result": MISSING}]
        worst = MISSING
        count = 0
    else:
        tables = [_medicine_table_for_leaves(buckets[k], k) for k in keys]
        worst = _worst_result([t["result"] for t in tables])
        count = len(keys)
    result = worst or MISSING
    return {
        "id": "plan.medicine.0",
        "section": "plan",
        "group": "medications",
        "label": "Medicine",
        "base_field": "medicine",
        "result": result,
        "ground_truth": "—",
        "generated": "—",
        "gt_empty": True,
        "gen_empty": True,
        "raw_ground_truth": "",
        "raw_generated": "",
        "categories": ["medication"],
        "criticality": "",
        "confidence": None,
        "error_type": error_type_label(result),
        "subtype": "Medication",
        "difference": (
            "No medication facts to compare."
            if result == MISSING
            else "No difference. Generated output matches Ground Truth."
            if result == CORRECT
            else "See medicine table for name, dose, schedule, and instruction differences."
        ),
        "index": 0,
        "is_na": False,
        "is_match": result == CORRECT,
        "is_incorrect": result == INCORRECT,
        "is_missing": result == MISSING,
        "is_hallucinated": result == HALLUCINATION,
        "is_medicine": True,
        "medicine_tables": tables,
        "medication_count": count,
    }


def result_counts(fields: list[dict] | None) -> dict[str, int]:
    rows = list(fields or [])
    return {
        FILTER_ALL: len(rows),
        FILTER_MATCH: sum(1 for f in rows if f.get("is_match")),
        FILTER_INCORRECT: sum(1 for f in rows if f.get("is_incorrect")),
        FILTER_MISSING: sum(1 for f in rows if f.get("is_missing")),
        FILTER_HALLUCINATED: sum(1 for f in rows if f.get("is_hallucinated")),
    }


def section_match_pct(fields: list[dict] | None) -> int | None:
    rows = list(fields or [])
    if not rows:
        return None
    correct = sum(1 for f in rows if f.get("is_match"))
    return int(round(100.0 * correct / len(rows)))


def pct_tone(pct: int | None) -> str:
    if pct is None:
        return "empty"
    if pct >= 90:
        return "high"
    if pct >= 80:
        return "good"
    return "mid"


def medication_count(fields: list[dict] | None) -> int:
    rows = list(fields or [])
    if not rows:
        return 0
    indexes = [f.get("index") for f in rows]
    if any(i is not None and i != "" for i in indexes):
        return len({0 if i is None or i == "" else i for i in indexes})
    drugs = [
        f for f in rows
        if "drug" in _norm_name(f.get("label"))
    ]
    return len(drugs) or 1


def group_count_label(group_key: str, fields: list[dict] | None) -> str:
    rows = list(fields or [])
    if group_key == "medications":
        n = 0
        for field in rows:
            extra = field.get("medication_count")
            if extra:
                n += int(extra)
            elif field.get("is_medicine"):
                n += 1
        if not n:
            n = medication_count(rows)
        return f"{n} medication{'s' if n != 1 else ''}"
    n = len(rows)
    return f"{n} field{'s' if n != 1 else ''}"


def field_count_label(n: int) -> str:
    return f"{n} field{'s' if n != 1 else ''}"


def matches_status(field: dict, status: str) -> bool:
    key = _text(status).lower() or FILTER_ALL
    if key == FILTER_ALL or key == "":
        return True
    if key == FILTER_MATCH:
        return bool(field.get("is_match"))
    if key == FILTER_INCORRECT:
        return bool(field.get("is_incorrect"))
    if key == FILTER_MISSING:
        return bool(field.get("is_missing"))
    if key == FILTER_HALLUCINATED:
        return bool(field.get("is_hallucinated"))
    return True


def matches_query(field: dict, query: str) -> bool:
    needle = _text(query).lower()
    if not needle:
        return True
    hay = " ".join([
        _text(field.get("label")),
        _text(field.get("ground_truth")),
        _text(field.get("generated")),
        _text(field.get("raw_ground_truth")),
        _text(field.get("raw_generated")),
        _text(field.get("section")),
        _text(field.get("group")),
    ]).lower()
    return needle in hay


def filter_fields(
    fields: list[dict] | None,
    *,
    status: str = FILTER_ALL,
    query: str = "",
) -> list[dict[str, Any]]:
    return [
        f for f in (fields or [])
        if matches_status(f, status) and matches_query(f, query)
    ]


def _section_fields(fields: list[dict], key: str) -> list[dict]:
    return [f for f in fields if f.get("section") == key]


def build_plan_groups(fields: list[dict] | None) -> list[dict[str, Any]]:
    rows = [f for f in (fields or []) if f.get("section") == "plan"]
    buckets: dict[str, list[dict]] = {k: [] for k in PLAN_GROUP_ORDER}
    for field in rows:
        group = field.get("group")
        if group in buckets:
            buckets[group].append(field)
        else:
            buckets["other"].append(field)
    groups = []
    for key in PLAN_GROUP_ORDER:
        items = buckets[key]
        if not items and key not in ALWAYS_PLAN_GROUPS:
            continue
        groups.append({
            "id": f"plan.{key}",
            "key": key,
            "label": PLAN_GROUP_LABELS[key],
            "count_label": group_count_label(key, items),
            "field_ids": [f["id"] for f in items],
            "fields": items,
        })
    return groups


def build_sections(fields: list[dict] | None) -> list[dict[str, Any]]:
    rows = list(fields or [])
    sections = []
    for key in SECTION_KEYS:
        owned = _section_fields(rows, key)
        pct = section_match_pct(owned)
        item = {
            "key": key,
            "label": SECTION_LABELS[key],
            "field_count": len(owned),
            "count_label": field_count_label(len(owned)),
            "match_pct": pct,
            "pct_tone": pct_tone(pct),
            "pct_label": "" if pct is None else f"{pct}%",
            "expanded": bool(DEFAULT_EXPANDED.get(key)),
            "field_ids": [f["id"] for f in owned],
            "fields": owned,
            "groups": build_plan_groups(owned) if key == "plan" else [],
        }
        sections.append(item)
    return sections


def ordered_field_ids(fields: list[dict] | None) -> list[str]:
    """Subjective → Objective → Assessment → Plan, facts in stored order."""
    rows = list(fields or [])
    ids = []
    for key in SECTION_KEYS:
        ids.extend(f["id"] for f in rows if f.get("section") == key)
    ids.extend(f["id"] for f in rows if f.get("section") not in SECTION_KEYS)
    return ids


def adjacent_field_id(fields: list[dict] | None, current_id: str, step: int) -> str | None:
    ids = ordered_field_ids(fields)
    if not ids or current_id not in ids:
        return None
    idx = ids.index(current_id) + step
    if idx < 0 or idx >= len(ids):
        return None
    return ids[idx]


def next_field_id(fields: list[dict] | None, current_id: str) -> str | None:
    return adjacent_field_id(fields, current_id, 1)


def prev_field_id(fields: list[dict] | None, current_id: str) -> str | None:
    return adjacent_field_id(fields, current_id, -1)


def nav_model(
    result: dict | None = None,
    *,
    facts: list[dict] | None = None,
    selected_ids: list[str] | None = None,
    status: str = FILTER_ALL,
    query: str = "",
) -> dict[str, Any]:
    """Summary SOAP navigator. selected_ids defaults to empty (nothing selected)."""
    all_fields = fields_from_facts(facts) if facts is not None else fields_from_result(result)
    counts = result_counts(all_fields)
    visible = filter_fields(all_fields, status=status, query=query)
    selected = [sid for sid in (selected_ids or []) if any(f["id"] == sid for f in all_fields)]
    return {
        "fields": all_fields,
        "visible": visible,
        "sections": build_sections(visible),
        "counts": counts,
        "status": status or FILTER_ALL,
        "query": query or "",
        "selected_ids": selected,
        "selected_count": len(selected),
        "total_count": counts[FILTER_ALL],
        "nothing_selected": len(selected) == 0,
        "empty_heading": EMPTY_HEADING,
        "empty_body": EMPTY_BODY,
        "search_placeholder": SEARCH_PLACEHOLDER,
        "expanded": dict(DEFAULT_EXPANDED),
    }
