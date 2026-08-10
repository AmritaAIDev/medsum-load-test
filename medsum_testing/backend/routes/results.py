"""GET /api/medsum-test/results"""

from __future__ import annotations

from flask import Blueprint, jsonify

from medsum_testing.backend.services.result_store import list_results, load_result

bp = Blueprint("medsum_results", __name__)


@bp.route("/results", methods=["GET"])
@bp.route("/results/", methods=["GET"])
def list_all_results():
    return jsonify(list_results())


@bp.route("/results/<test_id>", methods=["GET"])
def get_result(test_id: str):
    result = load_result(test_id)
    if not result:
        return jsonify({"error": "Test result not found"}), 404
    return jsonify(result.to_dict())
