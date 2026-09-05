"""GET /api/medsum-test/results"""

from __future__ import annotations

import logging
import mimetypes
import traceback

import requests
from flask import Blueprint, Response, jsonify

from medsum_testing.backend.models.test_result import attach_row_display, is_done_status
from medsum_testing.backend.services.config_loader import get_config
from medsum_testing.backend.services.drive_service import (
    _match_key,
    download_file,
    get_drive_service,
    list_drive_children,
    list_test_cases,
)
from medsum_testing.backend.services.medsum_api import authenticate_doctor, verify_patient
from medsum_testing.backend.services.result_store import (
    list_results,
    list_results_by_batch,
)
from medsum_testing.backend.services.test_case_view import (
    load_result_by_stable_id,
    prefer_local_batch_runs,
    stable_test_id,
)
from medsum_testing.backend.services.soap_gt_comparison_report import (
    build_soap_gt_comparison_report,
)

bp = Blueprint("medsum_results", __name__)
log = logging.getLogger("medsum_results")


def _normalize_batch_response(data: dict, source: str = "django") -> dict:
    stats = data.get("stats") or {}
    runs = data.get("results") or data.get("runs") or []
    completed = stats.get("completed", data.get("completed", 0))
    failed = stats.get("failed", data.get("failed", 0))
    pending = stats.get("pending", data.get("pending", 0))
    total = stats.get("total", data.get("total", len(runs)))
    avg_accuracy = stats.get("avg_accuracy", data.get("avg_accuracy"))
    passed = stats.get("passed_output_validation", data.get("passed", 0))
    if any(r.get("status") for r in runs):
        passed = sum(1 for r in runs if is_done_status(r.get("status")))

    return {
        "batch_id": data.get("batch_id"),
        "batch_ref": data.get("batch_ref"),
        "total": total,
        "completed": completed,
        "failed": failed,
        "pending": pending,
        "passed": passed,
        "avg_accuracy": avg_accuracy,
        "results": runs,
        "source": source,
    }


def _get_batch_from_local(batch_id: str):
    """Build batch summary from local JSON — these rows have harness test_id."""
    batch_runs = list_results_by_batch(batch_id)
    if not batch_runs:
        batch_runs = [r for r in list_results() if r.get("batch_id") == batch_id]

    if not batch_runs:
        return jsonify({"error": "Batch not found"}), 404

    scores = [
        r.get("similarity_score")
        or (r.get("comparison") or {}).get("similarity_score")
        for r in batch_runs
        if r.get("similarity_score") is not None
        or (r.get("comparison") or {}).get("similarity_score") is not None
    ]
    completed = sum(1 for r in batch_runs if r.get("status") == "complete")
    failed = sum(1 for r in batch_runs if r.get("status") == "failed")
    pending = sum(1 for r in batch_runs if r.get("status") in ("pending", "running"))
    passed = sum(1 for r in batch_runs if is_done_status(r.get("status")))

    batch_ref = next(
        (str(r.get("batch_id") or "").strip() for r in batch_runs if r.get("batch_id")),
        str(batch_id or ""),
    )

    return jsonify(_normalize_batch_response({
        "batch_id": batch_id,
        "batch_ref": batch_ref,
        "total": len(batch_runs),
        "completed": completed,
        "failed": failed,
        "pending": pending,
        "passed": passed,
        "avg_accuracy": round(sum(scores) / len(scores), 1) if scores else None,
        "results": batch_runs,
    }, source="local"))


@bp.route("/results", methods=["GET"])
@bp.route("/results/", methods=["GET"])
def list_all_results():
    return jsonify(list_results())


