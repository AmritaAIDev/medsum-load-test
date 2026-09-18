"""GPT-4 / DeepSeek comparison logic."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI

from medsum_testing.backend.models.test_result import ComparisonResult, MedComparisonResult
from medsum_testing.backend.services.config_loader import get_config
from medsum_testing.backend.services.translation_metrics import (
    compute_translation_metrics,
)

log = logging.getLogger("medsum_ai")


SYSTEM_PROMPT = """

You are a meticulous clinical QA reviewer comparing a model-generated SOAP note
field-by-field against a ground-truth SOAP note.

For EACH field, classify the model's value against the ground truth using
EXACTLY ONE of these categories:

- "correct":
  The model communicates the same clinically meaningful information as the
  ground truth. Different wording, synonyms, medical terminology, abbreviations,
  unit formatting, sentence order, verbosity, or paraphrasing do not change
  the classification when the complete clinical meaning is preserved.

- "partial_match":
  The model captures the main clinical information but omits or changes one or
  more clinically meaningful components without directly contradicting the
  ground truth.

  Clinically meaningful components include symptoms, associated symptoms,
  severity, location, laterality, duration, timing, frequency, progression,
  triggers, negative findings, relevant modifiers, diagnosis status, medication
  dose, route, frequency, duration, instructions, safety-net advice, and other
  clinically relevant qualifiers.

- "wrong":
  The model gives information that contradicts, changes, or is factually
  different from the ground truth. This includes incorrect numerical values,
  different medications or doses, opposite findings, incorrect laterality,
  incorrect diagnosis, changed negation, or materially different clinical
  meaning.

- "missing":
  The ground truth contains a clinically meaningful value, but the model field
  is empty or contains no corresponding information.

- "hallucination":
  The ground-truth field has no value, but the model provides a clinical value
  that is unsupported by the ground-truth value for that field.

EMPTY-VALUE RULES:
- Treat "", null, "NA", "N/A", and equivalent no-value markers as EMPTY when
  they represent absence of a ground-truth value.
- GT empty + Model empty -> correct.
- GT empty + Model non-empty -> hallucination.
- GT non-empty + Model empty -> missing.
- Clinically meaningful negative findings are NOT empty values.

SEMANTIC EQUIVALENCE:
- Preserve semantic equivalence across paraphrasing, synonyms, medical
  terminology, abbreviations, unit representations, sentence restructuring,
  and different levels of verbosity.
- Do not downgrade a semantically equivalent value because it is shorter or
  differently worded.
- A shorter value that omits clinically meaningful information should be
  classified as partial_match.

NUMERICAL AND QUANTITATIVE VALUES:
- Numerical, dosage, frequency, duration, age, measurement, and quantitative
  clinical values must match semantically.
- Different numerical values are wrong.
- Equivalent numerical representations and units are correct.

NEGATION:
- Preserve the meaning of positive and negative findings.
- A changed or reversed negation is wrong.

CLINICAL QUALIFIERS:
- Duration, timing, frequency, severity, location, laterality, progression,
  triggers, associated findings, and other clinically meaningful qualifiers
  must be considered.
- Omission of a clinically meaningful qualifier without contradiction is
  partial_match.
- A changed qualifier that alters the clinical meaning is wrong.

DECISION ORDER:
1. Determine whether the ground-truth field is empty.
2. If GT is empty and Model is empty, classify as correct.
3. If GT is empty and Model contains unsupported information, classify as
   hallucination.
4. If GT is non-empty and Model is empty, classify as missing.
5. Determine whether the Model contradicts or factually changes the GT.
6. If there is a contradiction or factual difference, classify as wrong.
7. Determine whether all clinically meaningful information is preserved.
8. If all information is preserved, classify as correct.
9. If some information is preserved but clinically meaningful information is
   omitted or altered without contradiction, classify as partial_match.

IMPORTANT:
Do not classify a value as wrong merely because it is shorter, more concise,
differently worded, paraphrased, or uses different terminology.
Use partial_match for genuine omissions.
Use wrong only for factual differences, contradictions, or clinically
meaningful changes in information.

Return ONLY a valid JSON object of exactly this shape:

{
  "results": [
    {
      "field": "<field path exactly as given>",
      "category": "correct|partial_match|wrong|missing|hallucination",
      "reason": "<one short sentence>"
    }
  ]
}

Include exactly one result per field given, in the same order, using the exact
field values provided.

No markdown and no extra text outside the JSON object.

