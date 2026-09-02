"""Fact-level weighted SOAP scorer (MOM clinical-fact model).

Transcription/translation scoring is unchanged and is not blended in.

Overall Weighted Clinical Score
    = Σ(Correct fact weight) / Σ(Applicable fact weight) × 100
NA facts are excluded from both sums.

LLM `section_details[].differences[]` types remap:
    missing → Missing, incorrect → Incorrect, extra → Hallucination
Correct is explicit (catalog field with no diff, or type=Correct).
NA vs Missing: empty/NA GT is NA; established GT (incl. “No known allergies”)
with empty generated is Missing. NA is never scored as Missing.

4-level LLM severity → 3-level MOM criticality (config severity_to_criticality):
    critical → Critical (5), high → High (3),
    medium → Normal (1), low → Normal (1).
Catalog `fields.*.criticality` wins for known fields; severity is fallback.

Numeric tolerance is unset (config numeric_tolerance: null). 101 vs 100.4
is Incorrect. Same number as digits vs words is not a mismatch.

Dose is medication, not numerical, so Numerical/Unit Accuracy is BP + Temperature.
"""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from medsum_testing.backend.services.accuracy_thresholds import (
    accuracy_band_from_score,
    get_accuracy_thresholds,
)
from medsum_testing.backend.services.config_loader import get_repo_root

CORRECT = "Correct"
INCORRECT = "Incorrect"
MISSING = "Missing"
HALLUCINATION = "Hallucination"
NA = "NA"
CONTRADICTORY = "Contradictory"

EXTERNAL_RESULTS = (CORRECT, INCORRECT, MISSING, HALLUCINATION, NA)
GENERATED_RESULTS = (CORRECT, INCORRECT, HALLUCINATION)
ERROR_RESULTS = (INCORRECT, MISSING, HALLUCINATION)

_SCORING_CACHE: dict[str, Any] | None = None
_NUMBER_RE = re.compile(
    r"(\d+(?:\.\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?",
)
_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_FILLER = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "to",
        "for",
        "in",
        "on",
        "at",
        "and",
        "with",
        "review",
        "please",
        "tab",
        "tablet",
    }
)


def load_scoring_config(path: Path | None = None, force_reload: bool = False) -> dict[str, Any]:
    """Load config/soap_fact_scoring.yaml. Independent of medsum_config.yaml."""
    global _SCORING_CACHE
    if _SCORING_CACHE is not None and not force_reload and path is None:
        return _SCORING_CACHE
    cfg_path = path or (get_repo_root() / "config" / "soap_fact_scoring.yaml")
    with open(cfg_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if path is None:
        _SCORING_CACHE = data
    return data


DIFF_TYPE_REMAP = {
    "missing": MISSING,
    "incorrect": INCORRECT,
    "extra": HALLUCINATION,
    "hallucination": HALLUCINATION,
    "correct": CORRECT,
    "na": NA,
    "n/a": NA,
    "not applicable": NA,
    "contradictory": INCORRECT,
    "field changed": INCORRECT,
    "field_changed": INCORRECT,
}


def remap_diff_type(raw: Any) -> str:
    """Map LLM / legacy diff types onto the four-way (+ NA) model."""
    key = _norm_name(raw)
    if not key:
        return ""
    return DIFF_TYPE_REMAP.get(key, "")


def severity_to_criticality(
    severity: Any, scoring_config: dict | None = None
) -> str | None:
    """Map LLM 4-level severity onto MOM Critical / High / Normal."""
    cfg = scoring_config or load_scoring_config()
    table = cfg.get("severity_to_criticality") or {}
    key = str(severity or "").strip().lower()
    mapped = table.get(key)
    if mapped:
        return str(mapped)
    return table.get(_norm_name(severity)) or None


def criticality_weight(criticality: str, scoring_config: dict | None = None) -> int:
    cfg = scoring_config or load_scoring_config()
    weights = cfg.get("criticality_weights") or {}
    key = str(criticality or "").strip()
    if key not in weights:
        raise KeyError(
            f"Unknown criticality {key!r} — add it to soap_fact_scoring.yaml "
            f"criticality_weights (do not hardcode)"
        )
    return int(weights[key])


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _norm_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _text(value).lower()).strip()


