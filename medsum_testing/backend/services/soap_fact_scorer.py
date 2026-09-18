"""Fact-level weighted SOAP scorer (MOM clinical-fact model).

Transcription/translation scoring is unchanged and is not blended in.

Overall Weighted Clinical Score
    = Σ(section score × renormalized section weight) for scored sections
Fixed quotas (config section_weights): Subjective 25, Objective 20,
Assessment 25, Plan 30. A section with no applicable facts (score is
null) is excluded and the remaining quotas are renormalized to 100.
Within a section, Critical=5 / High=3 / Normal=1 still apply.
NA facts are excluded from both the section numerator and denominator.

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

import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("medsum_ai")

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
# Digits and letters are separate alternatives (not one [a-z0-9]+ class) so a
# unit glued to its number tokenizes the same as when it's spaced out —
# "100mg" -> ["100", "mg"], same as "100 mg" -> ["100", "mg"]. Otherwise
# "100mg" was one token ("100mg") that never matched "100 mg"'s two tokens.
_WORD_RE = re.compile(r"\d+|[a-z]+(?:'[a-z]+)?")
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
        "it",
        "this",
        "that",
        "these",
        "those",
        "about",
        "approximately",
        "currently",
    }
)

# Spelled-out numbers, so "Four weeks" matches "4 weeks". Compound forms like
# "twenty-five" tokenize as two words ("twenty", "five") and normalize to two
# separate digit tokens rather than "25" — an accepted gap for durations/doses,
# which are almost always small standalone numbers in practice.
_WORD_TO_NUM = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90", "hundred": "100",
}

# Clinical wording variants that mean the same thing, so free-text fields
# (diagnosis, allergy reaction, exam findings) aren't marked Incorrect just
# because the generated note used a different but equivalent word.
_WORD_SYNONYMS = {
    "allergic": "allergy",
    "allergies": "allergy",
    "causing": "cause",
    "causes": "cause",
    "caused": "cause",
    "regular": "normal",
    "heartbeat": "heart",
    "heartbeats": "heart",
    "difficulty": "problem",
    "difficulties": "problem",
    "problems": "problem",
    "milligram": "mg",
    "milligrams": "mg",
    "microgram": "mcg",
    "micrograms": "mcg",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "kilogram": "kg",
    "kilograms": "kg",
    "gram": "g",
    "grams": "g",
}


def _canon_word(word: str) -> str:
    if word in _WORD_TO_NUM:
        return _WORD_TO_NUM[word]
    return _WORD_SYNONYMS.get(word, word)


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
    words_a = [_canon_word(w) for w in _WORD_RE.findall(a) if w not in _FILLER]
    words_b = [_canon_word(w) for w in _WORD_RE.findall(b) if w not in _FILLER]
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
        if mapped == INCORRECT and row.get("result") != NA:
            deterministic = classify_pair(
                row.get("ground_truth"), row.get("generated"), cfg, gt_applicable=None
            )
            if deterministic.get("result") == CORRECT:
                # LLM flagged a paraphrase as a mismatch, but the deterministic
                # word-overlap match already confirms the same clinical content
                # (e.g. "Gastritis with acid reflux symptoms" vs "Gastritis and
                # acid reflux") — trust the deterministic Correct instead of
                # letting a single LLM diff zero out a Critical-weighted fact.
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
    section_scores = _section_scores(evaluated)
    overall, section_weight_breakdown = weighted_overall_from_sections(
        section_scores, cfg
    )
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
        "clinical_fact_precision": _percent(n_ok, n_cap, 1),
        "hallucination_rate": _percent(n_hall, n_cap, 1),
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
        "section_weight_breakdown": section_weight_breakdown,
    }


def _section_score_for_rows(rows: list[dict]) -> float | None:
    applicable = [r for r in rows if r.get("result") != NA]
    denom = sum(int(r["weight"]) for r in applicable)
    numer = sum(int(r["weight"]) for r in applicable if r.get("result") == CORRECT)
    return _percent(numer, denom, places=2)


def _section_scores(
    evaluated: list[dict],
    section_keys: tuple[str, ...] = ("subjective", "objective", "assessment", "plan"),
) -> dict[str, float | None]:
    scores: dict[str, float | None] = {}
    for key in section_keys:
        rows = [row for row in evaluated if _norm_name(row.get("section")) == key]
        scores[key] = _section_score_for_rows(rows)
    return scores


def weighted_overall_from_sections(
    section_scores: dict[str, Any] | None,
    cfg: dict,
) -> tuple[float | None, dict[str, Any]]:
    """Fixed section quotas, renormalized when a section has no ground truth."""
    weights = cfg.get("section_weights") or {}
    scores = section_scores or {}
    kept: dict[str, tuple[float, float]] = {}
    for key, raw_weight in weights.items():
        score = scores.get(key)
        if score is not None and raw_weight:
            kept[key] = (float(raw_weight), float(score))
    if not kept:
        return None, {}
    total = sum(weight for weight, _ in kept.values())
    if not total:
        return None, {}
    overall = round(sum((weight / total) * score for weight, score in kept.values()), 2)
    breakdown: dict[str, Any] = {}
    for key, raw_weight in weights.items():
        score = scores.get(key)
        if key in kept:
            normalized = kept[key][0] / total * 100
            breakdown[key] = {
                "configured_weight": raw_weight,
                "normalized_weight": round(normalized, 2),
                "score": score,
                "scored": True,
            }
        else:
            breakdown[key] = {
                "configured_weight": raw_weight,
                "normalized_weight": None,
                "score": score,
                "scored": False,
            }
    return overall, breakdown


def _section_details(evaluated: list[dict]) -> dict[str, Any]:
    grouped: dict[str, list[dict]] = {}
    for row in evaluated:
        key = _norm_name(row.get("section")) or "other"
        grouped.setdefault(key, []).append(row)
    soap_scores = _section_scores(evaluated)
    details: dict[str, Any] = {}
    for key, rows in grouped.items():
        applicable = [r for r in rows if r.get("result") != NA]
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
            "score": (
                soap_scores[key] if key in soap_scores else _section_score_for_rows(rows)
            ),
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


# ---------------------------------------------------------------------------
# Scoring method switch.
#   "weighted"          — fact-level MOM scorer above (section-weighted,
#                          Critical/High/Normal criticality weights).
#   "simple_key_match"  — fixed-schema key match: +1 per key that matches
#                          ground truth, 0 otherwise; score = correct/total*100.
# Flip this constant to change which method score_soap() uses.
# ---------------------------------------------------------------------------
SCORING_METHOD = "simple_key_match"

# Simple method's fixed 24-key schema (Subjective 7 / Objective 9 /
# Assessment 4 / Plan 4, excluding medications) plus 5 keys per medicine
# (Drug name, Dose, Schedule, Duration, Instructions) added on top.
# e.g. 1 medicine = 29 keys, 2 medicines = 34 keys. Medicine count is read
# from ground truth (plan.medications), not generated.
# "text": True marks long free-text narrative fields where deterministic
# word-overlap matching (values_match) under-matches genuine paraphrases —
# these get a semantic LLM verdict when the deterministic check disagrees.
# See _apply_llm_text_verification().
SIMPLE_FIXED_KEYS: tuple[dict[str, Any], ...] = (
    {"field": "Chief complaint", "section": "Subjective", "path": "subjective.chief_complaint", "text": True},
    {"field": "History of present illness", "section": "Subjective", "path": "subjective.history_of_present_illness", "text": True},
    {"field": "Past medical history", "section": "Subjective", "path": "subjective.past_medical_history", "text": True},
    {"field": "Current medications", "section": "Subjective", "path": "subjective.medications", "text": True},
    {"field": "Allergy", "section": "Subjective", "path": "subjective.allergies"},
    {"field": "Social history", "section": "Subjective", "path": "subjective.social_history", "text": True},
    {"field": "Family history", "section": "Subjective", "path": "subjective.family_history", "text": True},
    {"field": "Blood pressure", "section": "Objective", "path": "objective.vitals.blood_pressure"},
    {"field": "Heart rate", "section": "Objective", "path": "objective.vitals.heart_rate"},
    {"field": "Respiratory rate", "section": "Objective", "path": "objective.vitals.respiratory_rate"},
    {"field": "Temperature", "section": "Objective", "path": "objective.vitals.temperature"},
    {"field": "SpO2", "section": "Objective", "path": "objective.vitals.spo2"},
    {"field": "Heart exam", "section": "Objective", "path": "objective.physical_exam.heart"},
    {"field": "Other findings", "section": "Objective", "path": "objective.physical_exam.other_findings", "text": True},
    {"field": "Height", "section": "Objective", "path": "objective.vitals.height"},
    {"field": "Weight", "section": "Objective", "path": "objective.vitals.weight"},
    {"field": "Diagnosis", "section": "Assessment", "path": "assessment.diagnosis", "text": True},
    {"field": "Diagnosis type", "section": "Assessment", "path": "assessment.type"},
    {"field": "Diagnosis status", "section": "Assessment", "path": "assessment.status"},
    {"field": "Assessment reasoning", "section": "Assessment", "path": "assessment.reasoning", "text": True},
    {"field": "Activity", "section": "Plan", "path": "plan.activity"},
    {"field": "Investigations", "section": "Plan", "path": "plan.investigations", "text": True},
    {"field": "Education", "section": "Plan", "path": "plan.education", "text": True},
    {"field": "Follow-up", "section": "Plan", "path": "plan.follow_up", "text": True},
)

SIMPLE_MEDICATION_KEYS: tuple[tuple[str, str, bool], ...] = (
    ("drug_name", "Drug name", False),
    ("dose", "Dose", False),
    ("schedule", "Schedule", False),
    ("duration", "Duration", False),
    ("instructions", "Instructions", True),
)


def _simple_first_value(root: Any, path: str) -> Any:
    values = _path_get(root, path)
    return values[0] if values else None


def _simple_medication_list(root: Any) -> list[dict]:
    return [m for m in _path_get(root, "plan.medications.*") if isinstance(m, dict)]


def _simple_values_match(gt_val: Any, gen_val: Any) -> bool:
    """values_match(), plus: GT "NA"/"N/A"/etc. matched against a null/empty
    generated value counts as a match. GT is established but literally marked
    not-applicable, and the generated SOAP has nothing there either — that's
    agreement, not a miss. is_na_value() (na_markers in soap_fact_scoring.yaml)
    already treats a true empty string as NA too, so this also covers GT=""
    vs generated="" without a separate check.
    """
    if is_na_value(gt_val) and is_na_value(gen_val):
        return True
    return values_match(gt_val, gen_val)


def build_simple_key_facts(
    soap_ground_truth: Any, soap_generated: Any
) -> list[dict[str, Any]]:
    """Fixed-schema fact list for SCORING_METHOD == 'simple_key_match'."""
    gt = soap_ground_truth if isinstance(soap_ground_truth, dict) else {}
    gen = soap_generated if isinstance(soap_generated, dict) else {}
    facts: list[dict[str, Any]] = []

    for spec in SIMPLE_FIXED_KEYS:
        gt_val = _leaf_text(_simple_first_value(gt, spec["path"]))
        gen_val = _leaf_text(_simple_first_value(gen, spec["path"]))
        result = CORRECT if _simple_values_match(gt_val, gen_val) else INCORRECT
        facts.append(
            {
                "section": spec["section"],
                "field": spec["field"],
                "base_field": spec["field"],
                "ground_truth": gt_val,
                "generated": gen_val,
                "criticality": "Normal",
                "weight": 1,
                "result": result,
                "internal": result,
                "index": None,
                "is_text": bool(spec.get("text")),
            }
        )

    gt_meds = _simple_medication_list(gt)
    gen_meds = _simple_medication_list(gen)
    for i, gt_med in enumerate(gt_meds):
        gen_med = gen_meds[i] if i < len(gen_meds) else {}
        for sub_key, label, is_text in SIMPLE_MEDICATION_KEYS:
            gt_val = _leaf_text(gt_med.get(sub_key))
            gen_val = _leaf_text(gen_med.get(sub_key))
            result = CORRECT if _simple_values_match(gt_val, gen_val) else INCORRECT
            facts.append(
                {
                    "section": "Plan",
                    "field": f"{label} [{i + 1}]",
                    "base_field": label,
                    "ground_truth": gt_val,
                    "generated": gen_val,
                    "criticality": "Normal",
                    "weight": 1,
                    "result": result,
                    "internal": result,
                    "index": i,
                    "is_text": is_text,
                }
            )
    return facts


def _apply_llm_text_verification(
    facts: list[dict[str, Any]], model: str | None, config: dict | None
) -> None:
    """Upgrade INCORRECT text-field facts to CORRECT when the LLM confirms the
    same clinical meaning. Deterministic word-overlap matching (values_match)
    under-matches long narrative fields that say the same thing in very
    different words (e.g. two differently-phrased HPI paragraphs) — this is
    a semantic second opinion, only invoked where the deterministic check
    already disagreed, and only for fields flagged "text" in SIMPLE_FIXED_KEYS
    / SIMPLE_MEDICATION_KEYS. Mutates facts in place.
    """
    if not model:
        return
    candidates = [
        f
        for f in facts
        if f.get("is_text")
        and f.get("result") == INCORRECT
        and _text(f.get("ground_truth"))
        and _text(f.get("generated"))
    ]
    if not candidates:
        return
    from medsum_testing.backend.services.ai_comparator import llm_verify_text_matches

    pairs = [
        {
            "field": f["field"],
            "ground_truth": f["ground_truth"],
            "generated": f["generated"],
        }
        for f in candidates
    ]
    try:
        verdicts = llm_verify_text_matches(pairs, model, config)
    except Exception:
        log.warning(
            "SOAP_TEXT_VERIFY: llm_verify_text_matches raised, keeping "
            "deterministic results for %d text field(s)",
            len(candidates),
            exc_info=True,
        )
        return
    upgraded = sum(1 for v in verdicts if v)
    log.info(
        "SOAP_TEXT_VERIFY: %d/%d text-field mismatches upgraded to Correct by LLM",
        upgraded,
        len(candidates),
    )
    for fact, is_match in zip(candidates, verdicts):
        if is_match:
            fact["result"] = CORRECT
            fact["internal"] = CORRECT


def score_soap_simple_key_match(
    soap_ground_truth: Any,
    soap_generated: Any,
    model: str | None = None,
    config: dict | None = None,
) -> dict[str, Any]:
    """Key-match scorer: +1 per matching key, 0 otherwise; score = correct/total*100.

    model: when given, free-text fields (is_text=True) that fail the
    deterministic word-overlap check get a semantic LLM re-check before being
    scored 0 — see _apply_llm_text_verification(). Pass None to stay fully
    deterministic (no API calls).
    """
    facts = build_simple_key_facts(soap_ground_truth, soap_generated)
    _apply_llm_text_verification(facts, model, config)
    total = len(facts)
    correct = sum(1 for f in facts if f["result"] == CORRECT)
    incorrect = total - correct
    score = _percent(correct, total, places=1)
    section_details = _section_details(facts)

    metrics = {
        "overall_weighted_clinical_score": score,
        "applicable_weight": total,
        "correct_weight": correct,
        "fill_rate": score,
        "clinical_fact_recall": score,
        "clinical_fact_precision": score,
        "hallucination_rate": None,
        "critical_fact_accuracy": None,
        "medication_accuracy": None,
        "diagnosis_accuracy": None,
        "temporal_accuracy": None,
        "numerical_unit_accuracy": None,
        "critical_error_count": 0,
        "applicable_count": total,
        "correct_count": correct,
        "missing_count": 0,
        "captured_count": total,
        "hallucination_count": 0,
        "incorrect_count": incorrect,
        "numeric_tolerance": None,
        "section_weight_breakdown": {},
        "scoring_method": "simple_key_match",
        "total_keys": total,
        "correct_keys": correct,
    }

    def _doc(side: str) -> dict[str, Any]:
        return {
            "facts": [
                {"section": f["section"], "field": f["field"], "value": f[side], "criticality": "Normal"}
                for f in facts
            ]
        }

    return {
        "similarity_score": score,
        "overall_weighted_clinical_score": score,
        "overall_severity": "none" if incorrect == 0 else "medium",
        "summary": (
            f"SOAP simple key-match score {score}% ({correct}/{total} keys correct)"
            if score is not None
            else "SOAP not scored"
        ),
        "section_details": section_details,
        "metrics": metrics,
        "facts": facts,
        "ground_truth_facts": _doc("ground_truth"),
        "generated_facts": _doc("generated"),
        "findings": [
            "Scoring method: simple_key_match (fixed 24-key schema + 5 keys per GT medicine).",
            "Every key is worth 1 point: correct/total_keys * 100, no criticality weighting.",
            "Medicine count is read from ground truth; extra generated medicines are ignored.",
            "Free-text fields (HPI, histories, reasoning, education, follow-up, "
            "investigations, other findings, instructions) get an LLM semantic "
            "re-check when word-overlap matching disagrees, so differently "
            "worded paraphrases still score correct."
            if model
            else "Free-text fields used deterministic word-overlap matching only "
            "(no model passed to score_soap_simple_key_match) — long paraphrased "
            "narrative fields may be under-scored.",
        ],
        "error": "",
        "numeric_tolerance": None,
    }


def score_soap(
    soap_ground_truth: Any,
    soap_generated: Any,
    scoring_config: dict | None = None,
    section_details: Any = None,
    model: str | None = None,
    app_config: dict | None = None,
) -> dict[str, Any]:
    """SOAP-only fact-level evaluation. Does not read transcription/translation.

    model / app_config are only used by the simple_key_match method (SCORING_METHOD)
    to semantically re-check free-text fields via the LLM — see
    score_soap_simple_key_match(). The weighted method ignores them.
    """
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

    if SCORING_METHOD == "simple_key_match":
        return score_soap_simple_key_match(
            soap_ground_truth, soap_generated, model=model, config=app_config
        )

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
            "(section-weighted: Subjective/Objective/Assessment/Plan)"
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
