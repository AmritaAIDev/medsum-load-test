"""Per-case accuracy tooltip. Explains the existing score — does not recompute it.

What the code actually does (test_runner + ai_comparator):
- The displayed overall accuracy_score IS the transcription LLM similarity
  (0–100, medical meaning). Translation and SOAP scores are stored separately
  and are NOT averaged into that percentage.
- Scoring is an LLM judgment, not near-exact / normalized string similarity.
- A missing transcription ground truth skips scoring (NOT_SCORED /
  complete_no_accuracy). An empty *generated* transcript is scored 0.

ARCHITECTURE.md §5 matches the transcription-primary overall score.
There is no §12; §8 is Drive layout; §11 is the compare-vs-GT sequence.
"""

from __future__ import annotations

from typing import Any

from medsum_testing.backend.models.test_result import is_execution_error

NOT_SCORED = "NOT_SCORED"

# Criteria the transcription scorer is told to flag (ai_comparator SYSTEM_PROMPT).
TRANSCRIPTION_CRITERIA = (
    "drug names",
    "dosages",
    "diagnoses",
    "procedures",
    "symptoms",
    "frequencies",
    "durations",
)

METHOD_BLURB = (
    "LLM medical-meaning similarity (0–100). Not exact text match — "
    "punctuation, number format, and spacing are ignored."
)

OVERALL_IS = "Overall % is the transcription score only."

DETAIL_HINT = "Open the test case for the full breakdown."


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _first_error(result: dict) -> str:
    errors = result.get("errors") or []
    if isinstance(errors, str):
        return errors.strip()
    for item in errors:
        text = _text(item)
        if text and "traceback" not in text.lower():
            return text.splitlines()[0]
    return ""


def _score(value: Any):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _comp(result: dict, *keys: str) -> dict:
    data = result or {}
    for key in keys:
        val = data.get(key)
        if isinstance(val, dict):
            return val
    return {}


def _has_gt_text(*values: Any) -> bool:
    return any(_text(v) for v in values)


def _diff_types(comp: dict) -> list[str]:
    details = comp.get("medical_difference_details") or []
    types: list[str] = []
    seen: set[str] = set()
    for item in details:
        if isinstance(item, dict):
            kind = _text(item.get("type")).replace("_", " ")
        else:
            kind = ""
        if kind and kind not in seen:
            seen.add(kind)
            types.append(kind)
    if types:
        return types
    diffs = comp.get("medical_differences") or []
    if diffs:
        return [f"{len(diffs)} listed"]
    return []