def _markers(cfg: dict, key: str) -> tuple[str, ...]:
    raw = cfg.get(key) or []
    return tuple(_norm_name(item) for item in raw if _text(item))


def _matches_marker(value: Any, markers: tuple[str, ...], *, exact: bool = False) -> bool:
    text = _norm_name(value)
    if not text:
        return False
    if exact:
        return text in markers
    return any(text == marker or marker in text for marker in markers if marker)


def is_na_value(value: Any, scoring_config: dict | None = None) -> bool:
    cfg = scoring_config or load_scoring_config()
    text = _text(value)
    if not text:
        return True
    return _matches_marker(text, _markers(cfg, "na_markers"), exact=True)


def is_absence_value(value: Any, scoring_config: dict | None = None) -> bool:
    cfg = scoring_config or load_scoring_config()
    return _matches_marker(value, _markers(cfg, "absence_markers"))


def is_established_negative(value: Any, scoring_config: dict | None = None) -> bool:
    cfg = scoring_config or load_scoring_config()
    return _matches_marker(value, _markers(cfg, "established_negative_markers"))


def is_established_gt(value: Any, scoring_config: dict | None = None) -> bool:
    """GT establishes a fact, including explicit negatives. Empty/NA does not."""
    if is_na_value(value, scoring_config):
        return False
    return True


def _spec_name_keys(spec: dict, catalog_key: str = "") -> set[str]:
    """Catalog display name, aliases, YAML key, and path tails (pulse / heart_rate)."""
    names: list[Any] = [spec.get("field"), catalog_key]
    names.extend(spec.get("aliases") or [])
    for path in spec.get("paths") or []:
        tail = str(path).rsplit(".", 1)[-1]
        if tail and "*" not in tail:
            names.append(tail)
    return {_norm_name(name) for name in names if _text(name)}


def _lookup_field(field_name: str, scoring_config: dict) -> dict[str, Any] | None:
    wanted = _norm_name(field_name)
    if not wanted:
        return None
    catalog = scoring_config.get("fields") or {}
    objective_hit = None
    for key, spec in catalog.items():
        if not isinstance(spec, dict):
            continue
        if _norm_name(spec.get("field")) == wanted:
            return spec
        if (
            objective_hit is None
            and _text(spec.get("section")) == "Objective"
            and wanted in _spec_name_keys(spec, str(key))
        ):
            objective_hit = spec
    return objective_hit


def resolve_field_spec(field_name: str, scoring_config: dict | None = None) -> dict[str, Any]:
    cfg = scoring_config or load_scoring_config()
    spec = _lookup_field(field_name, cfg)
    if spec:
        return spec
    default = str(cfg.get("unmapped_field_criticality") or "Normal")
    return {
        "field": _text(field_name) or "Unknown",
        "section": "",
        "criticality": default,
        "categories": [],
        "paths": [],
    }


def _percent(numerator: float, denominator: float, places: int = 1) -> float | None:
    if not denominator:
        return None
    return round(100.0 * numerator / denominator, places)


def extract_numeric_tokens(value: Any) -> tuple[str, ...]:
    text = _text(value).lower().replace("°", " ")
    found: list[str] = []
    for match in _NUMBER_RE.finditer(text):
        left, right = match.group(1), match.group(2)
        if right:
            found.append(f"{left}/{right}")
        else:
            found.append(left)
    return tuple(found)


def numbers_conflict(left: Any, right: Any) -> bool:
    a = extract_numeric_tokens(left)
    b = extract_numeric_tokens(right)
    if not a or not b:
        return False
    return a != b


def values_match(left: Any, right: Any) -> bool:
    a = _norm_name(left)
    b = _norm_name(right)
    if not a and not b:
        return True
    if a == b:
        return True
    if numbers_conflict(left, right):
        return False
    words_a = [w for w in _WORD_RE.findall(a) if w not in _FILLER]
    words_b = [w for w in _WORD_RE.findall(b) if w not in _FILLER]
    if words_a and words_a == words_b:
        return True
    if words_a and words_b and set(words_a) <= set(words_b):
        return True
    if words_a and words_b and set(words_b) <= set(words_a):
        return True
    return False


