"""GET /api/medsum-test/results"""

from __future__ import annotations

import traceback

import requests
from flask import Blueprint, jsonify

from medsum_testing.backend.services.config_loader import get_config
from medsum_testing.backend.services.drive_service import (
    _match_key,
    get_drive_service,
    list_test_cases,
)
from medsum_testing.backend.services.medsum_api import authenticate_doctor, verify_patient
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

        folders_resp = service.files().list(
            q=f"'{root_id}' in parents and trashed = false",
            fields="files(id, name, mimeType)",
            pageSize=200,
        ).execute()

        folders = folders_resp.get("files", [])
        dump = []

        for folder in folders:
            files_resp = service.files().list(
                q=f"'{folder['id']}' in parents and trashed = false",
                fields="files(id, name, mimeType, fileExtension, size)",
                pageSize=200,
            ).execute()

            files = files_resp.get("files", [])
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