"""


def get_ai_client(model: str, config: dict) -> tuple[OpenAI, str]:
    return _get_client(model, config)


def _get_client(model: str, config: dict) -> tuple[OpenAI, str]:
    """Returns (OpenAI client, model_name) for the given model string."""
    ai_config = config.get("ai_comparison", {})

    if model == "deepseek":
        client = OpenAI(
            api_key=(ai_config.get("deepseek_api_key") or "").strip(),
            base_url=ai_config.get(
                "deepseek_base_url", "https://api.deepseek.com/v1"
            ),
        )
        model_name = ai_config.get("deepseek_model", "deepseek-chat")
        return client, model_name

    client = OpenAI(api_key=(ai_config.get("openai_api_key") or "").strip())
    if model in ("gpt-4", "gpt-4o-mini", "gpt-4o"):
        model_name = model
    else:
        model_name = ai_config.get("openai_model", "gpt-4o")
    return client, model_name


def parse_ai_json(raw: str) -> dict[str, Any]:
    clean = re.sub(r"```json|```", "", raw).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def _models_to_try(model: str) -> list[str]:
    """Requested comparison model first; fall back to gpt-4o-mini when it differs."""
    if model == "deepseek":
        return ["deepseek", "gpt-4o-mini"]
    models = [model]
    if model != "gpt-4o-mini":
        models.append("gpt-4o-mini")
    return models


def _format_medical_diffs(items: list[Any]) -> list[str]:
    formatted: list[str] = []
    for item in items:
        if isinstance(item, dict):
            formatted.append(
                f"[{item.get('type', '?')}] "
                f"{item.get('ground_truth', '')} → {item.get('generated', '')} "
                f"({item.get('severity', '')})"
            )
        else:
            formatted.append(str(item))
    return formatted


def _call_llm(prompt: str, model: str) -> dict[str, Any]:
    config = get_config()
    client, model_name = get_ai_client(model, config)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )
    content = response.choices[0].message.content or "{}"
    return parse_ai_json(content)


def _to_comparison(data: dict[str, Any]) -> ComparisonResult:
    medical = _format_medical_diffs(data.get("medical_differences") or [])
    severity = (
        data.get("overall_severity")
        or data.get("severity")
        or "low"
    )
    if severity == "none":
        severity = "low"
    return ComparisonResult(
        similarity_score=data.get("similarity_score"),
        medical_differences=medical,
        medical_difference_details=data.get("medical_differences") or [],
        general_differences=data.get("general_differences") or [],
        severity=severity,
        summary=data.get("summary") or "",
        error=data.get("error") or "",
    )


SOAP_KEYS = ["subjective", "objective", "assessment", "plan", "summary"]


def extract_soap_from_result(tr: dict, allow_raw_fallback: bool = True) -> dict | None:
    """
    Extract SOAP sections from Flask transcription result.
    Returns dict with SOAP sections, or None if LLM failed or no SOAP found.
    """
    if not tr or not isinstance(tr, dict):
        return None

    # Check if LLM failed — "error" key at top level means SOAP not generated
    top_level_error = tr.get("error")
    if top_level_error:
        log.warning(
            "extract_soap_from_result: Flask LLM error detected: %s",
            str(top_level_error)[:200],
        )
        # Don't return None yet — still try to get SOAP if present

    # Try top level first
    top_level = {}
    for k in SOAP_KEYS:
        val = tr.get(k)
        if isinstance(val, dict) and val:
            top_level[k] = val
        elif isinstance(val, str) and val.strip():
            top_level[k] = val

    if top_level:
        log.info(
            "SOAP extracted from top level: keys=%s", list(top_level.keys())
        )
        return top_level

    if not allow_raw_fallback:
        log.warning(
            "extract_soap_from_result: no top-level SOAP (raw fallback disabled). "
            "Top-level error: %s. Top-level keys: %s",
            bool(top_level_error),
            list(tr.keys()),
        )
        return None

    # Fallback — try debug.raw_soap or debug["raw soap"]
    debug = tr.get("debug") or {}
    # Try both key variants — Flask uses "raw soap" (space) in some responses
    raw_soap = debug.get("raw_soap") or debug.get("raw soap") or {}

    # Validate raw_soap is actual SOAP data, not an error object
    if isinstance(raw_soap, dict) and "error" not in raw_soap:
        fallback = {}
        for k in SOAP_KEYS:
            val = raw_soap.get(k)
            if isinstance(val, dict) and val:
                fallback[k] = val
            elif isinstance(val, str) and val.strip():
                fallback[k] = val
        if fallback:
            log.info(
                "SOAP extracted from raw_soap fallback: keys=%s",
                list(fallback.keys()),
            )
            return fallback
    elif isinstance(raw_soap, dict) and "error" in raw_soap:
        log.warning(
            "extract_soap_from_result: raw_soap contains error, not SOAP data: %s",
            str(raw_soap.get("error", ""))[:200],
        )

    log.warning(
        "extract_soap_from_result: no SOAP sections found. "
        "Top-level error: %s. Top-level keys: %s",
        bool(top_level_error),
        list(tr.keys()),
    )
    return None


SOAP_COMPARE_PROMPT = """
You are a meticulous clinical QA reviewer comparing a model-generated SOAP note field-by-field against a ground-truth SOAP note.
For EACH field, classify the model's value against the ground truth using EXACTLY ONE of these categories:

- "correct":
  The model communicates the same clinically meaningful information as the ground truth. Different wording, synonyms, medical terminology, abbreviations,
  unit formatting, sentence order, verbosity, or paraphrasing do not change the classification when the complete clinical meaning is preserved.

- "partial_match":
  The model captures the main clinical information but omits or changes one or more clinically meaningful components without directly contradicting the ground truth.

  Clinically meaningful components include symptoms, associated symptoms, severity, location, laterality, duration, timing, frequency, progression,
  triggers, negative findings, relevant modifiers, diagnosis status, medication dose, route, frequency, duration, instructions, safety-net advice, and other
  clinically relevant qualifiers.

- "wrong":
  The model gives information that contradicts, changes, or is factually different from the ground truth. This includes incorrect numerical values,
  different medications or doses, opposite findings, incorrect laterality, incorrect diagnosis, changed negation, or materially different clinical meaning.

- "missing":
  The ground truth contains a clinically meaningful value, but the model field is empty or contains no corresponding information.

- "hallucination":
  The ground-truth field has no value, but the model provides a clinical value that is unsupported by the ground-truth value for that field.

EMPTY-VALUE RULES:
- Treat "", null, "NA", "N/A", and equivalent no-value markers as EMPTY when they represent absence of a ground-truth value.
- GT empty + Model empty -> correct.
- GT empty + Model non-empty -> hallucination.
- GT non-empty + Model empty -> missing.
- Clinically meaningful negative findings are NOT empty values.

SEMANTIC EQUIVALENCE:
- Preserve semantic equivalence across paraphrasing, synonyms, medical terminology, abbreviations, unit representations, sentence restructuring, and different levels of verbosity.
- Do not downgrade a semantically equivalent value because it is shorter or differently worded.
- A shorter value that omits clinically meaningful information should be classified as partial_match.

NUMERICAL AND QUANTITATIVE VALUES:
- Numerical, dosage, frequency, duration, age, measurement, and quantitative clinical values must match semantically.
- Different numerical values are wrong.
- Equivalent numerical representations and units are correct (e.g., "5 days" vs "five days", ">101°F" vs "above 101", "ER" vs "emergency room").

NEGATION:
- Preserve the meaning of positive and negative findings.
- A changed or reversed negation is wrong.

