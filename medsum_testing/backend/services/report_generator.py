"""PDF and Excel report generation."""

from __future__ import annotations

import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from medsum_testing.backend.models.test_result import TestResult, display_soap_accuracy
from medsum_testing.backend.services.batch_identity import display_batch_label
from medsum_testing.backend.services.batch_report import (
    BATCH_REPORT_SECTIONS,
    build_batch_report,
)
from medsum_testing.backend.services.individual_report import (
    extra_report_fields,
    individual_report_fields,
)
from medsum_testing.backend.services.soap_detail_table import (
    SOAP_SECTION_ORDER,
    detail_table_from_result,
)
from medsum_testing.backend.services.test_case_view import format_audio_length


# One PDF table row cannot be taller than the page frame (~686pt on A4
# with these margins). 8pt type at 11pt leading wraps ~80 chars/line, so
# ~1800 characters stays well under one frame. Longer values are split
# across continuation rows instead of overflowing (LayoutError 500).
PDF_CELL_CHAR_LIMIT = 1800

_HEADER_FILL = colors.HexColor("#2563eb")
_GRID_COLOR = colors.HexColor("#d9dce6")

log = logging.getLogger(__name__)

# reportlab's built-in Helvetica is a core-14 PDF font with only Latin-1
# glyphs: Hindi/Marathi/etc. text renders as missing-glyph boxes ("nnnn").
# These are Indic-script TTFs, checked in priority order and referenced
# from wherever they're already installed rather than bundled in the repo
# (most are OS-licensed, e.g. Microsoft's Nirmala UI on Windows, and must
# not be redistributed). Set MEDSUM_PDF_UNICODE_FONT to override.
_UNICODE_FONT = "MedsumUnicode"
_UNICODE_FONT_BOLD = "MedsumUnicode-Bold"


def _unicode_font_candidates() -> list[str]:
    paths = []
    env_path = os.environ.get("MEDSUM_PDF_UNICODE_FONT")
    if env_path:
        paths.append(env_path)
    paths.extend(
        [
            r"C:\Windows\Fonts\Nirmala.ttf",  # Windows pan-Indic UI font
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
            "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
            "/usr/share/fonts/noto/NotoSansDevanagari-Regular.ttf",
            "/Library/Fonts/NotoSansDevanagari-Regular.ttf",
        ]
    )
    return paths


def _unicode_font_bold_candidates() -> list[str]:
    paths = []
    env_path = os.environ.get("MEDSUM_PDF_UNICODE_FONT_BOLD")
    if env_path:
        paths.append(env_path)
    paths.extend(
        [
            r"C:\Windows\Fonts\NirmalaB.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
        ]
    )
    return paths


_unicode_font_ready: bool | None = None


def _ensure_unicode_font() -> bool:
    """Register an Indic-capable font once per process. Idempotent; safe to
    call from every report function. Returns False (Helvetica stays in use)
    when no suitable font is installed on this machine.
    """
    global _unicode_font_ready
    if _unicode_font_ready is not None:
        return _unicode_font_ready

    regular_path = next(
        (p for p in _unicode_font_candidates() if p and Path(p).is_file()), None
    )
    if not regular_path:
        log.warning(
            "No Unicode PDF font found for Indic scripts (Hindi, etc.); reports will "
            "show missing glyphs for non-Latin text. Set MEDSUM_PDF_UNICODE_FONT to a "
            ".ttf path to fix this."
        )
        _unicode_font_ready = False
        return False

    try:
        pdfmetrics.registerFont(TTFont(_UNICODE_FONT, regular_path))
    except Exception:
        log.warning("Could not register PDF unicode font %s", regular_path, exc_info=True)
        _unicode_font_ready = False
        return False

    bold_path = next(
        (p for p in _unicode_font_bold_candidates() if p and Path(p).is_file()),
        regular_path,
    )
    try:
        pdfmetrics.registerFont(TTFont(_UNICODE_FONT_BOLD, bold_path))
    except Exception:
        log.warning("Could not register PDF unicode bold font %s", bold_path, exc_info=True)
        pdfmetrics.registerFont(TTFont(_UNICODE_FONT_BOLD, regular_path))

    _unicode_font_ready = True
    return True


