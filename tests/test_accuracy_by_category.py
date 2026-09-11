"""Clinical fact accuracy table: matcher, thresholds, calculator, API."""

from __future__ import annotations

from flask import Flask

from medsum_testing.backend.routes.accuracy import bp as accuracy_bp
from medsum_testing.backend.services.accuracy_by_category import (
    SOAP_CATEGORIES,
    AccuracyCalculator,
    FactMatcher,
    apply_accuracy_and_status,
    category_status,
    clear_accuracy_cache,
    empty_category_metrics,
)


def _fact(field, result, ground_truth="cough", generated="cough"):
    return {
        "field": field,
        "base_field": field,
        "ground_truth": ground_truth,
        "generated": generated,
        "result": result,
        "value": ground_truth,
    }


def _run(batch_id, facts, **extra):
    payload = {
        "batch_id": batch_id,
        "test_id": extra.pop("test_id", "tc-1"),
        "audio_filename": extra.pop("audio_filename", "case.mp3"),
        "has_soap_ground_truth": True,
        "soap_comparison": {"gt_vs_generated": {"facts": facts}},
        "ai_model_used": extra.pop("ai_model_used", "gpt-4o-mini"),
        "test_type": extra.pop("test_type", "accuracy"),
    }
    payload.update(extra)
    return payload


def test_fact_matcher_similarity_and_labels():
    matcher = FactMatcher()
    assert matcher.calculate_similarity("fever for 3 days", "fever for 3 days") == 1.0
    assert matcher.calculate_similarity("", "x") == 0.0
    close = matcher.calculate_similarity(
        "gargle with warm salt water",
        "gargle with salt water",
    )
    assert close >= 0.7

    matched = matcher.match_facts(
        ["cough for 5 days", "sore throat"],
        ["cough for 5 days", "unrelated hallucination"],
    )
    assert matched["ground_truth"] == 2
    assert matched["correct"] == 1
    assert matched["missed"] == 1
    assert matched["invented"] == 1
    assert matched["wrong"] == 0

    partial = matcher.match_facts(["acute bacterial sinusitis"], ["sinus infection noted"])
    assert partial["wrong"] + partial["missed"] + partial["correct"] == 1


def test_category_status_pass_review_fail_and_safety():
    threshold = {
        "max_missed_pct": 10,
        "max_wrong_pct": 10,
        "max_invented": None,
        "zero_missed": False,
        "safety_critical": False,
    }
    passing = {
        "ground_truth": 100,
        "missed": 5,
        "wrong": 5,
        "invented": 0,
        "has_ground_truth": True,
    }
    assert category_status(passing, threshold) == "pass"

    review = dict(passing, missed=8)
    assert category_status(review, threshold) == "review"

    failing = dict(passing, missed=20)
    assert category_status(failing, threshold) == "fail"

    diagnosis = {
        "max_missed_pct": 5,
        "max_wrong_pct": 5,
        "max_invented": 0,
        "zero_missed": False,
        "safety_critical": True,
    }
    invented = {
        "ground_truth": 20,
        "missed": 0,
        "wrong": 0,
        "invented": 1,
        "has_ground_truth": True,
    }
    assert category_status(invented, diagnosis) == "fail"

    allergy = {
        "max_missed_pct": 0,
        "max_wrong_pct": 5,
        "max_invented": 0,
        "zero_missed": True,
        "safety_critical": True,
    }
    missed = {
        "ground_truth": 10,
        "missed": 1,
        "wrong": 0,
        "invented": 0,
        "has_ground_truth": True,
    }
    assert category_status(missed, allergy) == "fail"

    empty = empty_category_metrics()
    assert category_status(empty, threshold) == "na"
    row = apply_accuracy_and_status(
        {"ground_truth": 20, "correct": 19, "missed": 1, "wrong": 0, "invented": 0},
        threshold,
    )
    assert row["accuracy_percent"] == 95.0
    assert row["status"] == "pass"


