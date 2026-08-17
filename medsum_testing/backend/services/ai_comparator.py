"""GPT-4 / DeepSeek comparison logic."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI

from medsum_testing.backend.models.test_result import ComparisonResult, MedComparisonResult
from medsum_testing.backend.services.config_loader import get_config

log = logging.getLogger("medsum_ai")

SYSTEM_PROMPT = """You are a medical AI evaluator comparing two medical
transcriptions. Return ONLY valid JSON — no markdown, no preamble.

IMPORTANT RULES FOR COMPARISON:
- Do NOT flag differences in punctuation (commas, full stops, em-dashes, hyphens)
- Do NOT flag differences between digit form and written form of the same number
  (e.g. "150" vs "one hundred fifty" = SAME, "2" vs "two" = SAME)
- Do NOT flag differences in spacing or line breaks
- Do NOT flag differences in capitalisation of non-medical terms
- DO flag differences in: drug names, dosages, diagnoses, procedures,
  symptoms, medical terminology, frequencies, durations
- A missing or extra em-dash, comma, or full stop is NOT a medical difference

Schema:
{
  "similarity_score": <0-100, where 100 = identical meaning>,
  "medical_differences": [
    {
      "type": "dosage|medication_name|diagnosis|procedure|frequency|symptom|duration",
      "ground_truth": "<exact phrase from ground truth>",
      "generated": "<exact phrase from generated>",
      "severity": "critical|high|medium|low"
    }
  ],
  "general_differences": [
    "<only non-trivial wording differences — NOT punctuation or number format>"
  ],
  "overall_severity": "critical|high|medium|low|none",
  "regression_vs_previous": "<better|worse|same|not_applicable>",
  "summary": "<2 sentence verdict focusing on medical accuracy only>"
}"""


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


def compare_soap(
    soap_ground_truth: dict,
    soap_generated: dict,
    model: str,
    config: dict | None = None,
) -> dict[str, Any]:
    """
    Compare ground truth SOAP with generated SOAP section by section.
    Returns similarity score, per-section differences, and severity.
    Falls back to GPT-4 if DeepSeek fails.
    """
    config = config or get_config()
    if not soap_ground_truth or not soap_generated:
        return {
            "similarity_score": None,
            "overall_severity": "unknown",
            "section_details": {},
            "error": "Missing ground truth or generated SOAP",
        }

    system_prompt = """You are a medical AI evaluator comparing two SOAP notes.
Return ONLY valid JSON. No markdown, no preamble.

Compare section by section. For each section identify:
- Missing information (in ground truth but not in generated)
- Incorrect information (different values for same field)
- Extra information (in generated but not in ground truth)

IGNORE: punctuation differences, em-dashes, number format (150 vs one-fifty),
        capitalisation of non-medical terms, "NA" vs blank.

Schema:
{
  "similarity_score": <0-100>,
  "overall_severity": "none|low|medium|high|critical",
  "summary": "<2 sentence verdict>",
  "section_details": {
    "subjective": {
      "score": <0-100>,
      "differences": [
        {
          "field": "chief_complaint",
          "ground_truth": "<value>",
          "generated": "<value>",
          "type": "missing|incorrect|extra",
          "severity": "low|medium|high|critical"
        }
      ]
    },
    "objective":  { "score": <0-100>, "differences": [] },
    "assessment": { "score": <0-100>, "differences": [] },
    "plan":       { "score": <0-100>, "differences": [] }
  }
}"""

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
                    {"role": "system", "content": system_prompt},
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
        "similarity_score": None,
        "overall_severity": "unknown",
        "section_details": {},
        "error": str(last_error),
    }


def compare_soap_three_way(
    soap_ground_truth: dict | None,
    soap_generated: dict | None,
    soap_raw: dict | None,
    model: str,
    config: dict,
) -> dict:
    """
    Three-way SOAP comparison:
      gt_vs_generated  — GT vs final Flask output  (main accuracy)
      gt_vs_raw        — GT vs raw LLM output      (raw accuracy)
      raw_vs_generated — raw vs final              (post-processing delta)
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
    """
    config = config or get_config()
    if not ground_truth_translation or not generated_translation:
        return {
            "similarity_score": None,
            "overall_severity": "unknown",
            "differences": [],
            "error": "Missing ground truth or generated translation",
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
            return result
        except Exception as exc:
            log.warning("TRANS_COMPARE failed with %s: %s", attempt_model, exc)
            last_error = exc
            continue

    return {
        "similarity_score": None,
        "overall_severity": "unknown",
        "differences": [],
        "error": str(last_error),
    }


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
