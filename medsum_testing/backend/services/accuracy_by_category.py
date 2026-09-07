"""Clinical fact accuracy by SOAP category for the API Testing Dashboard.

Uses already-scored Prompt 1 facts when present. Otherwise FactMatcher
pairs ground-truth vs extracted strings with word-overlap + sequence
similarity (≥0.7 Correct, 0.5–0.7 Wrong, else Missed / Invented).
"""

from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher
from typing import Any

from medsum_testing.backend.services.batch_identity import canonical_batch_id
from medsum_testing.backend.services.config_loader import get_results_dir
from medsum_testing.backend.services.result_store import load_all_results_raw
from medsum_testing.backend.services.soap_detail_table import (
    fact_classification,
    soap_facts_from_result,
)
from medsum_testing.backend.services.soap_fact_scorer import (
    CORRECT,
    HALLUCINATION,
    INCORRECT,
    MISSING,
    NA,
    coerce_fact_list,
    is_established_gt,
    load_scoring_config,
    resolve_field_spec,
)

SOAP_CATEGORIES = (
    "Symptoms & History",
    "Diagnosis",
    "Medicines",
    "Medication Instructions",
    "Investigation",
    "Vitals and measurements",
    "Allergies & Follow-up Plan",
)

STATUS_PASS = "pass"
STATUS_REVIEW = "review"
STATUS_FAIL = "fail"
STATUS_NA = "na"

FLAG_INVENTED = "Has invented fact"
FLAG_ALLERGY = "Allergy error"
FLAG_DOSE = "Dose / frequency error"
FLAG_NUMERAL = "Hindi numeral error"
FLAG_DRUG = "Brand / sound-alike drug"
_DEVANAGARI_DIGIT_RE = re.compile(r"[०-९]")

# Clinical Facts "Error tag" taxonomy (Presence + Value). Codes map to known
# MedSum tag ids when present on a fact; otherwise tags are inferred.
ERROR_TAG_OMISSION = "omission"
ERROR_TAG_HALLUCINATION = "hallucination"
ERROR_TAG_DUPLICATE = "duplicate"
ERROR_TAG_MISCLASSIFIED = "misclassified"
ERROR_TAG_WRONG_VALUE = "wrong_value"
ERROR_TAG_NUMERIC_DOSE = "numeric_dose"
ERROR_TAG_UNIT = "unit"
ERROR_TAG_LATERALITY = "laterality"
ERROR_TAG_TEMPORAL = "temporal"
ERROR_TAG_NEGATION = "negation"
ERROR_TAG_CERTAINTY = "certainty"
ERROR_TAG_EXPERIENCER = "experiencer"
ERROR_TAG_BRAND = "brand"
ERROR_TAG_ABBREVIATION = "abbreviation"
ERROR_TAG_PARTIAL = "partial"

ERROR_TAG_LABELS: dict[str, str] = {
    ERROR_TAG_OMISSION: "Omission",
    ERROR_TAG_HALLUCINATION: "Hallucination / Invented",
    ERROR_TAG_DUPLICATE: "Duplicate entry",
    ERROR_TAG_MISCLASSIFIED: "Misclassified category",
    ERROR_TAG_WRONG_VALUE: "Wrong value/substitution",
    ERROR_TAG_NUMERIC_DOSE: "Numeric/dose error",
    ERROR_TAG_UNIT: "Unit error",
    ERROR_TAG_LATERALITY: "Laterality error",
    ERROR_TAG_TEMPORAL: "Temporal error",
    ERROR_TAG_NEGATION: "Negation error",
    ERROR_TAG_CERTAINTY: "Certainty/hedging error",
    ERROR_TAG_EXPERIENCER: "Subject/experiencer error",
    ERROR_TAG_BRAND: "Brand–generic mapping error",
    ERROR_TAG_ABBREVIATION: "Abbreviation misexpansion",
    ERROR_TAG_PARTIAL: "Partial capture",
}

ERROR_TAG_CODES: dict[str, str] = {
    "medmiss": ERROR_TAG_OMISSION,
    "ixmiss": ERROR_TAG_OMISSION,
    "planmiss": ERROR_TAG_OMISSION,
    "symmiss": ERROR_TAG_OMISSION,
    "inventeddx": ERROR_TAG_HALLUCINATION,
    "inventedmed": ERROR_TAG_HALLUCINATION,
    "lasa": ERROR_TAG_WRONG_VALUE,
    "numeraldedh": ERROR_TAG_NUMERIC_DOSE,
    "numeraldhai": ERROR_TAG_NUMERIC_DOSE,
    "numericvital": ERROR_TAG_NUMERIC_DOSE,
    "laterality": ERROR_TAG_LATERALITY,
    "temporality": ERROR_TAG_TEMPORAL,
    "negation": ERROR_TAG_NEGATION,
    "uncertainty": ERROR_TAG_CERTAINTY,
    "experiencer": ERROR_TAG_EXPERIENCER,
    "brand": ERROR_TAG_BRAND,
}

# UI result labels (Clinical Facts filter / Result column).
UI_RESULT_CORRECT = "Correct"
UI_RESULT_MISSING = "Missing"
UI_RESULT_WRONG = "Wrong"
UI_RESULT_PARTIAL = "Partial"
UI_RESULT_INVENTED = "Invented"

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_DEFAULT_TTL = 300.0
_LATERALITY_RE = re.compile(r"\b(left|right|l\/r|bilateral|unilateral)\b", re.I)
_TEMPORAL_RE = re.compile(
    r"\b(acute|chronic|past|current|previous|history of|ongoing|recent)\b", re.I
)
_NEGATION_RE = re.compile(r"\b(no |not |denies|without|negative for)\b", re.I)
_CERTAINTY_RE = re.compile(
    r"\b(possible|probable|suspected|likely|confirmed|definite|maybe)\b", re.I
)
_UNIT_RE = re.compile(
    r"\b(mg|mcg|µg|ug|g|ml|mL|L|l|mmol|mmhg|cm|kg|iu)\b", re.I
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _norm_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _text(value).lower()).strip()


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _percent(numerator: float, denominator: float, places: int = 1) -> float | None:
    if not denominator:
        return None
    return round(100.0 * numerator / denominator, places)


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = _text(value)
    if not text:
        return {}
    try:
        return json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def clinical_accuracy_config(scoring_config: dict | None = None) -> dict[str, Any]:
    cfg = scoring_config or load_scoring_config()
    block = _as_dict(cfg.get("clinical_accuracy"))
    categories = []
    for raw in _as_list(block.get("categories")):
        if not isinstance(raw, dict) or not _text(raw.get("name")):
            continue
        categories.append({
            "name": _text(raw.get("name")),
            "max_missed_pct": float(raw.get("max_missed_pct") or 0),
            "max_wrong_pct": float(raw.get("max_wrong_pct") or 0),
            "max_invented": raw.get("max_invented"),
            "zero_missed": bool(raw.get("zero_missed")),
            "safety_critical": bool(raw.get("safety_critical")),
            "fields": [_text(name) for name in _as_list(raw.get("fields")) if _text(name)],
        })
    if not categories:
        categories = [_default_category(name) for name in SOAP_CATEGORIES]
    return {
        "review_ratio": float(block.get("review_ratio") or 0.8),
        "correct_similarity": float(block.get("correct_similarity") or 0.7),
        "wrong_similarity": float(block.get("wrong_similarity") or 0.5),
        "cache_ttl_seconds": float(block.get("cache_ttl_seconds") or _DEFAULT_TTL),
        "categories": categories,
    }


