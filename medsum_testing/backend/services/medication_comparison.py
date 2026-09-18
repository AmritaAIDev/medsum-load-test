"""Unified medication comparison for SOAP scoring and raw↔final validation.

Shared pipeline: MedicationNormalizer → MedicationMatcher → MedicationComparator,
with ScoringComparator (GT vs generated) and ValidationComparator (raw vs final).
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any


class MedicationNormalizer:
    """Normalize drug names and medication fields for consistent comparison."""

    IDENTITY_FIELDS = ("drug_name", "matched_drug_name", "generic_name")
    EMPTY_TOKENS = frozenset({"", "na", "n/a", "none", "null"})

    @staticmethod
    def normalize_drug_name(name: str | None) -> str:
        """Normalize drug name for comparison (lowercase, strip whitespace)."""
        return name.strip().lower() if name else ""

    @staticmethod
    def extract_drug_identity(med: dict | None) -> str:
        """Extract and normalize drug identity from a medication dict.

        Tries: drug_name → matched_drug_name → generic_name → "".
        """
        if not isinstance(med, dict):
            return ""
        for field in MedicationNormalizer.IDENTITY_FIELDS:
            raw = med.get(field)
            if raw is None:
                continue
            normalized = MedicationNormalizer.normalize_drug_name(str(raw))
            if normalized and normalized not in MedicationNormalizer.EMPTY_TOKENS:
                return normalized
        return ""

    @staticmethod
    def normalize_field(field_name: str, value: Any) -> str:
        """Normalize individual medication field (dose, schedule, etc.)."""
        _ = field_name
        if value is None or value in ("", "NA", "N/A", "None", "null"):
            return ""
        text = str(value).strip().lower()
        if text in MedicationNormalizer.EMPTY_TOKENS:
            return ""
        return text


class MedicationMatcher:
    """Match medications from two arrays by drug name (order-independent)."""

    def __init__(self, fuzzy_threshold: float = 0.85):
        """
        Args:
            fuzzy_threshold: 0.85 for scoring (GT vs Gen); 0.90 for validation
                (raw vs final — stricter).
        """
        self.fuzzy_threshold = float(fuzzy_threshold)

    def drugs_match(self, drug1: str, drug2: str) -> bool:
        """Check if two drug names match (exact or fuzzy via SequenceMatcher)."""
        norm1 = MedicationNormalizer.normalize_drug_name(drug1)
        norm2 = MedicationNormalizer.normalize_drug_name(drug2)
        if norm1 == norm2 and bool(norm1):
            return True
        if not norm1 or not norm2:
            return False
        similarity = SequenceMatcher(None, norm1, norm2).ratio()
        return similarity >= self.fuzzy_threshold

    def match_medications(
        self, left_meds: list, right_meds: list
    ) -> list[dict[str, Any]]:
        """
        Order-independent medication matching.

        Returns list of
        ``{'gt': med, 'gen': med, 'drug_name': str, 'gt_index': int, 'gen_index': int}``.
        """
        if not left_meds or not right_meds:
            return []

        matched_pairs: list[dict[str, Any]] = []
        used_right: set[int] = set()

        for left_idx, left_med in enumerate(left_meds):
            if not isinstance(left_med, dict):
                continue
            left_drug = MedicationNormalizer.extract_drug_identity(left_med)
            if not left_drug:
                continue
            for right_idx, right_med in enumerate(right_meds):
                if right_idx in used_right or not isinstance(right_med, dict):
                    continue
                right_drug = MedicationNormalizer.extract_drug_identity(right_med)
                if self.drugs_match(left_drug, right_drug):
                    matched_pairs.append(
                        {
                            "gt": left_med,
                            "gen": right_med,
                            "drug_name": left_drug,
                            "gt_index": left_idx,
                            "gen_index": right_idx,
                        }
                    )
                    used_right.add(right_idx)
                    break
        return matched_pairs

    def match_indices(
        self, left_meds: list[dict], right_meds: list[dict]
    ) -> list[tuple[int | None, int | None]]:
        """Pair left/right medication indices by fuzzy drug name."""
        pairs: list[tuple[int | None, int | None]] = []
        used_right: set[int] = set()
        for left_i, left_med in enumerate(left_meds):
            if not isinstance(left_med, dict):
                pairs.append((left_i, None))
                continue
            left_name = MedicationNormalizer.extract_drug_identity(left_med) or str(
                left_med.get("drug_name") or ""
            )
            hit: int | None = None
            for right_i, right_med in enumerate(right_meds):
                if right_i in used_right or not isinstance(right_med, dict):
                    continue
                right_name = MedicationNormalizer.extract_drug_identity(
                    right_med
                ) or str(right_med.get("drug_name") or "")
                if self.drugs_match(left_name, right_name):
                    hit = right_i
                    break
            if hit is not None:
                used_right.add(hit)
                pairs.append((left_i, hit))
            else:
                pairs.append((left_i, None))
        for right_i in range(len(right_meds)):
            if right_i not in used_right:
                pairs.append((None, right_i))
        return pairs


class MedicationComparator:
    """Compare two matched medications field-by-field."""

    CRITICAL_FIELDS = frozenset({"drug_name", "dose", "snomed_ct_id"})
    IMPORTANT_FIELDS = frozenset({"schedule", "instructions", "duration"})

    FIELDS_TO_CHECK: dict[str, str] = {
        "dose": "CRITICAL",
        "schedule": "IMPORTANT",
        "instructions": "IMPORTANT",
        "duration": "NORMAL",
        "snomed_ct_id": "CRITICAL",
    }

    # Extra fields checked during raw↔final validation.
    VALIDATION_EXTRA_FIELDS: dict[str, str] = {
        "drug_name": "CRITICAL",
        "generic_name": "IMPORTANT",
    }

    @classmethod
    def compare_fields(
        cls,
        gt_med: dict,
        gen_med: dict,
        *,
        extra_fields: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Compare medication fields.

        Returns match flags, overall_match (dose+schedule+instructions),
        accuracy over checked fields, and a differences list.
        """
        fields = dict(cls.FIELDS_TO_CHECK)
        if extra_fields:
            fields.update(extra_fields)

        differences: list[dict[str, Any]] = []
        matched_count = 0
        flags: dict[str, bool] = {}

        for field, severity in fields.items():
            gt_val = MedicationNormalizer.normalize_field(field, gt_med.get(field, ""))
            gen_val = MedicationNormalizer.normalize_field(field, gen_med.get(field, ""))
            match = gt_val == gen_val
            flag_key = (
                "snomed_match" if field == "snomed_ct_id" else f"{field}_match"
            )
            flags[flag_key] = match
            if match:
                matched_count += 1
            else:
                differences.append(
                    {
                        "field": field,
                        "gt": gt_med.get(field, ""),
                        "gen": gen_med.get(field, ""),
                        "severity": severity,
                    }
                )

        total_fields = len(fields)
        accuracy = matched_count / total_fields if total_fields else 0.0
        overall_match = all(
            flags.get(key, False)
            for key in ("dose_match", "schedule_match", "instructions_match")
        )
        return {
            "dose_match": flags.get("dose_match", False),
            "schedule_match": flags.get("schedule_match", False),
            "instructions_match": flags.get("instructions_match", False),
            "duration_match": flags.get("duration_match", False),
            "snomed_match": flags.get("snomed_match", False),
            "overall_match": overall_match,
            "accuracy": accuracy,
            "differences": differences,
        }