CLINICAL QUALIFIERS:
- Duration, timing, frequency, severity, location, laterality, progression, triggers, associated findings, and other clinically meaningful qualifiers must be considered.
- Omission of a clinically meaningful qualifier without contradiction is partial_match.
- A changed qualifier that alters the clinical meaning is wrong.

SAFETY-NET AND FOLLOW-UP INSTRUCTIONS:
- Compare all clinically meaningful components: timing of return visit, trigger conditions (symptoms, lab values, thresholds), action instructions (seek care, return immediately, go to ER), severity thresholds, and secondary actions.
- Different phrasings of equivalent actions are correct if clinical meaning is preserved (e.g., "seek immediate medical attention" vs "come back immediately", "high fever (>101°F)" vs "fever above 101").
- Abbreviated and expanded forms of equivalent terms are correct (e.g., "ER" vs "emergency room", "antibiotics" vs "antibiotic course").
- Omission of a specific trigger condition or action instruction is partial_match or missing, depending on clinical significance.
- Addition of unsupported warnings or triggers not in the ground truth is hallucination.

DECISION ORDER:
1. Determine whether the ground-truth field is empty.
2. If GT is empty and Model is empty, classify as correct.
3. If GT is empty and Model contains unsupported information, classify as hallucination.
4. If GT is non-empty and Model is empty, classify as missing.
5. Determine whether the Model contradicts or factually changes the GT.
6. If there is a contradiction or factual difference, classify as wrong.
7. Determine whether all clinically meaningful information is preserved.
8. If all information is preserved, classify as correct.
9. If some information is preserved but clinically meaningful information is omitted or altered without contradiction, classify as partial_match.

IMPORTANT:
Do not classify a value as wrong merely because it is shorter, more concise, differently worded, paraphrased, or uses different terminology.
Use partial_match for genuine omissions of clinically meaningful components. Use wrong only for factual differences, contradictions, or clinically
meaningful changes in information. For safety-net instructions, verify that all trigger conditions and action
directives are present and semantically equivalent before classifying as correct.

Return ONLY a valid JSON object of exactly this shape:

{
  "results": [
    {
      "field": "<field path exactly as given>",
      "category": "correct|partial_match|wrong|missing|hallucination",
      "reason": "<one short sentence>"
    }
  ]
}

Include exactly one result per field given, in the same order, using the exact field values provided.
No markdown and no extra text outside the JSON object.

"""


# SOAP_COMPARE_PROMPT = """
# You are a medical AI evaluator comparing a Ground Truth SOAP note with a Generated SOAP note.

# Return ONLY valid JSON. No markdown, no preamble.

# Compare clinical facts by semantic meaning, not exact wording.
# Do not use SOAP section weights.

# ### NA vs Missing

# NA:
# Ground Truth does not establish the clinical fact.

# Missing:
# Ground Truth establishes the clinical fact, but Generated does not capture it.

# GT = NA/null/empty and Generated = NA/null/empty is not an error.

# Explicit positive and negative findings are established facts.

# ### Semantic Matching

# Compare the clinical meaning of facts, not their surface wording.

# Mark a fact Correct when Generated conveys the same clinical meaning as Ground Truth.

# Treat the following as semantically equivalent when the underlying clinical meaning is unchanged:

# * synonyms
# * paraphrases
# * abbreviations and their standard full forms
# * standard medical terminology and equivalent plain-language terminology
# * equivalent clinical terminology
# * equivalent descriptions of physical examination findings
# * equivalent descriptions of diagnoses
# * equivalent descriptions of allergies
# * equivalent descriptions of symptoms
# * equivalent descriptions of medication information
# * equivalent descriptions of investigation information
# * equivalent descriptions of follow-up instructions
# * different sentence structures
# * different grammatical constructions
# * standard unit representations
# * numbers written as digits or words
# * equivalent formatting of numerical values

# Do not mark a fact Incorrect only because wording, grammar, terminology, abbreviation, sentence structure, or phrasing is different.

# Do not require Generated to reproduce the exact wording of Ground Truth.

# A shorter statement is Correct when it preserves the complete clinical meaning of the fact.

# Do not penalize clinically equivalent terminology.

# ### Clinical Equivalence

# Evaluate whether two statements describe the same clinical fact.

# Equivalent clinical expressions must be treated as Correct when they preserve:

# * the same finding
# * the same clinical state
# * the same polarity
# * the same severity
# * the same certainty
# * the same temporality
# * the same quantity
# * the same frequency
# * the same duration
# * the same anatomical location
# * the same laterality
# * the same attribution
# * the same clinically relevant qualifiers

# A difference in wording alone is not a clinical error.

# A difference is Incorrect only when it changes the underlying clinical meaning.

# ### Fact-Level Comparison

# Break every field into individual atomic clinical facts before comparing.

# For each atomic fact:

# * same clinical meaning → Correct
# * Ground Truth fact omitted from Generated → Missing
# * Generated fact conflicts with Ground Truth → Incorrect
# * Generated fact is unsupported by the complete Ground Truth → Hallucination

# Do not mark an entire field Incorrect because only part of the field is incorrect or missing.

# When a field contains multiple facts, evaluate each fact independently.

# A single field may therefore contain multiple difference entries with different types.

# ### Incorrect

# Use Incorrect only when Generated conflicts with or materially changes an established Ground Truth fact.

# A clinically meaningful change may involve:

# * different numerical value
# * different medication
# * different dose
# * different frequency
# * different duration
# * different route
# * different anatomical site
# * different laterality
# * reversed positive/negative finding
# * materially different diagnosis
# * materially different clinical status
# * materially different certainty
# * materially different severity
# * materially different timing
# * materially different clinical condition

# Do not use Incorrect for:

# * different wording
# * synonyms
# * paraphrasing
# * abbreviations
# * standard medical terminology
# * equivalent clinical terminology
# * standard unit formatting
# * digits versus words
# * equivalent examination descriptions
# * equivalent allergy descriptions
# * equivalent diagnosis descriptions
# * equivalent medication descriptions
# * equivalent follow-up descriptions
# * shorter wording that preserves the same clinical meaning
# * omitted details when the remaining information is not contradictory

