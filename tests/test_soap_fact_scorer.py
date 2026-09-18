"""Phase-1/2 SOAP scorer: med arrays, empty norm, vitals, semantic, thresholds."""

from __future__ import annotations

from medsum_testing.backend.services.medication_comparison import (
    ScoringComparator,
    ValidationComparator,
    compare_medication_arrays,
)
from medsum_testing.backend.services.soap_fact_scorer import (
    CORRECT,
    HALLUCINATION,
    INCORRECT,
    MISSING,
    NA,
    PARTIAL,
    align_facts,
    apply_section_details,
    calculate_section_accuracy,
    classify_pair,
    is_na_value,
    load_scoring_config,
    nested_soap_to_facts,
    score_soap,
    values_match,
)


def setup_function() -> None:
    load_scoring_config(force_reload=True)


def test_na_markers_include_not_measured_and_unknown():
    assert is_na_value("")
    assert is_na_value("NA")
    assert is_na_value("null")
    assert is_na_value("Not measured")
    assert is_na_value("Did not measure")
    assert is_na_value("Unknown")
    # Bare none is an explicit clinical token, not global NA.
    assert not is_na_value("none")
    # Established negatives are never NA.
    assert not is_na_value("No known allergies")
    assert not is_na_value("NKA")


def test_empty_vs_na_is_na_not_incorrect():
    assert classify_pair("", "NA")["result"] == NA
    assert classify_pair("", "Not measured")["result"] == NA
    assert classify_pair("", "Did not measure")["result"] == NA
    assert classify_pair("Unknown", "NA")["result"] == NA


def test_established_negative_empty_is_missing_without_field_path():
    """Safety default (no field_path): NKA vs blank → Missing."""
    assert classify_pair("No known allergies", "")["result"] == MISSING
    assert classify_pair("NKA", None)["result"] == MISSING
    assert classify_pair("No known allergies", "NA")["result"] == MISSING


def test_allergy_negative_phrasing_variants_match():
    assert (
        classify_pair(
            "No known allergy to medicines reported.",
            "No known allergies.",
        )["result"]
        == CORRECT
    )
    assert (
        classify_pair(
            "No known allergy to medicines reported.",
            "No known drug allergies",
        )["result"]
        == CORRECT
    )
    assert (
        classify_pair(
            "No known allergies",
            "Patient is not allergic to any medicines.",
        )["result"]
        == CORRECT
    )
    assert classify_pair("No known allergies", "NKA")["result"] == CORRECT
    assert classify_pair("NKA", "NKDA")["result"] == CORRECT


def test_none_medications_equivalence():
    assert (
        classify_pair("No current medications", "none")["result"] == CORRECT
    )
    assert (
        classify_pair("No investigations needed", "None")["result"] == CORRECT
    )


def test_vitals_format_variants_match():
    assert values_match("140/90 mmHg", "140/90", numerical=True)
    assert values_match("140/90 mmHg", "BP 140/90", numerical=True)
    assert values_match("88 bpm", "88", numerical=True)
    assert values_match("88 bpm", "Heart rate 88", numerical=True)
    assert values_match("37.5°C", "37.5", numerical=True)
    assert classify_pair("140/90 mmHg", "BP 140/90", numerical=True)[
        "result"
    ] == CORRECT
    # Different numbers still fail.
    assert classify_pair("97°F", "96°F", numerical=True)["result"] == INCORRECT
    # Qualitative ≠ numeric.
    assert classify_pair("140/90", "Elevated BP", numerical=True)[
        "result"
    ] == INCORRECT


def test_subset_coverage_rejects_short_fragments():
    long_cc = (
        "High-grade fever since yesterday with cough, cold, body pain, and headache."
    )
    assert values_match(long_cc, "Fever since yesterday.") is False
    assert values_match(long_cc, "Fever") is False
    # Near-complete paraphrase with high coverage still matches.
    assert values_match(
        "Severe burning sensation when urinating since yesterday.",
        "Burning sensation when urinating since yesterday.",
    )