def test_accuracy_calculator_aggregates_classified_facts():
    facts = [
        _fact("Chief complaint", "Correct", "fever", "fever"),
        _fact("History of present illness", "Missing", "cough", ""),
        _fact("Diagnosis", "Correct", "URI", "URI"),
        _fact("Diagnosis", "Hallucination", "", "sepsis"),
        _fact("Drug name", "Incorrect", "azithromycin", "azithro 250"),
        _fact("Instructions", "Correct", "after food", "after food"),
        _fact("Investigations", "Missing", "CBC", ""),
        _fact("Blood pressure", "Correct", "120/80", "120/80"),
        _fact("Allergy", "Correct", "NKA", "NKA"),
        _fact("Follow-up", "Missing", "3 days", ""),
    ]
    calc = AccuracyCalculator(
        "BATCH-1",
        runs=[_run("BATCH-1", facts)],
    )
    payload = calc.get_all_metrics()
    cats = payload["categories"]
    assert set(cats) == set(SOAP_CATEGORIES)
    assert cats["Symptoms & History"]["ground_truth"] == 2
    assert cats["Symptoms & History"]["correct"] == 1
    assert cats["Symptoms & History"]["missed"] == 1
    assert cats["Diagnosis"]["invented"] == 1
    assert cats["Diagnosis"]["status"] == "fail"
    assert cats["Medicines"]["wrong"] == 1
    assert cats["Allergies & Follow-up Plan"]["missed"] == 1
    assert cats["Allergies & Follow-up Plan"]["status"] == "fail"
    overall = payload["overall"]
    assert overall["ground_truth"] == 9
    assert overall["correct"] == 5
    assert overall["accuracy_percent"] == 55.6
    assert overall["status"] == "fail"
    assert payload["review_ratio"] == 0.8
    assert calc.batch_exists() is True
    recordings = payload["recordings"]
    assert len(recordings) == 1
    rec = recordings[0]
    assert rec["tc_ref"]
    assert rec["correct"] == 5
    assert rec["ground_truth"] == 9
    assert rec["missed"] == 3
    assert rec["wrong"] == 1
    assert rec["invented"] == 1
    assert rec["status"] == "FAIL"
    assert rec["has_safety_flag"] is True
    assert "Has invented fact" in rec["safety_flags"]


def test_recording_rows_pass_when_thresholds_met():
    facts = [
        _fact("Chief complaint", "Correct", "fever", "fever"),
        _fact("Diagnosis", "Correct", "URI", "URI"),
        _fact("Drug name", "Correct", "azithromycin", "azithromycin"),
        _fact("Allergy", "Correct", "NKA", "NKA"),
    ]
    calc = AccuracyCalculator(
        "B-pass",
        runs=[_run(
            "B-pass",
            facts,
            test_id="tc-pass",
            tc_ref="TC-HI-014",
            audio_duration_seconds=328,
            transcription_result={"total-time": 28, "audio_length": 328},
        )],
    )
    rows = calc.get_recording_rows()
    assert len(rows) == 1
    assert rows[0]["tc_ref"] == "TC-HI-014"
    assert rows[0]["duration_seconds"] == 328
    assert rows[0]["latency_seconds"] == 28
    assert rows[0]["correct"] == 4
    assert rows[0]["ground_truth"] == 4
    assert rows[0]["status"] == "PASS"
    assert rows[0]["test_case_number"] == "TC-HI-014"


def test_recordings_follow_selected_batch():
    runs = [
        _run("02-Sep-2026 | 014", [_fact("Diagnosis", "Correct", "URI", "URI")], tc_ref="TC-A"),
        _run("02-Sep-2026 | 021", [_fact("Diagnosis", "Incorrect", "URI", "cold")], tc_ref="TC-B"),
    ]
    selected = AccuracyCalculator(
        "all",
        batch_ids=["02-Sep-2026 | 021"],
        runs=runs,
    )
    rows = selected.get_recording_rows()
    assert [row["tc_ref"] for row in rows] == ["TC-B"]
    assert rows[0]["status"] == "FAIL"

    all_rows = AccuracyCalculator("all", runs=runs).get_recording_rows()
    assert [row["tc_ref"] for row in all_rows] == ["TC-A", "TC-B"]


def test_accuracy_calculator_filters_model_and_missing_batch():
    runs = [
        _run("B-1", [_fact("Diagnosis", "Correct", "URI", "URI")], ai_model_used="gpt-4o-mini"),
        _run("B-1", [_fact("Diagnosis", "Correct", "URI", "URI")], ai_model_used="deepseek"),
        _run("B-2", [_fact("Diagnosis", "Correct", "URI", "URI")]),
    ]
    filtered = AccuracyCalculator("B-1", model="deepseek", runs=runs)
    metrics = filtered.calculate_accuracy_percentages()
    assert metrics["Diagnosis"]["ground_truth"] == 1
    assert metrics["Diagnosis"]["runs_evaluated"] == 1

    missing = AccuracyCalculator("missing-batch", runs=runs)
    assert missing.batch_exists() is False