# ### Hallucination

# Use Hallucination when Generated adds a clinical fact that is not supported anywhere in the complete Ground Truth.

# Before marking a Generated fact as Hallucination, check the entire Ground Truth across:

# * subjective
# * objective
# * assessment
# * plan
# * summary

# A fact supported anywhere in Ground Truth is not a Hallucination merely because it appears in a different Generated field.

# Do not infer support from general medical knowledge.

# Do not treat a fact as supported merely because it is medically plausible.

# Do not mark a fact as Hallucination solely because it is placed in a different SOAP field.

# ### Polarity and Negation

# Preserve the polarity of clinical facts.

# Positive and negative findings must be compared separately.

# If Ground Truth states a condition or finding is present and Generated states it is absent, mark Incorrect.

# If Ground Truth states a condition or finding is absent and Generated states it is present, mark Incorrect.

# If an explicit Ground Truth positive or negative finding is omitted, mark Missing.

# Do not infer a negative finding from the absence of documentation.

# Equivalent negative clinical terminology must be treated as Correct when it conveys the same negative finding.

# ### Clinical Context

# Preserve clinically meaningful:

# * negation
# * certainty
# * temporality
# * quantity
# * frequency
# * duration
# * severity
# * anatomical location
# * laterality
# * attribution
# * conditionality

# Do not mark a fact Incorrect merely because Generated expresses the same clinical context using different terminology.

# If the difference changes the actual clinical meaning, mark Incorrect.

# ### Field Placement

# A supported clinical fact appearing in a different SOAP field is not automatically Hallucination.

# Use the complete Ground Truth to determine whether the fact is supported.

# Do not treat simple relocation as a clinical error when the underlying clinical meaning remains correct.

# However, evaluate whether the Generated field represents the fact appropriately.

# Field placement alone must not determine Correct, Incorrect, Missing, or Hallucination.

# ### Vitals

# Use these exact field names in differences[].field:

# Blood pressure
# Pulse
# Respiratory rate
# Temperature
# SpO2
# Heart exam
# Height
# Weight

# Treat heart_rate as Pulse.

# For numerical measurements:

# * same numeric value with different formatting → Correct
# * same numeric value with standard unit representation → Correct
# * same numeric value expressed using digits or words → Correct
# * different numeric value → Incorrect
# * Ground Truth value present and Generated absent → Missing
# * Generated value unsupported by Ground Truth → Hallucination

# Do not apply numeric tolerance unless explicitly stated.

# Do not calculate or infer a different value.

# For examination findings, compare clinical meaning rather than exact wording.

# Equivalent descriptions of the same examination finding are Correct.

# ### Medications

# Compare medication facts separately:

# * drug name
# * dose
# * schedule
# * duration
# * route
# * indication
# * medication instructions

# Equivalent generic, brand, abbreviated, or standard clinical terminology is Correct when both expressions refer to the same medication.

# A different medication identity is Incorrect.

# A different dose, schedule, duration, or route is Incorrect when established in Ground Truth.

# An omitted medication fact is Missing.

# ### Medication Instructions

# Compare medication instructions separately from medication name, dose, schedule, and duration.

# If an instruction is explicitly supported by Ground Truth, equivalent wording is Correct.

# If only part of an instruction is captured:

# * captured information → Correct
# * omitted information → Missing

# If Generated adds an instruction that is not supported anywhere in Ground Truth, mark Hallucination.

# Do not assume an instruction is supported merely because it is medically common or logically related to the medication.

# ### Medication Indication

# Do not infer medication purpose from:

# * medication name
# * pharmacological knowledge
# * dose
# * schedule
# * route
# * common clinical usage

# An indication is supported only when it is established somewhere in Ground Truth.

# If Generated adds an unsupported indication, mark Hallucination.

# ### Allergies

# Compare allergy information by clinical meaning.

# Equivalent clinical expressions describing the same allergy status are Correct.

# Do not mark an allergy fact Incorrect because Generated uses different standard medical terminology, abbreviations, or equivalent clinical wording.

# A true change in allergy status or allergen identity is Incorrect.

# An omitted established allergy fact is Missing.

# An unsupported allergy or allergy status added by Generated is Hallucination.

# ### Diagnosis

# Compare diagnosis by clinical meaning rather than exact wording.

# Equivalent diagnostic terminology is Correct when the underlying meaning is unchanged.

# Evaluate:

# * diagnosis
# * type
# * status
# * reasoning

# according to the information established by Ground Truth.

# Do not mark a diagnosis Incorrect merely because Generated uses a clinically equivalent diagnostic expression.

# If type or status represents a genuinely different clinical meaning, mark Incorrect.

# If Ground Truth establishes type or status and Generated does not provide it, mark Missing.

# ### Clinical Reasoning

# Compare reasoning at the fact level.

# Generated does not need to reproduce the exact Ground Truth reasoning.

# If Generated correctly captures some reasoning but omits other established reasoning:

# * captured reasoning → Correct
# * omitted reasoning → Missing

# Use Incorrect only when Generated reasoning contradicts or materially changes Ground Truth.

# Unsupported reasoning is Hallucination.

# ### Investigation

# Compare:

# * investigation name
# * body site
# * result when present
# * indication or condition
# * timing
# * conditional requirements

# Equivalent clinical terminology is Correct.

# Omitted established investigation information is Missing.

# A different investigation or materially different condition is Incorrect.

# An unsupported investigation added by Generated is Hallucination.

# ### Follow-up

# Compare:

# * follow-up timing
# * conditions
# * clinically important instructions

# Equivalent expressions of the same timing or condition are Correct.

# Omitted follow-up conditions are Missing.

# Contradictory timing or conditions are Incorrect.

# Unsupported follow-up instructions are Hallucination.

# ### Summary

# Evaluate Summary using the same fact-level rules.

# Do not expect Summary to reproduce every detail of the SOAP.

# Important facts present in Ground Truth and correctly represented in Summary are Correct.