def classify_pair(
    gt_value: Any,
    gen_value: Any,
    scoring_config: dict | None = None,
    *,
    gt_applicable: bool | None = None,
) -> dict[str, str]:
    """Return external result plus optional internal tag (contradictory)."""
    cfg = scoring_config or load_scoring_config()
    gt_text = _text(gt_value)
    gen_text = _text(gen_value)
    gt_empty = is_na_value(gt_text, cfg)
    gen_empty = is_na_value(gen_text, cfg)

    if gt_applicable is False or (gt_empty and gen_empty):
        return {"result": NA, "internal": NA}

    if gt_empty and not gen_empty:
        return {"result": HALLUCINATION, "internal": HALLUCINATION}

    if not gt_empty and gen_empty:
        return {"result": MISSING, "internal": MISSING}

    if values_match(gt_text, gen_text):
        return {"result": CORRECT, "internal": CORRECT}

    if is_absence_value(gt_text, cfg) and not is_absence_value(gen_text, cfg):
        return {"result": HALLUCINATION, "internal": HALLUCINATION}

    if is_established_negative(gt_text, cfg) and not is_established_negative(gen_text, cfg):
        return {"result": INCORRECT, "internal": CONTRADICTORY}

    if numbers_conflict(gt_text, gen_text):
        return {"result": INCORRECT, "internal": INCORRECT}

    return {"result": INCORRECT, "internal": CONTRADICTORY}


def _section_has_established_facts(
    facts: list[dict], section: str, scoring_config: dict
) -> bool:
    wanted = _norm_name(section)
    for fact in facts:
        if _norm_name(fact.get("section")) != wanted:
            continue
        if is_established_gt(fact.get("value"), scoring_config):
            return True
    return False


def apply_section_na(
    facts: list[dict], scoring_config: dict | None = None
) -> list[dict]:
    """If a section has no established GT at all, remaining facts in it are NA."""
    cfg = scoring_config or load_scoring_config()
    out: list[dict] = []
    sections = {str(f.get("section") or "") for f in facts}
    established = {
        section: _section_has_established_facts(facts, section, cfg)
        for section in sections
        if section
    }
    for fact in facts:
        row = dict(fact)
        section = str(row.get("section") or "")
        if section and not established.get(section, True):
            if not is_established_gt(row.get("value"), cfg):
                row["applicable"] = False
                row["value"] = row.get("value") or NA
        out.append(row)
    return out


def _path_get(root: Any, path: str) -> list[Any]:
    if not path:
        return []
    current: list[Any] = [root]
    for part in path.split("."):
        nxt: list[Any] = []
        for node in current:
            if part == "*":
                if isinstance(node, list):
                    nxt.extend(node)
                continue
            if isinstance(node, dict) and part in node:
                nxt.append(node[part])
        current = nxt
        if not current:
            return []
    return current


def _leaf_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [_leaf_text(item) for item in value]
        return "; ".join(p for p in parts if p)
    if isinstance(value, dict):
        if "drug_name" in value or "dose" in value:
            bits = [
                _text(value.get(key))
                for key in ("drug_name", "dose", "schedule", "duration", "instructions")
            ]
            return " ".join(b for b in bits if b)
        return ""
    return _text(value)


def _make_fact(
    spec: dict,
    value: Any,
    *,
    index: int | None = None,
    scoring_config: dict,
) -> dict[str, Any]:
    field = spec.get("field") or "Unknown"
    if index is not None:
        display = f"{field} [{index + 1}]" if index else field
    else:
        display = field
    criticality = spec.get("criticality") or scoring_config.get(
        "unmapped_field_criticality", "Normal"
    )
    return {
        "section": spec.get("section") or "",
        "field": display,
        "value": _leaf_text(value),
        "criticality": criticality,
        "categories": list(spec.get("categories") or []),
        "index": index,
        "base_field": field,
    }