class ScoringComparator:
    """Compare ground-truth vs generated SOAP medications for fact scoring."""

    def __init__(self, fuzzy_threshold: float = 0.85):
        self.matcher = MedicationMatcher(fuzzy_threshold=fuzzy_threshold)

    def compare(self, gt_meds: list, gen_meds: list) -> dict[str, Any]:
        """Compare medication arrays order-independently."""
        if not isinstance(gt_meds, list):
            gt_meds = []
        if not isinstance(gen_meds, list):
            gen_meds = []

        if not gt_meds and not gen_meds:
            return {
                "match": True,
                "accuracy": 1.0,
                "matched_pairs": [],
                "unmatched_gt_count": 0,
                "unmatched_gen_count": 0,
                "unmatched_gt": 0,
                "unmatched_gen": 0,
            }

        if not gt_meds or not gen_meds:
            return {
                "match": False,
                "accuracy": 0.0,
                "matched_pairs": [],
                "unmatched_gt_count": len(gt_meds),
                "unmatched_gen_count": len(gen_meds),
                "unmatched_gt": len(gt_meds),
                "unmatched_gen": len(gen_meds),
            }

        matched_pairs = self.matcher.match_medications(gt_meds, gen_meds)
        if not matched_pairs:
            return {
                "match": False,
                "accuracy": 0.0,
                "matched_pairs": [],
                "unmatched_gt_count": len(gt_meds),
                "unmatched_gen_count": len(gen_meds),
                "unmatched_gt": len(gt_meds),
                "unmatched_gen": len(gen_meds),
            }

        results: list[dict[str, Any]] = []
        fully_matched = 0
        for pair in matched_pairs:
            comparison = MedicationComparator.compare_fields(pair["gt"], pair["gen"])
            results.append(
                {
                    "gt": pair["gt"],
                    "gen": pair["gen"],
                    "drug_name": pair["drug_name"],
                    "comparison": comparison,
                }
            )
            if comparison["overall_match"]:
                fully_matched += 1

        accuracy = fully_matched / len(gt_meds) if gt_meds else 0.0
        unmatched_gt = len(gt_meds) - len(matched_pairs)
        unmatched_gen = len(gen_meds) - len(matched_pairs)
        return {
            "match": accuracy >= 0.8,
            "accuracy": accuracy,
            "matched_pairs": results,
            "unmatched_gt_count": unmatched_gt,
            "unmatched_gen_count": unmatched_gen,
            # Aliases kept for soap_fact_scorer callers.
            "unmatched_gt": unmatched_gt,
            "unmatched_gen": unmatched_gen,
        }