@bp.route("/results/batch/<batch_id>", methods=["GET"])
def get_batch_results(batch_id: str):
    """Batch rows for the Test Run table / View.

    Prefer local JSON (named by harness test_id). Django batch payloads often
    identify runs by integer PK `id`, which made View open the wrong case.
    """
    local_runs = list_results_by_batch(batch_id)
    if not local_runs:
        local_runs = [r for r in list_results() if r.get("batch_id") == batch_id]
    if local_runs:
        scores = [
            r.get("similarity_score")
            or (r.get("comparison") or {}).get("similarity_score")
            for r in local_runs
            if r.get("similarity_score") is not None
            or (r.get("comparison") or {}).get("similarity_score") is not None
        ]
        completed = sum(1 for r in local_runs if r.get("status") == "complete")
        failed = sum(1 for r in local_runs if r.get("status") == "failed")
        pending = sum(1 for r in local_runs if r.get("status") in ("pending", "running"))
        passed = sum(1 for r in local_runs if is_done_status(r.get("status")))
        batch_ref = next(
            (str(r.get("batch_id") or "").strip() for r in local_runs if r.get("batch_id")),
            str(batch_id or ""),
        )
        return jsonify(_normalize_batch_response({
            "batch_id": batch_id,
            "batch_ref": batch_ref,
            "total": len(local_runs),
            "completed": completed,
            "failed": failed,
            "pending": pending,
            "passed": passed,
            "avg_accuracy": round(sum(scores) / len(scores), 1) if scores else None,
            "results": local_runs,
        }, source="local"))

    try:
        config = get_config()
        token, _ = authenticate_doctor(config)

        base_url = config["backends"]["django_base_url"].rstrip("/")
        batch_path = config["backends"].get(
            "at_batch_detail", "/api/accuracy-testing/batches/"
        )
        url = f"{base_url}{batch_path}{batch_id}/"

        log.info("GET_BATCH → GET %s", url)
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        log.info("GET_BATCH ← %s", resp.status_code)

        if resp.status_code == 200:
            body = resp.json()
            django_runs = body.get("results") or body.get("runs") or []
            body["results"] = prefer_local_batch_runs([], django_runs)
            return jsonify(_normalize_batch_response(body))
        if resp.status_code == 404:
            return _get_batch_from_local(batch_id)
        return jsonify({"error": f"Django returned {resp.status_code}"}), resp.status_code

    except Exception as exc:
        log.error("GET_BATCH error: %s\n%s", exc, traceback.format_exc())
        return _get_batch_from_local(batch_id)


@bp.route("/stats", methods=["GET"])
def get_stats():
    try:
        config = get_config()
        token, _ = authenticate_doctor(config)
        base_url = config["backends"]["django_base_url"].rstrip("/")
        stats_path = config["backends"].get(
            "at_stats", "/api/accuracy-testing/stats/"
        )
        url = f"{base_url}{stats_path}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify({"error": str(resp.status_code)}), resp.status_code
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/results/<test_id>", methods=["GET"])
def get_result(test_id: str):
    result = load_result_by_stable_id(test_id)
    if not result:
        return jsonify({"error": "Test result not found"}), 404
    data = result.to_dict()
    if stable_test_id(data) != test_id:
        return jsonify({"error": "Test result not found"}), 404
    payload = attach_row_display(data)
    payload["soap_gt_comparison_report"] = build_soap_gt_comparison_report(payload)
    return jsonify(payload)


