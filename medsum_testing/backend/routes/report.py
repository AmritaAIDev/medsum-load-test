"""Individual reports are GET. Total report is POST (JSON body of selected ids).

POST /report/total sends `test_ids` / `batch_id` — too structured for a
query string, so POST is the right method. `/report/<test_id>` is GET-only
for a real case id, but must also accept POST when `test_id` is the literal
`total`; otherwise Flask matches `/report/total` to this rule and returns 405.
"""

from __future__ import annotations

import io
import logging

from flask import Blueprint, jsonify, request, send_file

from medsum_testing.backend.services.config_loader import get_config
from medsum_testing.backend.services import medsum_api
from medsum_testing.backend.services.report_generator import (
    generate_batch_excel,
    generate_batch_pdf,
    generate_excel,
    generate_pdf,
    save_report_path,
)
from medsum_testing.backend.services.result_store import list_results_by_batch
from medsum_testing.backend.services.test_case_view import load_result_by_stable_id

bp = Blueprint("medsum_report", __name__)
log = logging.getLogger("medsum_report")


def _send(data: bytes, fmt: str, name: str):
    if fmt == "excel":
        return send_file(
            io.BytesIO(data),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"{name}.xlsx",
        )
    return send_file(
        io.BytesIO(data),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{name}.pdf",
    )


def _total_report():
    """Batch / total report for the current run (or explicit test_ids)."""
    body = request.get_json(silent=True) or {}
    fmt = (request.args.get("format") or body.get("format") or "pdf").lower()
    if fmt not in {"pdf", "excel"}:
        return jsonify({"error": "format must be pdf or excel"}), 400

    test_ids = body.get("test_ids") or request.args.getlist("test_id")
    batch_id = body.get("batch_id") or request.args.get("batch_id") or ""

    rows = []
    if test_ids:
        for tid in test_ids:
            result = load_result_by_stable_id(str(tid))
            if result:
                rows.append(result.to_dict())
    elif batch_id:
        rows = list_results_by_batch(str(batch_id))

    if not rows:
        return jsonify({"error": "No test results to include in the total report"}), 404

    data = generate_batch_excel(rows) if fmt == "excel" else generate_batch_pdf(rows)
    slug = (batch_id or "run")[:12]
    return _send(data, fmt, f"medsum-batch-{slug}")


@bp.route("/report/total", methods=["GET", "POST"])
def download_total_report():
    return _total_report()


@bp.route("/report/<test_id>", methods=["GET", "POST"])
def download_report(test_id: str):
    # Literal "total" must not 405: this parameterized rule is what Flask
    # binds when the dedicated /report/total route is missing or shadowed.
    if test_id == "total":
        return _total_report()
    if request.method != "GET":
        return jsonify({"error": "Use GET to download an individual report"}), 405

    result = load_result_by_stable_id(test_id)
    if not result:
        return jsonify({"error": "Test result not found"}), 404

    fmt = (request.args.get("format") or "pdf").lower()
    if fmt not in {"pdf", "excel"}:
        return jsonify({"error": "format must be pdf or excel"}), 400

    base_name = f"medsum-test-{test_id[:8]}"
    report_path = f"/api/medsum-test/report/{test_id}?format={fmt}"

    try:
        config = get_config()
        token, _ = medsum_api.authenticate_doctor(config)
        save_report_path(test_id, fmt, report_path, token, config)
        if fmt == "pdf":
            result.report_pdf_path = report_path
        else:
            result.report_excel_path = report_path
    except Exception as exc:
        log.warning("Could not save report path to Django for %s: %s", test_id, exc)

    data = generate_excel(result) if fmt == "excel" else generate_pdf(result)
    return _send(data, fmt, base_name)
