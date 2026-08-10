"""GPT-4 / DeepSeek comparison logic."""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from medsum_testing.backend.models.test_result import ComparisonResult, MedComparisonResult
from medsum_testing.backend.services.config_loader import get_config

SYSTEM_PROMPT = (
    "You are a medical AI evaluator. Compare the following texts and return ONLY valid JSON "
    "with no markdown, no preamble.\n"
    'Schema: { "similarity_score": 0-100, "medical_differences": [...], '
    '"general_differences": [...], "severity": "low|medium|high", "summary": "..." }'
)


def _get_client(model: str) -> tuple[OpenAI, str]:
    config = get_config()
    ai = config.get("ai_comparison", {})
    if model == "deepseek":
        return (
            OpenAI(api_key=ai.get("deepseek_api_key", ""), base_url=ai.get("deepseek_base_url")),
            "deepseek-chat",
        )
    return OpenAI(api_key=ai.get("openai_api_key", "")), "gpt-4o"


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def _call_llm(prompt: str, model: str) -> dict[str, Any]:
    client, model_name = _get_client(model)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )
    content = response.choices[0].message.content or "{}"
    return _parse_json_response(content)


def _to_comparison(data: dict[str, Any]) -> ComparisonResult:
    return ComparisonResult(
        similarity_score=data.get("similarity_score"),
        medical_differences=data.get("medical_differences") or [],
        general_differences=data.get("general_differences") or [],
        severity=data.get("severity") or "low",
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
        "Check: missing clinical info, incorrect info, structural differences, regression.\n\n"
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


def compare_medications(
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
        'Return JSON schema: { "similarity_score": 0-100, "medical_differences": [...], '
        '"general_differences": [...], "severity": "low|medium|high", "summary": "...", '
        '"added": [...], "removed": [...], "changed": [...] }\n\n'
        f"MEDICATIONS BEFORE:\n{_fmt(before)}\n\n"
        f"MEDICATIONS AFTER NORMALIZATION:\n{_fmt(after_normalized)}\n\n"
        f"GENERATED MEDICATIONS:\n{_fmt(generated)}"
    )
    try:
        data = _call_llm(prompt, model)
        return MedComparisonResult(
            added=data.get("added") or [],
            removed=data.get("removed") or [],
            changed=data.get("changed") or [],
            similarity_score=data.get("similarity_score"),
            medical_differences=data.get("medical_differences") or [],
            general_differences=data.get("general_differences") or [],
            severity=data.get("severity") or "low",
            summary=data.get("summary") or "",
        )
    except Exception as exc:
        return MedComparisonResult(
            severity="medium",
            summary=f"Medication comparison failed: {exc}",
            general_differences=[str(exc)],
        )