# Important established facts omitted from Summary are Missing.

# Contradictory facts are Incorrect.

# Unsupported clinical facts are Hallucination.

# Do not mark Summary Incorrect merely because it is shorter than Ground Truth.

# ### No Partial Category

# Do not use Partial.

# When a Generated field contains both correct and missing information, evaluate the individual facts separately.

# When a Generated field contains both supported and unsupported information, evaluate the individual facts separately.

# ### Comparison Priority

# For each Generated clinical fact:

# 1. Check whether the fact is supported anywhere in the complete Ground Truth.
# 2. If it is not supported → Hallucination.
# 3. If it is supported, identify the corresponding Ground Truth fact.
# 4. Compare the clinical meaning.
# 5. If the meaning is equivalent → Correct.
# 6. If the meaning contradicts or materially changes Ground Truth → Incorrect.

# Then check Ground Truth for established facts that Generated failed to capture:

# 7. Established Ground Truth fact absent from Generated → Missing.

# ### Severity

# Use severity only for actual errors.

# low:
# minor non-critical omission or minor documentation difference

# medium:
# meaningful clinical omission or non-critical incorrect information

# high:
# clinically important incorrect, missing, or hallucinated information

# critical:
# potentially dangerous medication, diagnosis, allergy, vital, or other clinically significant error

# Correct and NA entries should not be treated as errors.

# ### Scoring

# Calculate section scores from the underlying fact-level comparison.

# Correct facts increase the score.

# Missing facts reduce the score.

# Incorrect and Hallucination receive stronger penalties than ordinary omissions.

# Do not make an entire section incorrect because of one incorrect or missing fact.

# Do not penalize semantically equivalent wording.

# similarity_score must represent overall semantic similarity between Ground Truth and Generated SOAP.

# overall_severity must represent the highest clinically relevant severity of actual errors:

# none
# low
# medium
# high
# critical

# ### Allowed Types

# Each difference must use exactly one of:

# Correct
# Incorrect
# Missing
# Hallucination
# NA

# Never use:

# Wrong
# Invented
# Extra
# Partial

# ### Output Schema

# {
# "similarity_score": <0-100>,
# "overall_severity": "none|low|medium|high|critical",
# "summary": "<maximum 2 sentence verdict>",
# "section_details": {
# "subjective": {
# "score": <0-100>,
# "differences": [
# {
# "field": "<field name>",
# "ground_truth": "<value>",
# "generated": "<value>",
# "type": "Correct|Incorrect|Missing|Hallucination|NA",
# "severity": "low|medium|high|critical"
# }
# ]
# },
# "objective": {
# "score": <0-100>,
# "differences": []
# },
# "assessment": {
# "score": <0-100>,
# "differences": []
# },
# "plan": {
# "score": <0-100>,
# "differences": []
# }
# }
# }

# ### Final Verification

# Before returning the JSON, silently verify:

# 1. Semantically equivalent facts are Correct.
# 2. Equivalent clinical terminology is Correct.
# 3. Equivalent examination descriptions are Correct.
# 4. Equivalent allergy descriptions are Correct.
# 5. Equivalent medication descriptions are Correct.
# 6. Digit and word number representations are Correct.
# 7. Standard unit representations are Correct when the numeric value is unchanged.
# 8. Every Incorrect is a genuine clinical conflict or material change.
# 9. Every Missing fact is explicitly established by Ground Truth.
# 10. Every Hallucination is unsupported by the entire Ground Truth.
# 11. Positive and negative findings are not reversed.
# 12. Medication names, doses, schedules, routes, and durations are compared by clinical meaning.
# 13. Medication instructions are not assumed from medical knowledge.
# 14. Field relocation alone is not treated as Hallucination.
# 15. Compound fields are evaluated at fact level.
# 16. Omitted details are not labeled Incorrect.
# 17. Semantically equivalent shorter statements are not penalized.
# 18. Only the allowed types are used.
# 19. Return only valid JSON.
# """


def _looks_nested_soap(payload: Any) -> bool:
    if not isinstance(payload, dict) or isinstance(payload.get("facts"), list):
        return False
    return any(key in payload for key in SOAP_KEYS)


def _llm_compare_soap(
    soap_ground_truth: dict,
    soap_generated: dict,
    model: str,
    config: dict,
) -> dict[str, Any]:
    """LLM section_details pass. Fact-level score is applied afterwards."""
    user_prompt = (
        f"Ground Truth SOAP:\n{json.dumps(soap_ground_truth, indent=2)[:3000]}\n\n"
        f"Generated SOAP:\n{json.dumps(soap_generated, indent=2)[:3000]}\n\n"
        "Compare these SOAP notes and return JSON only."
    )
    models_to_try = _models_to_try(model)
    last_error = None
    for attempt_model in models_to_try:
        try:
            log.info("SOAP_COMPARE: trying model=%s", attempt_model)
            client, model_name = _get_client(attempt_model, config)
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": SOAP_COMPARE_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=2000,
                temperature=0,
            )
            raw = resp.choices[0].message.content or "{}"
            result = parse_ai_json(raw)
            log.info(
                "SOAP_COMPARE ✓ model=%s score=%s",
                attempt_model,
                result.get("similarity_score"),
            )
            return result
        except Exception as exc:
            log.warning("SOAP_COMPARE failed with %s: %s", attempt_model, exc)
            last_error = exc
            continue
    return {
        "section_details": {},
        "error": str(last_error) if last_error else "SOAP LLM comparison failed",
    }