@bp.route("/results/<test_id>/audio", methods=["GET"])
def get_result_audio(test_id: str):
    """Stream this case's audio. Source file is never deleted."""
    result = load_result_by_stable_id(test_id)
    if not result or stable_test_id(result.to_dict()) != test_id:
        return jsonify({"error": "Test result not found"}), 404
    file_id = (result.drive_audio_file_id or "").strip()
    if not file_id:
        return jsonify({"error": "No playable audio stored for this test case"}), 404
    try:
        audio_bytes = download_file(file_id)
    except Exception as exc:
        log.error("AUDIO stream failed for %s: %s", test_id, exc)
        return jsonify({"error": "Audio could not be loaded"}), 502
    filename = result.drive_audio_filename or result.audio_filename or "audio"
    safe_name = "".join(
        ch for ch in filename if ch.isalnum() or ch in "._-"
    ) or "audio"
    mime = mimetypes.guess_type(filename)[0] or "audio/mpeg"
    return Response(
        audio_bytes,
        mimetype=mime,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


@bp.route("/debug/drive", methods=["GET"])
def debug_drive():
    try:
        config = get_config()
        service = get_drive_service(config)

        folder_id = config["google_drive"]["root_folder_id"]
        folder = service.files().get(
            fileId=folder_id,
            fields="id, name",
        ).execute()

        cases = list_test_cases(config)
        test_key = _match_key("05_Hindi_06_script")

        return jsonify(
            {
                "status": "ok",
                "code_version": "v8",
                "match_key_test": {
                    "input": "05_Hindi_06_script",
                    "output": test_key,
                    "expected": "hindi_06",
                    "correct": test_key == "hindi_06",
                },
                "folder_name": folder.get("name"),
                "folder_id": folder_id,
                "test_cases_found": len(cases),
                "soap_gt_count": sum(1 for c in cases if c.get("has_soap_ground_truth")),
                "test_cases": cases,
            }
        )
    except Exception as exc:
        return (
            jsonify(
                {
                    "status": "error",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            ),
            500,
        )


@bp.route("/debug/patient/list", methods=["GET"])
def debug_patient_list():
    """Try common patient list endpoints and return the first working one."""
    try:
        config = get_config()
        token, doctor_id = authenticate_doctor(config)
        base_url = config["backends"]["django_base_url"].rstrip("/")

        attempts = []
        for path in ["/api/patient-data/", "/api/patients/", "/api/patient/"]:
            url = f"{base_url}{path}"
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            attempts.append({"url": url, "status": resp.status_code})
            if resp.status_code == 200:
                data = resp.json()
                results = data if isinstance(data, list) else data.get("results", [])
                return jsonify(
                    {
                        "status": "ok",
                        "doctor_id": doctor_id,
                        "url": url,
                        "count": len(results),
                        "first_5": results[:5],
                    }
                )

        return jsonify(
            {
                "status": "error",
                "error": "No patient list endpoint found",
                "attempts": attempts,
            }
        ), 404

    except Exception as exc:
        return (
            jsonify(
                {
                    "status": "error",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            ),
            500,
        )


@bp.route("/debug/patient/<patient_id>", methods=["GET"])
def debug_patient(patient_id: str):
    """
    Test patient lookup for a given ID.
    Usage: GET /api/medsum-test/debug/patient/123
    """
    try:
        config = get_config()
        token, doctor_id = authenticate_doctor(config)
        patient_data = verify_patient(patient_id, token, config)

        return jsonify(
            {
                "status": "ok",
                "doctor_id": doctor_id,
                "patient_id": patient_id,
                "patient_data": patient_data,
            }
        )

    except Exception as exc:
        return (
            jsonify(
                {
                    "status": "error",
                    "patient_id": patient_id,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            ),
            500,
        )


@bp.route("/debug/drive-raw", methods=["GET"])
def debug_drive_raw():
    """
    Dumps the raw Drive API response for every subfolder.
    Use this to see exact filenames and MIME types as Drive returns them.
    """
    try:
        config = get_config()
        service = get_drive_service(config)
        root_id = config["google_drive"]["root_folder_id"]

        folders = list_drive_children(service, root_id, fields="files(id, name, mimeType)")
        dump = []

        for folder in folders:
            files = list_drive_children(
                service,
                folder["id"],
                fields="files(id, name, mimeType, fileExtension, size)",
            )
            dump.append(
                {
                    "folder_id": folder["id"],
                    "folder_name": folder["name"],
                    "folder_mime": folder["mimeType"],
                    "file_count": len(files),
                    "files": [
                        {
                            "name": f["name"],
                            "mimeType": f["mimeType"],
                            "fileExtension": f.get("fileExtension", ""),
                            "size": f.get("size", "N/A"),
                            "id": f["id"],
                        }
                        for f in files
                    ],
                }
            )

        return jsonify(
            {
                "status": "ok",
                "root_folder_id": root_id,
                "subfolder_count": len(folders),
                "dump": dump,
            }
        )

    except Exception as exc:
        return (
            jsonify(
                {
                    "status": "error",
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            ),
            500,
        )