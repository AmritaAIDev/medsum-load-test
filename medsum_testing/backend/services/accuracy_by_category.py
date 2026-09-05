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

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_DEFAULT_TTL = 300.0


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

    def _get_filtered_runs(self) -> list[dict]:
        if self._filtered is not None:
            return self._filtered
        pool = list(self._provided_runs) if self._provided_runs is not None else load_all_results_raw()
        wanted = {_norm_name(item) for item in self._wanted_batch_ids()}
        matched: list[dict] = []
        for run in pool:
            if not isinstance(run, dict):
                continue
            batch = self._run_batch_id(run)
            if wanted and _norm_name(batch) not in wanted and _norm_name(run.get("batch_id")) not in wanted:
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
            "note": (
                ""
                if overall.get("has_ground_truth")
                else "Accuracy was not calculated — no SOAP ground truth in the selected runs."
            ),
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


def resolve_category_name(raw: str) -> str:
    wanted = _norm_name(raw)
    for name in SOAP_CATEGORIES:
        if _norm_name(name) == wanted:
            return name
    return ""


def _resolve_category_name(raw: str) -> str:
    return resolve_category_name(raw)


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
