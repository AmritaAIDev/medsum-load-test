"""Clinical fact accuracy API for the API Testing Dashboard.

GET /api/batches/<batch_id>/accuracy-by-category/
GET /api/batches/<batch_id>/accuracy/<category>/
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from medsum_testing.backend.services.accuracy_by_category import (
    AccuracyCalculator,
    SOAP_CATEGORIES,
    get_cached_metrics,
    resolve_category_name,
)

bp = Blueprint("clinical_accuracy", __name__)


def _query_filters() -> tuple[str, str, list[str]]:
    test_type = (
        request.args.get("test_type")
        or request.args.get("testType")
        or "All"
    ).strip() or "All"
    model = (request.args.get("model") or "All").strip() or "All"
    raw_ids = request.args.get("batch_ids") or request.args.get("batchIds") or ""
    batch_ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
    return test_type, model, batch_ids


def _missing_batch(batch_id: str):
    return jsonify({
        "status": "error",
        "error": "Batch not found",
        "batch_id": batch_id,
    }), 404


@bp.route("/api/batches/<batch_id>/accuracy-by-category/", methods=["GET"])
@bp.route("/api/batches/<batch_id>/accuracy-by-category", methods=["GET"])
@bp.route("/api/medsum-test/batches/<batch_id>/accuracy-by-category/", methods=["GET"])
@bp.route("/api/medsum-test/batches/<batch_id>/accuracy-by-category", methods=["GET"])
def get_batch_accuracy_metrics(batch_id: str):
    test_type, model, batch_ids = _query_filters()
    payload = dict(
        get_cached_metrics(
            batch_id,
            test_type=test_type,
            model=model,
            batch_ids=batch_ids or None,
        )
    )
    found = payload.pop("_batch_found", True)
    if not found:
        return _missing_batch(batch_id)
    return jsonify({"status": "success", "data": payload})


@bp.route("/api/batches/<batch_id>/accuracy/<path:category>/", methods=["GET"])
@bp.route("/api/batches/<batch_id>/accuracy/<path:category>", methods=["GET"])
@bp.route("/api/medsum-test/batches/<batch_id>/accuracy/<path:category>/", methods=["GET"])
@bp.route("/api/medsum-test/batches/<batch_id>/accuracy/<path:category>", methods=["GET"])
def get_category_details(batch_id: str, category: str):
    name = resolve_category_name(category)
    if not name:
        return jsonify({
            "status": "error",
            "error": "Unknown category",
            "category": category,
            "allowed": list(SOAP_CATEGORIES),
        }), 400
    test_type, model, batch_ids = _query_filters()
    calc = AccuracyCalculator(
        batch_id,
        test_type=test_type,
        model=model,
        batch_ids=batch_ids or None,
    )
    if not calc.batch_exists():
        return _missing_batch(batch_id)
    return jsonify({
        "status": "success",
        "data": {
            "batch_id": calc.batch_id if not calc.batch_ids else ",".join(calc.batch_ids),
            "category": name,
            "test_type": calc.test_type,
            "model": calc.model,
            "runs": calc.get_category_run_details(name),
        },
    })
