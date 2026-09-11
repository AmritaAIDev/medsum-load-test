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
with empty generated is Missing — unless field_path is in the Fix #1 empty-norm
set (allergies/medications/investigations/physical_exam), where NKA/NA/None
collapse together. NA is never scored as Missing.
LLM overlays never rewrite deterministic NA into Incorrect/Missing/Hallucination.

Established negatives (NKA / no known allergies / …) match each other as Correct.
Numerical/vital fields match on equal extracted numbers (units/labels ignored).
Word-subset matches require config subset_coverage_min (default 0.75).
Medication arrays rematch order-independently by drug name (Fix #0).
Narrative fields may match via semantic similarity thresholds (Fix #4/#6).

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
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from medsum_testing.backend.services.accuracy_thresholds import (
    accuracy_band_from_score,
    get_accuracy_thresholds,
)
from medsum_testing.backend.services.config_loader import get_repo_root
from medsum_testing.backend.services.medication_comparison import (
    ScoringComparator,
    compare_medication_arrays,
    drug_names_match,
    match_medication_indices,
    normalize_drug_name,
)

CORRECT = "Correct"
INCORRECT = "Incorrect"
MISSING = "Missing"
HALLUCINATION = "Hallucination"
NA = "NA"
CONTRADICTORY = "Contradictory"
PARTIAL = "Partial"

EXTERNAL_RESULTS = (CORRECT, INCORRECT, MISSING, HALLUCINATION, NA, PARTIAL)
GENERATED_RESULTS = (CORRECT, INCORRECT, HALLUCINATION, PARTIAL)
ERROR_RESULTS = (INCORRECT, MISSING, HALLUCINATION)

# Fields where empty/absence tokens normalize together (Fix #1).
_EMPTY_NORM_FIELDS = frozenset(
    {
        "allergies",
        "allergy",
        "medications",
        "current medications",
        "investigations",
        "physical_exam",
        "physical exam",
        "other findings",
        "heart exam",
        "subjective.allergies",
        "subjective.medications",
        "plan.medications",
        "plan.investigations",
        "objective.physical_exam",
    }
)

NORMALIZED_EMPTY = {
    "",
    None,
    "NA",
    "N/A",
    "None",
    "none",
    "null",
    "Not applicable",
    "Not known",
    "Unknown",
    "not applicable",
    "not known",
    "unknown",
    "na",
    "n/a",
}

# Fix #5 — per-field pass bars (similarity / accuracy).
FIELD_PASS_THRESHOLDS = {
    "assessment.diagnosis": 0.95,
    "plan.medications.drug_name": 0.95,
    "subjective.allergies": 0.95,
    "objective.vitals": 0.85,
    "plan.medications.dose": 0.90,
    "subjective.chief_complaint": 0.70,
    "assessment.reasoning": 0.70,
    "plan.education": 0.70,
}

# Fix #4 — semantic thresholds by field path (also covers Fix #6 reasoning).
SEMANTIC_THRESHOLDS = {
    "assessment.diagnosis": 0.95,
    "plan.medications.drug_name": 0.95,
    "plan.medications.dose": 0.95,
    "plan.medications.snomed_ct_id": 0.95,
    "subjective.allergies": 0.95,
    "objective.vitals.blood_pressure": 0.85,
    "objective.vitals.heart_rate": 0.85,
    "objective.vitals.respiratory_rate": 0.85,
    "objective.vitals.temperature": 0.85,
    "plan.medications.schedule": 0.85,
    "assessment.status": 0.85,
    "subjective.chief_complaint": 0.75,
    "subjective.history_of_present_illness": 0.75,
    "subjective.past_medical_history": 0.75,
    "subjective.current_medications": 0.75,
    "objective.physical_exam": 0.75,
    "assessment.reasoning": 0.75,
    "plan.activity": 0.75,
    "plan.investigations": 0.75,
    "plan.education": 0.75,
    "plan.follow_up": 0.75,
    "summary": 0.70,
}

_SEMANTIC_MODEL = None
_SEMANTIC_MODEL_FAILED = False

_VITAL_FIELD_PATHS = frozenset(
    {
        "objective.vitals.blood_pressure",
        "objective.vitals.heart_rate",
        "objective.vitals.respiratory_rate",
        "objective.vitals.temperature",
        "blood pressure",
        "heart rate",
        "pulse",
        "respiratory rate",
        "temperature",
    }
)

_MED_LEAF_FIELDS = frozenset(
    {"drug name", "dose", "schedule", "duration", "instructions", "snomed ct id"}
)

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
# Labels/units stripped before word compare on vitals / numeric fields.
_VITAL_NOISE = frozenset(
    {
        "bp",
        "blood",
        "pressure",
        "mmhg",
        "hr",
        "heart",
        "rate",
        "pulse",
        "bpm",
        "beats",
        "min",
        "rr",
        "respiratory",
        "resp",
        "breaths",
        "breathing",
        "temp",
        "temperature",
        "c",
        "f",
        "celsius",
        "fahrenheit",
        "deg",
        "degree",
        "degrees",
        "spo2",
        "oxygen",
        "saturation",
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


def is_established_none(value: Any, scoring_config: dict | None = None) -> bool:
    """Explicit 'none/no medications/no investigations' (still an established fact)."""
    cfg = scoring_config or load_scoring_config()
    return _matches_marker(value, _markers(cfg, "established_none_markers"))


def is_established_gt(value: Any, scoring_config: dict | None = None) -> bool:
    """GT establishes a fact, including explicit negatives. Empty/NA does not."""
    if is_na_value(value, scoring_config):
        return False
    return True


def equivalent_established_absence(
    left: Any, right: Any, scoring_config: dict | None = None
) -> bool:
    """True when both sides state the same kind of established absence.

    Explicit tokens like 'none' / 'nil' count as absence companions for either
    allergy negatives or none-of-X phrases. Empty string does not — that stays
    Missing when GT established (safety).
    """
    cfg = scoring_config or load_scoring_config()
    left_neg = is_established_negative(left, cfg)
    right_neg = is_established_negative(right, cfg)
    left_none = is_established_none(left, cfg)
    right_none = is_established_none(right, cfg)
    if left_neg and right_neg:
        return True
    if left_none and right_none:
        return True
    # Cross-family only for bare none/nil tokens (shared vocabulary).
    bare = frozenset({"none", "nil"})
    left_bare = _norm_name(left) in bare
    right_bare = _norm_name(right) in bare
    if left_neg and right_bare:
        return True
    if right_neg and left_bare:
        return True
    if left_none and right_bare:
        return True
    if right_none and left_bare:
        return True
    return False


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


def numbers_equal(left: Any, right: Any) -> bool:
    a = extract_numeric_tokens(left)
    b = extract_numeric_tokens(right)
    return bool(a) and a == b


def normalize_vital_text(value: Any) -> str:
    """Strip common vital labels/units so 'BP 140/90' aligns with '140/90 mmHg'."""
    words = [
        w
        for w in _WORD_RE.findall(_norm_name(value))
        if w not in _FILLER and w not in _VITAL_NOISE
    ]
    return " ".join(words)


# --- Fix #0: order-independent medication array comparison --------------------
# Implemented in medication_comparison.ScoringComparator / compare_medication_arrays.


# --- Fix #1: empty / null normalization --------------------------------------


def normalize_for_comparison(value: Any, *, collapse_established: bool = False) -> Any:
    """Convert common empty/absence representations to empty string."""
    if isinstance(value, str):
        stripped = value.strip()
        normalized = stripped.lower()
        empty_tokens = {
            str(x).lower() for x in NORMALIZED_EMPTY if x is not None
        } | {""}
        if normalized in empty_tokens:
            return ""
        if is_na_value(stripped):
            return ""
        # Scoped fields (allergies/meds/investigations/exam): NKA / none → empty.
        if collapse_established and (
            is_established_negative(stripped) or is_established_none(stripped)
        ):
            return ""
        return stripped
    if value is None or value == "":
        return ""
    return value


def _field_allows_empty_norm(field_path: str | None) -> bool:
    if not field_path:
        return False
    key = _norm_name(field_path)
    return key in {_norm_name(f) for f in _EMPTY_NORM_FIELDS} or any(
        key.endswith(_norm_name(tail))
        for tail in ("allergies", "allergy", "medications", "investigations", "physical exam")
    )


# --- Fix #2: vitals format standardization -----------------------------------


def is_vital_field(field_path: str | None) -> bool:
    """Check if field is a vital sign."""
    if not field_path:
        return False
    key = _norm_name(field_path)
    return key in {_norm_name(p) for p in _VITAL_FIELD_PATHS} or key.endswith(
        ("blood pressure", "heart rate", "pulse", "respiratory rate", "temperature")
    )


def extract_vital_name(field_path: str) -> str:
    """Extract vital name from field path."""
    tail = field_path.split(".")[-1]
    key = _norm_name(tail)
    if key in {"pulse", "heart rate"}:
        return "heart_rate"
    if key == "blood pressure":
        return "blood_pressure"
    if key == "respiratory rate":
        return "respiratory_rate"
    return key.replace(" ", "_")


def _normalize_bp(value: str) -> str:
    """Normalize BP to SYS/DIA mmHg format."""
    if "mmhg" in value.lower():
        return value if value.endswith("mmHg") or "mmHg" in value else f"{value}"
    if "/" in value:
        return f"{value} mmHg" if not value.lower().endswith("mmhg") else value
    return value


def _add_unit(value: str, unit: str) -> str:
    """Add unit if missing."""
    if unit.lower() in value.lower():
        return value
    clean = re.sub(
        r"\s*(bpm|breaths/min|°C|beats/min|c|f)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return f"{clean} {unit}".strip()


def normalize_vital(vital_name: str, value: Any) -> str:
    """Standardize vital sign formats for comparison."""
    if value in ("", None, "NA", "N/A") or value is None:
        return ""
    if isinstance(value, str) and not value.strip():
        return ""
    value_str = str(value).strip()
    templates = {
        "blood_pressure": _normalize_bp,
        "heart_rate": lambda v: _add_unit(v, "bpm"),
        "pulse": lambda v: _add_unit(v, "bpm"),
        "respiratory_rate": lambda v: _add_unit(v, "breaths/min"),
        "temperature": lambda v: _add_unit(v, "°C"),
    }
    normalizer = templates.get(vital_name, lambda x: x)
    return normalizer(value_str)


# --- Fix #4 / #6: semantic matching for narrative fields ---------------------


def _get_semantic_model():
    global _SEMANTIC_MODEL, _SEMANTIC_MODEL_FAILED
    if _SEMANTIC_MODEL_FAILED:
        return None
    if _SEMANTIC_MODEL is not None:
        return _SEMANTIC_MODEL
    try:
        from sentence_transformers import SentenceTransformer

        _SEMANTIC_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        return _SEMANTIC_MODEL
    except Exception:
        _SEMANTIC_MODEL_FAILED = True
        return None


def semantic_similarity(text1: str, text2: str) -> float:
    """Calculate semantic similarity between two texts (0-1 scale)."""
    if not text1 or not text2:
        return 0.0
    model = _get_semantic_model()
    if model is not None:
        try:
            from sentence_transformers import util

            embeddings1 = model.encode(text1, convert_to_tensor=True)
            embeddings2 = model.encode(text2, convert_to_tensor=True)
            similarity = util.pytorch_cos_sim(embeddings1, embeddings2)
            return float(similarity[0][0])
        except Exception:
            pass
    # Lightweight fallback: max(sequence ratio, stemmed-token Jaccard/recall).
    a = _norm_name(text1)
    b = _norm_name(text2)
    seq = SequenceMatcher(None, a, b).ratio()
    words_a = {_stem_token(w) for w in _WORD_RE.findall(a) if w not in _FILLER}
    words_b = {_stem_token(w) for w in _WORD_RE.findall(b) if w not in _FILLER}
    if not words_a or not words_b:
        return seq
    inter = words_a & words_b
    union = words_a | words_b
    jaccard = len(inter) / len(union)
    soft_recall = len(inter) / min(len(words_a), len(words_b))
    return max(seq, jaccard, soft_recall)


def _stem_token(word: str) -> str:
    """Very light stem so day/days and fever variants align in the fallback."""
    if word in {"days", "day"}:
        return "day"
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    if word.endswith("ed") and len(word) > 5:
        return word[:-2]
    return word


def _semantic_field_key(field_path: str | None) -> str | None:
    if not field_path:
        return None
    raw = str(field_path).strip()
    if raw in SEMANTIC_THRESHOLDS:
        return raw
    key = _norm_name(raw)
    aliases = {
        "chief complaint": "subjective.chief_complaint",
        "history of present illness": "subjective.history_of_present_illness",
        "past medical history": "subjective.past_medical_history",
        "current medications": "subjective.current_medications",
        "allergy": "subjective.allergies",
        "allergies": "subjective.allergies",
        "physical exam": "objective.physical_exam",
        "other findings": "objective.physical_exam",
        "assessment reasoning": "assessment.reasoning",
        "reasoning": "assessment.reasoning",
        "diagnosis": "assessment.diagnosis",
        "activity": "plan.activity",
        "investigations": "plan.investigations",
        "education": "plan.education",
        "follow up": "plan.follow_up",
        "follow-up": "plan.follow_up",
        "summary": "summary",
        "drug name": "plan.medications.drug_name",
        "dose": "plan.medications.dose",
        "schedule": "plan.medications.schedule",
        "blood pressure": "objective.vitals.blood_pressure",
        "heart rate": "objective.vitals.heart_rate",
        "pulse": "objective.vitals.heart_rate",
        "respiratory rate": "objective.vitals.respiratory_rate",
        "temperature": "objective.vitals.temperature",
    }
    return aliases.get(key)


def _content_words(value: Any, *, strip_vital_noise: bool = False) -> list[str]:
    text = normalize_vital_text(value) if strip_vital_noise else _norm_name(value)
    return [w for w in _WORD_RE.findall(text) if w not in _FILLER]


def _subset_coverage_ok(
    shorter: list[str], longer: list[str], min_ratio: float
) -> bool:
    if not shorter or not longer:
        return False
    if set(shorter) <= set(longer):
        return (len(set(shorter)) / len(set(longer))) >= min_ratio
    return False


def values_match(
    left: Any,
    right: Any,
    scoring_config: dict | None = None,
    *,
    numerical: bool = False,
    field_path: str | None = None,
) -> bool:
    """True when values are equivalent under SOAP fact rules.

    Established negatives / none-phrases are handled in classify_pair.
    For numerical/vital fields, equal extracted numbers win (units/labels ignored).
    Word-subset matches require coverage >= config subset_coverage_min so a
    short fragment cannot pass against a long ground-truth narrative.
    """
    cfg = scoring_config or load_scoring_config()

    # FIX #1: empty/null normalization (scoped fields, or always for token empties)
    if _field_allows_empty_norm(field_path):
        left = normalize_for_comparison(left, collapse_established=True)
        right = normalize_for_comparison(right, collapse_established=True)
    else:
        # Still normalize bare NA/null tokens globally for equality of empties.
        if (
            normalize_for_comparison(left) == ""
            and normalize_for_comparison(right) == ""
            and (
                is_na_value(left, cfg)
                or left in ("", None)
                or is_na_value(right, cfg)
                or right in ("", None)
            )
        ):
            # Only when both are true NA/empty — not established negatives.
            left_emptyish = is_na_value(left, cfg) or left in ("", None)
            right_emptyish = is_na_value(right, cfg) or right in ("", None)
            if left_emptyish and right_emptyish:
                return True

    # FIX #2: normalize vitals format
    if is_vital_field(field_path) or numerical:
        vital_name = extract_vital_name(field_path or "temperature")
        if is_vital_field(field_path):
            left = normalize_vital(vital_name, left)
            right = normalize_vital(vital_name, right)
            numerical = True

    a = _norm_name(left)
    b = _norm_name(right)
    if not a and not b:
        return True
    if a == b:
        return True
    if numbers_conflict(left, right):
        return False
    if numerical and numbers_equal(left, right):
        return True

    words_a = _content_words(left, strip_vital_noise=numerical)
    words_b = _content_words(right, strip_vital_noise=numerical)
    if words_a and words_a == words_b:
        return True
    if words_a and words_b and set(words_a) == set(words_b):
        return True

    min_cov = float(cfg.get("subset_coverage_min") or 0.75)
    if _subset_coverage_ok(words_a, words_b, min_cov):
        return True
    if _subset_coverage_ok(words_b, words_a, min_cov):
        return True

    # FIX #4 / #6: semantic matching for narrative fields after exact match fails
    sem_key = _semantic_field_key(field_path)
    left_s, right_s = str(left or "").strip(), str(right or "").strip()
    if sem_key and len(left_s) > 10 and len(right_s) > 10:
        threshold = SEMANTIC_THRESHOLDS.get(sem_key, 0.80)
        similarity = semantic_similarity(left_s, right_s)
        if similarity >= threshold:
            return True
    return False


def classify_pair(
    gt_value: Any,
    gen_value: Any,
    scoring_config: dict | None = None,
    *,
    gt_applicable: bool | None = None,
    numerical: bool = False,
    field_path: str | None = None,
) -> dict[str, str]:
    """Return external result plus optional internal tag (contradictory).

    Empty/NA markers (including None/null/Not measured) are not-established.
    Established negatives (NKA, no known allergies, …) are never treated as empty
    unless field_path is in the Fix #1 empty-norm set (allergies/meds/etc.).
    """
    cfg = scoring_config or load_scoring_config()

    # SPECIAL HANDLING FOR MEDICATION ARRAYS (Fix #0)
    if field_path == "plan.medications" and isinstance(gt_value, list):
        med_comparison = ScoringComparator().compare(
            gt_value, gen_value if isinstance(gen_value, list) else []
        )
        if med_comparison.get("match"):
            return {"result": CORRECT, "internal": CORRECT}
        if float(med_comparison.get("accuracy") or 0) >= 0.6:
            return {"result": PARTIAL, "internal": PARTIAL}
        return {"result": INCORRECT, "internal": INCORRECT}

    allow_empty_norm = _field_allows_empty_norm(field_path)
    if allow_empty_norm:
        gt_norm = normalize_for_comparison(gt_value, collapse_established=True)
        gen_norm = normalize_for_comparison(gen_value, collapse_established=True)
        gt_blank = gt_value is None or (
            isinstance(gt_value, str) and not gt_value.strip()
        )
        gen_blank = gen_value is None or (
            isinstance(gen_value, str) and not gen_value.strip()
        )
        # Truly blank both sides → NA (not established).
        # Explicit tokens (NA/None/NKA/…) that normalize empty → Correct.
        if gt_norm == "" and gen_norm == "":
            if gt_blank and gen_blank:
                return {"result": NA, "internal": NA}
            return {"result": CORRECT, "internal": CORRECT}
        if not isinstance(gt_value, (dict, list)):
            gt_value = gt_norm
        if not isinstance(gen_value, (dict, list)):
            gen_value = gen_norm
        gt_text = _text(gt_value)
        gen_text = _text(gen_value)
        gt_empty = gt_norm == ""
        gen_empty = gen_norm == ""
    else:
        gt_text = _text(gt_value)
        gen_text = _text(gen_value)
        gt_empty = is_na_value(gt_text, cfg)
        gen_empty = is_na_value(gen_text, cfg)

    if gt_applicable is False or (gt_empty and gen_empty and not allow_empty_norm):
        return {"result": NA, "internal": NA}

    if gt_empty and gen_empty and allow_empty_norm:
        return {"result": CORRECT, "internal": CORRECT}

    if gt_empty and not gen_empty:
        return {"result": HALLUCINATION, "internal": HALLUCINATION}

    if not gt_empty and gen_empty:
        # Fix #1 scoped: established-negative GT vs empty already collapsed above.
        return {"result": MISSING, "internal": MISSING}

    if equivalent_established_absence(gt_text, gen_text, cfg):
        return {"result": CORRECT, "internal": CORRECT}

    if values_match(
        gt_text, gen_text, cfg, numerical=numerical, field_path=field_path
    ):
        return {"result": CORRECT, "internal": CORRECT}

    if is_absence_value(gt_text, cfg) and not is_absence_value(gen_text, cfg):
        return {"result": HALLUCINATION, "internal": HALLUCINATION}

    if is_established_negative(gt_text, cfg) and not is_established_negative(
        gen_text, cfg
    ):
        return {"result": INCORRECT, "internal": CONTRADICTORY}

    if is_established_none(gt_text, cfg) and not is_established_none(gen_text, cfg):
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


def _is_medication_leaf(fact: dict) -> bool:
    base = _norm_name(fact.get("base_field") or fact.get("field"))
    cats = {_norm_name(c) for c in (fact.get("categories") or [])}
    return base in {_norm_name(x) for x in _MED_LEAF_FIELDS} or "medication" in cats


def _medication_group_maps(
    facts: list[dict],
) -> dict[int, dict[str, dict]]:
    """index → {drug_name/dose/... → fact} for medication leaf facts."""
    groups: dict[int, dict[str, dict]] = {}
    for fact in facts:
        if not _is_medication_leaf(fact):
            continue
        idx = fact.get("index")
        if idx is None:
            match = re.search(r"\[(\d+)\]$", _text(fact.get("field")))
            idx = int(match.group(1)) - 1 if match else 0
        leaf = _norm_name(fact.get("base_field") or fact.get("field"))
        groups.setdefault(int(idx or 0), {})[leaf] = fact
    return groups


def _drug_name_from_group(group: dict[str, dict]) -> str:
    for key in ("drug name", "drug_name"):
        fact = group.get(_norm_name(key))
        if fact:
            return _text(fact.get("value"))
    return ""


def align_facts(
    gt_facts: list[dict],
    gen_facts: list[dict],
    scoring_config: dict | None = None,
) -> list[tuple[dict | None, dict | None]]:
    """Align GT/Gen facts. Medication leaves rematch by drug name (Fix #0)."""
    cfg = scoring_config or load_scoring_config()

    gt_med = _medication_group_maps(gt_facts)
    gen_med = _medication_group_maps(gen_facts)
    med_index_pairs: list[tuple[int | None, int | None]] = []
    if gt_med or gen_med:
        gt_stub = [
            {"drug_name": _drug_name_from_group(gt_med[i])} for i in sorted(gt_med)
        ]
        gen_stub = [
            {"drug_name": _drug_name_from_group(gen_med[i])} for i in sorted(gen_med)
        ]
        gt_keys = sorted(gt_med)
        gen_keys = sorted(gen_med)
        raw_pairs = match_medication_indices(gt_stub, gen_stub)
        for gt_i, gen_i in raw_pairs:
            med_index_pairs.append(
                (
                    gt_keys[gt_i] if gt_i is not None else None,
                    gen_keys[gen_i] if gen_i is not None else None,
                )
            )

    # Remap gen medication fact indices onto GT order for leaf alignment.
    gen_index_remap: dict[int, int] = {}
    synthetic = 10_000
    for gt_i, gen_i in med_index_pairs:
        if gt_i is not None and gen_i is not None:
            gen_index_remap[gen_i] = gt_i
        elif gen_i is not None:
            gen_index_remap[gen_i] = synthetic
            synthetic += 1

    gt_map: dict[tuple[str, int], dict] = {}
    gen_map: dict[tuple[str, int], dict] = {}
    for fact in gt_facts:
        gt_map[_align_key(fact, cfg)] = fact
    for fact in gen_facts:
        key = _align_key(fact, cfg)
        if _is_medication_leaf(fact) and key[1] in gen_index_remap:
            key = (key[0], gen_index_remap[key[1]])
        gen_map[key] = fact
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
            preview_spec = resolve_field_spec(
                template.get("base_field") or template.get("field") or "", cfg
            )
            categories = list(
                template.get("categories") or preview_spec.get("categories") or []
            )
            numerical = "numerical" in {_norm_name(c) for c in categories}
            field_path = _text(
                template.get("base_field") or template.get("field") or ""
            )
            # Prefer catalog path when available for vitals / semantic keys.
            paths = list(preview_spec.get("paths") or [])
            if paths:
                field_path = str(paths[0]).replace(".*", "")
            classified = classify_pair(
                gt_value,
                gen_value,
                cfg,
                gt_applicable=gt_applicable,
                numerical=numerical,
                field_path=field_path,
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
    """Overlay LLM diffs onto aligned facts.

    Deterministic NA (empty/not-established both sides, or expanded NA markers)
    is never overwritten to Missing/Incorrect/Hallucination — the LLM often
    mislabels '' vs 'NA' as Incorrect.
    """
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
        # Never let the LLM turn not-established into an error.
        if row.get("result") == NA and mapped in ERROR_RESULTS:
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
    correct = [
        row for row in applicable if row.get("result") in (CORRECT, PARTIAL)
    ]
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
        hits = [
            row for row in rows if row.get("result") in (CORRECT, PARTIAL)
        ]
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
    critical_correct = [
        row for row in critical_denom if row.get("result") in (CORRECT, PARTIAL)
    ]
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

    # Fix #5: field-threshold section accuracies (similarity-aware).
    field_threshold_scores: dict[str, float] = {}
    for section_key in ("subjective", "objective", "assessment", "plan"):
        section_rows = [
            row
            for row in evaluated
            if _norm_name(row.get("section")) == section_key and row.get("result") != NA
        ]
        section_map = {
            _text(row.get("base_field") or row.get("field")): {
                "similarity": _row_similarity(row),
                "result": row.get("result"),
            }
            for row in section_rows
        }
        if section_map:
            field_threshold_scores[section_key] = round(
                calculate_section_accuracy(section_map) * 100.0, 2
            )

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
        "field_threshold_section_scores": field_threshold_scores,
    }


def _section_score_for_rows(rows: list[dict]) -> float | None:
    applicable = [r for r in rows if r.get("result") != NA]
    denom = sum(int(r["weight"]) for r in applicable)
    numer = sum(
        int(r["weight"])
        for r in applicable
        if r.get("result") in (CORRECT, PARTIAL)
    )
    return _percent(numer, denom, places=2)


def calculate_section_accuracy(section_results: dict) -> float:
    """Calculate accuracy respecting per-field thresholds (Fix #5).

    section_results values may be dicts with ``similarity`` (0-1) or a result
    label (Correct/Partial/Incorrect/…).
    """
    total_weight = 0.0
    weighted_score = 0.0
    for field, result in (section_results or {}).items():
        threshold = FIELD_PASS_THRESHOLDS.get(field, 0.80)
        # Also try catalog / display aliases
        if field not in FIELD_PASS_THRESHOLDS:
            sem = _semantic_field_key(field)
            if sem and sem in FIELD_PASS_THRESHOLDS:
                threshold = FIELD_PASS_THRESHOLDS[sem]
        weight = 1.0
        if isinstance(result, dict):
            similarity = float(result.get("similarity") or 0.0)
            if "similarity" not in result:
                label = str(result.get("result") or "")
                if label in (CORRECT, PARTIAL):
                    similarity = 1.0
                elif label == NA:
                    continue
                else:
                    similarity = 0.0
        else:
            label = str(result or "")
            if label in (CORRECT, PARTIAL):
                similarity = 1.0
            elif label == NA:
                continue
            else:
                similarity = 0.0
        if similarity >= threshold:
            weighted_score += weight
        total_weight += weight
    return weighted_score / total_weight if total_weight > 0 else 0.0


def _row_similarity(row: dict) -> float:
    if row.get("similarity") is not None:
        return float(row["similarity"])
    result = row.get("result")
    if result in (CORRECT, PARTIAL):
        return 1.0 if result == CORRECT else 0.7
    if result == NA:
        return 0.0
    return 0.0


def _section_scores(
    evaluated: list[dict],
    section_keys: tuple[str, ...] = ("subjective", "objective", "assessment", "plan"),
) -> dict[str, float | None]:
    scores: dict[str, float | None] = {}
    for key in section_keys:
        rows = [row for row in evaluated if _norm_name(row.get("section")) == key]
        # Prefer weight-based MOM score; expose field-threshold score in metrics.
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
            if row.get("result") in (CORRECT, NA, PARTIAL):
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