def llm_verify_text_matches(
    pairs: list[dict[str, str]],
    model: str,
    config: dict | None = None,
) -> list[bool]:
    """Semantic correct/wrong verdict for free-text SOAP fields.

    Used by soap_fact_scorer.score_soap_simple_key_match() to re-check
    long narrative fields (HPI, histories, reasoning, ...) that the
    deterministic word-overlap matcher flagged as different — two SOAP
    notes can describe the same clinical facts in very different words.

    pairs: [{"field", "ground_truth", "generated"}, ...]. Returns one bool
    per pair, same order/length (True = same clinical meaning). All models
    failing returns all-False, so callers keep the deterministic verdict.
    """
    if not pairs:
        return []
    config = config or get_config()
    prompt = (
        "Each numbered pair below is a free-text SOAP field (chief complaint, "
        "history of present illness, past/social/family history, current "
        "medications, diagnosis, assessment reasoning, education, "
        "investigations, follow-up, other findings, or medication "
        "instructions). These fields are written in prose and are almost "
        "never worded identically between Ground Truth and Generated even "
        "when they describe the exact same clinical facts — judge them by "
        "SEMANTIC / CLINICAL MEANING ONLY, never by exact wording.\n\n"
        "Decide whether Generated conveys the same clinical meaning as "
        "Ground Truth. Treat different phrasing, synonyms, sentence order, "
        "reordered details, abbreviations, and differences in verbosity or "
        "level of detail as a MATCH as long as no clinical fact actually "
        "conflicts. Only mark it not-a-match when Generated contradicts, "
        "omits, or changes a clinically meaningful fact from Ground Truth "
        "(different symptom, timing, severity, cause, diagnosis, or "
        "finding).\n\n"
        + "\n\n".join(
            f"{i + 1}. Field: {p['field']}\n"
            f"Ground Truth: {p['ground_truth']}\n"
            f"Generated: {p['generated']}"
            for i, p in enumerate(pairs)
        )
        + '\n\nReturn ONLY JSON of exactly this shape: '
        '{"results": [{"index": 1, "match": true|false}, ...]} '
        "with exactly one entry per numbered pair, in order, no extra text."
    )
    for attempt_model in _models_to_try(model):
        try:
            client, model_name = _get_client(attempt_model, config)
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a precise clinical QA reviewer. Return only JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1500,
                temperature=0,
            )
            raw = resp.choices[0].message.content or "{}"
            data = parse_ai_json(raw)
            by_index = {
                int(r["index"]): bool(r.get("match"))
                for r in (data.get("results") or [])
                if isinstance(r, dict) and r.get("index") is not None
            }
            return [by_index.get(i + 1, False) for i in range(len(pairs))]
        except Exception as exc:
            log.warning("SOAP_TEXT_VERIFY failed with %s: %s", attempt_model, exc)
            continue
    return [False] * len(pairs)


def compare_soap(
    soap_ground_truth: dict,
    soap_generated: dict,
    model: str,
    config: dict | None = None,
) -> dict[str, Any]:
    """Fact-level weighted SOAP score. Reuses gt_vs_generated, not a fourth comparator.

    Nested SOAP may call the LLM for section_details (NA vs Missing prompt).
    Flat {facts: [...]} skips the LLM so fixture tests stay deterministic.
    The weighted score always comes from soap_fact_scorer, not the LLM percent.
    """
    from medsum_testing.backend.services.soap_fact_scorer import (
        SCORING_METHOD,
        score_soap,
    )

    config = config or get_config()
    section_details = None
    llm_error = ""
    is_nested = _looks_nested_soap(soap_ground_truth) or _looks_nested_soap(soap_generated)
    if is_nested and SCORING_METHOD != "simple_key_match":
        llm = _llm_compare_soap(
            soap_ground_truth or {}, soap_generated or {}, model, config
        )
        section_details = llm.get("section_details") or None
        llm_error = llm.get("error") or ""
    scored = score_soap(
        soap_ground_truth,
        soap_generated,
        section_details=section_details,
        model=model,
        app_config=config,
    )
    if llm_error and not scored.get("facts"):
        scored["error"] = llm_error
    return scored


def compare_soap_three_way(
    soap_ground_truth: dict | None,
    soap_generated: dict | None,
    soap_raw: dict | None,
    model: str,
    config: dict,
) -> dict:
    """
    Three-way SOAP comparison (each pair uses the fact-level weighted scorer):
      gt_vs_generated  — GT vs final Flask output  (main SOAP accuracy)
      gt_vs_raw        — GT vs raw LLM output
      raw_vs_generated — raw vs final
    Transcription/translation scores are not mixed in.
    """
    results = {
        "gt_vs_generated":  None,
        "gt_vs_raw":        None,
        "raw_vs_generated": None,
        "scores": {
            "gt_vs_generated":  None,
            "gt_vs_raw":        None,
            "raw_vs_generated": None,
        }
    }

    if soap_ground_truth and soap_generated:
        results["gt_vs_generated"] = compare_soap(
            soap_ground_truth, soap_generated, model, config
        )
        results["scores"]["gt_vs_generated"] = (
            results["gt_vs_generated"].get("similarity_score")
        )

    if soap_ground_truth and soap_raw:
        results["gt_vs_raw"] = compare_soap(
            soap_ground_truth, soap_raw, model, config
        )
        results["scores"]["gt_vs_raw"] = (
            results["gt_vs_raw"].get("similarity_score")
        )

    if soap_raw and soap_generated:
        results["raw_vs_generated"] = compare_soap(
            soap_raw, soap_generated, model, config
        )
        results["scores"]["raw_vs_generated"] = (
            results["raw_vs_generated"].get("similarity_score")
        )

    return results