def nested_soap_to_facts(
    soap: Any, scoring_config: dict | None = None
) -> list[dict[str, Any]]:
    """Flatten nested MedSum SOAP using the catalog paths. Unknown leaves kept."""
    cfg = scoring_config or load_scoring_config()
    if not isinstance(soap, dict) or not soap:
        return []
    facts: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    catalog = cfg.get("fields") or {}
    for spec in catalog.values():
        if not isinstance(spec, dict):
            continue
        paths = list(spec.get("paths") or [])
        for path in paths:
            seen_paths.add(path)
        glob_paths = [path for path in paths if "*" in path]
        simple_paths = [path for path in paths if "*" not in path]
        emitted = False
        skip_empty = _text(spec.get("section")) == "Objective"
        for path in glob_paths:
            values = _path_get(soap, path)
            if skip_empty:
                values = [
                    val for val in values if not is_na_value(_leaf_text(val), cfg)
                ]
            if not values:
                continue
            for idx, val in enumerate(values):
                facts.append(_make_fact(spec, val, index=idx, scoring_config=cfg))
            emitted = True
        if emitted:
            continue
        for path in simple_paths:
            values = _path_get(soap, path)
            if skip_empty:
                values = [
                    val for val in values if not is_na_value(_leaf_text(val), cfg)
                ]
            if values:
                facts.append(_make_fact(spec, values[0], scoring_config=cfg))
                emitted = True
                break
        if not emitted:
            facts.append(_make_fact(spec, "", scoring_config=cfg))

    _collect_unmapped(soap, "", facts, seen_paths, cfg)
    return apply_section_na(facts, cfg)


def _collect_unmapped(
    node: Any,
    prefix: str,
    facts: list[dict],
    seen_paths: set[str],
    scoring_config: dict,
) -> None:
    if not isinstance(node, dict):
        return
    labels = scoring_config.get("section_labels") or {}
    for key, val in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if path in seen_paths or any(
            path == seen.split(".*")[0] or seen.startswith(path + ".")
            for seen in seen_paths
        ):
            if isinstance(val, dict) and "*" not in path:
                _collect_unmapped(val, path, facts, seen_paths, scoring_config)
            continue
        if isinstance(val, dict):
            _collect_unmapped(val, path, facts, seen_paths, scoring_config)
            continue
        if isinstance(val, list):
            continue
        text = _leaf_text(val)
        if not text:
            continue
        root = prefix.split(".")[0] if prefix else key
        section = labels.get(root, root.title() if root else "")
        default = str(scoring_config.get("unmapped_field_criticality") or "Normal")
        facts.append(
            {
                "section": section,
                "field": str(key).replace("_", " ").title(),
                "value": text,
                "criticality": default,
                "categories": [],
                "index": None,
                "base_field": str(key).replace("_", " ").title(),
                "unmapped": True,
            }
        )


def coerce_fact_list(payload: Any, scoring_config: dict | None = None) -> list[dict[str, Any]]:
    """Accept flat {facts: [...]} , a list of facts, or nested SOAP JSON."""
    cfg = scoring_config or load_scoring_config()
    if payload is None:
        return []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("facts"), list):
        rows = payload["facts"]
    elif isinstance(payload, dict):
        return nested_soap_to_facts(payload, cfg)
    else:
        return []

    out: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        field = _text(raw.get("field")) or "Unknown"
        spec = resolve_field_spec(raw.get("base_field") or field, cfg)
        known = _lookup_field(raw.get("base_field") or field, cfg)
        if (
            known
            and _text(known.get("section")) == "Objective"
            and known.get("field")
            and not re.search(r"\[\d+\]$", field)
        ):
            field = str(known["field"])
        criticality = raw.get("criticality") or spec.get("criticality")
        categories = list(raw.get("categories") or spec.get("categories") or [])
        canonical = (
            known.get("field")
            if known and _text(known.get("section")) == "Objective"
            else None
        )
        fact = {
            "section": raw.get("section") or spec.get("section") or "",
            "field": field,
            "value": raw.get("value"),
            "criticality": criticality,
            "categories": categories,
            "index": raw.get("index"),
            "base_field": canonical or raw.get("base_field") or spec.get("field") or field,
        }
        if "applicable" in raw:
            fact["applicable"] = bool(raw["applicable"])
        if raw.get("result"):
            fact["result"] = raw["result"]
        if raw.get("internal"):
            fact["internal"] = raw["internal"]
        if raw.get("weight") is not None:
            fact["weight"] = int(raw["weight"])
        out.append(fact)
    return apply_section_na(out, cfg)