def _default_category(name: str) -> dict[str, Any]:
    safety = name in ("Diagnosis", "Medicines", "Allergies & Follow-up Plan")
    zero_missed = name == "Allergies & Follow-up Plan"
    tight = name in ("Diagnosis", "Medicines")
    return {
        "name": name,
        "max_missed_pct": 0.0 if zero_missed else (5.0 if tight else 10.0),
        "max_wrong_pct": 5.0 if (tight or zero_missed) else 10.0,
        "max_invented": 0 if safety else None,
        "zero_missed": zero_missed,
        "safety_critical": safety,
        "fields": [],
    }


def category_thresholds(scoring_config: dict | None = None) -> dict[str, dict[str, Any]]:
    cfg = clinical_accuracy_config(scoring_config)
    return {row["name"]: row for row in cfg["categories"]}


def _field_lookup(scoring_config: dict) -> dict[str, str]:
    """Normalized field / alias / catalog key → clinical category name."""
    lookup: dict[str, str] = {}
    for spec in category_thresholds(scoring_config).values():
        name = spec["name"]
        for field in spec.get("fields") or []:
            key = _norm_name(field)
            if key:
                lookup[key] = name
    catalog = _as_dict(scoring_config.get("fields"))
    for catalog_key, spec in catalog.items():
        if not isinstance(spec, dict):
            continue
        mapped = lookup.get(_norm_name(spec.get("field"))) or lookup.get(
            _norm_name(catalog_key)
        )
        if not mapped:
            continue
        lookup[_norm_name(catalog_key)] = mapped
        lookup[_norm_name(spec.get("field"))] = mapped
        for alias in _as_list(spec.get("aliases")):
            key = _norm_name(alias)
            if key:
                lookup[key] = mapped
        for path in _as_list(spec.get("paths")):
            tail = str(path).rsplit(".", 1)[-1]
            if tail and "*" not in tail:
                lookup[_norm_name(tail)] = mapped
    return lookup


def resolve_clinical_category(
    fact: dict | None,
    scoring_config: dict | None = None,
) -> str:
    cfg = scoring_config or load_scoring_config()
    lookup = _field_lookup(cfg)
    data = _as_dict(fact)
    candidates = [
        data.get("base_field"),
        data.get("field"),
        data.get("clinical_category"),
    ]
    spec = resolve_field_spec(data.get("base_field") or data.get("field") or "", cfg)
    candidates.append(spec.get("field"))
    for raw in candidates:
        key = _norm_name(raw)
        if key in lookup:
            return lookup[key]
        # Strip " [2]" medication indexes.
        bare = re.sub(r"\s*\[\d+\]\s*$", "", key)
        if bare in lookup:
            return lookup[bare]
    return ""


def empty_category_metrics() -> dict[str, Any]:
    return {
        "ground_truth": 0,
        "correct": 0,
        "missed": 0,
        "wrong": 0,
        "invented": 0,
        "accuracy_percent": None,
        "runs_evaluated": 0,
        "status": STATUS_NA,
        "has_ground_truth": False,
    }


def category_status(
    metrics: dict[str, Any],
    threshold: dict[str, Any],
    *,
    review_ratio: float = 0.8,
) -> str:
    """PASS / REVIEW / FAIL / N/A from counts vs category thresholds."""
    ground_truth = int(metrics.get("ground_truth") or 0)
    if ground_truth <= 0 and not metrics.get("has_ground_truth"):
        return STATUS_NA
    if ground_truth <= 0:
        invented = int(metrics.get("invented") or 0)
        max_invented = threshold.get("max_invented")
        if max_invented is not None and invented > int(max_invented):
            return STATUS_FAIL
        return STATUS_NA

    missed = int(metrics.get("missed") or 0)
    wrong = int(metrics.get("wrong") or 0)
    invented = int(metrics.get("invented") or 0)
    missed_pct = 100.0 * missed / ground_truth
    wrong_pct = 100.0 * wrong / ground_truth
    max_missed = float(threshold.get("max_missed_pct") or 0)
    max_wrong = float(threshold.get("max_wrong_pct") or 0)
    max_invented = threshold.get("max_invented")
    safety = bool(threshold.get("safety_critical"))
    zero_missed = bool(threshold.get("zero_missed"))

    if safety and max_invented is not None and invented > int(max_invented):
        return STATUS_FAIL
    if zero_missed and missed > 0:
        return STATUS_FAIL
    if missed_pct > max_missed or wrong_pct > max_wrong:
        return STATUS_FAIL
    if max_invented is not None and invented > int(max_invented):
        return STATUS_FAIL

    approaches = False
    if max_missed > 0 and missed_pct >= review_ratio * max_missed:
        approaches = True
    if max_wrong > 0 and wrong_pct >= review_ratio * max_wrong:
        approaches = True
    return STATUS_REVIEW if approaches else STATUS_PASS


def apply_accuracy_and_status(
    metrics: dict[str, Any],
    threshold: dict[str, Any],
    *,
    review_ratio: float = 0.8,
) -> dict[str, Any]:
    row = dict(metrics)
    gt = int(row.get("ground_truth") or 0)
    row["has_ground_truth"] = bool(row.get("has_ground_truth") or gt > 0)
    row["accuracy_percent"] = _percent(int(row.get("correct") or 0), gt)
    row["status"] = category_status(row, threshold, review_ratio=review_ratio)
    return row


def overall_status(category_rows: dict[str, dict[str, Any]]) -> str:
    statuses = [
        str(row.get("status") or STATUS_NA)
        for row in category_rows.values()
        if str(row.get("status") or STATUS_NA) != STATUS_NA
    ]
    if not statuses:
        return STATUS_NA
    if STATUS_FAIL in statuses:
        return STATUS_FAIL
    if STATUS_REVIEW in statuses:
        return STATUS_REVIEW
    return STATUS_PASS