def test_accuracy_calculator_na_without_ground_truth():
    calc = AccuracyCalculator(
        "B-empty",
        runs=[{
            "batch_id": "B-empty",
            "has_soap_ground_truth": False,
            "ai_model_used": "gpt-4o-mini",
        }],
    )
    payload = calc.get_all_metrics()
    assert payload["overall"]["status"] == "na"
    assert payload["overall"]["accuracy_percent"] is None
    assert "not calculated" in payload["note"].lower()


def _api_client():
    app = Flask(__name__)
    app.register_blueprint(accuracy_bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_accuracy_api_success_and_filters(monkeypatch):
    clear_accuracy_cache()
    sample = {
        "batch_id": "BATCH-20260731-00001",
        "categories": {
            name: {
                "ground_truth": 10,
                "correct": 9,
                "missed": 1,
                "wrong": 0,
                "invented": 0,
                "accuracy_percent": 90.0,
                "runs_evaluated": 2,
                "status": "pass",
                "has_ground_truth": True,
            }
            for name in SOAP_CATEGORIES
        },
        "overall": {
            "ground_truth": 70,
            "correct": 63,
            "missed": 7,
            "wrong": 0,
            "invented": 0,
            "accuracy_percent": 90.0,
            "status": "pass",
            "has_ground_truth": True,
            "categories_passed": 7,
            "categories_total": 7,
        },
        "thresholds": {},
        "review_ratio": 0.8,
        "_batch_found": True,
    }

    captured = {}

    def fake_cached(batch_id, test_type="All", model="All", **kwargs):
        captured["batch_id"] = batch_id
        captured["test_type"] = test_type
        captured["model"] = model
        captured["batch_ids"] = kwargs.get("batch_ids")
        return dict(sample)

    monkeypatch.setattr(
        "medsum_testing.backend.routes.accuracy.get_cached_metrics",
        fake_cached,
    )
    client = _api_client()
    resp = client.get(
        "/api/batches/BATCH-20260731-00001/accuracy-by-category/"
        "?test_type=accuracy&model=gpt-4o-mini"
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["data"]["batch_id"] == "BATCH-20260731-00001"
    assert "Symptoms & History" in body["data"]["categories"]
    assert "_batch_found" not in body["data"]
    assert captured["test_type"] == "accuracy"
    assert captured["model"] == "gpt-4o-mini"


def test_accuracy_api_missing_batch(monkeypatch):
    monkeypatch.setattr(
        "medsum_testing.backend.routes.accuracy.get_cached_metrics",
        lambda *a, **k: {"_batch_found": False, "batch_id": "missing"},
    )
    client = _api_client()
    resp = client.get("/api/batches/missing/accuracy-by-category/")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Batch not found"


def test_recordings_api_returns_rows(monkeypatch):
    class FakeCalc:
        batch_id = "02-Sep-2026 | 021"
        batch_ids = []

        def batch_exists(self):
            return True

        def get_recordings_payload(self, status_filter=""):
            return {
                "batch_id": "02-Sep-2026 | 021",
                "total_recordings": 2,
                "recordings": [
                    {
                        "test_case_number": "TC-HI-041",
                        "status": "PASS",
                        "correct": 20,
                        "ground_truth": 22,
                    }
                ] if status_filter != "invented" else [],
            }

    monkeypatch.setattr(
        "medsum_testing.backend.routes.accuracy.AccuracyCalculator",
        lambda *a, **k: FakeCalc(),
    )
    client = _api_client()
    resp = client.get("/api/batches/all/recordings/?batch_ids=02-Sep-2026 | 021")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["data"]["total_recordings"] == 2
    assert body["data"]["recordings"][0]["test_case_number"] == "TC-HI-041"


def test_category_details_unknown_and_ok(monkeypatch):
    client = _api_client()
    bad = client.get("/api/batches/B-1/accuracy/NotACategory/")
    assert bad.status_code == 400

    class FakeCalc:
        batch_id = "B-1"
        batch_ids = []
        test_type = "All"
        model = "All"

        def batch_exists(self):
            return True

        def get_category_run_details(self, category):
            return [{"test_id": "tc-1", "category": category, "missed": 1}]

    monkeypatch.setattr(
        "medsum_testing.backend.routes.accuracy.AccuracyCalculator",
        lambda *a, **k: FakeCalc(),
    )
    ok = client.get("/api/batches/B-1/accuracy/Diagnosis/")
    assert ok.status_code == 200
    assert ok.get_json()["data"]["runs"][0]["missed"] == 1


def test_recording_details_includes_category_facts():
    facts = [
        _fact("Chief complaint", "Correct", "fever", "fever"),
        _fact("History of present illness", "Missing", "cough for 3 days", ""),
        _fact("Diagnosis", "Incorrect", "URI", "cold"),
        _fact("Diagnosis", "Hallucination", "", "sepsis"),
        _fact("Drug name", "Correct", "azithromycin", "azithromycin"),
        _fact("Allergy", "Missing", "Penicillin", ""),
    ]
    calc = AccuracyCalculator(
        "BATCH-DET",
        runs=[_run(
            "BATCH-DET",
            facts,
            test_id="run-det-1",
            tc_ref="TC-HI-041",
            run_ref="RUN-20260731-00001",
            audio_duration_seconds=487,
            transcription_result={"total-time": 22, "audio_length": 487},
            ai_model_used="gpt-4o-mini",
        )],
    )
    payload = calc.get_recording_details("TC-HI-041")
    assert payload is not None
    assert payload["recording"]["test_case_number"] == "TC-HI-041"
    assert payload["recording"]["run_number"] == "RUN-20260731-00001"
    assert payload["recording"]["duration_seconds"] == 487
    assert payload["recording"]["model_used"] == "gpt-4o-mini"

    summary = payload["summary"]
    assert summary["total_ground_truth"] == 5
    assert summary["total_correct"] == 2
    assert summary["total_missed"] == 2
    assert summary["total_wrong"] == 1
    assert summary["total_invented"] == 1
    assert summary["overall_accuracy_percent"] == 40.0
    assert summary["mean_latency_seconds"] == 22

    cats = payload["categories"]
    assert cats["Symptoms & History"]["missed"] == 1
    assert "cough for 3 days" in cats["Symptoms & History"]["missed_facts"][0]
    assert cats["Diagnosis"]["wrong"] == 1
    assert "expected: URI" in cats["Diagnosis"]["wrong_facts"][0]
    assert cats["Diagnosis"]["invented"] == 1
    assert "sepsis" in cats["Diagnosis"]["invented_facts"][0]
    assert cats["Medicines"]["accuracy_percent"] == 100.0
    assert cats["Investigation"]["accuracy_percent"] is None
    assert cats["Allergies & Follow-up Plan"]["missed"] == 1

    by_id = calc.get_recording_details("run-det-1")
    assert by_id is not None
    assert by_id["recording"]["test_id"] == "run-det-1"
    assert calc.get_recording_details("missing-recording") is None


def test_recording_details_api(monkeypatch):
    sample = {
        "recording": {
            "test_case_number": "TC-HI-041",
            "run_number": "run-1",
            "duration_seconds": 100,
            "model_used": "gpt-4o-mini",
        },
        "summary": {
            "total_ground_truth": 10,
            "total_correct": 5,
            "total_missed": 2,
            "total_wrong": 2,
            "total_invented": 1,
            "overall_accuracy_percent": 50.0,
            "mean_latency_seconds": 12,
            "asr_wer": None,
            "real_time_factor": None,
        },
        "categories": {
            name: {
                "ground_truth": 0,
                "correct": 0,
                "missed": 0,
                "wrong": 0,
                "invented": 0,
                "accuracy_percent": None,
                "missed_facts": [],
                "wrong_facts": [],
                "invented_facts": [],
            }
            for name in SOAP_CATEGORIES
        },
    }

    class FakeCalc:
        def batch_exists(self):
            return True

        def get_recording_details(self, recording_id):
            if recording_id == "missing":
                return None
            return sample

    monkeypatch.setattr(
        "medsum_testing.backend.routes.accuracy.AccuracyCalculator",
        lambda *a, **k: FakeCalc(),
    )
    client = _api_client()
    ok = client.get("/api/batches/BATCH-1/recordings/run-1/details/")
    assert ok.status_code == 200
    body = ok.get_json()
    assert body["status"] == "success"
    assert body["data"]["recording"]["test_case_number"] == "TC-HI-041"
    assert body["data"]["summary"]["total_correct"] == 5

    missing = client.get("/api/batches/BATCH-1/recordings/missing/details/")
    assert missing.status_code == 404
    assert missing.get_json()["error"] == "Recording not found"
