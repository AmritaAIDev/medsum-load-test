"""GET /api/medsum-test/report/:test_id"""

from __future__ import annotations

import io

from flask import Blueprint, jsonify, request, send_file

from medsum_testing.backend.services.report_generator import generate_excel, generate_pdf
from medsum_testing.backend.services.result_store import load_result

bp = Blueprint("medsum_report", __name__)


@bp.route("/report/<test_id>", methods=["GET"])
def download_report(test_id: str):
    result = load_result(test_id)
    if not result:
        return jsonify({"error": "Test result not found"}), 404

    fmt = (request.args.get("format") or "pdf").lower()
    base_name = f"medsum-test-{test_id[:8]}"

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
