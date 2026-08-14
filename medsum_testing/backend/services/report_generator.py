"""PDF and Excel report generation."""

from __future__ import annotations

import io
import json
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from medsum_testing.backend.models.test_result import TestResult


def _fmt(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _section_rows(result: TestResult) -> list[tuple[str, str]]:
    tc = result.transcription_comparison
    sc = result.summary_comparison
    mc = result.medication_comparison
    rc = result.regression_comparison

    rows = [
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


def generate_pdf(test_result: TestResult) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.5 * cm, leftMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        fontSize=16,
        spaceAfter=12,
        textColor=colors.HexColor("#2563eb"),
    )
    label_style = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=colors.HexColor("#374151"),
    )
    value_style = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
    )

    story = [
        Paragraph("MEDSUM Accuracy Test Report", title_style),
        Spacer(1, 0.3 * cm),
    ]

    table_data = []
    for label, value in _section_rows(test_result):
        safe_value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe_value = safe_value.replace("\n", "<br/>")
        table_data.append(
            [
                Paragraph(label, label_style),
                Paragraph(safe_value[:8000], value_style),
            ]
        )

    table = Table(table_data, colWidths=[5.5 * cm, 12 * cm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f1f5")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9dce6")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
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