def facts_document(facts: list[dict]) -> dict[str, Any]:
    slim = []
    for fact in facts:
        slim.append(
            {
                "section": fact.get("section") or "",
                "field": fact.get("field"),
                "value": fact.get("value"),
                "criticality": fact.get("criticality"),
            }
        )
    return {"facts": slim}


def _align_key(fact: dict, scoring_config: dict | None = None) -> tuple[str, int]:
    cfg = scoring_config or load_scoring_config()
    spec = _lookup_field(_text(fact.get("base_field") or fact.get("field")), cfg)
    if spec and _text(spec.get("section")) == "Objective":
        base = _norm_name(spec.get("field") or fact.get("base_field") or fact.get("field"))
    else:
        base = _norm_name(fact.get("base_field") or fact.get("field"))
    idx = fact.get("index")
    if idx is None:
        match = re.search(r"\[(\d+)\]$", _text(fact.get("field")))
        idx = int(match.group(1)) - 1 if match else 0
    return (base, int(idx or 0))


def align_facts(
    gt_facts: list[dict],
    gen_facts: list[dict],
    scoring_config: dict | None = None,
) -> list[tuple[dict | None, dict | None]]:
    cfg = scoring_config or load_scoring_config()
    gt_map: dict[tuple[str, int], dict] = {}
    gen_map: dict[tuple[str, int], dict] = {}
    for fact in gt_facts:
        gt_map[_align_key(fact, cfg)] = fact
    for fact in gen_facts:
        gen_map[_align_key(fact, cfg)] = fact
    keys = sorted(set(gt_map) | set(gen_map), key=lambda item: (item[0], item[1]))
    return [(gt_map.get(key), gen_map.get(key)) for key in keys]


def _weight_for(fact: dict, scoring_config: dict) -> int:
    if fact.get("weight") is not None:
        return int(fact["weight"])
    return criticality_weight(str(fact.get("criticality") or "Normal"), scoring_config)


def evaluate_aligned(
    pairs: list[tuple[dict | None, dict | None]],
    scoring_config: dict | None = None,
) -> list[dict[str, Any]]:
    cfg = scoring_config or load_scoring_config()
    evaluated: list[dict[str, Any]] = []
    for gt_fact, gen_fact in pairs:
        template = dict(gt_fact or gen_fact or {})
        gt_value = (gt_fact or {}).get("value")
        gen_value = (gen_fact or {}).get("value")
        gt_applicable = (gt_fact or {}).get("applicable")
        if gt_fact is None:
            gt_applicable = False if is_na_value(gt_value, cfg) else None
            if gen_fact is not None and is_established_gt(gen_value, cfg):
                classified = classify_pair("", gen_value, cfg, gt_applicable=None)
            else:
                classified = {"result": NA, "internal": NA}
        elif "result" in template and template.get("result") in EXTERNAL_RESULTS:
            classified = {
                "result": template["result"],
                "internal": template.get("internal") or template["result"],
            }
        else:
            classified = classify_pair(
                gt_value, gen_value, cfg, gt_applicable=gt_applicable
            )
        result = classified["result"]
        if gt_applicable is False and result != HALLUCINATION:
            result = NA
            classified["internal"] = NA
        # Catalog criticality for known fields; LLM severity only if unknown.
        criticality = template.get("criticality")
        spec = resolve_field_spec(
            template.get("base_field") or template.get("field") or "", cfg
        )
        known = _lookup_field(
            template.get("base_field") or template.get("field") or "", cfg
        )
        raw_field = _text(template.get("field"))
        if (
            known
            and _text(known.get("section")) == "Objective"
            and known.get("field")
            and not re.search(r"\[\d+\]$", raw_field)
        ):
            display_field = known["field"]
        else:
            display_field = template.get("field")
        if not criticality:
            criticality = spec.get("criticality")
        weight = _weight_for({**template, "criticality": criticality}, cfg)
        canonical = (
            known.get("field")
            if known and _text(known.get("section")) == "Objective"
            else None
        )
        evaluated.append(
            {
                "section": template.get("section") or spec.get("section") or "",
                "field": display_field,
                "base_field": canonical
                or template.get("base_field")
                or spec.get("field")
                or template.get("field"),
                "ground_truth": gt_value,
                "generated": gen_value,
                "criticality": criticality,
                "categories": list(
                    template.get("categories") or spec.get("categories") or []
                ),
                "weight": weight,
                "result": result,
                "internal": classified.get("internal") or result,
                "index": template.get("index"),
            }
        )
    return evaluated