def _pdf_fonts() -> tuple[str, str]:
    """(body_font, bold_font) — the Unicode pair when available, else Helvetica."""
    if _ensure_unicode_font():
        return _UNICODE_FONT, _UNICODE_FONT_BOLD
    return "Helvetica", "Helvetica-Bold"


def _fmt(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _escape_pdf(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _chunks_for_pdf(text: str, limit: int = PDF_CELL_CHAR_LIMIT) -> list[str]:
    raw = str(text or "")
    if len(raw) <= limit:
        return [raw]
    parts: list[str] = []
    remaining = raw
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut < limit // 4:
            cut = window.rfind(" ")
        if cut < limit // 4:
            cut = limit
        else:
            cut += 1
        parts.append(remaining[:cut])
        remaining = remaining[cut:]
    return parts or [""]


def _section_rows(result: TestResult) -> list[tuple[str, str]]:
    required = individual_report_fields(result)
    tc = result.transcription_comparison
    sc = result.summary_comparison
    mc = result.medication_comparison
    rc = result.regression_comparison

    rows = required + extra_report_fields(result) + [
        ("Test Case ID", result.test_case_id or "N/A"),
        ("Test ID", result.test_id),
        ("Patient ID", result.patient_id or "N/A"),
        ("Doctor ID", result.doctor_id or "N/A"),
        ("Session Date/Time", result.session_datetime or result.timestamp),
        ("Timestamp", result.timestamp),
        ("Language", result.language),
        ("Audio File", result.audio_filename),
        ("Audio Duration (s)", str(result.audio_duration_seconds)),
        ("AI Model", result.ai_model),
        ("Final Result", result.final_result),
        ("Accuracy Score", str(result.accuracy_score) if result.accuracy_score is not None else "N/A"),
        ("Accuracy Skipped", "Yes" if result.accuracy_skipped else "No"),
        ("Accuracy Skip Reason", result.accuracy_skip_reason or "N/A"),
        ("Retry Count", str(result.retry_count)),
        ("Errors", "; ".join(result.errors) if result.errors else "None"),
        ("Ground Truth Transcription", result.ground_truth_transcription or "N/A"),
        ("Generated Transcription", result.generated_transcription or "N/A"),
        ("Previous Transcription", result.previous_transcription or "N/A"),
        ("Generated Summary", _fmt(result.generated_summary)),
        ("Previous Summary", _fmt(result.previous_summary)),
        ("Text Translation", result.text_translation or "N/A"),
        ("Medications Before", _fmt(result.medications_before)),
        ("Medications After Normalization", _fmt(result.medications_after_normalization)),
        ("Medications Generated", _fmt(result.medications_generated)),
    ]

    if tc:
        rows.extend(
            [
                ("Transcription Similarity", str(tc.similarity_score or "N/A")),
                ("Transcription Severity", tc.severity),
                ("Medical Terminology Differences", "\n".join(tc.medical_differences) or "None"),
                ("General Transcription Differences", "\n".join(tc.general_differences) or "None"),
                ("Transcription Comparison Summary", tc.summary or "N/A"),
            ]
        )

    if sc:
        rows.extend(
            [
                ("Summary Similarity", str(sc.similarity_score or "N/A")),
                ("Summary Severity", sc.severity),
                ("Summary Differences", "\n".join(sc.medical_differences + sc.general_differences) or "None"),
                ("Summary Comparison Summary", sc.summary or "N/A"),
            ]
        )

    if mc:
        rows.extend(
            [
                ("Medication Similarity", str(mc.similarity_score or "N/A")),
                ("Medication Severity", mc.severity),
                ("Medications Added", "\n".join(mc.added) or "None"),
                ("Medications Removed", "\n".join(mc.removed) or "None"),
                ("Medications Changed", "\n".join(mc.changed) or "None"),
                ("Medication Differences", "\n".join(mc.medical_differences) or "None"),
                ("Medication Comparison Summary", mc.summary or "N/A"),
            ]
        )

    if rc and not rc.skipped:
        rows.extend(
            [
                ("Regression Similarity", str(rc.similarity_score or "N/A")),
                ("Regression Severity", rc.severity),
                ("Regression Differences", "\n".join(rc.medical_differences + rc.general_differences) or "None"),
                ("Regression Summary", rc.summary or "N/A"),
            ]
        )

    return rows


def _pct(value: Any) -> str:
    if value is None or value == "":
        return "N/A"
    try:
        return f"{round(float(value))}%"
    except (TypeError, ValueError):
        return "N/A"


def _model_name(*candidates: Any) -> str:
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return "N/A"


def _wrap(text: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_escape_pdf(_fmt(text)), style)


# Same palette as .diff-missing-gt / .diff-incorrect-gen in medsum_test.css,
# so a highlighted word means the same thing on the detail page and in the PDF.
_DIFF_MISSING_GT = ("#92400E", "#FFF3CD")  # GT word absent from Generated
_DIFF_INCORRECT_GEN = ("#B91C1C", "#FEE2E2")  # Generated word absent from GT

_DIFF_PUNCT_RE = re.compile(r"[.,\-–—;:!?()'\"“”‘’]")


def _normalize_diff_word(word: str) -> str:
    return _DIFF_PUNCT_RE.sub("", word.lower()).strip()


def _diff_span(word: str, palette: tuple[str, str] | None) -> str:
    escaped = _escape_pdf(word)
    if not palette:
        return escaped
    text_color, back_color = palette
    return f'<font color="{text_color}" backColor="{back_color}">{escaped}</font>'


def _word_diff_tokens(gt_text: Any, gen_text: Any) -> tuple[list[str], list[str]]:
    """Word-level error highlighting for Transcription/Translation cells, as a
    list of self-contained per-word markup tokens (never pre-joined into one
    string — see _chunk_tokens for why).

    Mirrors computeWordDiff() in medsum_test.js (case/punctuation-insensitive,
    order-independent word membership) so the PDF agrees with the detail page:
    a GT word missing from Generated is amber, a Generated word not in GT is red.
    """
    gt_words = _fmt(gt_text).split()
    gen_words = _fmt(gen_text).split()

    if not gt_words:
        return ["N/A"], ([_diff_span(w, None) for w in gen_words] or ["N/A"])
    if not gen_words:
        return [_diff_span(w, _DIFF_MISSING_GT) for w in gt_words], ["—"]

    gt_norm = {_normalize_diff_word(w) for w in gt_words}
    gen_norm = {_normalize_diff_word(w) for w in gen_words}

    gt_tokens = [
        _diff_span(w, _DIFF_MISSING_GT if _normalize_diff_word(w) not in gen_norm else None)
        for w in gt_words
    ]
    gen_tokens = [
        _diff_span(w, _DIFF_INCORRECT_GEN if _normalize_diff_word(w) not in gt_norm else None)
        for w in gen_words
    ]
    return gt_tokens, gen_tokens


def _chunk_tokens(tokens: list[str], limit: int = PDF_CELL_CHAR_LIMIT) -> list[str]:
    """Pack whole tokens (word spans) into <=limit-char chunks, joined by a
    space. Unlike _chunks_for_pdf's character-position search, this can never
    cut inside a <font ...> tag — a `<font color="..." backColor="...">` tag
    itself contains spaces, so searching raw markup text for the last space
    can land inside a tag's attribute list, not between two word spans.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for token in tokens:
        add_len = len(token) + (1 if current else 0)
        if current and current_len + add_len > limit:
            chunks.append(" ".join(current))
            current = [token]
            current_len = len(token)
        else:
            current.append(token)
            current_len += add_len
    if current:
        chunks.append(" ".join(current))
    return chunks or [""]


def _two_col_rows_from_tokens(
    gt_tokens: list[str], gen_tokens: list[str], style: ParagraphStyle
) -> list[list[Paragraph]]:
    """GT/Generated token lists (from _word_diff_tokens) are already-escaped
    reportlab markup — must not be re-escaped."""
    left_chunks = _chunk_tokens(gt_tokens)
    right_chunks = _chunk_tokens(gen_tokens)
    total = max(len(left_chunks), len(right_chunks))
    rows = []
    for i in range(total):
        left = left_chunks[i] if i < len(left_chunks) else ""
        right = right_chunks[i] if i < len(right_chunks) else ""
        rows.append([Paragraph(left, style), Paragraph(right, style)])
    return rows


def _header_table_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), _HEADER_FILL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, _GRID_COLOR),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
    )


def generate_pdf(test_result: TestResult) -> bytes:
    data = test_result.to_dict()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )
    styles = getSampleStyleSheet()
    body_font, bold_font = _pdf_fonts()
    title_style = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        fontName=bold_font,
        fontSize=18,
        alignment=TA_CENTER,
        spaceAfter=16,
        textColor=colors.HexColor("#111827"),
    )
    heading_style = ParagraphStyle(
        "Heading",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=13,
        spaceBefore=14,
        spaceAfter=6,
        textColor=colors.HexColor("#111827"),
    )
    subheading_style = ParagraphStyle(
        "SubHeading",
        parent=styles["Heading3"],
        fontName=bold_font,
        fontSize=11,
        spaceBefore=10,
        spaceAfter=6,
        textColor=colors.HexColor("#1f2937"),
    )
    section_style = ParagraphStyle(
        "SoapSection",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=10.5,
        spaceBefore=8,
        spaceAfter=4,
        textColor=colors.HexColor("#4b5563"),
    )
    value_style = ParagraphStyle(
        "Value", parent=styles["Normal"], fontName=body_font, fontSize=8, leading=11
    )
    header_cell_style = ParagraphStyle(
        "HeaderCell",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=8,
        textColor=colors.white,
    )

    story: list = [Paragraph("Medsum Test Report", title_style)]

    # --- Metadata ---
    story.append(Paragraph("Metadata:", heading_style))
    tc_ref = data.get("tc_ref") or data.get("test_case_id") or data.get("test_id") or "N/A"
    batch_label = display_batch_label(data.get("batch_id") or "", data.get("batch_ref") or "")
    audio_name = data.get("audio_filename") or data.get("uploaded_audio_filename") or "N/A"
    duration_display = format_audio_length(data.get("audio_duration_seconds"))
    date_display = data.get("session_datetime") or data.get("timestamp") or "N/A"
    meta_headers = ["Test Case Number", "Batch ID", "Audio file Name", "Duration", "Date/Time"]
    meta_values = [tc_ref, batch_label, audio_name, duration_display, date_display]
    metadata_table = Table(
        [
            [Paragraph(h, header_cell_style) for h in meta_headers],
            [_wrap(v, value_style) for v in meta_values],
        ],
        colWidths=[3.2 * cm, 3.2 * cm, 5 * cm, 2.8 * cm, 3.8 * cm],
    )
    metadata_table.setStyle(_header_table_style())
    story.append(metadata_table)

    # --- Accuracy ---
    story.append(Paragraph("Accuracy:", heading_style))
    trans_comp = data.get("transcription_comparison") or {}
    transl_comp = data.get("translation_comparison") or {}
    soap_shown = display_soap_accuracy(data.get("soap_comparison"))
    transcription_score = trans_comp.get("similarity_score")
    translation_score = transl_comp.get("similarity_score")
    soap_score = soap_shown.get("percent_value")

    acc_rows = [
        [
            "Transcription",
            _model_name(data.get("stt_model"), data.get("ai_model_used"), data.get("ai_model")),
            _pct(transcription_score),
        ],
        [
            "Translation",
            _model_name(
                data.get("translation_model"), data.get("ai_model_used"), data.get("ai_model")
            ),
            _pct(translation_score),
        ],
        [
            "SOAP",
            _model_name(data.get("llm_model"), data.get("ai_model_used"), data.get("ai_model")),
            _pct(soap_score),
        ],
    ]
    accuracy_table = Table(
        [[Paragraph(h, header_cell_style) for h in ("Step", "Model Name", "Accuracy")]]
        + [[_wrap(cell, value_style) for cell in row] for row in acc_rows],
        colWidths=[5 * cm, 8 * cm, 5 * cm],
    )
    accuracy_table.setStyle(_header_table_style())
    story.append(accuracy_table)

    legend_style = ParagraphStyle(
        "DiffLegend", parent=styles["Normal"], fontName=body_font, fontSize=7.5,
        textColor=colors.HexColor("#6b7280"), spaceAfter=4,
    )
    diff_legend_markup = (
        f'<font color="{_DIFF_MISSING_GT[0]}" backColor="{_DIFF_MISSING_GT[1]}">amber</font> '
        "= missing from generated output &nbsp;&nbsp; "
        f'<font color="{_DIFF_INCORRECT_GEN[0]}" backColor="{_DIFF_INCORRECT_GEN[1]}">red</font> '
        "= not present in ground truth"
    )

    # --- Transcription ---
    story.append(
        Paragraph(f"Transcription: (Accuracy: {_pct(transcription_score)})", subheading_style)
    )
    story.append(Paragraph(diff_legend_markup, legend_style))
    trans_gt_tokens, trans_gen_tokens = _word_diff_tokens(
        data.get("ground_truth_transcription"), data.get("generated_transcription")
    )
    transcription_rows = [
        [Paragraph("Ground truth", header_cell_style), Paragraph("Generated output", header_cell_style)]
    ] + _two_col_rows_from_tokens(trans_gt_tokens, trans_gen_tokens, value_style)
    transcription_table = Table(transcription_rows, colWidths=[9 * cm, 9 * cm])
    transcription_table.setStyle(_header_table_style())
    story.append(transcription_table)

    # --- Translation ---
    story.append(
        Paragraph(f"Translation: (Accuracy: {_pct(translation_score)})", subheading_style)
    )
    story.append(Paragraph(diff_legend_markup, legend_style))
    transl_gt_tokens, transl_gen_tokens = _word_diff_tokens(
        data.get("translation_ground_truth"),
        data.get("generated_translation") or data.get("translation") or data.get("text_translation"),
    )
    translation_rows = [
        [Paragraph("Ground truth", header_cell_style), Paragraph("Generated output", header_cell_style)]
    ] + _two_col_rows_from_tokens(transl_gt_tokens, transl_gen_tokens, value_style)
    translation_table = Table(translation_rows, colWidths=[9 * cm, 9 * cm])
    translation_table.setStyle(_header_table_style())
    story.append(translation_table)

    # --- SOAP Summary ---
    story.append(Paragraph(f"SOAP Summary: (Accuracy: {_pct(soap_score)})", subheading_style))
    detail = detail_table_from_result(data, include_na=False)
    rows_by_section = {section["section"]: section["rows"] for section in detail["sections"]}
    soap_headers = ["Sub Category", "Ground Truth", "Generated output", "Status"]
    for section_name in SOAP_SECTION_ORDER:
        story.append(Paragraph(section_name, section_style))
        rows = rows_by_section.get(section_name) or []
        table_rows = [[Paragraph(h, header_cell_style) for h in soap_headers]]
        style = _header_table_style()
        if rows:
            for row in rows:
                table_rows.append(
                    [
                        _wrap(row.get("field_name"), value_style),
                        _wrap(row.get("ground_truth"), value_style),
                        _wrap(row.get("generated"), value_style),
                        _wrap(row.get("result"), value_style),
                    ]
                )
        else:
            table_rows.append([Paragraph("No comparison data", value_style), "", "", ""])
            style.add("SPAN", (0, 1), (-1, 1))
        soap_table = Table(table_rows, colWidths=[4 * cm, 6 * cm, 6 * cm, 2 * cm])
        soap_table.setStyle(style)
        story.append(soap_table)
        story.append(Spacer(1, 0.2 * cm))

    doc.build(story)
    return buffer.getvalue()


def generate_excel(test_result: TestResult) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Test Result"

    header_fill = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    ws.cell(row=1, column=1, value="Field").fill = header_fill
    ws.cell(row=1, column=1).font = header_font
    ws.cell(row=1, column=2, value="Value").fill = header_fill
    ws.cell(row=1, column=2).font = header_font

    for idx, (label, value) in enumerate(_section_rows(test_result), start=2):
        ws.cell(row=idx, column=1, value=label)
        cell = ws.cell(row=idx, column=2, value=value)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 80

    # Side-by-side transcription sheet
    ws2 = wb.create_sheet("Transcription Diff")
    ws2.cell(row=1, column=1, value="Ground Truth").fill = header_fill
    ws2.cell(row=1, column=1).font = header_font
    ws2.cell(row=1, column=2, value="Generated").fill = header_fill
    ws2.cell(row=1, column=2).font = header_font
    ws2.cell(row=2, column=1, value=test_result.ground_truth_transcription or "N/A")
    ws2.cell(row=2, column=2, value=test_result.generated_transcription or "N/A")
    ws2.column_dimensions["A"].width = 60
    ws2.column_dimensions["B"].width = 60

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def save_report_path(
    test_id: str,
    fmt: str,
    path: str,
    token: str,
    config: dict,
) -> None:
    """Update the run record in Django with the generated report path."""
    from datetime import datetime, timezone

    from medsum_testing.backend.services import medsum_api

    field = "report_pdf_path" if fmt == "pdf" else "report_excel_path"
    medsum_api.save_test_run(
        {
            "test_id": test_id,
            field: path,
            "report_generated_at": datetime.now(timezone.utc).isoformat(),
        },
        token,
        config,
    )


def _kv_rows(mapping: dict) -> list[tuple[str, str]]:
    rows = []
    for key, value in mapping.items():
        if key in {"status_counts", "execution_counts", "evaluation_counts"} and isinstance(value, dict):
            rows.append((key, ", ".join(f"{k}={v}" for k, v in value.items()) or "—"))
        elif key == "stage_averages" and isinstance(value, dict):
            rows.append((key, ", ".join(f"{k}={v}" for k, v in value.items())))
        elif isinstance(value, list):
            continue
        else:
            rows.append((key, _fmt(value)))
    return rows


def generate_batch_pdf(rows: list) -> bytes:
    report = build_batch_report(rows)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5 * cm, leftMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    body_font, bold_font = _pdf_fonts()
    title_style = ParagraphStyle(
        "BatchTitle",
        parent=styles["Heading1"],
        fontName=bold_font,
        fontSize=16,
        spaceAfter=12,
        textColor=colors.HexColor("#2563eb"),
    )
    heading = ParagraphStyle(
        "BatchH",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=12,
        spaceBefore=10,
        spaceAfter=6,
    )
    label_style = ParagraphStyle(
        "BatchLabel",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=8,
    )
    value_style = ParagraphStyle(
        "BatchValue",
        parent=styles["Normal"],
        fontName=body_font,
        fontSize=8,
        leading=11,
    )

    def _cell(text: str, style) -> Paragraph:
        return Paragraph(_escape_pdf(str(text)[: PDF_CELL_CHAR_LIMIT * 2]), style)

    def _kv_table(pairs: list[tuple[str, str]]):
        rows = []
        for key, value in pairs:
            chunks = _chunks_for_pdf(value)
            for i, chunk in enumerate(chunks):
                rows.append([
                    _cell(key if i == 0 else "", label_style),
                    _cell(chunk, value_style),
                ])
        return rows

    story = [Paragraph(report["title"], title_style)]
    for section in BATCH_REPORT_SECTIONS:
        story.append(Paragraph(section, heading))
        body = report.get(section)
        if section == "Test Case Details":
            table_data = [[
                _cell("Test Case ID", label_style),
                _cell("Execution Status", label_style),
                _cell("SOAP Evaluation", label_style),
                _cell("Accuracy", label_style),
                _cell("Latency", label_style),
                _cell("Individual Report", label_style),
            ]]
            for case in body or []:
                table_data.append([
                    _cell(case.get("test_case_id", ""), value_style),
                    _cell(case.get("execution_status", ""), value_style),
                    _cell(case.get("soap_evaluation", ""), value_style),
                    _cell(case.get("accuracy", ""), value_style),
                    _cell(case.get("latency", ""), value_style),
                    _cell(case.get("individual_report", ""), value_style),
                ])
        elif isinstance(body, dict):
            pairs = list(_kv_rows(body))
            if body.get("per_case"):
                pairs.append((
                    "per_case",
                    "\n".join(
                        f"{c.get('test_case_id') or c.get('audio_file')}: "
                        f"{c.get('accuracy') or c.get('total_time')}"
                        for c in body["per_case"]
                    ),
                ))
            table_data = _kv_table(pairs)
        else:
            table_data = _kv_table([("", _fmt(body))])
        table = Table(
            table_data,
            colWidths=[4.5 * cm, 13 * cm] if section != "Test Case Details" else None,
            splitByRow=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f1f5")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9dce6")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 0.2 * cm))
    doc.build(story)
    return buffer.getvalue()


def generate_batch_excel(rows: list) -> bytes:
    report = build_batch_report(rows)
    wb = Workbook()
    header_fill = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")

    first = True
    for section in BATCH_REPORT_SECTIONS:
        ws = wb.active if first else wb.create_sheet(section[:31])
        if first:
            ws.title = section[:31]
            first = False
        body = report.get(section)
        if section == "Test Case Details":
            headers = [
                "Test Case ID",
                "Execution Status",
                "SOAP Evaluation",
                "Accuracy",
                "Latency",
                "Audio File",
                "Individual Report",
            ]
            for col, title in enumerate(headers, start=1):
                cell = ws.cell(row=1, column=col, value=title)
                cell.fill = header_fill
                cell.font = header_font
            for idx, case in enumerate(body or [], start=2):
                ws.cell(row=idx, column=1, value=case.get("test_case_id"))
                ws.cell(row=idx, column=2, value=case.get("execution_status"))
                ws.cell(row=idx, column=3, value=case.get("soap_evaluation"))
                ws.cell(row=idx, column=4, value=case.get("accuracy"))
                ws.cell(row=idx, column=5, value=case.get("latency"))
                ws.cell(row=idx, column=6, value=case.get("audio_file"))
                ws.cell(row=idx, column=7, value=case.get("individual_report"))
        else:
            ws.cell(row=1, column=1, value="Field").fill = header_fill
            ws.cell(row=1, column=1).font = header_font
            ws.cell(row=1, column=2, value="Value").fill = header_fill
            ws.cell(row=1, column=2).font = header_font
            row_i = 2
            for key, value in _kv_rows(body or {}):
                ws.cell(row=row_i, column=1, value=key)
                ws.cell(row=row_i, column=2, value=value)
                row_i += 1
            if isinstance(body, dict) and body.get("per_case"):
                ws.cell(row=row_i, column=1, value="per_case")
                ws.cell(row=row_i, column=1).font = Font(bold=True)
                row_i += 1
                keys = list(body["per_case"][0].keys()) if body["per_case"] else []
                for col, key in enumerate(keys, start=1):
                    cell = ws.cell(row=row_i, column=col, value=key)
                    cell.fill = header_fill
                    cell.font = header_font
                row_i += 1
                for case in body["per_case"]:
                    for col, key in enumerate(keys, start=1):
                        ws.cell(row=row_i, column=col, value=case.get(key))
                    row_i += 1
        ws.column_dimensions["A"].width = 28
        ws.column_dimensions["B"].width = 50

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
