#!/usr/bin/env python3
"""Aggregate SOAP accuracy investigation report from results/*.json."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
sys.path.insert(0, str(ROOT))

from medsum_testing.backend.services.accuracy_by_category import (  # noqa: E402
    infer_error_tag,
    resolve_clinical_category,
)
from medsum_testing.backend.services.soap_fact_scorer import (  # noqa: E402
    CORRECT,
    HALLUCINATION,
    INCORRECT,
    MISSING,
    NA,
    load_scoring_config,
)

_WORD_RE = re.compile(r"[a-z0-9]+", re.I)
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_UNIT_RE = re.compile(
    r"(°\s*[CF]|deg(?:ree)?s?\s*[CF]?|\b(?:mg|mcg|µg|ug|g|ml|mmhg|cm|kg|bpm|%|iu)\b)",
    re.I,
)


def _text(v: Any) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    return str(v).strip()


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _mean(vals: list[float]) -> float | None:
    return round(sum(vals) / len(vals), 2) if vals else None


def _pct(n: float, d: float, places: int = 1) -> float | None:
    return round(100.0 * n / d, places) if d else None


def _status(acc: float | None) -> str:
    if acc is None:
        return "N/A"
    if acc >= 85:
        return "HIGH"
    if acc >= 70:
        return "MEDIUM"
    return "LOW"


def _section_status(acc: float | None) -> str:
    if acc is None:
        return "N/A"
    if acc < 50:
        return "CRITICAL"
    if acc < 75:
        return "WARNING"
    return "OK"


def classify_error_shape(fact: dict) -> str:
    """Heuristic structural/content error category for investigation."""
    result = _text(fact.get("result") or fact.get("internal"))
    gt = _text(fact.get("ground_truth") if "ground_truth" in fact else fact.get("value"))
    gen = _text(fact.get("generated") or fact.get("value"))
    field = _text(fact.get("base_field") or fact.get("field")).lower()

    if result == HALLUCINATION:
        return "Extra/Hallucinated Fields"
    if result == MISSING:
        return "Missing Fields"
    if result != INCORRECT:
        return "Other"

    # Type-ish: numeric vs non-numeric mismatch patterns
    gt_nums = _NUM_RE.findall(gt)
    gen_nums = _NUM_RE.findall(gen)
    gt_has_unit = bool(_UNIT_RE.search(gt))
    gen_has_unit = bool(_UNIT_RE.search(gen))

    if gt_nums and gen_nums and gt_nums != gen_nums:
        if "dose" in field or "schedule" in field or "bp" in field or "blood" in field or "temp" in field or "pulse" in field or "rate" in field:
            return "Value Mismatches"
        return "Value Mismatches"
    if (gt_has_unit != gen_has_unit) and (gt_nums or gen_nums):
        return "Format Mismatches"
    if gt and gen and gt.lower() == gen.lower():
        return "Format Mismatches"
    if (gt in ("", "null", "None") and gen) or (gen in ("null", "None") and gt):
        return "Null vs Empty String"
    if ("[" in gt) != ("[" in gen) or ("{" in gt) != ("{" in gen):
        return "Array Handling"
    # Partial overlap → partial capture / value mismatch
    gt_words = set(_WORD_RE.findall(gt.lower()))
    gen_words = set(_WORD_RE.findall(gen.lower()))
    if gt_words and gen_words:
        overlap = len(gt_words & gen_words) / len(gt_words | gen_words)
        if overlap >= 0.4:
            return "Value Mismatches"
    return "Value Mismatches"


def soap_pair(data: dict) -> dict:
    sc = data.get("soap_comparison")
    if not isinstance(sc, dict):
        return {}
    pair = sc.get("gt_vs_generated")
    return pair if isinstance(pair, dict) else {}


def load_runs() -> list[dict]:
    runs = []
    for path in sorted(RESULTS.glob("*.json")):
        if path.name.startswith("."):
            continue
        try:
            data = json.load(path.open(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        data["_path"] = path.name
        runs.append(data)
    return runs


def main() -> None:
    runs = load_runs()
    scoring_cfg = load_scoring_config()

    # Field-type accuracy (per-run scores)
    transcript_scores: list[float] = []
    translation_scores: list[float] = []
    soap_scores: list[float] = []
    soap_pass = soap_fail = soap_review = 0
    trans_pass = trans_fail = trans_review = 0
    transl_pass = transl_fail = transl_review = 0

    # SOAP section / field stats
    section_counts: dict[str, Counter] = defaultdict(Counter)
    field_counts: dict[str, Counter] = defaultdict(Counter)  # section|field
    field_examples: dict[str, list] = defaultdict(list)
    error_shapes = Counter()
    error_tags = Counter()
    clinical_cats: dict[str, Counter] = defaultdict(Counter)
    by_lang: dict[str, dict[str, list]] = defaultdict(lambda: {"soap": [], "trans": [], "transl": []})
    by_model: dict[str, dict[str, list]] = defaultdict(lambda: {"soap": [], "trans": [], "transl": []})
    section_scores_from_details: dict[str, list[float]] = defaultdict(list)

    # Individual diagnosis/medication if present as separate comparisons
    diagnosis_scores: list[float] = []
    med_scores: list[float] = []
    structured_scores: list[float] = []

    runs_with_soap_gt = 0
    fact_total = 0
    error_examples_by_shape: dict[str, list] = defaultdict(list)
    subsection_error_shapes: dict[str, Counter] = defaultdict(Counter)

    for run in runs:
        pair = soap_pair(run)
        facts = pair.get("facts") if isinstance(pair.get("facts"), list) else []
        soap_score = _f(pair.get("overall_weighted_clinical_score") or pair.get("similarity_score"))
        trans = _f((run.get("comparison") or {}).get("similarity_score") or run.get("similarity_score"))
        transl_comp = run.get("translation_comparison") or {}
        transl = _f(
            transl_comp.get("similarity_score")
            if isinstance(transl_comp, dict)
            else None
        ) or _f(run.get("translation_score"))

        lang = _text(run.get("language") or run.get("audio_language")) or "Unknown"
        model = _text(run.get("ai_model_used") or run.get("ai_model") or run.get("llm_model")) or "Unknown"

        if soap_score is not None and (facts or run.get("has_soap_ground_truth")):
            soap_scores.append(soap_score)
            runs_with_soap_gt += 1
            by_lang[lang]["soap"].append(soap_score)
            by_model[model]["soap"].append(soap_score)
            # Band using typical thresholds
            if soap_score >= 85:
                soap_pass += 1
            elif soap_score >= 70:
                soap_review += 1
            else:
                soap_fail += 1

        if trans is not None:
            transcript_scores.append(trans)
            by_lang[lang]["trans"].append(trans)
            by_model[model]["trans"].append(trans)
            if trans >= 85:
                trans_pass += 1
            elif trans >= 70:
                trans_review += 1
            else:
                trans_fail += 1

        if transl is not None:
            translation_scores.append(transl)
            by_lang[lang]["transl"].append(transl)
            by_model[model]["transl"].append(transl)
            if transl >= 85:
                transl_pass += 1
            elif transl >= 70:
                transl_review += 1
            else:
                transl_fail += 1

        # Optional individual structured scores
        for key, bucket in (
            ("diagnosis_comparison", diagnosis_scores),
            ("medication_comparison", med_scores),
            ("structured_comparison", structured_scores),
            ("summary_comparison", structured_scores),
        ):
            block = run.get(key)
            if isinstance(block, dict):
                s = _f(block.get("similarity_score"))
                if s is not None:
                    bucket.append(s)

        # Section scores from LLM section_details when present
        details = pair.get("section_details") if isinstance(pair.get("section_details"), dict) else {}
        for sec, detail in details.items():
            if isinstance(detail, dict):
                s = _f(detail.get("score"))
                if s is not None:
                    section_scores_from_details[sec.lower()].append(s)

        for fact in facts:
            if not isinstance(fact, dict):
                continue
            result = _text(fact.get("result") or fact.get("internal")) or "Unknown"
            if result == NA:
                continue
            fact_total += 1
            section = _text(fact.get("section")) or "Unknown"
            field = _text(fact.get("base_field") or fact.get("field")) or "Unknown"
            key = f"{section}|{field}"
            section_counts[section][result] += 1
            field_counts[key][result] += 1

            cat = resolve_clinical_category(fact, scoring_cfg) or "Unmapped"
            clinical_cats[cat][result] += 1

            if result in (INCORRECT, MISSING, HALLUCINATION):
                shape = classify_error_shape(fact)
                error_shapes[shape] += 1
                subsection_error_shapes[section][shape] += 1
                tag = infer_error_tag(fact, result, cat)
                if tag:
                    error_tags[tag] += 1
                if len(error_examples_by_shape[shape]) < 8:
                    error_examples_by_shape[shape].append({
                        "section": section,
                        "field": field,
                        "result": result,
                        "gt": _text(fact.get("ground_truth"))[:180],
                        "gen": _text(fact.get("generated"))[:180],
                        "file": run.get("_path"),
                        "lang": lang,
                        "model": model,
                        "tag": tag,
                    })
                if len(field_examples[key]) < 5:
                    field_examples[key].append({
                        "result": result,
                        "gt": _text(fact.get("ground_truth"))[:160],
                        "gen": _text(fact.get("generated"))[:160],
                        "shape": shape,
                        "lang": lang,
                        "model": model,
                    })

    def rate(pass_n, fail_n, review_n):
        total = pass_n + fail_n + review_n
        if not total:
            return None, None, None
        return (
            _pct(pass_n, total),
            _pct(fail_n, total),
            _pct(review_n, total),
        )

    soap_acc = _mean(soap_scores)
    trans_acc = _mean(transcript_scores)
    transl_acc = _mean(translation_scores)
    diag_acc = _mean(diagnosis_scores)
    med_acc = _mean(med_scores)
    struct_acc = _mean(structured_scores)

    soap_pr, soap_fr, soap_rr = rate(soap_pass, soap_fail, soap_review)
    trans_pr, trans_fr, trans_rr = rate(trans_pass, trans_fail, trans_review)
    transl_pr, transl_fr, transl_rr = rate(transl_pass, transl_fail, transl_review)

    lines: list[str] = []
    w = lines.append

    w("=" * 78)
    w("SOAP ACCURACY INVESTIGATION REPORT")
    w("=" * 78)
    w(f"Results analyzed: {len(runs)} files")
    w(f"Runs with SOAP GT + score: {runs_with_soap_gt}")
    w(f"Scored clinical facts (excl. NA): {fact_total}")
    w("")

    # Phase 1.1
    w("ACCURACY COMPARISON ACROSS FIELD TYPES:")
    w("=" * 70)
    w(f"{'Field Type':<24} | {'Accuracy':>8} | {'Pass':>7} | {'Fail':>7} | {'Review':>7} | Status")
    w("-" * 70)

    def row(name, acc, pr, fr, rr, n):
        a = f"{acc:.1f}%" if acc is not None else "n/a"
        p = f"{pr:.0f}%" if pr is not None else "n/a"
        f_ = f"{fr:.0f}%" if fr is not None else "n/a"
        r = f"{rr:.0f}%" if rr is not None else "n/a"
        st = _status(acc)
        mark = "HIGH" if st == "HIGH" else ("MEDIUM" if st == "MEDIUM" else "LOW")
        w(f"{name:<24} | {a:>8} | {p:>7} | {f_:>7} | {r:>7} | {mark} (n={n})")

    row("Transcript", trans_acc, trans_pr, trans_fr, trans_rr, len(transcript_scores))
    row("Translation", transl_acc, transl_pr, transl_fr, transl_rr, len(translation_scores))
    row("Diagnosis (individual)", diag_acc, None, None, None, len(diagnosis_scores))
    row("Medications (individual)", med_acc, None, None, None, len(med_scores))
    row("SOAP (Complete)", soap_acc, soap_pr, soap_fr, soap_rr, len(soap_scores))
    row("Structured Output", struct_acc, None, None, None, len(structured_scores))
    w("")
    w("GAP ANALYSIS:")
    if soap_acc is not None and trans_acc is not None:
        w(f"  Transcript → SOAP: {soap_acc - trans_acc:+.1f}%")
    if soap_acc is not None and transl_acc is not None:
        w(f"  Translation → SOAP: {soap_acc - transl_acc:+.1f}%")
    if soap_acc is not None and struct_acc is not None:
        w(f"  Structured → SOAP: {soap_acc - struct_acc:+.1f}%")
    w("")

    # Phase 1.2 — fact-level accuracy by section
    w("SOAP SUBSECTION ACCURACY BREAKDOWN (fact-level Correct/GT):")
    w("=" * 70)

    section_order = ["Subjective", "Objective", "Assessment", "Plan", "Summary"]
    section_accs: dict[str, float | None] = {}
    for sec in section_order + sorted(set(section_counts) - set(section_order)):
        c = section_counts.get(sec) or Counter()
        gt = c[CORRECT] + c[INCORRECT] + c[MISSING]
        # hallucinations not in GT denom for accuracy_percent style
        acc = _pct(c[CORRECT], gt)
        section_accs[sec] = acc
        pass_like = _pct(c[CORRECT], gt)  # rough
        w("")
        w(f"{sec.upper()} SECTION:")
        w(f"  Overall Accuracy: {acc if acc is not None else 'n/a'}%")
        w(f"  Correct={c[CORRECT]} Incorrect={c[INCORRECT]} Missing={c[MISSING]} Hallucination={c[HALLUCINATION]} GT={gt}")
        w(f"  Status: {_section_status(acc)}")
        # field breakdown
        fields = [(k, v) for k, v in field_counts.items() if k.startswith(sec + "|")]
        field_rows = []
        for key, counts in fields:
            field = key.split("|", 1)[1]
            fgt = counts[CORRECT] + counts[INCORRECT] + counts[MISSING]
            facc = _pct(counts[CORRECT], fgt)
            field_rows.append((field, facc, counts, fgt))
        field_rows.sort(key=lambda x: (x[1] is None, x[1] if x[1] is not None else 999))
        w("  Field-by-Field:")
        for field, facc, counts, fgt in field_rows:
            a = f"{facc}%" if facc is not None else "n/a"
            w(
                f"    - {field}: {a} "
                f"(C={counts[CORRECT]} I={counts[INCORRECT]} M={counts[MISSING]} H={counts[HALLUCINATION]} GT={fgt})"
            )
        worst = [f"{f} ({a}%)" for f, a, _, g in field_rows if g > 0][:5]
        w(f"  Worst Performers: {', '.join(worst) if worst else 'n/a'}")

        if sec.lower() in section_scores_from_details:
            w(f"  LLM section_details mean score: {_mean(section_scores_from_details[sec.lower()])}%")

    w("")
    w("CLINICAL CATEGORY ACCURACY (dashboard categories):")
    for cat in sorted(clinical_cats):
        c = clinical_cats[cat]
        gt = c[CORRECT] + c[INCORRECT] + c[MISSING]
        w(
            f"  {cat}: {_pct(c[CORRECT], gt)}% "
            f"(C={c[CORRECT]} I={c[INCORRECT]} M={c[MISSING]} H={c[HALLUCINATION]})"
        )

    # Phase 2
    w("")
    w("ERROR TYPE BREAKDOWN (ALL SOAP ERRORS):")
    w("=" * 70)
    err_total = sum(error_shapes.values())
    w(f"{'ERROR CATEGORY':<28} | {'COUNT':>6} | {'%':>7}")
    w("-" * 50)
    for name, count in error_shapes.most_common():
        w(f"{name:<28} | {count:>6} | {_pct(count, err_total) or 0:>6}%")
    w(f"{'TOTAL':<28} | {err_total:>6} | 100%")
    w("")
    w("ERROR TAG DISTRIBUTION (inferred / explicit):")
    for tag, count in error_tags.most_common():
        w(f"  {tag}: {count} ({_pct(count, sum(error_tags.values()))}%)")
    w("")
    w("CRITICAL ERRORS (Highest Impact):")
    for i, (name, count) in enumerate(error_shapes.most_common(5), 1):
        w(f"  {i}. {name} — {count} occurrences ({_pct(count, err_total)}%)")
        for ex in error_examples_by_shape[name][:3]:
            w(f"     e.g. [{ex['section']}/{ex['field']}] GT={ex['gt']!r} GEN={ex['gen']!r} ({ex['result']})")

    w("")
    w("ERROR PATTERNS BY SUBSECTION:")
    for sec in section_order:
        shapes = subsection_error_shapes.get(sec)
        if not shapes:
            continue
        top = shapes.most_common(1)[0]
        w(f"  {sec}: most common={top[0]} ({_pct(top[1], sum(shapes.values()))}% of section errors)")
        for shape, cnt in shapes.most_common(3):
            w(f"    - {shape}: {cnt}")

    # Phase 3 — worst fields deep dive
    w("")
    w("PER-FIELD ROOT CAUSE CANDIDATES (worst fields by accuracy, min GT=10):")
    w("=" * 70)
    ranked = []
    for key, counts in field_counts.items():
        gt = counts[CORRECT] + counts[INCORRECT] + counts[MISSING]
        if gt < 10:
            continue
        acc = _pct(counts[CORRECT], gt)
        ranked.append((acc if acc is not None else 0, key, counts, gt))
    ranked.sort(key=lambda x: x[0])
    for acc, key, counts, gt in ranked[:15]:
        sec, field = key.split("|", 1)
        fails = counts[INCORRECT] + counts[MISSING] + counts[HALLUCINATION]
        w("")
        w(f"Field: {field} ({sec})")
        w(f"  Accuracy: {acc}%  Passes={counts[CORRECT]} Failures≈{fails} GT={gt}")
        w(f"  Breakdown: I={counts[INCORRECT]} M={counts[MISSING]} H={counts[HALLUCINATION]}")
        # Heuristic hypotheses
        hyps = []
        if counts[MISSING] >= counts[INCORRECT] and counts[MISSING] > 0:
            hyps.append("Omission / empty generation when GT established (Missing)")
        if counts[INCORRECT] > counts[MISSING]:
            hyps.append("Value mismatch / paraphrasing scored Incorrect (strict scorer)")
        if counts[HALLUCINATION] > 0:
            hyps.append("Hallucinated content not in GT")
        if "dose" in field.lower() or "schedule" in field.lower():
            hyps.append("Ambiguous medication schedule/dose formats")
        if "snomed" in field.lower():
            hyps.append("SNOMED IDs often absent from generation")
        if sec == "Objective":
            hyps.append("Nested vitals formatting / units / numeric strictness")
        w("  Hypotheses: " + "; ".join(hyps) if hyps else "  Hypotheses: (see examples)")
        for ex in field_examples.get(key, [])[:3]:
            w(f"  Example ({ex['result']}/{ex['shape']}): GT={ex['gt']!r} GEN={ex['gen']!r}")

    # Phase 4
    w("")
    w("COMPARATIVE ANALYSIS:")
    w("=" * 70)
    w("Why Transcript >> SOAP:")
    w("  - Transcript is flat text similarity; SOAP is multi-field nested structure")
    w("  - SOAP score = weighted clinical facts across 4 sections; one miss hurts more")
    w("  - SOAP scorer has zero numeric tolerance and counts Missing/Hallucination")
    w(f"  - Observed mean gap: {((soap_acc or 0) - (trans_acc or 0)):.1f} pts")
    w("")
    w("ACCURACY BY LLM MODEL:")
    for model, buckets in sorted(by_model.items(), key=lambda x: -len(x[1]["soap"])):
        if not buckets["soap"]:
            continue
        w(
            f"  {model}: SOAP {_mean(buckets['soap'])}% "
            f"(Transcript {_mean(buckets['trans'])}%, Translation {_mean(buckets['transl'])}%, n={len(buckets['soap'])})"
        )
    w("")
    w("ACCURACY BY INPUT LANGUAGE:")
    for lang, buckets in sorted(by_lang.items(), key=lambda x: -len(x[1]["soap"])):
        if not buckets["soap"]:
            continue
        w(
            f"  {lang}: SOAP {_mean(buckets['soap'])}% "
            f"(Transcript {_mean(buckets['trans'])}%, Translation {_mean(buckets['transl'])}%, n={len(buckets['soap'])})"
        )

    # Phase 5 recommendations derived from data
    w("")
    w("TOP ISSUES (data-driven):")
    w("=" * 70)
    for i, (acc, key, counts, gt) in enumerate(ranked[:5], 1):
        sec, field = key.split("|", 1)
        impact = counts[INCORRECT] + counts[MISSING] + counts[HALLUCINATION]
        w(f"ISSUE #{i}: Low accuracy on {field} ({sec})")
        w(f"  Impact: {impact} error facts / {gt} GT facts; accuracy {acc}%")
        dominant = max(
            [(INCORRECT, counts[INCORRECT]), (MISSING, counts[MISSING]), (HALLUCINATION, counts[HALLUCINATION])],
            key=lambda x: x[1],
        )
        w(f"  Dominant error: {dominant[0]} ({dominant[1]})")
        if dominant[0] == MISSING:
            fix = "Prompt: require explicit capture of established facts; avoid dropping empty-looking but established negatives"
            gain = "medium"
        elif dominant[0] == HALLUCINATION:
            fix = "Prompt: forbid inventing facts not in transcript; post-validate against source"
            gain = "high"
        else:
            fix = "Relax paraphrasing / format matching OR tighten generation schema for this field"
            gain = "medium"
        w(f"  Recommended fix: {fix}")
        w(f"  Effort/gain: Medium / {gain}")

    w("")
    w("IMMEDIATE FIXES (suggested):")
    top_shape = error_shapes.most_common(1)[0][0] if error_shapes else None
    if top_shape == "Missing Fields":
        w("  1. Reduce Missing: strengthen SOAP prompt completeness checklist for established GT fields")
    elif top_shape == "Value Mismatches":
        w("  1. Reduce Incorrect: review scorer strictness on paraphrase / synonym matching for Normal fields")
    else:
        w(f"  1. Address dominant error class: {top_shape}")
    w("  2. Add format normalization for vitals/dose/units before scoring (backend)")
    w("  3. Ensure nested objective.vitals paths always populated (schema enforcement)")
    w("  4. Separate structure validation from clinical content scoring in reporting")
    w("")
    w("REALISTIC TARGET:")
    if soap_acc is not None and transl_acc is not None:
        w(f"  Current SOAP mean ~{soap_acc}%. Translation ~{transl_acc}%.")
        w("  Near-term target: SOAP ≥70% (REVIEW band) via missing+format fixes;")
        w("  Stretch: SOAP ≥85% if content parity approaches translation quality.")
    w("")
    w("DONE")

    report = "\n".join(lines)
    out = ROOT / "results" / "_soap_accuracy_investigation_report.txt"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[Wrote {out}]")


if __name__ == "__main__":
    main()
