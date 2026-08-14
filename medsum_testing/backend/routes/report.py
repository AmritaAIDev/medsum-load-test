"""GET /api/medsum-test/report/:test_id"""

from __future__ import annotations

import io
import logging

from flask import Blueprint, jsonify, request, send_file

from medsum_testing.backend.services.config_loader import get_config
from medsum_testing.backend.services import medsum_api
from medsum_testing.backend.services.report_generator import (
    generate_excel,
    generate_pdf,
    save_report_path,
)
from medsum_testing.backend.services.result_store import load_result

bp = Blueprint("medsum_report", __name__)
log = logging.getLogger("medsum_report")


@bp.route("/report/<test_id>", methods=["GET"])
def download_report(test_id: str):
    result = load_result(test_id)
    if not result:
        return jsonify({"error": "Test result not found"}), 404

    fmt = (request.args.get("format") or "pdf").lower()
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

    if fmt == "excel":
        data = generate_excel(result)
        return send_file(
            io.BytesIO(data),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"{base_name}.xlsx",
        )

    if fmt == "pdf":
        data = generate_pdf(result)
        return send_file(
            io.BytesIO(data),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{base_name}.pdf",
        )

    return jsonify({"error": "format must be pdf or excel"}), 400