class ValidationComparator:
    """Cross-check raw LLM SOAP vs final processed SOAP medications."""

    def __init__(self, fuzzy_threshold: float = 0.90):
        self.matcher = MedicationMatcher(fuzzy_threshold=fuzzy_threshold)

    def compare(self, raw_meds: list, final_meds: list) -> dict[str, Any]:
        """
        Cross-check medications between raw and final SOAP.

        Returns added / removed / changed / unchanged buckets.
        """
        if not isinstance(raw_meds, list):
            raw_meds = []
        if not isinstance(final_meds, list):
            final_meds = []

        matched_pairs = self.matcher.match_medications(raw_meds, final_meds)
        matched_raw = {p["gt_index"] for p in matched_pairs}
        matched_final = {p["gen_index"] for p in matched_pairs}

        changed: list[dict[str, Any]] = []
        unchanged: list[dict[str, Any]] = []

        for pair in matched_pairs:
            comparison = MedicationComparator.compare_fields(
                pair["gt"],
                pair["gen"],
                extra_fields=MedicationComparator.VALIDATION_EXTRA_FIELDS,
            )
            # Name-normalization note (final matched_drug_name ≠ drug_name).
            final = pair["gen"]
            if final.get("matched_drug_name") and final.get("drug_name"):
                if str(final["matched_drug_name"]).strip() != str(
                    final["drug_name"]
                ).strip():
                    comparison["differences"].append(
                        {
                            "field": "matched_drug_name",
                            "gt": final.get("drug_name", ""),
                            "gen": final.get("matched_drug_name", ""),
                            "severity": "NORMAL",
                            "type": "name_normalized",
                        }
                    )

            entry = {
                "drug_name": pair["drug_name"],
                "gt": pair["gt"],
                "gen": pair["gen"],
                "differences": comparison["differences"],
            }
            if comparison["differences"]:
                changed.append(entry)
            else:
                unchanged.append(entry)

        removed = [
            raw_meds[i] for i in range(len(raw_meds)) if i not in matched_raw
        ]
        added = [
            final_meds[i] for i in range(len(final_meds)) if i not in matched_final
        ]
        return {
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged": unchanged,
        }


# --- Convenience wrappers (stable call sites) ---------------------------------


_SCORING = ScoringComparator()
_VALIDATION = ValidationComparator()


def compare_medication_arrays(gt_meds: list, gen_meds: list) -> dict[str, Any]:
    """Backward-compatible alias used by soap_fact_scorer / tests."""
    return _SCORING.compare(gt_meds, gen_meds)


def match_medication_indices(
    gt_meds: list[dict], gen_meds: list[dict], *, fuzzy_threshold: float = 0.85
) -> list[tuple[int | None, int | None]]:
    """Pair GT/Gen medication indices by fuzzy drug name (order-independent)."""
    return MedicationMatcher(fuzzy_threshold=fuzzy_threshold).match_indices(
        gt_meds, gen_meds
    )


def normalize_drug_name(name: str | None) -> str:
    return MedicationNormalizer.normalize_drug_name(name)


def drug_names_match(drug1: str, drug2: str, threshold: float = 0.85) -> bool:
    return MedicationMatcher(fuzzy_threshold=threshold).drugs_match(drug1, drug2)


def compare_medication_details(gt_med: dict, gen_med: dict) -> dict[str, Any]:
    return MedicationComparator.compare_fields(gt_med, gen_med)