def test_llm_cannot_override_na_to_incorrect():
    rows = [
        {
            "field": "Blood pressure",
            "base_field": "Blood pressure",
            "section": "Objective",
            "ground_truth": "",
            "generated": "NA",
            "result": NA,
            "internal": NA,
            "criticality": "Critical",
            "weight": 5,
        }
    ]
    section_details = {
        "objective": {
            "score": 0,
            "differences": [
                {
                    "field": "Blood pressure",
                    "type": "Incorrect",
                    "severity": "critical",
                }
            ],
        }
    }
    out = apply_section_details(rows, section_details)
    assert out[0]["result"] == NA


def test_empty_gt_with_real_vital_is_hallucination():
    assert classify_pair("", "140/90 mmHg", numerical=True)["result"] == HALLUCINATION


# --- Fix #0: medication array order-independence --------------------------------


def _med(name, dose="500mg", schedule="1-0-1", instructions="After food"):
    return {
        "drug_name": name,
        "dose": dose,
        "schedule": schedule,
        "instructions": instructions,
        "duration": "5 days",
    }


def test_fix0_medication_arrays_order_independent():
    gt = [_med("Amoxicillin"), _med("Paracetamol", "650mg", "1-1-1")]
    gen = [_med("Paracetamol", "650mg", "1-1-1"), _med("Amoxicillin")]
    # Index-order string compare would fail; array helper passes.
    result = compare_medication_arrays(gt, gen)
    assert result["match"] is True
    assert result["accuracy"] == 1.0
    assert classify_pair(gt, gen, field_path="plan.medications")["result"] == CORRECT


def test_scoring_comparator_order_independent():
    gt = [
        {"drug_name": "Paracetamol", "dose": "500mg", "schedule": "1-0-1", "instructions": "x"},
        {"drug_name": "Amoxicillin", "dose": "500mg", "schedule": "1-1-1", "instructions": "x"},
    ]
    gen = [
        {"drug_name": "Amoxicillin", "dose": "500mg", "schedule": "1-1-1", "instructions": "x"},
        {"drug_name": "Paracetamol", "dose": "500mg", "schedule": "1-0-1", "instructions": "x"},
    ]
    result = ScoringComparator().compare(gt, gen)
    assert result["match"] is True
    assert result["accuracy"] == 1.0


def test_validation_comparator_change_tracking():
    raw = [
        {"drug_name": "Paracetamol", "dose": "500mg", "schedule": "1-0-1", "instructions": "x"},
        {"drug_name": "Aspirin", "dose": "325mg", "schedule": "1-0-0", "instructions": "x"},
    ]
    final = [
        {"drug_name": "Paracetamol", "dose": "500mg", "schedule": "1-0-1", "instructions": "x"},
        {"drug_name": "Ibuprofen", "dose": "400mg", "schedule": "1-1-0", "instructions": "x"},
    ]
    result = ValidationComparator().compare(raw, final)
    assert len(result["changed"]) == 0  # Paracetamol unchanged
    assert len(result["removed"]) == 1  # Aspirin
    assert len(result["added"]) == 1  # Ibuprofen
    assert result["removed"][0]["drug_name"] == "Aspirin"
    assert result["added"][0]["drug_name"] == "Ibuprofen"


def test_validation_comparator_field_changes():
    raw = [{"drug_name": "Paracetamol", "dose": "500mg", "schedule": "1-0-1", "instructions": "x"}]
    final = [{"drug_name": "Paracetamol", "dose": "650mg", "schedule": "1-0-1", "instructions": "x"}]
    result = ValidationComparator().compare(raw, final)
    assert len(result["changed"]) == 1
    fields = {d["field"] for d in result["changed"][0]["differences"]}
    assert "dose" in fields