def _diff_field_key(diff: dict) -> str:
    return _norm_name(diff.get("field") or diff.get("name") or "")


def iter_section_diffs(section_details: Any) -> list[dict]:
    details = section_details if isinstance(section_details, dict) else {}
    out: list[dict] = []
    for block in details.values():
        if not isinstance(block, dict):
            continue
        for diff in block.get("differences") or []:
            if isinstance(diff, dict):
                out.append(diff)
    return out


def apply_section_details(
    evaluated: list[dict],
    section_details: Any,
    scoring_config: dict | None = None,
) -> list[dict]:
    """Overlay LLM diffs onto aligned facts. NA is never overwritten to Missing."""
    cfg = scoring_config or load_scoring_config()
    diffs = iter_section_diffs(section_details)
    if not diffs:
        return evaluated
    by_field: dict[str, dict] = {}
    for row in evaluated:
        spec = resolve_field_spec(row.get("base_field") or row.get("field") or "", cfg)
        keys = {_norm_name(row.get("base_field") or row.get("field"))}
        if _text(spec.get("section") or row.get("section")) == "Objective":
            keys |= _spec_name_keys(spec)
        for key in keys:
            if key:
                by_field[key] = row
    used: set[int] = set()
    for idx, diff in enumerate(diffs):
        mapped = remap_diff_type(diff.get("type"))
        if not mapped:
            continue
        key = _diff_field_key(diff)
        row = by_field.get(key)
        if row is None:
            continue
        used.add(idx)
        if row.get("result") == NA and mapped == MISSING:
            continue
        row["result"] = mapped
        if mapped == INCORRECT:
            row["internal"] = CONTRADICTORY if row.get("internal") == CONTRADICTORY else INCORRECT
        else:
            row["internal"] = mapped
        spec = resolve_field_spec(row.get("base_field") or row.get("field") or "", cfg)
        known = _lookup_field(row.get("base_field") or row.get("field") or "", cfg)
        if not known:
            mapped_c = severity_to_criticality(diff.get("severity"), cfg)
            if mapped_c:
                row["criticality"] = mapped_c
                row["weight"] = criticality_weight(mapped_c, cfg)
        elif spec.get("criticality"):
            row["criticality"] = spec["criticality"]
            row["weight"] = criticality_weight(str(spec["criticality"]), cfg)
    return evaluated


def _critical_metric_names(scoring_config: dict) -> set[str]:
    raw = scoring_config.get("critical_fact_metric_fields") or []
    return {_norm_name(name) for name in raw if _text(name)}


def _row_field_key(row: dict) -> str:
    return _norm_name(row.get("base_field") or row.get("field"))


