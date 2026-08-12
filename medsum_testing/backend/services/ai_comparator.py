"""GPT-4 / DeepSeek comparison logic."""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from medsum_testing.backend.models.test_result import ComparisonResult, MedComparisonResult
from medsum_testing.backend.services.config_loader import get_config

SYSTEM_PROMPT = """You are a medical AI evaluator. Compare the provided texts and return
ONLY valid JSON. No markdown, no explanation, no preamble. Raw JSON only.

Schema:
{
  "similarity_score": <0-100>,
  "medical_differences": [
    {
      "type": "dosage|medication_name|diagnosis|procedure|frequency|investigation|symptom",
      "ground_truth": "<exact text>",
      "generated": "<exact text>",
      "severity": "critical|high|medium|low"
    }
  ],
  "general_differences": ["<plain text difference>"],
  "overall_severity": "critical|high|medium|low|none",
  "regression_vs_previous": "<better|worse|same|not_applicable>",
  "summary": "<2 sentence human-readable verdict>"
}"""


def get_ai_client(model: str, config: dict) -> tuple[OpenAI, str]:
    ai = config.get("ai_comparison", {})
    if model == "deepseek":
        client = OpenAI(
            api_key=ai.get("deepseek_api_key", ""),
            base_url=ai.get("deepseek_base_url", "https://api.deepseek.com/v1"),
        )
        return client, "deepseek-chat"
    return OpenAI(api_key=ai.get("openai_api_key", "")), "gpt-4"


def parse_ai_json(raw: str) -> dict[str, Any]:
    clean = re.sub(r"```json|```", "", raw).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


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
        general_differences=data.get("general_differences") or [],
        severity=severity,
        summary=data.get("summary") or "",
    )


def compare_transcriptions(
    ground_truth: str, generated: str, model: str = "gpt-4"
) -> ComparisonResult:
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
        "Identify word-level differences. Flag medical differences: drug names, dosages, "
        "frequencies, diagnoses, procedures.\n\n"
        f"GROUND TRUTH:\n{ground_truth}\n\nGENERATED:\n{generated}"
    )
    try:
        return _to_comparison(_call_llm(prompt, model))
    except Exception as exc:
        return ComparisonResult(
            severity="high",
            summary=f"AI comparison failed: {exc}",
            general_differences=[str(exc)],
        )


def compare_summaries(
    previous_summary: Any, current_summary: Any, model: str = "gpt-4"
) -> ComparisonResult:
    if previous_summary is None or current_summary is None:
        return ComparisonResult(
            skipped=True,
            skip_reason="No previous summary for regression comparison",
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
        "Compare previous summary vs current summary for regression.\n"
        "Check: missing clinical info, incorrect info, structural differences, regression.\n"
        "Set regression_vs_previous accordingly.\n\n"
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


def compare_medication_lists(
    before: Any,
    after_normalized: Any,
    generated: Any,
    model: str = "gpt-4",
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


def validate_medications(transcription_result: dict) -> dict:
    """
    Cross-check plan.medications vs debug.raw soap.plan.medications.
    """
    final_meds: list = []
    raw_meds: list = []

    try:
        final_meds = transcription_result.get("plan", {}).get("medications", [])
        if isinstance(final_meds, str):
            final_meds = []
        raw_meds = (
            transcription_result
            .get("debug", {})
            .get("raw soap", {})
            .get("plan", {})
            .get("medications", [])
        )
        if isinstance(raw_meds, str):
            raw_meds = []
    except Exception:
        pass

    differences = []
    max_len = max(len(final_meds), len(raw_meds))

    for i in range(max_len):
        final = final_meds[i] if i < len(final_meds) else None
        raw = raw_meds[i] if i < len(raw_meds) else None

        if final is None and raw is not None:
            differences.append({
                "type": "removed_in_final",
                "raw_drug": raw.get("drug_name", ""),
                "severity": "high",
                "detail": f"Drug '{raw.get('drug_name')}' present in raw but missing in final output",
            })
            continue

        if raw is None and final is not None:
            differences.append({
                "type": "added_in_final",
                "final_drug": final.get("drug_name", ""),
                "severity": "medium",
                "detail": f"Drug '{final.get('drug_name')}' added in final but not in raw",
            })
            continue

        if final is None or raw is None:
            continue

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

    return {
        "raw_medications": raw_meds,
        "final_medications": final_meds,
        "raw_count": len(raw_meds),
        "final_count": len(final_meds),
        "differences": differences,
        "has_critical_differences": any(d["severity"] == "high" for d in differences),
        "difference_count": len(differences),
    }