def _piece(
    name: str,
    compared: str,
    score,
    *,
    not_scored: bool,
    reason: str,
    criteria: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    if not_scored or score is None:
        return {
            "name": name,
            "compared": compared,
            "status": NOT_SCORED,
            "score": None,
            "reason": reason or "No ground truth for this field",
            "criteria": list(criteria),
        }
    return {
        "name": name,
        "compared": compared,
        "status": "scored",
        "score": score,
        "reason": "",
        "criteria": list(criteria),
    }


def _transcription_piece(result: dict) -> dict[str, Any]:
    comp = _comp(result, "transcription_comparison", "comparison")
    score = _score(
        result.get("accuracy_score")
        or comp.get("similarity_score")
        or result.get("similarity_score")
    )
    skipped = bool(
        result.get("accuracy_skipped")
        or comp.get("skipped")
        or (result.get("final_result") == "complete_no_accuracy")
    )
    skip_reason = _text(
        result.get("accuracy_skip_reason")
        or comp.get("skip_reason")
        or (comp.get("summary") if skipped else "")
    )
    gt_missing = skipped or not (
        result.get("has_ground_truth")
        or _has_gt_text(
            result.get("ground_truth"),
            result.get("ground_truth_transcription"),
        )
        or score is not None
    )
    # Empty generated transcript is scored 0 in compare_transcriptions — keep 0%.
    if score is not None and not skipped:
        gt_missing = False
    reason = skip_reason or "No ground truth transcript found for this audio"
    return _piece(
        "Transcription",
        "generated transcript vs ground-truth transcript",
        None if gt_missing else score,
        not_scored=gt_missing,
        reason=reason,
        criteria=TRANSCRIPTION_CRITERIA,
    )


def _translation_piece(result: dict) -> dict[str, Any]:
    comp = _comp(result, "translation_comparison")
    score = _score(comp.get("similarity_score") or result.get("translation_score"))
    lang = _text(result.get("language")).lower()
    english = lang in {"english", "en"}
    gt = _has_gt_text(
        result.get("translation_ground_truth"),
        result.get("ground_truth_translation"),
    )
    if english:
        gt = gt or _has_gt_text(
            result.get("ground_truth"),
            result.get("ground_truth_transcription"),
        )
    explicit = result.get("has_translation_ground_truth")
    gt_missing = explicit is False or (
        score is None and not gt and not (comp.get("skipped") is False and score is not None)
    )
    if score is not None:
        gt_missing = False
    error = _text(comp.get("error") or comp.get("skip_reason"))
    if "missing ground truth" in error.lower():
        gt_missing = True
        score = None
    compared = (
        "generated translation vs ground-truth transcript (English)"
        if english
        else "generated translation vs ground-truth translation"
    )
    reason = error or (
        "No translation ground truth found for this audio"
        if not english
        else "No transcription ground truth to use as English translation GT"
    )
    return _piece(
        "Translation",
        compared,
        score,
        not_scored=gt_missing or score is None,
        reason=reason,
        criteria=("medical terms", "drug names", "dosages", "diagnoses"),
    )


def _soap_piece(result: dict) -> dict[str, Any]:
    soap = _comp(result, "soap_comparison")
    scores = soap.get("scores") if isinstance(soap.get("scores"), dict) else {}
    gt_vs_gen = soap.get("gt_vs_generated") if isinstance(soap.get("gt_vs_generated"), dict) else {}
    score = _score(
        scores.get("gt_vs_generated")
        or gt_vs_gen.get("similarity_score")
        or result.get("soap_score")
    )
    explicit = result.get("has_soap_ground_truth")
    gt_missing = explicit is False or (
        score is None and not result.get("soap_ground_truth")
    )
    if score is not None:
        gt_missing = False
    reason = _text(soap.get("skip_reason")) or "No SOAP ground truth found for this audio"
    return _piece(
        "SOAP",
        "generated SOAP vs ground-truth SOAP "
        "(subjective, objective, assessment, plan)",
        score,
        not_scored=gt_missing or score is None,
        reason=reason,
        criteria=(
            "Correct",
            "Incorrect",
            "Missing",
            "Hallucination",
        ),
    )


def _why_this_case(result: dict, transcription: dict) -> str:
    if transcription.get("status") == NOT_SCORED:
        return transcription.get("reason") or "Transcription was not scored."
    comp = _comp(result, "transcription_comparison", "comparison")
    types = _diff_types(comp)
    score = transcription.get("score")
    if types:
        n = len(comp.get("medical_difference_details") or comp.get("medical_differences") or types)
        return f"{n} medical difference(s): {', '.join(types)}."
    summary = _text(comp.get("summary"))
    if summary:
        first = summary.split(".")[0].strip()
        return (first[:160] + "…") if len(first) > 160 else first
    if score is not None:
        return f"Transcription similarity {int(round(score))}%."
    return ""


def build_accuracy_tooltip(result: dict | None, *, focus: str = "overall") -> dict[str, Any]:
    """Structured tooltip for one case. `focus` is overall|transcription|translation|soap."""
    data = result or {}
    if is_execution_error(data.get("status"), data.get("final_result")):
        reason = (
            _text(data.get("accuracy_skip_reason"))
            or _first_error(data)
            or "This case did not produce output — no SOAP evaluation."
        )
        empty = _piece(
            "SOAP",
            "no generated output to evaluate",
            None,
            not_scored=True,
            reason=reason,
            criteria=("Correct", "Incorrect", "Missing", "Hallucination"),
        )
        transcription = _piece(
            "Transcription",
            "no generated output to evaluate",
            None,
            not_scored=True,
            reason=reason,
            criteria=TRANSCRIPTION_CRITERIA,
        )
        translation = _piece(
            "Translation",
            "no generated output to evaluate",
            None,
            not_scored=True,
            reason=reason,
            criteria=("medical terms", "drug names", "dosages", "diagnoses"),
        )
        return {
            "focus": (focus or "overall").strip().lower(),
            "overall_score": None,
            "overall_status": NOT_SCORED,
            "compared": "no generated output to evaluate",
            "criteria": list(TRANSCRIPTION_CRITERIA),
            "method": METHOD_BLURB,
            "overall_note": OVERALL_IS,
            "pieces": [transcription, translation, empty],
            "why": reason,
            "hint": DETAIL_HINT,
            "not_scored_present": True,
        }
    transcription = _transcription_piece(data)
    translation = _translation_piece(data)
    soap = _soap_piece(data)
    pieces = [transcription, translation, soap]
    focus_key = (focus or "overall").strip().lower()
    focused = {
        "transcription": transcription,
        "translation": translation,
        "soap": soap,
    }.get(focus_key, transcription)

    overall_score = transcription.get("score")
    overall_status = transcription.get("status")
    return {
        "focus": focus_key,
        "overall_score": overall_score,
        "overall_status": overall_status,
        "compared": focused.get("compared") if focus_key != "overall" else transcription["compared"],
        "criteria": focused.get("criteria") if focus_key != "overall" else list(TRANSCRIPTION_CRITERIA),
        "method": METHOD_BLURB,
        "overall_note": OVERALL_IS,
        "pieces": pieces,
        "why": _why_this_case(data, transcription),
        "hint": DETAIL_HINT,
        "not_scored_present": any(p["status"] == NOT_SCORED for p in pieces),
    }


def format_piece_line(piece: dict) -> str:
    if piece.get("status") == NOT_SCORED:
        return f"{piece['name']}: {NOT_SCORED} — {piece.get('reason') or 'no ground truth'}"
    score = piece.get("score")
    shown = int(round(score)) if score is not None else "—"
    return f"{piece['name']}: {shown}%"


def tooltip_plain_text(model: dict) -> str:
    """Plain-text form for tests and non-HTML callers."""
    lines = [
        f"Compared: {model.get('compared')}",
        "Evaluated: " + ", ".join(model.get("criteria") or []),
        f"Method: {model.get('method')}",
        model.get("overall_note") or "",
    ]
    lines.extend(format_piece_line(p) for p in model.get("pieces") or [])
    if model.get("why"):
        lines.append(f"This case: {model['why']}")
    lines.append(model.get("hint") or DETAIL_HINT)
    return "\n".join(line for line in lines if line)


def overall_average_tooltip() -> dict[str, Any]:
    """Explains the dashboard / run 'Avg Accuracy' figure — not a single case."""
    return {
        "focus": "average",
        "overall_score": None,
        "overall_status": "scored",
        "compared": "each case's generated transcript vs that case's ground-truth transcript",
        "criteria": list(TRANSCRIPTION_CRITERIA),
        "method": METHOD_BLURB,
        "overall_note": (
            "Avg Accuracy is the mean of scored transcription similarities. "
            "NOT_SCORED cases (no transcription ground truth) are omitted — not counted as 0%."
        ),
        "pieces": [],
        "why": "",
        "hint": "Open a test case for that case's field-level breakdown.",
        "not_scored_present": True,
    }
