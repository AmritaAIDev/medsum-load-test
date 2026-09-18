"""How It Works copy — documents the implemented accuracy-test workflow.

Does not change execution. Steps match Prompts 1–8 as the harness actually
runs them (selection, scoring, latency, reports).
"""

from __future__ import annotations

HOW_IT_WORKS_STEPS = (
    (
        "Add/select audio files",
        "Select which Drive audio files to include. Local uploads can appear "
        "in the file list, but Run All executes only remaining Drive files "
        "(matched against Drive discovery). Exclude drops a file from this "
        "run only — Drive and catalog sources are not deleted. An empty "
        "Drive selection runs nothing.",
    ),
    (
        "Provide/upload Ground Truth",
        "Ground truth is the matching Drive sidecar: a transcript, plus "
        "optional translation or SOAP files. A case with no transcription "
        "ground truth still runs, but accuracy is NOT_SCORED, not 0%.",
    ),
    (
        "Select GPT/LLM model",
        "Pick the comparison model used to score generated text against "
        "ground truth. That is separate from MedSum’s own STT, translation, "
        "and SOAP models.",
    ),
    (
        "Start the test",
        "Click Run All Tests. Each doctor may have one patient. Only the "
        "selected Drive files are executed. Execution Status is Completed, "
        "Error, or Not evaluated — or NOT_SCORED when accuracy was not "
        "generated. SOAP accuracy shows the SOAP percentage as "
        "N% accuracy. Low SOAP accuracy is not an execution error.",
    ),
    (
        "Transcribe the audio",
        "The harness sends audio to Flask /transcribe and reads back the "
        "generated transcript plus the API’s transcription-time.",
    ),
    (
        "Generate translation",
        "The same /transcribe response includes a translation when the "
        "pipeline ran one. English often reports translation-time as 0 "
        "because no translation step ran.",
    ),
    (
        "Generate SOAP output",
        "SOAP/summary is the Flask LLM step. The API returns one llm-time "
        "figure for that work — there is no separate SOAP timer.",
    ),
    (
        "Compare generated output with Ground Truth",
        "An LLM judges medical-meaning similarity (not exact string match) "
        "of generated vs ground-truth transcription, and translation/SOAP "
        "when those ground truths exist. SOAP accuracy is the Prompt 1 "
        "weighted clinical score when SOAP ground truth exists, shown as "
        "N% accuracy. Clinical Quality maps that into Clinically Acceptable "
        "/ Minor Deviation / Moderate Deviation / Major Deviation.",
    ),
    (
        "Calculate accuracy and latency",
        "Average accuracy is the mean of scored transcription similarities; "
        "NOT_SCORED cases are omitted, not counted as 0%. The Done KPI "
        "counts finished executions, not accuracy bands. Latency is Flask "
        "transcription-time, translation-time, llm-time (SOAP), and "
        "total-time. Missing timings show as unavailable.",
    ),
    (
        "Review individual test cases or download the complete report",
        "Click a row for per-field scores and case materials. Download one "
        "row or Download Total Report for the run (overall counts, accuracy, "
        "latency, per-case detail).",
    ),
)
