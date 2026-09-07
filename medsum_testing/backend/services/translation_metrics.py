"""Translation quality metrics with explicit equations.

Computed from ground-truth vs generated English translation text.

Metrics
-------
BLEU (Papineni et al., with Lin & Och add-1 smoothing)
    BP = 1 if c > r else exp(1 - r/c)
    p_n = modified n-gram precision for n = 1..4
    BLEU = 100 * BP * exp((1/4) * Σ ln p_n)

chrF++ (Popović)
    Character n-grams n=1..6 and word n-grams n=1..2.
    β = 2;  chrF = 100 * (1+β²)·P·R / (β²·P + R)

TER (Snover et al., word-level edit rate)
    TER = 100 * Levenshtein(hyp, ref) / max(1, |ref|)

Medical Terminology Accuracy
    Extract dosage / unit / clinical-looking tokens from GT.
    Acc = 100 * |GT_terms ∩ Hyp_terms| / max(1, |GT_terms|)

COMET-style quality estimate (no neural COMET dependency)
    q = 0.45·(chrF/100) + 0.25·(BLEU/100) + 0.30·(1 - TER/100)
    COMET = round(clamp(q, 0, 1), 2)

Human/Clinical Accuracy
    LLM medical-meaning similarity_score on [0, 100], when provided.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?|[०-९]+")
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when",
    "at", "by", "for", "with", "about", "against", "between", "into",
    "through", "during", "before", "after", "above", "below", "to", "from",
    "up", "down", "in", "out", "on", "off", "over", "under", "again",
    "further", "once", "here", "there", "all", "any", "both", "each",
    "few", "more", "most", "other", "some", "such", "no", "nor", "not",
    "only", "own", "same", "so", "than", "too", "very", "can", "will",
    "just", "should", "now", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "of", "as", "it",
    "this", "that", "these", "those", "he", "she", "they", "them", "his",
    "her", "their", "we", "you", "i", "me", "my", "our", "your",
    "patient", "doctor", "said", "says", "tell", "told", "also",
}
_MED_UNIT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug|g|ml|mL|L|l|mmol|mmhg|cm|kg|iu|"
    r"tablet|tablets|tab|tabs|capsule|capsules|drop|drops|od|bd|tds|qid|"
    r"bid|tid|qhs|prn)\b",
    re.I,
)
_MED_TOKEN_RE = re.compile(
    r"\b(?:[A-Z][a-z]+(?:cillin|mycin|vir|azole|pril|sartan|olol|statin|pine|"
    r"idone|dopa|formin|gliptin|gliflozin|xaban|parin)|"
    r"paracetamol|acetaminophen|ibuprofen|amoxicillin|azithromycin|"
    r"metformin|amlodipine|atorvastatin|omeprazole|pantoprazole|"
    r"cetirizine|levocetirizine|montelukast|salbutamol|prednisolone|"
    r"dexamethasone|insulin|warfarin|aspirin|clopidogrel|losartan|"
    r"telmisartan|ramipril|enalapril|metoprolol|propranolol|"
    r"hypertension|diabetes|asthma|allergy|allergies|fever|cough|"
    r"infection|inflammation|diagnosis|dosage|dose|frequency)\b",
    re.I,
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(_text(text))]


def _ngrams(tokens: list[str], n: int) -> Counter:
    if n <= 0 or len(tokens) < n:
        return Counter()
    return Counter(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def bleu_score(hypothesis: str, reference: str, max_n: int = 4) -> float | None:
    """Sentence BLEU × 100 with add-1 smoothing. Higher is better."""
    hyp = _tokenize(hypothesis)
    ref = _tokenize(reference)
    if not hyp or not ref:
        return None
    log_sum = 0.0
    for n in range(1, max_n + 1):
        hyp_n = _ngrams(hyp, n)
        ref_n = _ngrams(ref, n)
        overlap = sum(min(count, ref_n[gram]) for gram, count in hyp_n.items())
        total = sum(hyp_n.values())
        # Lin & Och add-1 smoothing
        precision = (overlap + 1.0) / (total + 1.0)
        log_sum += math.log(precision)
    bp = 1.0 if len(hyp) > len(ref) else math.exp(1.0 - (len(ref) / max(len(hyp), 1)))
    return round(100.0 * bp * math.exp(log_sum / max_n), 1)


def chrfpp_score(hypothesis: str, reference: str, beta: float = 2.0) -> float | None:
    """chrF++ × 100 (char n=1..6 + word n=1..2). Higher is better."""
    hyp = _text(hypothesis)
    ref = _text(reference)
    if not hyp or not ref:
        return None

    def _stats(hyp_grams: Counter, ref_grams: Counter) -> tuple[float, float]:
        overlap = sum(min(count, ref_grams[g]) for g, count in hyp_grams.items())
        hyp_total = sum(hyp_grams.values())
        ref_total = sum(ref_grams.values())
        precision = overlap / hyp_total if hyp_total else 0.0
        recall = overlap / ref_total if ref_total else 0.0
        return precision, recall

    precisions: list[float] = []
    recalls: list[float] = []

    hyp_chars = list(re.sub(r"\s+", " ", hyp.lower()))
    ref_chars = list(re.sub(r"\s+", " ", ref.lower()))
    for n in range(1, 7):
        p, r = _stats(_ngrams(hyp_chars, n), _ngrams(ref_chars, n))
        precisions.append(p)
        recalls.append(r)

    hyp_words = _tokenize(hyp)
    ref_words = _tokenize(ref)
    for n in range(1, 3):
        p, r = _stats(_ngrams(hyp_words, n), _ngrams(ref_words, n))
        precisions.append(p)
        recalls.append(r)

    if not precisions:
        return None
    precision = sum(precisions) / len(precisions)
    recall = sum(recalls) / len(recalls)
    if precision == 0 and recall == 0:
        return 0.0
    beta2 = beta * beta
    score = (1.0 + beta2) * precision * recall / (beta2 * precision + recall)
    return round(100.0 * score, 1)


def _levenshtein(a: list[str], b: list[str]) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            ins = curr[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            curr.append(min(ins, delete, sub))
        prev = curr
    return prev[-1]


def ter_score(hypothesis: str, reference: str) -> float | None:
    """Translation Edit Rate as percent of reference words. Lower is better."""
    hyp = _tokenize(hypothesis)
    ref = _tokenize(reference)
    if not ref and not hyp:
        return None
    if not ref:
        return 100.0
    edits = _levenshtein(hyp, ref)
    return round(100.0 * edits / len(ref), 1)


def extract_medical_terms(text: str) -> set[str]:
    """Dosage/unit phrases + clinical-looking tokens from text."""
    raw = _text(text)
    if not raw:
        return set()
    terms: set[str] = set()
    for match in _MED_UNIT_RE.finditer(raw):
        terms.add(re.sub(r"\s+", " ", match.group(0).lower()))
    for match in _MED_TOKEN_RE.finditer(raw):
        terms.add(match.group(0).lower())
    for token in _tokenize(raw):
        if len(token) < 5 or token in _STOPWORDS:
            continue
        if any(ch.isdigit() for ch in token):
            terms.add(token)
        elif len(token) >= 7:
            terms.add(token)
    return terms


def medical_terminology_accuracy(hypothesis: str, reference: str) -> float | None:
    """Percent of GT medical terms preserved in hypothesis. Higher is better."""
    gt_terms = extract_medical_terms(reference)
    if not gt_terms:
        # Fall back to content-word recall when no medical lexicon hits.
        gt_words = {w for w in _tokenize(reference) if w not in _STOPWORDS and len(w) > 3}
        hyp_words = set(_tokenize(hypothesis))
        if not gt_words:
            return None
        hit = sum(1 for w in gt_words if w in hyp_words)
        return round(100.0 * hit / len(gt_words), 1)
    hyp_terms = extract_medical_terms(hypothesis)
    hyp_tokens = set(_tokenize(hypothesis))
    hit = 0
    for term in gt_terms:
        if term in hyp_terms or term in hyp_tokens:
            hit += 1
            continue
        # Multi-word dosage phrases: require all tokens present.
        parts = term.split()
        if len(parts) > 1 and all(part in hyp_tokens for part in parts):
            hit += 1
    return round(100.0 * hit / len(gt_terms), 1)


def comet_style_score(bleu: float | None, chrf: float | None, ter: float | None) -> float | None:
    """COMET-style 0–1 quality estimate from surface metrics (no neural model)."""
    if bleu is None and chrf is None and ter is None:
        return None
    b = (bleu or 0.0) / 100.0
    c = (chrf or 0.0) / 100.0
    t = 1.0 - _clamp((ter or 100.0) / 100.0)
    quality = 0.45 * c + 0.25 * b + 0.30 * t
    return round(_clamp(quality), 2)


def human_clinical_accuracy(similarity_score: Any) -> float | None:
    """Map LLM similarity_score onto 0–100 clinical accuracy percent."""
    if similarity_score is None or similarity_score == "":
        return None
    try:
        n = float(similarity_score)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n):
        return None
    if 0.0 <= n <= 1.0:
        n *= 100.0
    return round(_clamp(n, 0.0, 100.0), 1)


def compute_translation_metrics(
    hypothesis: str,
    reference: str,
    *,
    similarity_score: Any = None,
) -> dict[str, Any]:
    """Return display-ready translation quality metrics + raw numeric values."""
    hyp = _text(hypothesis)
    ref = _text(reference)
    if not hyp or not ref:
        return {
            "comet": None,
            "medical_terminology_accuracy": None,
            "chrf": None,
            "bleu": None,
            "ter": None,
            "human_clinical_accuracy": human_clinical_accuracy(similarity_score),
            "equations": {
                "bleu": "BP*exp(avg ln p_n) x 100 (n=1..4, add-1 smooth)",
                "chrf": "chrF++ x 100 (char 1..6 + word 1..2, beta=2)",
                "ter": "100 * Levenshtein(hyp, ref) / |ref|",
                "medical_terminology_accuracy": "100 * |GT_med intersect Hyp| / |GT_med|",
                "comet": "0.45*chrF + 0.25*BLEU + 0.30*(1-TER)  (0-1)",
                "human_clinical_accuracy": "LLM similarity_score (0-100)",
            },
        }

    bleu = bleu_score(hyp, ref)
    chrf = chrfpp_score(hyp, ref)
    ter = ter_score(hyp, ref)
    med = medical_terminology_accuracy(hyp, ref)
    comet = comet_style_score(bleu, chrf, ter)
    human = human_clinical_accuracy(similarity_score)

    return {
        "comet": comet,
        "medical_terminology_accuracy": med,
        "chrf": chrf,
        "bleu": bleu,
        "ter": ter,
        "human_clinical_accuracy": human,
        "equations": {
            "bleu": "BP*exp(avg ln p_n) x 100 (n=1..4, add-1 smooth)",
            "chrf": "chrF++ x 100 (char 1..6 + word 1..2, beta=2)",
            "ter": "100 * Levenshtein(hyp, ref) / |ref|",
            "medical_terminology_accuracy": "100 * |GT_med intersect Hyp| / |GT_med|",
            "comet": "0.45*chrF + 0.25*BLEU + 0.30*(1-TER)  (0-1)",
            "human_clinical_accuracy": "LLM similarity_score (0-100)",
        },
    }