def test_ai_comparator_validate_medications_uses_unified_module():
    from medsum_testing.backend.services.ai_comparator import validate_medications

    payload = {
        "plan": {
            "medications": [
                {"drug_name": "Paracetamol", "dose": "500mg", "schedule": "1-0-1"},
                {"drug_name": "Ibuprofen", "dose": "400mg", "schedule": "1-1-0"},
            ]
        },
        "debug": {
            "raw_soap": {
                "plan": {
                    "medications": [
                        {"drug_name": "Paracetamol", "dose": "500mg", "schedule": "1-0-1"},
                        {"drug_name": "Aspirin", "dose": "325mg", "schedule": "1-0-0"},
                    ]
                }
            }
        },
    }
    out = validate_medications(payload)
    assert out["raw_count"] == 2
    assert out["final_count"] == 2
    assert out["difference_count"] >= 2
    assert any(d["type"] == "removed_in_final" for d in out["differences"])
    assert any(d["type"] == "added_in_final" for d in out["differences"])
    assert "added_medicines" in out
    assert "removed_medicines" in out


def test_fix0_reordered_meds_score_soap_pass():
    gt_soap = {
        "plan": {
            "medications": [
                _med("Amoxicillin"),
                _med("Paracetamol", "650mg", "SOS", "As needed"),
            ]
        }
    }
    gen_soap = {
        "plan": {
            "medications": [
                _med("Paracetamol", "650mg", "SOS", "As needed"),
                _med("Amoxicillin"),
            ]
        }
    }
    scored = score_soap(gt_soap, gen_soap)
    med_facts = [
        f
        for f in scored["facts"]
        if f.get("result") != NA
        and (
            "medication" in (f.get("categories") or [])
            or str(f.get("base_field") or "").lower()
            in {"drug name", "dose", "schedule", "duration", "instructions"}
        )
        and str(f.get("base_field") or "").lower() != "current medications"
    ]
    assert med_facts, "expected medication facts"
    assert all(f["result"] == CORRECT for f in med_facts)


# --- Fix #1: empty/null normalization (scoped fields) -------------------------


def test_fix1_allergies_nka_vs_empty_passes():
    assert (
        classify_pair(
            "No known allergies", "", field_path="subjective.allergies"
        )["result"]
        == CORRECT
    )


def test_fix1_na_vs_none_passes_on_scoped_field():
    assert (
        classify_pair("NA", "None", field_path="plan.investigations")["result"]
        == CORRECT
    )
    assert values_match(
        "NA", "None", field_path="plan.investigations"
    )


# --- Fix #2: vitals format standardization ------------------------------------


def test_fix2_vitals_unit_normalization():
    assert values_match(
        "140/90",
        "140/90 mmHg",
        field_path="objective.vitals.blood_pressure",
    )
    assert values_match(
        "88", "88 bpm", field_path="objective.vitals.heart_rate"
    )
    assert (
        classify_pair(
            "140/90",
            "140/90 mmHg",
            field_path="objective.vitals.blood_pressure",
            numerical=True,
        )["result"]
        == CORRECT
    )


# --- Fix #4 / #6: semantic matching -------------------------------------------


def test_fix4_semantic_narrative_match():
    assert values_match(
        "Fever for 3 days",
        "3-day fever",
        field_path="subjective.chief_complaint",
    )
    # Incomplete / different clinical content should still fail.
    assert (
        values_match(
            "Severe abdominal pain",
            "Pain for 4 days",
            field_path="subjective.chief_complaint",
        )
        is False
    )


def test_fix6_reasoning_uses_semantic_threshold():
    assert values_match(
        "Assessment suggests typhoid based on fever and rose spots.",
        "Typhoid suggested by rose spots and fever on assessment.",
        field_path="assessment.reasoning",
    )
    # Incomplete reasoning must still fail.
    assert (
        values_match(
            "Assessment suggests typhoid based on fever and rose spots.",
            "Patient has a fever.",
            field_path="assessment.reasoning",
        )
        is False
    )


# --- Fix #5: field-level thresholds -------------------------------------------


def test_fix5_calculate_section_accuracy():
    section = {
        "assessment.diagnosis": {"similarity": 0.96},
        "subjective.chief_complaint": {"similarity": 0.72},
        "plan.education": {"similarity": 0.65},  # below 0.70 → fail
    }
    acc = calculate_section_accuracy(section)
    # 2 of 3 fields pass their thresholds
    assert abs(acc - (2 / 3)) < 1e-9