def compare_translations(
    ground_truth_translation: str,
    generated_translation: str,
    model: str,
    config: dict | None = None,
) -> dict[str, Any]:
    """
    Compare ground truth translation with generated translation.
    Both are plain English text.
    Same DeepSeek → GPT-4 fallback as other comparisons.
    Also attaches surface-metric quality scores (BLEU / chrF++ / TER / …).
    """
    config = config or get_config()
    if not ground_truth_translation or not generated_translation:
        metrics = compute_translation_metrics(
            generated_translation or "",
            ground_truth_translation or "",
        )
        return {
            "similarity_score": None,
            "overall_severity": "unknown",
            "differences": [],
            "error": "Missing ground truth or generated translation",
            "metrics": metrics,
            "quality_metrics": metrics,
        }

    system_prompt = """You are a medical AI evaluator comparing two English
translations of a doctor-patient conversation.
Return ONLY valid JSON. No markdown, no preamble.

IGNORE: punctuation, em-dashes, number format differences (150 vs one-fifty),
        minor wording differences that preserve meaning.
DO FLAG: missing medical information, incorrect medical terms,
         wrong drug names, wrong dosages, wrong diagnoses.

Schema:
{
  "similarity_score": <0-100>,
  "overall_severity": "none|low|medium|high|critical",
  "differences": [
    {
      "ground_truth": "<phrase from ground truth>",
      "generated":   "<phrase from generated>",
      "type":        "missing|incorrect|extra",
      "severity":    "low|medium|high|critical"
    }
  ],
  "summary": "<2 sentence verdict>"
}"""

    user_prompt = (
        f"Ground Truth Translation:\n{ground_truth_translation[:3000]}\n\n"
        f"Generated Translation:\n{generated_translation[:3000]}\n\n"
        "Compare and return JSON only."
    )

    models_to_try = _models_to_try(model)
    last_error = None
    result: dict[str, Any] | None = None

    for attempt_model in models_to_try:
        try:
            log.info("TRANS_COMPARE: trying model=%s", attempt_model)
            client, model_name = _get_client(attempt_model, config)
            resp = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=1000,
                temperature=0,
            )
            raw = resp.choices[0].message.content or "{}"
            result = parse_ai_json(raw)
            log.info(
                "TRANS_COMPARE ✓ model=%s score=%s",
                attempt_model,
                result.get("similarity_score"),
            )
            break
        except Exception as exc:
            log.warning("TRANS_COMPARE failed with %s: %s", attempt_model, exc)
            last_error = exc
            continue

    if result is None:
        result = {
            "similarity_score": None,
            "overall_severity": "unknown",
            "differences": [],
            "error": str(last_error),
        }

    metrics = compute_translation_metrics(
        generated_translation,
        ground_truth_translation,
        similarity_score=result.get("similarity_score"),
    )
    result["metrics"] = metrics
    result["quality_metrics"] = metrics
    return result


def compare_transcriptions(
    ground_truth: str,
    generated: str,
    model: str = "gpt-4o-mini",
    config: dict | None = None,
) -> ComparisonResult:
    config = config or get_config()

    if not ground_truth.strip():
        return ComparisonResult(
            skipped=True,
            skip_reason="No ground truth transcription available",
            summary="Accuracy scoring skipped",
        )
    if not generated.strip():
        return ComparisonResult(
            similarity_score=0,
            severity="high",
            summary="Generated transcription is empty",
            medical_differences=["No generated transcription produced"],
        )

    prompt = (
        "Compare ground truth transcription vs generated transcription.\n"
        "Focus on medical meaning only — ignore punctuation, em-dashes, spacing, "
        "and number format differences (digits vs words).\n"
        "Flag medical differences: drug names, dosages, frequencies, diagnoses, "
        "procedures, symptoms.\n\n"
        f"GROUND TRUTH:\n{ground_truth}\n\nGENERATED:\n{generated}"
    )

    models_to_try = _models_to_try(model)
    log.info("AI_COMPARE: will try models in order: %s", models_to_try)

    last_error = None
    for attempt_model in models_to_try:
        try:
            log.info("AI_COMPARE: attempting with model=%s", attempt_model)
            result = _to_comparison(_call_llm(prompt, attempt_model))
            log.info(
                "AI_COMPARE ✓ succeeded with model=%s score=%s",
                attempt_model,
                result.similarity_score,
            )
            return result
        except Exception as exc:
            log.warning("AI_COMPARE: model=%s failed: %s", attempt_model, exc)
            last_error = exc
            continue

    err_msg = f"All models failed. Last: {last_error}"
    log.error("AI_COMPARE: ALL models failed. Last error: %s", last_error)
    return ComparisonResult(
        severity="high",
        summary=err_msg,
        error=err_msg,
        general_differences=[str(last_error) if last_error else err_msg],
    )


def compare_summaries(
    previous_summary: Any, current_summary: Any, model: str = "gpt-4o-mini"
) -> ComparisonResult:
    if previous_summary is None or current_summary is None:
        return ComparisonResult(
            skipped=True,
            skip_reason="No previous summary for comparison",
        )

    prev = (
        previous_summary
        if isinstance(previous_summary, str)
        else json.dumps(previous_summary, ensure_ascii=False, indent=2)
    )
    curr = (
        current_summary
        if isinstance(current_summary, str)
        else json.dumps(current_summary, ensure_ascii=False, indent=2)
    )

    prompt = (
        "Compare previous summary vs current summary.\n"
        "Check: missing clinical info, incorrect info, structural differences.\n\n"
        f"PREVIOUS SUMMARY:\n{prev}\n\nCURRENT SUMMARY:\n{curr}"
    )
    try:
        return _to_comparison(_call_llm(prompt, model))
    except Exception as exc:
        return ComparisonResult(
            severity="medium",
            summary=f"Summary comparison failed: {exc}",
            general_differences=[str(exc)],
        )


def compare_regression(
    previous_transcription: str,
    current_transcription: str,
    model: str = "gpt-4o-mini",
) -> ComparisonResult:
    """Compare the previous run's transcription against the current one for degradation."""
    prev = (previous_transcription or "").strip()
    curr = (current_transcription or "").strip()
    if not prev:
        return ComparisonResult(
            skipped=True,
            skip_reason="No previous transcription for regression comparison",
        )
    if not curr:
        return ComparisonResult(
            similarity_score=0,
            severity="high",
            summary="Current transcription is empty while a previous run produced output",
            medical_differences=["No generated transcription produced in this run"],
        )

    prompt = (
        "Compare the previous test-run transcription (baseline) vs the current run "
        "to detect REGRESSION — medical information lost, newly incorrect, or newly added.\n"
        "This is not a ground-truth comparison; the previous run is the baseline.\n"
        "Set regression_vs_previous to better, worse, same, or not_applicable.\n\n"
        f"PREVIOUS TRANSCRIPTION (baseline):\n{prev}\n\n"
        f"CURRENT TRANSCRIPTION:\n{curr}"
    )
    try:
        return _to_comparison(_call_llm(prompt, model))
    except Exception as exc:
        return ComparisonResult(
            severity="medium",
            summary=f"Regression comparison failed: {exc}",
            general_differences=[str(exc)],
        )