def compute_metrics(
    evaluated: list[dict], scoring_config: dict | None = None
) -> dict[str, Any]:
    """Formula layer used by the worked-example fixtures."""
    cfg = scoring_config or load_scoring_config()
    applicable = [row for row in evaluated if row.get("result") != NA]
    correct = [row for row in applicable if row.get("result") == CORRECT]
    missing = [row for row in applicable if row.get("result") == MISSING]
    captured = [row for row in applicable if row.get("result") in GENERATED_RESULTS]
    hallucinations = [
        row for row in applicable if row.get("result") == HALLUCINATION
    ]

    applicable_weight = sum(int(row["weight"]) for row in applicable)
    correct_weight = sum(int(row["weight"]) for row in correct)
    overall = _percent(correct_weight, applicable_weight, places=2)
    if overall is not None and overall == int(overall):
        overall = float(int(overall))

    def subset(predicate) -> float | None:
        rows = [row for row in applicable if predicate(row)]
        if not rows:
            return None
        hits = [row for row in rows if row.get("result") == CORRECT]
        return _percent(len(hits), len(rows), places=1)

    names = _critical_metric_names(cfg)
    core_critical = [
        row for row in applicable if _row_field_key(row) in names
    ]
    denom_ids = {id(row) for row in core_critical}
    critical_denom = list(core_critical)
    for row in hallucinations:
        if id(row) not in denom_ids:
            critical_denom.append(row)
            denom_ids.add(id(row))
    critical_correct = [row for row in critical_denom if row.get("result") == CORRECT]
    critical_errors = [
        row
        for row in core_critical
        if row.get("result") in (INCORRECT, MISSING)
    ]

    n_app = len(applicable)
    n_cap = len(captured)
    n_ok = len(correct)
    n_hall = len(hallucinations)
    num_tol = cfg.get("numeric_tolerance")

    return {
        "overall_weighted_clinical_score": overall,
        "applicable_weight": applicable_weight,
        "correct_weight": correct_weight,
        "fill_rate": _percent(n_cap, n_app, 1),
        "clinical_fact_recall": _percent(n_ok, n_app, 1),
        "clinical_fact_precision": _percent(n_ok, n_app, 1),
        "hallucination_rate": _percent(n_hall, n_app, 1),
        "critical_fact_accuracy": _percent(
            len(critical_correct), len(critical_denom), 1
        )
        if critical_denom
        else None,
        "medication_accuracy": subset(lambda r: "medication" in (r.get("categories") or [])),
        "diagnosis_accuracy": subset(lambda r: "diagnosis" in (r.get("categories") or [])),
        "temporal_accuracy": subset(lambda r: "temporal" in (r.get("categories") or [])),
        "numerical_unit_accuracy": subset(
            lambda r: "numerical" in (r.get("categories") or [])
        ),
        "critical_error_count": len(critical_errors),
        "applicable_count": n_app,
        "correct_count": n_ok,
        "missing_count": len(missing),
        "captured_count": n_cap,
        "hallucination_count": n_hall,
        "numeric_tolerance": num_tol,
    }


def _section_details(evaluated: list[dict]) -> dict[str, Any]:
    grouped: dict[str, list[dict]] = {}
    for row in evaluated:
        key = _norm_name(row.get("section")) or "other"
        grouped.setdefault(key, []).append(row)
    details: dict[str, Any] = {}
    for key, rows in grouped.items():
        applicable = [r for r in rows if r.get("result") != NA]
        denom = sum(int(r["weight"]) for r in applicable)
        numer = sum(int(r["weight"]) for r in applicable if r.get("result") == CORRECT)
        diffs = []
        for row in applicable:
            if row.get("result") in (CORRECT, NA):
                continue
            diffs.append(
                {
                    "field": row.get("field"),
                    "ground_truth": row.get("ground_truth"),
                    "generated": row.get("generated"),
                    "type": str(row.get("result") or "").lower(),
                    "severity": str(row.get("criticality") or "Normal").lower(),
                    "internal": row.get("internal"),
                }
            )
        details[key] = {
            "score": _percent(numer, denom, 1),
            "differences": diffs,
        }
    return details


def findings_for(metrics: dict, scoring_config: dict) -> list[str]:
    notes = [
        "SOAP accuracy is independent of transcription/translation scores.",
        "NA facts are excluded from numerator and denominator.",
        "Missing is GT-established with no generated capture; NA is not established.",
        "Contradictory values are classified Incorrect (no fifth external label).",
        "LLM extra remaps to Hallucination; Correct is explicit, not inferred silence.",
        "Severity map: critical→Critical(5), high→High(3), medium→Normal(1), low→Normal(1).",
        "Numeric tolerance is unset; any numeric mismatch is Incorrect.",
        "Nested SOAP empty fields cannot distinguish NA vs omitted GT; empty GT is NA.",
        "Dose is medication, not numerical, so Numerical/Unit Accuracy is vitals only.",
        "Critical-Fact Accuracy uses config critical_fact_metric_fields plus hallucinations.",
    ]
    if scoring_config.get("numeric_tolerance") is None:
        notes.append(
            "Flag: define numeric_tolerance in soap_fact_scoring.yaml when doctors agree a threshold."
        )
    _ = metrics
    return notes