class FactMatcher:
    """Extract and match clinical facts with word-overlap + sequence similarity."""

    def __init__(self, scoring_config: dict | None = None):
        self.scoring_config = scoring_config or load_scoring_config()
        acc = clinical_accuracy_config(self.scoring_config)
        self.correct_threshold = float(acc["correct_similarity"])
        self.wrong_threshold = float(acc["wrong_similarity"])

    def extract_facts_from_summary(
        self, summary_data: Any, category: str
    ) -> list[str]:
        payload = _parse_jsonish(summary_data) if isinstance(summary_data, str) else summary_data
        facts = coerce_fact_list(payload, self.scoring_config)
        out: list[str] = []
        for fact in facts:
            if resolve_clinical_category(fact, self.scoring_config) != category:
                continue
            if not is_established_gt(fact.get("value"), self.scoring_config):
                continue
            text = _text(fact.get("value"))
            if text:
                out.append(text)
        return out

    def calculate_similarity(self, fact1: Any, fact2: Any) -> float:
        left = _norm_name(fact1)
        right = _norm_name(fact2)
        if not left and not right:
            return 1.0
        if not left or not right:
            return 0.0
        words_a = set(_WORD_RE.findall(left))
        words_b = set(_WORD_RE.findall(right))
        union = words_a | words_b
        overlap = (len(words_a & words_b) / len(union)) if union else 0.0
        sequence = SequenceMatcher(None, left, right).ratio()
        return (overlap + sequence) / 2.0

    def match_facts(
        self,
        ground_truth_facts: list[Any],
        extracted_facts: list[Any],
        threshold: float | None = None,
    ) -> dict[str, Any]:
        correct_cut = float(threshold if threshold is not None else self.correct_threshold)
        wrong_cut = self.wrong_threshold
        gt_rows = [_text(item) for item in ground_truth_facts if _text(item)]
        ex_rows = [_text(item) for item in extracted_facts if _text(item)]
        used_ex: set[int] = set()
        correct = missed = wrong = 0
        pairs: list[dict[str, Any]] = []

        for gt in gt_rows:
            best_idx = -1
            best_sim = -1.0
            for idx, extracted in enumerate(ex_rows):
                if idx in used_ex:
                    continue
                sim = self.calculate_similarity(gt, extracted)
                if sim > best_sim:
                    best_sim = sim
                    best_idx = idx
            if best_idx < 0 or best_sim < wrong_cut:
                missed += 1
                pairs.append({"ground_truth": gt, "extracted": "", "similarity": max(best_sim, 0.0), "label": "missed"})
                continue
            used_ex.add(best_idx)
            extracted = ex_rows[best_idx]
            if best_sim >= correct_cut:
                correct += 1
                label = "correct"
            else:
                wrong += 1
                label = "wrong"
            pairs.append({
                "ground_truth": gt,
                "extracted": extracted,
                "similarity": round(best_sim, 4),
                "label": label,
            })

        invented = 0
        for idx, extracted in enumerate(ex_rows):
            if idx in used_ex:
                continue
            invented += 1
            pairs.append({
                "ground_truth": "",
                "extracted": extracted,
                "similarity": 0.0,
                "label": "invented",
            })

        return {
            "ground_truth": len(gt_rows),
            "correct": correct,
            "missed": missed,
            "wrong": wrong,
            "invented": invented,
            "pairs": pairs,
        }