def compare_medication_lists(
    before: Any,
    after_normalized: Any,
    generated: Any,
    model: str = "gpt-4o-mini",
) -> MedComparisonResult:
    if not any([before, after_normalized, generated]):
        return MedComparisonResult(
            skipped=True,
            skip_reason="No medication data available",
        )

    def _fmt(val: Any) -> str:
        if val is None:
            return "(none)"
        if isinstance(val, str):
            return val
        return json.dumps(val, ensure_ascii=False, indent=2)

    prompt = (
        "Compare medication lists. Identify added/removed/changed medicines, dosage changes, "
        "frequency changes, name differences.\n"
        "Include added, removed, changed arrays in your JSON response.\n\n"
        f"MEDICATIONS BEFORE:\n{_fmt(before)}\n\n"
        f"MEDICATIONS AFTER NORMALIZATION:\n{_fmt(after_normalized)}\n\n"
        f"GENERATED MEDICATIONS:\n{_fmt(generated)}"
    )
    try:
        data = _call_llm(prompt, model)
        medical = _format_medical_diffs(data.get("medical_differences") or [])
        severity = data.get("overall_severity") or data.get("severity") or "low"
        if severity == "none":
            severity = "low"
        return MedComparisonResult(
            added=data.get("added") or [],
            removed=data.get("removed") or [],
            changed=data.get("changed") or [],
            similarity_score=data.get("similarity_score"),
            medical_differences=medical,
            general_differences=data.get("general_differences") or [],
            severity=severity,
            summary=data.get("summary") or "",
        )
    except Exception as exc:
        return MedComparisonResult(
            severity="medium",
            summary=f"Medication comparison failed: {exc}",
            general_differences=[str(exc)],
        )


def _as_med_dict(item: Any) -> dict:
    if isinstance(item, dict):
        return item
    if isinstance(item, str):
        return {"drug_name": item}
    return {"drug_name": str(item)}


def _med_identity(med: dict) -> str:
    """Normalized drug identity for matching across reordered lists."""
    for field in ("matched_drug_name", "generic_name", "drug_name"):
        val = str(med.get(field) or "").strip().lower()
        if val and val not in ("na", "n/a", "none"):
            return val
    return ""


def validate_medications(transcription_result: dict) -> dict:
    """
    Cross-check plan.medications vs debug.raw_soap.plan.medications.
    Entries are paired by drug identity, not list index.
    """
    final_meds: list = []
    raw_meds: list = []

    try:
        final_meds = transcription_result.get("plan", {}).get("medications", [])
        if isinstance(final_meds, str):
            final_meds = []
        debug = transcription_result.get("debug") or {}
        raw_soap = debug.get("raw_soap") or debug.get("raw soap") or {}

        # Guard against error-only raw_soap
        if isinstance(raw_soap, dict) and "error" in raw_soap and len(raw_soap) == 1:
            raw_soap = {}

        raw_meds = raw_soap.get("plan", {}).get("medications", [])
        if isinstance(raw_meds, str):
            raw_meds = []
    except Exception:
        pass

    final_meds = [_as_med_dict(m) for m in (final_meds or [])]
    raw_meds = [_as_med_dict(m) for m in (raw_meds or [])]

    differences = []
    used_final: set[int] = set()

    def _compare_fields(raw: dict, final: dict) -> None:
        for field in ("drug_name", "dose", "schedule", "duration", "generic_name", "instructions"):
            raw_val = raw.get(field, "NA")
            final_val = final.get(field, "NA")
            if str(raw_val).strip() != str(final_val).strip():
                differences.append({
                    "type": "field_changed",
                    "drug": final.get("drug_name", raw.get("drug_name", "")),
                    "field": field,
                    "raw_value": raw_val,
                    "final_value": final_val,
                    "severity": "high" if field in ("drug_name", "dose") else "medium",
                    "detail": f"{field}: raw='{raw_val}' → final='{final_val}'",
                })

        if final.get("matched_drug_name") and final.get("drug_name"):
            if final["matched_drug_name"] != final["drug_name"]:
                differences.append({
                    "type": "name_normalized",
                    "drug": final["drug_name"],
                    "matched_to": final["matched_drug_name"],
                    "severity": "low",
                    "detail": (
                        f"Drug name normalized: '{final['drug_name']}' "
                        f"→ '{final['matched_drug_name']}'"
                    ),
                })

    for raw in raw_meds:
        key = _med_identity(raw)
        match_idx = None
        if key:
            for i, final in enumerate(final_meds):
                if i in used_final:
                    continue
                if _med_identity(final) == key:
                    match_idx = i
                    break
        if match_idx is None:
            differences.append({
                "type": "removed_in_final",
                "raw_drug": raw.get("drug_name", ""),
                "severity": "high",
                "detail": f"Drug '{raw.get('drug_name')}' present in raw but missing in final output",
            })
            continue
        used_final.add(match_idx)
        _compare_fields(raw, final_meds[match_idx])

    for i, final in enumerate(final_meds):
        if i in used_final:
            continue
        differences.append({
            "type": "added_in_final",
            "final_drug": final.get("drug_name", ""),
            "severity": "medium",
            "detail": f"Drug '{final.get('drug_name')}' added in final but not in raw",
        })

    return {
        "raw_medications": raw_meds,
        "final_medications": final_meds,
        "raw_count": len(raw_meds),
        "final_count": len(final_meds),
        "differences": differences,
        "has_critical_differences": any(d["severity"] == "high" for d in differences),
        "difference_count": len(differences),
    }


def compare_medications(transcription_result: dict) -> dict:
    """Alias for validate_medications — raw SOAP meds vs generated plan.medications."""
    return validate_medications(transcription_result)