def classify_final_result(
    *,
    has_transcript_gt: bool,
    has_soap_gt: bool,
    transcription_skipped: bool,
    transcription_severity: str | None,
    transcription_score: float | None,
    soap_score: float | None,
    soap_severity: str | None,
    pass_score: float | None = None,
    scoring_config: dict | None = None,
    thresholds: Any = None,
) -> str:
    """Run verdict. Transcription path unchanged when transcript GT exists.

    SOAP-only (no transcript GT, SOAP GT present) uses the SOAP weighted score
    with the same pass bar as transcription (test_settings.accuracy_pass_score).
    SOAP fact criticality is a weight, not a fail override. Thresholds never
    set Execution Status.
    """
    _ = scoring_config
    t = thresholds or get_accuracy_thresholds()
    if pass_score is not None:
        t = t.with_pass_score(pass_score)

    if has_transcript_gt and not transcription_skipped:
        if transcription_score is None:
            sev = (transcription_severity or "").strip().lower()
            if sev in ("high", "critical"):
                return "fail"
            return "review"
        return accuracy_band_from_score(
            transcription_score,
            severity=transcription_severity,
            thresholds=t,
        )

    if has_soap_gt and soap_score is not None:
        # SOAP-only verdict is the weighted clinical score. Fact criticality
        # (Critical=5) is a weight, not a fail override — Example 1 is 90%
        # with one Critical vital mismatch and must still pass. Critical
        # Error Count stays a separate metric. soap_severity is unused here.
        _ = soap_severity
        return accuracy_band_from_score(
            soap_score,
            severity=None,
            thresholds=t,
        )

    return "complete_no_accuracy"


def score_soap(
    soap_ground_truth: Any,
    soap_generated: Any,
    scoring_config: dict | None = None,
    section_details: Any = None,
) -> dict[str, Any]:
    """SOAP-only fact-level evaluation. Does not read transcription/translation."""
    cfg = scoring_config or load_scoring_config()
    if not soap_ground_truth and not soap_generated:
        return {
            "similarity_score": None,
            "overall_weighted_clinical_score": None,
            "overall_severity": "unknown",
            "section_details": {},
            "metrics": {},
            "facts": [],
            "ground_truth_facts": {"facts": []},
            "generated_facts": {"facts": []},
            "findings": findings_for({}, cfg),
            "error": "Missing ground truth or generated SOAP",
        }

    gt_facts = coerce_fact_list(soap_ground_truth, cfg)
    gen_facts = coerce_fact_list(soap_generated, cfg)
    evaluated = evaluate_aligned(align_facts(gt_facts, gen_facts, cfg), cfg)
    evaluated = apply_section_details(evaluated, section_details, cfg)
    metrics = compute_metrics(evaluated, cfg)
    overall = metrics.get("overall_weighted_clinical_score")
    details = _section_details(evaluated)
    if section_details and isinstance(section_details, dict) and not details:
        details = section_details
    errors = [row for row in evaluated if row.get("result") in ERROR_RESULTS]
    critical_errors = [
        row
        for row in errors
        if str(row.get("criticality") or "") == "Critical" or int(row.get("weight") or 0) == 5
    ]
    if critical_errors:
        severity = "critical"
    elif errors:
        severity = "medium"
    else:
        severity = "none"

    return {
        "similarity_score": overall,
        "overall_weighted_clinical_score": overall,
        "overall_severity": severity,
        "summary": (
            f"SOAP weighted clinical score {overall}% "
            f"({metrics.get('correct_weight')}/{metrics.get('applicable_weight')} applicable weight)"
            if overall is not None
            else "SOAP not scored"
        ),
        "section_details": details,
        "metrics": metrics,
        "facts": evaluated,
        "ground_truth_facts": facts_document(gt_facts),
        "generated_facts": facts_document(gen_facts),
        "findings": findings_for(metrics, cfg),
        "error": "",
        "numeric_tolerance": deepcopy(cfg.get("numeric_tolerance")),
    }