class AccuracyCalculator:
    def __init__(
        self,
        batch_id: str,
        test_type: str = "All",
        model: str = "All",
        *,
        runs: list[dict] | None = None,
        scoring_config: dict | None = None,
        batch_ids: list[str] | None = None,
    ):
        self.batch_id = _text(batch_id)
        self.test_type = _text(test_type) or "All"
        self.model = _text(model) or "All"
        self.batch_ids = [_text(item) for item in (batch_ids or []) if _text(item)]
        self._provided_runs = runs
        self.scoring_config = scoring_config or load_scoring_config()
        self.acc_config = clinical_accuracy_config(self.scoring_config)
        self.thresholds = category_thresholds(self.scoring_config)
        self.matcher = FactMatcher(self.scoring_config)
        self._filtered: list[dict] | None = None
        self._batch_found = False

    def _wanted_batch_ids(self) -> list[str]:
        if self.batch_ids:
            return self.batch_ids
        if self._is_all_batches():
            return []
        return [self.batch_id] if self.batch_id else []

    def _is_all_batches(self) -> bool:
        key = _norm_name(self.batch_id)
        return key in {"", "all", "all batches"}

    def _run_batch_id(self, run: dict) -> str:
        return canonical_batch_id(
            str(run.get("batch_id") or ""),
            str(run.get("batch_ref") or ""),
        ) or _text(run.get("batch_id"))

    def _matches_test_type(self, run: dict) -> bool:
        wanted = _norm_name(self.test_type)
        if wanted in {"", "all"}:
            return True
        raw = _norm_name(
            run.get("test_type")
            or run.get("run_type")
            or run.get("initiated_by")
            or "accuracy"
        )
        if wanted in {"accuracy", "accuracy test"}:
            return wanted in raw or raw in {"", "accuracy", "manual", "scheduler"} or "load" not in raw
        if wanted in {"load", "load test"}:
            return "load" in raw
        return wanted in raw

    def _matches_model(self, run: dict) -> bool:
        wanted = _norm_name(self.model)
        if wanted in {"", "all"}:
            return True
        candidates = [
            run.get("ai_model_used"),
            run.get("ai_model"),
            run.get("llm_model"),
            run.get("stt_model"),
            run.get("asr_model"),
        ]
        config = run.get("model_config")
        if isinstance(config, dict):
            candidates.extend(config.values())
        return any(wanted in _norm_name(item) or _norm_name(item) == wanted for item in candidates if _text(item))

    def _wanted_batch_keys(self) -> set[str]:
        keys: set[str] = set()
        for item in self._wanted_batch_ids():
            raw = _text(item)
            if not raw:
                continue
            keys.add(_norm_name(raw))
            keys.add(_norm_name(canonical_batch_id(raw, "")))
        return {key for key in keys if key}

    def _run_matches_batch(self, run: dict, wanted: set[str]) -> bool:
        if not wanted:
            return True
        candidates = [
            self._run_batch_id(run),
            run.get("batch_id"),
            run.get("batch_ref"),
        ]
        return any(_norm_name(item) in wanted for item in candidates if _text(item))

    def _get_filtered_runs(self) -> list[dict]:
        if self._filtered is not None:
            return self._filtered
        pool = list(self._provided_runs) if self._provided_runs is not None else load_all_results_raw()
        wanted = self._wanted_batch_keys()
        matched: list[dict] = []
        for run in pool:
            if not isinstance(run, dict):
                continue
            if wanted and not self._run_matches_batch(run, wanted):
                continue
            self._batch_found = True
            if not self._matches_test_type(run):
                continue
            if not self._matches_model(run):
                continue
            matched.append(run)
        if not wanted:
            self._batch_found = bool(pool)
        self._filtered = matched
        return matched

    def batch_exists(self) -> bool:
        self._get_filtered_runs()
        if self._is_all_batches() and not self.batch_ids:
            return True
        if self._provided_runs is not None:
            return bool(self._get_filtered_runs()) or self._batch_found
        return self._batch_found

    def _soap_payloads(self, run: dict) -> tuple[Any, Any]:
        soap = _as_dict(run.get("soap_comparison"))
        pair = soap.get("gt_vs_generated") if isinstance(soap.get("gt_vs_generated"), dict) else {}
        gt = (
            run.get("soap_ground_truth")
            or pair.get("ground_truth")
            or pair.get("ground_truth_facts")
        )
        gen = (
            run.get("soap_generated")
            or run.get("generated_summary")
            or pair.get("generated")
            or pair.get("generated_facts")
            or _parse_jsonish(run.get("summary_json") or run.get("medsum_output"))
        )
        return gt, gen

    def _run_has_soap_gt(self, run: dict) -> bool:
        if run.get("has_soap_ground_truth") is False:
            return False
        if run.get("has_soap_ground_truth") is True:
            return True
        gt, _ = self._soap_payloads(run)
        return bool(gt)

    def _facts_for_run(self, run: dict) -> list[dict[str, Any]]:
        stored = soap_facts_from_result(run)
        if stored:
            return stored
        gt, gen = self._soap_payloads(run)
        if not gt and not gen:
            return []
        return []

    def _accumulate_classified(
        self, buckets: dict[str, dict[str, Any]], facts: list[dict]
    ) -> set[str]:
        touched: set[str] = set()
        for fact in facts:
            category = resolve_clinical_category(fact, self.scoring_config)
            if category not in buckets:
                continue
            result = fact_classification(fact)
            if result == NA:
                continue
            row = buckets[category]
            if result == HALLUCINATION:
                row["invented"] += 1
                touched.add(category)
                continue
            if result == CORRECT:
                gt_value = fact.get("ground_truth") if "ground_truth" in fact else fact.get("value")
                if not is_established_gt(gt_value, self.scoring_config):
                    continue
                row["ground_truth"] += 1
                row["correct"] += 1
                row["has_ground_truth"] = True
                touched.add(category)
            elif result == MISSING:
                row["ground_truth"] += 1
                row["missed"] += 1
                row["has_ground_truth"] = True
                touched.add(category)
            elif result == INCORRECT:
                row["ground_truth"] += 1
                row["wrong"] += 1
                row["has_ground_truth"] = True
                touched.add(category)
        return touched

    def _accumulate_matched(
        self, buckets: dict[str, dict[str, Any]], run: dict
    ) -> set[str]:
        gt, gen = self._soap_payloads(run)
        if not gt:
            return set()
        touched: set[str] = set()
        for name in buckets:
            gt_facts = self.matcher.extract_facts_from_summary(gt, name)
            gen_facts = self.matcher.extract_facts_from_summary(gen, name) if gen else []
            if not gt_facts and not gen_facts:
                continue
            matched = self.matcher.match_facts(gt_facts, gen_facts)
            row = buckets[name]
            row["ground_truth"] += int(matched["ground_truth"])
            row["correct"] += int(matched["correct"])
            row["missed"] += int(matched["missed"])
            row["wrong"] += int(matched["wrong"])
            row["invented"] += int(matched["invented"])
            if matched["ground_truth"]:
                row["has_ground_truth"] = True
                touched.add(name)
            elif matched["invented"]:
                touched.add(name)
        return touched

    def calculate_category_metrics(self) -> dict[str, dict[str, Any]]:
        buckets = {name: empty_category_metrics() for name in SOAP_CATEGORIES}
        evaluated_runs = {name: set() for name in SOAP_CATEGORIES}
        for index, run in enumerate(self._get_filtered_runs()):
            facts = self._facts_for_run(run)
            if facts:
                touched = self._accumulate_classified(buckets, facts)
            elif self._run_has_soap_gt(run):
                touched = self._accumulate_matched(buckets, run)
            else:
                touched = set()
            for name in touched:
                evaluated_runs[name].add(index)
        for name, row in buckets.items():
            row["runs_evaluated"] = len(evaluated_runs[name])
        return buckets

    def calculate_accuracy_percentages(
        self, metrics: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, dict[str, Any]]:
        rows = metrics if metrics is not None else self.calculate_category_metrics()
        ratio = float(self.acc_config["review_ratio"])
        out: dict[str, dict[str, Any]] = {}
        for name in SOAP_CATEGORIES:
            threshold = self.thresholds.get(name) or _default_category(name)
            out[name] = apply_accuracy_and_status(
                rows.get(name) or empty_category_metrics(),
                threshold,
                review_ratio=ratio,
            )
        return out

    def get_overall_metrics(
        self, category_metrics: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        rows = category_metrics if category_metrics is not None else self.calculate_accuracy_percentages()
        totals = empty_category_metrics()
        has_gt = False
        for row in rows.values():
            totals["ground_truth"] += int(row.get("ground_truth") or 0)
            totals["correct"] += int(row.get("correct") or 0)
            totals["missed"] += int(row.get("missed") or 0)
            totals["wrong"] += int(row.get("wrong") or 0)
            totals["invented"] += int(row.get("invented") or 0)
            if row.get("has_ground_truth"):
                has_gt = True
        totals["has_ground_truth"] = has_gt
        totals["runs_evaluated"] = max(
            (int(row.get("runs_evaluated") or 0) for row in rows.values()),
            default=0,
        )
        totals["accuracy_percent"] = _percent(totals["correct"], totals["ground_truth"])
        totals["status"] = overall_status(rows)
        totals["categories_passed"] = sum(
            1 for row in rows.values() if row.get("status") == STATUS_PASS
        )
        totals["categories_total"] = sum(
            1 for row in rows.values() if row.get("status") != STATUS_NA
        )
        return totals

    def get_all_metrics(self) -> dict[str, Any]:
        categories = self.calculate_accuracy_percentages()
        overall = self.get_overall_metrics(categories)
        return {
            "batch_id": (
                "all"
                if self._is_all_batches() and not self.batch_ids
                else (
                    ",".join(self.batch_ids)
                    if self.batch_ids
                    else self.batch_id
                )
            ),
            "test_type": self.test_type,
            "model": self.model,
            "categories": categories,
            "overall": overall,
            "review_ratio": float(self.acc_config["review_ratio"]),
            "thresholds": {
                name: {
                    "max_missed_pct": spec["max_missed_pct"],
                    "max_wrong_pct": spec["max_wrong_pct"],
                    "max_invented": spec["max_invented"],
                    "zero_missed": spec["zero_missed"],
                    "safety_critical": spec["safety_critical"],
                }
                for name, spec in self.thresholds.items()
            },
            "recordings": self.get_recording_rows(),
            "note": (
                ""
                if overall.get("has_ground_truth")
                else "Accuracy was not calculated — no SOAP ground truth in the selected runs."
            ),
        }

    def _run_duration_seconds(self, run: dict) -> float | None:
        tr = _as_dict(run.get("transcription_result"))
        for raw in (
            run.get("audio_duration_seconds"),
            tr.get("audio_length"),
            run.get("audio_length"),
            tr.get("audio_duration_seconds"),
        ):
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    def _run_latency_seconds(self, run: dict) -> float | None:
        tr = _as_dict(run.get("transcription_result"))
        nested = _as_dict(tr.get("time"))
        for raw in (
            tr.get("total-time"),
            nested.get("total"),
            run.get("total_test_time_seconds"),
        ):
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        return None

    def _run_recording_status(self, totals: dict[str, Any]) -> str:
        gt = int(totals.get("ground_truth") or 0)
        if gt <= 0 and not totals.get("has_ground_truth"):
            return "N/A"
        if gt <= 0:
            return "N/A"
        invented = int(totals.get("invented") or 0)
        if invented > 0:
            return "FAIL"
        accuracy = 100.0 * int(totals.get("correct") or 0) / gt
        if accuracy >= 95:
            return "PASS"
        if accuracy >= 85:
            return "REVIEW"
        return "FAIL"

    def _recording_safety_flags(self, run: dict, facts: list[dict], totals: dict) -> list[str]:
        flags: list[str] = []
        seen: set[str] = set()

        def add(flag: str) -> None:
            if flag and flag not in seen:
                seen.add(flag)
                flags.append(flag)

        if int(totals.get("invented") or 0) > 0:
            add(FLAG_INVENTED)
        for fact in facts:
            result = fact_classification(fact)
            if result not in (MISSING, INCORRECT, HALLUCINATION):
                continue
            field = _norm_name(fact.get("base_field") or fact.get("field"))
            values = " ".join(
                _text(fact.get(key))
                for key in ("ground_truth", "generated", "value")
            )
            if result == HALLUCINATION:
                add(FLAG_INVENTED)
            if "allerg" in field:
                add(FLAG_ALLERGY)
            if field in {"dose", "schedule"} or "dose" in field or "schedule" in field:
                add(FLAG_DOSE)
            if field in {"drug name", "drug_name"} or field.startswith("drug name"):
                add(FLAG_DRUG)
            if _DEVANAGARI_DIGIT_RE.search(values):
                add(FLAG_NUMERAL)
        return flags

    def _matches_recording_filter(self, row: dict[str, Any], status_filter: str) -> bool:
        wanted = _norm_name(status_filter)
        if wanted in {"", "all"}:
            return True
        status = _norm_name(row.get("status"))
        flags = {_norm_name(item) for item in (row.get("safety_flags") or [])}
        if wanted in {"pass", "passed"}:
            return status == "pass"
        if wanted in {"review", "needs review"}:
            return status == "review" or (
                status == "fail" and not row.get("has_safety_flag")
            )
        if wanted in {"safety flag", "safety_flag", "fail"}:
            return bool(row.get("has_safety_flag"))
        if wanted in {"invented", "has invented fact"}:
            return int(row.get("invented") or 0) > 0 or FLAG_INVENTED in (row.get("safety_flags") or [])
        if wanted in {"allergy error", "allergy_error"}:
            return _norm_name(FLAG_ALLERGY) in flags
        if wanted in {"dose error", "dose_error", "dose frequency error"}:
            return _norm_name(FLAG_DOSE) in flags
        if wanted in {"numeral error", "numeral_error", "hindi numeral error"}:
            return _norm_name(FLAG_NUMERAL) in flags
        if wanted in {"drug error", "drug_error", "brand sound alike drug"}:
            return _norm_name(FLAG_DRUG) in flags
        return True

    def _run_asr_wer_percent(self, run: dict) -> float | None:
        comp = _as_dict(run.get("comparison") or run.get("transcription_comparison"))
        for raw in (
            run.get("asr_wer"),
            run.get("wer"),
            comp.get("wer"),
            comp.get("word_error_rate"),
        ):
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value <= 1.0:
                value *= 100.0
            return round(value, 1)
        for raw in (
            comp.get("similarity_score"),
            run.get("similarity_score"),
            run.get("accuracy_score"),
        ):
            try:
                sim = float(raw)
            except (TypeError, ValueError):
                continue
            if 0 <= sim <= 100:
                # Treat high similarity as near-match; convert to WER-style %.
                if sim >= 40:
                    return round(max(0.0, 100.0 - sim), 1)
        return None

    def _run_realtime_factor(self, duration: float | None, latency: float | None) -> float | None:
        if duration is None or latency is None:
            return None
        try:
            dur = float(duration)
            lat = float(latency)
        except (TypeError, ValueError):
            return None
        if dur <= 0:
            return None
        return round(lat / dur, 2)

    def get_recording_rows(self) -> list[dict[str, Any]]:
        ratio = float(self.acc_config["review_ratio"])
        rows: list[dict[str, Any]] = []
        for run in self._get_filtered_runs():
            buckets = {name: empty_category_metrics() for name in SOAP_CATEGORIES}
            facts = self._facts_for_run(run)
            if facts:
                self._accumulate_classified(buckets, facts)
            elif self._run_has_soap_gt(run):
                self._accumulate_matched(buckets, run)
            scored = {}
            category_accuracy: dict[str, float | None] = {}
            for name in SOAP_CATEGORIES:
                threshold = self.thresholds.get(name) or _default_category(name)
                scored[name] = apply_accuracy_and_status(
                    buckets[name],
                    threshold,
                    review_ratio=ratio,
                )
                category_accuracy[name] = scored[name].get("accuracy_percent")
            totals = self.get_overall_metrics(scored)
            flags = self._recording_safety_flags(run, facts, totals)
            tc_ref = _text(run.get("tc_ref") or run.get("test_case_id") or run.get("test_id"))
            duration = self._run_duration_seconds(run)
            latency = self._run_latency_seconds(run)
            gt = int(totals.get("ground_truth") or 0)
            correct = int(totals.get("correct") or 0)
            row = {
                "test_id": run.get("test_id") or run.get("id"),
                "test_case_number": tc_ref,
                "tc_ref": tc_ref,
                "run_number": _text(run.get("run_ref") or run.get("run_number") or run.get("test_id")),
                "audio_filename": _text(run.get("audio_filename") or run.get("filename")),
                "duration_seconds": duration,
                "latency_seconds": latency,
                "ground_truth": gt,
                "correct": correct,
                "missed": int(totals.get("missed") or 0),
                "wrong": int(totals.get("wrong") or 0),
                "invented": int(totals.get("invented") or 0),
                "has_ground_truth": bool(totals.get("has_ground_truth")),
                "fact_accuracy_percent": _percent(correct, gt),
                "category_accuracy": category_accuracy,
                "asr_wer_percent": self._run_asr_wer_percent(run),
                "realtime_factor": self._run_realtime_factor(duration, latency),
                "status": self._run_recording_status(totals),
                "has_safety_flag": bool(flags),
                "safety_flags": flags,
                "model_used": _text(
                    run.get("ai_model_used") or run.get("ai_model") or run.get("llm_model")
                ),
            }
            rows.append(row)
        return rows

    def get_recordings_payload(self, status_filter: str = "") -> dict[str, Any]:
        all_rows = self.get_recording_rows()
        filtered = [
            row for row in all_rows
            if self._matches_recording_filter(row, status_filter)
        ]
        return {
            "batch_id": (
                "all"
                if self._is_all_batches() and not self.batch_ids
                else (
                    ",".join(self.batch_ids)
                    if self.batch_ids
                    else self.batch_id
                )
            ),
            "test_type": self.test_type,
            "model": self.model,
            "total_recordings": len(all_rows),
            "recordings": filtered,
        }

    def get_category_run_details(self, category: str) -> list[dict[str, Any]]:
        wanted = _resolve_category_name(category)
        if not wanted:
            return []
        details: list[dict[str, Any]] = []
        for run in self._get_filtered_runs():
            facts = [
                fact
                for fact in self._facts_for_run(run)
                if resolve_clinical_category(fact, self.scoring_config) == wanted
            ]
            counts = empty_category_metrics()
            if facts:
                self._accumulate_classified({wanted: counts}, facts)
            elif self._run_has_soap_gt(run):
                self._accumulate_matched({wanted: counts}, run)
            else:
                continue
            if (
                int(counts["missed"])
                + int(counts["wrong"])
                + int(counts["invented"])
                <= 0
            ):
                continue
            details.append({
                "test_id": run.get("test_id") or run.get("id"),
                "audio_filename": run.get("audio_filename") or run.get("filename"),
                "batch_id": self._run_batch_id(run),
                "category": wanted,
                "ground_truth": counts["ground_truth"],
                "correct": counts["correct"],
                "missed": counts["missed"],
                "wrong": counts["wrong"],
                "invented": counts["invented"],
            })
        return details

    def _empty_category_detail(self) -> dict[str, Any]:
        row = empty_category_metrics()
        row.update({
            "missed_facts": [],
            "wrong_facts": [],
            "invented_facts": [],
        })
        return row

    def _fact_display_gt(self, fact: dict) -> str:
        if "ground_truth" in fact:
            return _text(fact.get("ground_truth"))
        return _text(fact.get("value"))

    def _fact_display_gen(self, fact: dict) -> str:
        return _text(fact.get("generated") or fact.get("value"))

    def _format_wrong_fact(self, generated: str, expected: str) -> str:
        gen = _text(generated)
        exp = _text(expected)
        if gen and exp:
            return f"{gen} (expected: {exp})"
        return gen or exp

    def _comparison_rows_from_classified(self, facts: list[dict]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index, fact in enumerate(facts or []):
            category = resolve_clinical_category(fact, self.scoring_config)
            if category not in SOAP_CATEGORIES:
                continue
            result = fact_classification(fact)
            if result == NA:
                continue
            raw_result = _text(fact.get("result") or fact.get("type"))
            if _norm_name(raw_result) == "partial":
                result = "Partial"
            ui_result = ui_result_label(result)
            gt_text = self._fact_display_gt(fact)
            gen_text = self._fact_display_gen(fact)
            score_result = MISSING if result == "Partial" else result
            error_tag = infer_error_tag(fact, score_result, category)
            if result == "Partial":
                error_tag = ERROR_TAG_PARTIAL
            if ui_result == UI_RESULT_CORRECT:
                error_tag = ""
            safety = infer_safety_concern(
                fact,
                INCORRECT if result == "Partial" else result,
                category,
                error_tag,
            )
            source = (
                infer_error_source(
                    fact,
                    INCORRECT if result == "Partial" else result,
                )
                if error_tag
                else ""
            )
            rows.append({
                "id": _text(fact.get("id")) or f"fact-{index}",
                "category": category,
                "field": _text(fact.get("field") or fact.get("base_field")),
                "ground_truth": gt_text,
                "generated": gen_text,
                "result": ui_result,
                "error_tag": error_tag,
                "error_tag_label": ERROR_TAG_LABELS.get(error_tag, ""),
                "error_source": source,
                "safety_flagged": bool(safety.get("flagged")),
                "safety_auto_label": _text(safety.get("auto_label")),
                "safety_reason": _text(safety.get("reason")),
            })
        rank = {
            UI_RESULT_WRONG: 0,
            UI_RESULT_MISSING: 1,
            UI_RESULT_PARTIAL: 2,
            UI_RESULT_INVENTED: 3,
            UI_RESULT_CORRECT: 4,
        }
        rows.sort(key=lambda row: (rank.get(row.get("result"), 9), row.get("category") or ""))
        return rows

    def _comparison_rows_from_matcher(self, run: dict) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        gt, gen = self._soap_payloads(run)
        if not gt:
            return rows
        index = 0
        for name in SOAP_CATEGORIES:
            gt_facts = self.matcher.extract_facts_from_summary(gt, name)
            gen_facts = self.matcher.extract_facts_from_summary(gen, name) if gen else []
            if not gt_facts and not gen_facts:
                continue
            matched = self.matcher.match_facts(gt_facts, gen_facts)
            for pair in matched.get("pairs") or []:
                label = _norm_name(pair.get("label"))
                if label == "correct":
                    result = CORRECT
                    ui_result = UI_RESULT_CORRECT
                elif label == "missed":
                    result = MISSING
                    ui_result = UI_RESULT_MISSING
                elif label == "wrong":
                    result = INCORRECT
                    ui_result = UI_RESULT_WRONG
                elif label in {"invented", "extra", "hallucination"}:
                    result = HALLUCINATION
                    ui_result = UI_RESULT_INVENTED
                else:
                    continue
                gt_text = _text(pair.get("ground_truth"))
                gen_text = _text(pair.get("extracted") or pair.get("generated"))
                fake = {
                    "field": "",
                    "ground_truth": gt_text,
                    "generated": gen_text,
                }
                error_tag = infer_error_tag(fake, result, name)
                if ui_result == UI_RESULT_CORRECT:
                    error_tag = ""
                safety = infer_safety_concern(fake, result, name, error_tag)
                source = infer_error_source(fake, result) if error_tag else ""
                rows.append({
                    "id": f"match-{index}",
                    "category": name,
                    "field": "",
                    "ground_truth": gt_text,
                    "generated": gen_text,
                    "result": ui_result,
                    "error_tag": error_tag,
                    "error_tag_label": ERROR_TAG_LABELS.get(error_tag, ""),
                    "error_source": source,
                    "safety_flagged": bool(safety.get("flagged")),
                    "safety_auto_label": _text(safety.get("auto_label")),
                    "safety_reason": _text(safety.get("reason")),
                })
                index += 1
        rank = {
            UI_RESULT_WRONG: 0,
            UI_RESULT_MISSING: 1,
            UI_RESULT_PARTIAL: 2,
            UI_RESULT_INVENTED: 3,
            UI_RESULT_CORRECT: 4,
        }
        rows.sort(key=lambda row: (rank.get(row.get("result"), 9), row.get("category") or ""))
        return rows

    def _category_details_from_classified(
        self, facts: list[dict]
    ) -> dict[str, dict[str, Any]]:
        buckets = {name: self._empty_category_detail() for name in SOAP_CATEGORIES}
        for fact in facts:
            category = resolve_clinical_category(fact, self.scoring_config)
            if category not in buckets:
                continue
            result = fact_classification(fact)
            if result == NA:
                continue
            row = buckets[category]
            field = _text(fact.get("field") or fact.get("base_field"))
            gt_text = self._fact_display_gt(fact)
            gen_text = self._fact_display_gen(fact)
            label = f"{field}: {gt_text}" if field and gt_text else (gt_text or field)
            gen_label = (
                f"{field}: {gen_text}" if field and gen_text else (gen_text or field)
            )
            if result == HALLUCINATION:
                row["invented"] += 1
                if gen_label:
                    row["invented_facts"].append(gen_label)
                continue
            if result == CORRECT:
                if not is_established_gt(
                    fact.get("ground_truth") if "ground_truth" in fact else fact.get("value"),
                    self.scoring_config,
                ):
                    continue
                row["ground_truth"] += 1
                row["correct"] += 1
                row["has_ground_truth"] = True
            elif result == MISSING:
                row["ground_truth"] += 1
                row["missed"] += 1
                row["has_ground_truth"] = True
                if label:
                    row["missed_facts"].append(label)
            elif result == INCORRECT:
                row["ground_truth"] += 1
                row["wrong"] += 1
                row["has_ground_truth"] = True
                row["wrong_facts"].append(self._format_wrong_fact(gen_text, gt_text))
        return buckets

    def _category_details_from_matcher(self, run: dict) -> dict[str, dict[str, Any]]:
        buckets = {name: self._empty_category_detail() for name in SOAP_CATEGORIES}
        gt, gen = self._soap_payloads(run)
        if not gt:
            return buckets
        for name in SOAP_CATEGORIES:
            gt_facts = self.matcher.extract_facts_from_summary(gt, name)
            gen_facts = self.matcher.extract_facts_from_summary(gen, name) if gen else []
            if not gt_facts and not gen_facts:
                continue
            matched = self.matcher.match_facts(gt_facts, gen_facts)
            row = buckets[name]
            row["ground_truth"] = int(matched["ground_truth"])
            row["correct"] = int(matched["correct"])
            row["missed"] = int(matched["missed"])
            row["wrong"] = int(matched["wrong"])
            row["invented"] = int(matched["invented"])
            if matched["ground_truth"]:
                row["has_ground_truth"] = True
            for pair in matched.get("pairs") or []:
                label = str(pair.get("label") or "")
                if label == "missed" and _text(pair.get("ground_truth")):
                    row["missed_facts"].append(_text(pair.get("ground_truth")))
                elif label == "wrong":
                    row["wrong_facts"].append(
                        self._format_wrong_fact(
                            pair.get("extracted"),
                            pair.get("ground_truth"),
                        )
                    )
                elif label == "invented" and _text(pair.get("extracted")):
                    row["invented_facts"].append(_text(pair.get("extracted")))
        return buckets

    def _find_run(self, recording_id: str) -> dict | None:
        wanted = _norm_name(recording_id)
        if not wanted:
            return None
        for run in self._get_filtered_runs():
            candidates = [
                run.get("test_id"),
                run.get("id"),
                run.get("run_ref"),
                run.get("run_number"),
                run.get("tc_ref"),
                run.get("test_case_id"),
            ]
            for raw in candidates:
                if _norm_name(raw) == wanted:
                    return run
        return None

    def get_recording_details(self, recording_id: str) -> dict[str, Any] | None:
        """Fact-level category breakdown for one recording (detail page / drill-down)."""
        run = self._find_run(recording_id)
        if run is None:
            return None
        ratio = float(self.acc_config["review_ratio"])
        facts = self._facts_for_run(run)
        if facts:
            buckets = self._category_details_from_classified(facts)
            comparison_rows = self._comparison_rows_from_classified(facts)
        elif self._run_has_soap_gt(run):
            buckets = self._category_details_from_matcher(run)
            comparison_rows = self._comparison_rows_from_matcher(run)
        else:
            buckets = {name: self._empty_category_detail() for name in SOAP_CATEGORIES}
            comparison_rows = []

        categories: dict[str, dict[str, Any]] = {}
        for name in SOAP_CATEGORIES:
            threshold = self.thresholds.get(name) or _default_category(name)
            scored = apply_accuracy_and_status(
                buckets[name],
                threshold,
                review_ratio=ratio,
            )
            categories[name] = {
                "ground_truth": int(scored.get("ground_truth") or 0),
                "correct": int(scored.get("correct") or 0),
                "missed": int(scored.get("missed") or 0),
                "wrong": int(scored.get("wrong") or 0),
                "invented": int(scored.get("invented") or 0),
                "accuracy_percent": scored.get("accuracy_percent"),
                "status": scored.get("status"),
                "has_ground_truth": bool(scored.get("has_ground_truth")),
                "missed_facts": list(buckets[name].get("missed_facts") or []),
                "wrong_facts": list(buckets[name].get("wrong_facts") or []),
                "invented_facts": list(buckets[name].get("invented_facts") or []),
            }

        totals = self.get_overall_metrics(categories)
        duration = self._run_duration_seconds(run)
        latency = self._run_latency_seconds(run)
        tc_ref = _text(run.get("tc_ref") or run.get("test_case_id") or run.get("test_id"))
        run_number = _text(
            run.get("run_ref") or run.get("run_number") or run.get("test_id")
        )
        partial = sum(1 for row in comparison_rows if row.get("result") == UI_RESULT_PARTIAL)
        safety_count = sum(1 for row in comparison_rows if row.get("safety_flagged"))
        return {
            "recording": {
                "test_id": run.get("test_id") or run.get("id"),
                "test_case_number": tc_ref,
                "run_number": run_number,
                "duration_seconds": duration,
                "model_used": _text(
                    run.get("ai_model_used") or run.get("ai_model") or run.get("llm_model")
                ),
                "audio_filename": _text(run.get("audio_filename") or run.get("filename")),
                "batch_id": self._run_batch_id(run),
                "language": _text(run.get("language") or run.get("audio_language")),
            },
            "summary": {
                "total_ground_truth": int(totals.get("ground_truth") or 0),
                "total_correct": int(totals.get("correct") or 0),
                "total_missed": int(totals.get("missed") or 0),
                "total_wrong": int(totals.get("wrong") or 0),
                "total_invented": int(totals.get("invented") or 0),
                "total_partial": partial,
                "safety_concerns": safety_count,
                "overall_accuracy_percent": totals.get("accuracy_percent"),
                "mean_latency_seconds": latency,
                "asr_wer": self._run_asr_wer_percent(run),
                "real_time_factor": self._run_realtime_factor(duration, latency),
                "status": self._run_recording_status(totals),
                "has_ground_truth": bool(totals.get("has_ground_truth")),
            },
            "categories": categories,
            "comparison_rows": comparison_rows,
            "error_tag_options": {
                "presence": [
                    {"id": ERROR_TAG_OMISSION, "label": ERROR_TAG_LABELS[ERROR_TAG_OMISSION]},
                    {"id": ERROR_TAG_HALLUCINATION, "label": ERROR_TAG_LABELS[ERROR_TAG_HALLUCINATION]},
                    {"id": ERROR_TAG_DUPLICATE, "label": ERROR_TAG_LABELS[ERROR_TAG_DUPLICATE]},
                    {"id": ERROR_TAG_MISCLASSIFIED, "label": ERROR_TAG_LABELS[ERROR_TAG_MISCLASSIFIED]},
                ],
                "value": [
                    {"id": ERROR_TAG_WRONG_VALUE, "label": ERROR_TAG_LABELS[ERROR_TAG_WRONG_VALUE]},
                    {"id": ERROR_TAG_NUMERIC_DOSE, "label": ERROR_TAG_LABELS[ERROR_TAG_NUMERIC_DOSE]},
                    {"id": ERROR_TAG_UNIT, "label": ERROR_TAG_LABELS[ERROR_TAG_UNIT]},
                    {"id": ERROR_TAG_LATERALITY, "label": ERROR_TAG_LABELS[ERROR_TAG_LATERALITY]},
                    {"id": ERROR_TAG_TEMPORAL, "label": ERROR_TAG_LABELS[ERROR_TAG_TEMPORAL]},
                    {"id": ERROR_TAG_NEGATION, "label": ERROR_TAG_LABELS[ERROR_TAG_NEGATION]},
                    {"id": ERROR_TAG_CERTAINTY, "label": ERROR_TAG_LABELS[ERROR_TAG_CERTAINTY]},
                    {"id": ERROR_TAG_EXPERIENCER, "label": ERROR_TAG_LABELS[ERROR_TAG_EXPERIENCER]},
                    {"id": ERROR_TAG_BRAND, "label": ERROR_TAG_LABELS[ERROR_TAG_BRAND]},
                    {"id": ERROR_TAG_ABBREVIATION, "label": ERROR_TAG_LABELS[ERROR_TAG_ABBREVIATION]},
                    {"id": ERROR_TAG_PARTIAL, "label": ERROR_TAG_LABELS[ERROR_TAG_PARTIAL]},
                ],
            },
        }


def resolve_category_name(raw: str) -> str:
    wanted = _norm_name(raw)
    for name in SOAP_CATEGORIES:
        if _norm_name(name) == wanted:
            return name
    return ""


def _resolve_category_name(raw: str) -> str:
    return resolve_category_name(raw)


def ui_result_label(result: str) -> str:
    """Map Prompt-1 classification to Clinical Facts Result labels."""
    if result == CORRECT:
        return UI_RESULT_CORRECT
    if result == MISSING:
        return UI_RESULT_MISSING
    if result == INCORRECT:
        return UI_RESULT_WRONG
    if result == HALLUCINATION:
        return UI_RESULT_INVENTED
    if _norm_name(result) == "partial":
        return UI_RESULT_PARTIAL
    return _text(result) or UI_RESULT_WRONG


def _explicit_error_tag(fact: dict) -> str:
    for key in ("error_tag", "error_code", "tag", "errorTag", "errorCode"):
        raw = fact.get(key)
        if isinstance(raw, (list, tuple)):
            for item in raw:
                mapped = ERROR_TAG_CODES.get(_norm_name(item).replace(" ", ""))
                if mapped:
                    return mapped
                label_map = {_norm_name(v): k for k, v in ERROR_TAG_LABELS.items()}
                hit = label_map.get(_norm_name(item))
                if hit:
                    return hit
            continue
        code = _norm_name(raw).replace(" ", "")
        if code in ERROR_TAG_CODES:
            return ERROR_TAG_CODES[code]
        label_map = {_norm_name(v): k for k, v in ERROR_TAG_LABELS.items()}
        hit = label_map.get(_norm_name(raw))
        if hit:
            return hit
    return ""


def infer_error_tag(fact: dict, result: str, category: str = "") -> str:
    """Resolve Presence/Value error tag for a fact row."""
    explicit = _explicit_error_tag(fact)
    if explicit:
        return explicit
    if result == CORRECT:
        return ""
    if result == MISSING:
        return ERROR_TAG_OMISSION
    if result == HALLUCINATION:
        return ERROR_TAG_HALLUCINATION
    if _norm_name(result) == "partial":
        return ERROR_TAG_PARTIAL

    field = _norm_name(fact.get("base_field") or fact.get("field"))
    gt = _text(fact.get("ground_truth") if "ground_truth" in fact else fact.get("value"))
    gen = _text(fact.get("generated") or fact.get("value"))
    blob = f"{field} {gt} {gen} {category}"

    # Drug identity first — brand/generic rows often contain strengths (mg).
    if field in {"drug name", "drug_name"} or field.startswith("drug name"):
        if "brand" in blob.lower() or "(" in gt or "(" in gen:
            return ERROR_TAG_BRAND
        return ERROR_TAG_WRONG_VALUE
    if field in {"dose", "schedule"} or "dose" in field or "schedule" in field:
        return ERROR_TAG_NUMERIC_DOSE
    if _DEVANAGARI_DIGIT_RE.search(blob) or (
        any(ch.isdigit() for ch in gt + gen)
        and (
            "dose" in blob
            or "tablet" in blob.lower()
            or "mg" in blob.lower()
            or "vital" in field
            or "blood pressure" in field
            or "temperature" in field
        )
    ):
        return ERROR_TAG_NUMERIC_DOSE
    if _LATERALITY_RE.search(blob):
        return ERROR_TAG_LATERALITY
    if _TEMPORAL_RE.search(gt) and _TEMPORAL_RE.search(gen):
        return ERROR_TAG_TEMPORAL
    if _NEGATION_RE.search(gt) or _NEGATION_RE.search(gen):
        return ERROR_TAG_NEGATION
    if _CERTAINTY_RE.search(gt) or _CERTAINTY_RE.search(gen):
        return ERROR_TAG_CERTAINTY
    if _UNIT_RE.search(gt) and _UNIT_RE.search(gen):
        gt_units = {m.group(0).lower() for m in _UNIT_RE.finditer(gt)}
        gen_units = {m.group(0).lower() for m in _UNIT_RE.finditer(gen)}
        if gt_units and gen_units and gt_units != gen_units:
            return ERROR_TAG_UNIT
    return ERROR_TAG_WRONG_VALUE


def infer_error_source(fact: dict, result: str) -> str:
    """ASR vs Summarisation chip for the Error tag column."""
    raw = _text(
        fact.get("error_source")
        or fact.get("source")
        or fact.get("errorSource")
    )
    if raw:
        key = _norm_name(raw)
        if "asr" in key or "transcript" in key:
            return "ASR"
        if "summar" in key or "llm" in key or "soap" in key:
            return "Summarisation"
        return raw
    if result == MISSING or result == HALLUCINATION:
        return "Summarisation"
    tag = _explicit_error_tag(fact)
    if tag == ERROR_TAG_NUMERIC_DOSE and _DEVANAGARI_DIGIT_RE.search(
        _text(fact.get("ground_truth")) + _text(fact.get("generated"))
    ):
        return "ASR"
    return "Summarisation"


def infer_safety_concern(
    fact: dict,
    result: str,
    category: str,
    error_tag: str,
) -> dict[str, Any]:
    """Auto safety flag for the Safety concern? column."""
    if result == CORRECT:
        return {"flagged": False, "auto_label": "", "reason": ""}
    field = _norm_name(fact.get("base_field") or fact.get("field"))
    cat = _norm_name(category)
    if "allerg" in field or "allerg" in cat:
        return {"flagged": True, "auto_label": "Missed allergy", "reason": "allergy"}
    if error_tag == ERROR_TAG_NUMERIC_DOSE or field in {"dose", "schedule"}:
        return {"flagged": True, "auto_label": "Dose error", "reason": "dose"}
    if error_tag in {ERROR_TAG_BRAND, ERROR_TAG_WRONG_VALUE} and (
        "drug" in field or "medicine" in cat or "medicin" in cat
    ):
        return {"flagged": True, "auto_label": "Wrong medicine", "reason": "drug"}
    if result == HALLUCINATION or error_tag == ERROR_TAG_HALLUCINATION:
        return {"flagged": True, "auto_label": "Invented fact", "reason": "invented"}
    if cat in {"diagnosis"} and result in {MISSING, INCORRECT, HALLUCINATION}:
        return {"flagged": True, "auto_label": "Diagnosis error", "reason": "diagnosis"}
    return {"flagged": False, "auto_label": "", "reason": ""}


def _results_fingerprint() -> tuple[int, int]:
    """Invalidate the 5-minute cache when result files change."""
    try:
        files = [
            path
            for path in get_results_dir().glob("*.json")
            if path.name != ".batch_seq.json"
        ]
        if not files:
            return (0, 0)
        latest = max(int(path.stat().st_mtime) for path in files)
        return (len(files), latest)
    except OSError:
        return (0, 0)


def _cache_key(batch_id: str, test_type: str, model: str, batch_ids: list[str] | None) -> tuple[Any, ...]:
    extra = tuple(sorted(_text(item) for item in (batch_ids or []) if _text(item)))
    return (
        _text(batch_id),
        _text(test_type) or "All",
        _text(model) or "All",
        extra,
        _results_fingerprint(),
    )


def get_cached_metrics(
    batch_id: str,
    test_type: str = "All",
    model: str = "All",
    *,
    batch_ids: list[str] | None = None,
    runs: list[dict] | None = None,
    scoring_config: dict | None = None,
) -> dict[str, Any]:
    ttl = float(clinical_accuracy_config(scoring_config)["cache_ttl_seconds"])
    key = _cache_key(batch_id, test_type, model, batch_ids)
    now = time.monotonic()
    if runs is None:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    calc = AccuracyCalculator(
        batch_id,
        test_type=test_type,
        model=model,
        runs=runs,
        scoring_config=scoring_config,
        batch_ids=batch_ids,
    )
    payload = calc.get_all_metrics()
    payload["_batch_found"] = calc.batch_exists()
    if runs is None:
        _CACHE[key] = (now, payload)
    return payload


def clear_accuracy_cache() -> None:
    _CACHE.clear()
