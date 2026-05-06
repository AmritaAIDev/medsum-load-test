# LOAD_TEST_SPEC

# Medsum Load Testing Specification

## 1. Purpose

Evaluate the performance, stability, and scalability of the Medsum application under concurrent doctor sessions. The primary focus is end-to-end latency measurement across the entire workflow for  analysis and course correction

> **Workflow under test:** Login → Patient Selection → Audio(base64) Send to Flask backend→ STT (Transcription) → Translation → LLM Summary →Recieved by Frontend → Audio Upload(Django backend) → Store Summary(Django backend)

---

## 2. Scope

**In scope**

- Doctor authentication and profile resolution
- Patient metadata retrieval
- AI pipeline: STT → translation → LLM summarisation via Flask `/transcribe`
- Audio upload to Django backend                                                                                                                         - Summary persistence to Django backend

---

## 3. Workflow Under Test

```
Doctor Login
    │
    ├─► Fetch Doctor Profile
    │
    └─► [Per Patient — parallel]
            │
            ├─► Fetch Patient Metadata
            │
            ├─► Transcribe  ────────────────────────────► Flask   POST /transcribe
            │       (audio sent as base64; Flask decodes and processes it)
            │       ├── ALD  (automatic language detection, if language not specified)
            │       ├── STT              (transcription-time)
            │       ├── Translation      (translation-time)
            │       └── LLM Summary     (llm-time)
            │
            ├─► Upload Audio Recording  ────────────────► Django  POST /api/audio-data/
            │       (actual audio file stored for archival; returns audio_id)
            │
            └─► Store Summary  ─────────────────────────► Django POST /api/summary-data/
                    (references audio_id from previous step)
```

**Concurrency model:** doctors run in parallel threads; patients per doctor also run in parallel; audio files within a single patient run sequentially (transcribe → audio upload → summary store).

> **Note on audio delivery:** The audio file is encoded as base64 and sent directly in the JSON body of `POST /transcribe`. The Django `/api/audio-data/` endpoint is called separately **after** transcription completes — it stores the raw recording for archival and returns the `audio_id` required by `/api/summary-data/`.

---

## 4. API Reference

| Step | Endpoint                  | Method | Expected Status | Description                                                                                                            |
| ---- | ------------------------- | ------ | --------------- | ---------------------------------------------------------------------------------------------------------------------- |
| 1    | `/api/login/`             | POST   | 200             | Doctor authentication — returns JWT token and user info                                                                |
| 1b   | `/api/user/update/{id}/`  | GET    | 200             | Doctor profile — name, department, hospital                                                                            |
| 2    | `/api/patient-data/{id}/` | GET    | 200             | Patient metadata — name, age, gender                                                                                   |
| 3    | `/transcribe`             | POST   | 200             | AI pipeline — audio sent as base64; Flask decodes it internally; returns transcription, summary, and per-stage timings |
| 4    | `/api/audio-data/`        | POST   | 201             | Upload raw audio file for archival — returns `audio_id` and `session_id`                                               |
| 5    | `/api/summary-data/`      | POST   | 201             | Store summary — references `audio_id` from Step 4; returns `summary_id`                                                |

---

## 5. Metrics

### 5.1 Per-request timing

Each metric is wall-clock elapsed time in seconds.

| Metric                           | Description                                                                                                                 | Source                                       |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| `login_time`                     | (`/api/login/`)                                                                                                             | Harness timer                                |
| `doctor_profile_time`            | (`/api/user/update/`)                                                                                                       | Harness timer                                |
| `patient_metadata_time`          | (`/api/patient-data/`)                                                                                                      | Harness timer                                |
| `user_perceived_summary_latency` | Time taken from the user's request for summary generation to the moment the summary is generated and presented to the user. | Harness timer                                |
| `audio_processing_time`          | time taken by flask endpoint to process audio from base64                                                                   | calculated form `/transcribe` response       |
| `transcription_time`             | STT processing time inside Flask                                                                                            | `/transcribe` response: `transcription-time` |
| `translation_time`               | Translation processing time inside Flask                                                                                    | `/transcribe` response: `translation-time`   |
| `llm_time`                       | LLM summarisation time inside Flask                                                                                         | `/transcribe` response: `llm-time`           |
| `flask_total_time`               | Total internal time reported by Flask                                                                                       | `/transcribe` response: `total-time`         |
| `audio_upload_time`              | Duration of `POST /api/audio-data/` — Step 4 (archival, after transcription)                                                | Harness timer                                |
| `summary_store_time`             | Duration of `POST /api/summary-data/` — Step 5                                                                              | Harness timer                                |

> `flask_total_time`=`translation_time` + `llm_time` + `transcription_time`  + `audio_processing_time`

> `user_perceived_summary_latency` = `flask_total_time` + `network overhead`

> Audio upload is independent of the AI pipeline and occurs after transcription completes.

### 5.2 Derived metrics (computed post-run)

| Metric             | Formula                          |
| ------------------ | -------------------------------- |
| End-to-end latency | `user_percieved_summary_latency` |
| Pass rate          | `passed / total × 100`           |

---

## 6. Test Scenarios

### Phase A — Sequential Baseline

Run one doctor session at a time to establish baseline latency per audio file without concurrency effects.

| Scenario | Doctors | Patients per Doctor | Repetitions |
| -------- | ------- | ------------------- | ----------- |
| A1       | 1       | 1                   | 3           |
| A2       | 1       | 5                   | 3           |

### Phase B — Concurrent Load

Run multiple doctor sessions simultaneously to measure degradation under load.

| Scenario | Doctors | Patients per Doctor | Total Sessions | Repetitions |
| -------- | ------- | ------------------- | -------------- | ----------- |
| B1       | 5       | 1                   | 5              | 3           |
| B2       | 10      | 1                   | 10             | 3           |
| B3       | 5       | 2                   | 10             | 3           |

**Inter-run cool-down:** 30 seconds between repetitions.

All repetitions within a scenario use the same audio files and patient IDs to ensure comparability.

---

## 7. Test Parameters

### 7.1 Global parameters (Advanced Settings)

| Parameter          | Description                                        | Default   |
| ------------------ | -------------------------------------------------- | --------- |
| `llm`              | LLM backend                                        | `OpenAI`  |
| `stt_model`        | STT model                                          | `Bhasini` |
| `translate_model`  | Translation model                                  | `Bhasini` |
| `template_type`    | Summary template                                   | `soap`    |
| `audio_duration_s` | Silent WAV fallback duration (if no file uploaded) | `2.0`     |

### 7.2 Per-patient parameters

| Parameter     | Description                           | Default             |
| ------------- | ------------------------------------- | ------------------- |
| `language`    | Audio language code for this patient  | `en`                |
| `audio_files` | Audio file(s) to use for this patient | silent WAV fallback |

`language` is selected individually per patient via a dropdown inside each patient row (alongside the audio upload slot). This allows a single test run to exercise multiple languages concurrently — e.g. Doctor 1 → Patient A in `hi`, Patient B in `en`. The selected language is sent to both `POST /transcribe` and `POST /api/audio-data/` for that patient.

---

## 8. Database Schema

### Table: `load_test_result`

New table — create via Django migration. **No changes to any existing table.**

```python
class LoadTestResult(models.Model):
    # Run grouping
    test_run_id             = models.UUIDField(db_index=True)
    created_at              = models.DateTimeField(auto_now_add=True)

    # Identifiers — plain integer references, no FK constraint (avoids cascade)
    doctor_id               = models.IntegerField()
    patient_id              = models.IntegerField()
    audio_id                = models.IntegerField(null=True, blank=True)
    summary_id              = models.IntegerField(null=True, blank=True)

    # Test configuration
    language                = models.CharField(max_length=10)
    llm                     = models.CharField(max_length=50)
    stt_model               = models.CharField(max_length=50)
    translate_model         = models.CharField(max_length=50)
    audio_duration_s        = models.FloatField()

    # Timing — Django layer (harness-measured)
    login_time              = models.FloatField(null=True)
    doctor_profile_time     = models.FloatField(null=True)
    patient_metadata_time   = models.FloatField(null=True)
    audio_upload_time       = models.FloatField(null=True)
    summary_store_time      = models.FloatField(null=True)

    # Timing — Flask /transcribe (server-reported)
    transcription_time      = models.FloatField(null=True)
    translation_time        = models.FloatField(null=True)
    llm_time                = models.FloatField(null=True)
    flask_total_time        = models.FloatField(null=True)
    audio_processing_time   = models.FloatField(null=True)

    # Timing — harness wall-clock for full /transcribe call
    user_percieved_summary_latency = models.FloatField(null=True)

    # Outcome
    status                  = models.CharField(max_length=10)  # "pass" | "fail"
    error_message           = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'load_test_result'
        indexes  = [models.Index(fields=['test_run_id', 'created_at'])]
```

### Linkage to existing tables

`audio_id` and `summary_id` are loose integer references — they point to rows in `audios` and `summaries` but carry no FK constraint, so deleting test data from `load_test_result` has no cascade effect on existing tables.

```
load_test_result
  ├── audio_id   ──(integer ref)──► audios.audio_id
  ├── summary_id ──(integer ref)──► summaries.summary_id
  └── test_run_id (UUID, shared across the entire run)
```

### Test data isolation — `session_id` prefix convention

Both `audios` and `summaries` already have a `session_id` column (`CharField(max_length=100)`). **Zero schema changes are needed.** The harness stamps every audio and summary it creates with a prefixed `session_id`:

| Run type                   | `session_id` written to `audios` and `summaries` |
| -------------------------- | ------------------------------------------------ |
| Automated test (harness)   | `LOADTEST_<uuid>`                                |
| Manual test (toggle in UI) | `MANUALTEST_<uuid>`                              |
| Production                 | `<uuid>` (no prefix)                             |

The `<uuid>` portion is a fresh UUID generated **per audio**, keeping each row uniquely identifiable while still carrying the test prefix.

**Queries to distinguish test from production:**

```sql
-- Production records only (exclude all test runs)
SELECT * FROM medisum_app_audios
WHERE session_id NOT LIKE 'LOADTEST_%' AND session_id NOT LIKE 'MANUALTEST_%';

-- All test records for a specific run
SELECT a.*, s.*
FROM medisum_app_audios a
JOIN medisum_app_summaries s ON s.audio_id_id = a.audio_id
WHERE a.session_id LIKE 'LOADTEST_<test_run_uuid>%';

-- Full detail: load_test_result + linked audio + summary rows
SELECT ltr.*, a.audio, a.file_duration, s.summary
FROM load_test_result ltr
LEFT JOIN medisum_app_audios    a ON a.audio_id    = ltr.audio_id
LEFT JOIN medisum_app_summaries s ON s.summary_id  = ltr.summary_id
WHERE ltr.test_run_id = '<test_run_uuid>';
```

---

## 9. Data Collection

### Harness timers (`web_app.py` → `_run_patient()`)

| Code variable       | DB field                         |
| ------------------- | -------------------------------- |
| `step1_time`        | `login_time`                     |
| `step1b_time`       | `doctor_profile_time`            |
| `patient_data_time` | `patient_metadata_time`          |
| `step4_time`        | `user_percieved_summary_latency` |
| `step5_time`        | `audio_upload_time`              |
| `step6_time`        | `summary_store_time`             |

### Flask `/transcribe` response fields

```python
timing_data.update({
    "transcription_time":    b4.get("transcription-time"),
    "translation_time":      b4.get("translation-time"),
    "llm_time":              b4.get("llm-time"),
    "flask_total_time":      b4.get("total-time"),
    "audio_processing_time": b4.get("total-time", 0)
                             - (b4.get("transcription-time", 0)
                                + b4.get("translation-time", 0)
                                + b4.get("llm-time", 0)),
})
```

### `session_id` written to existing tables

When calling `POST /api/audio-data/` (Step 4) and `POST /api/summary-data/` (Step 5), the harness sets:

```python
# per-audio UUID keeps rows unique; prefix makes them filterable
audio_uuid = str(uuid.uuid4())
prefix     = "MANUALTEST" if cfg["test_mode"] == "manual" else "LOADTEST"
client_sid = f"{prefix}_{audio_uuid}"
```

This `client_sid` is passed as `session_id` to both endpoints. Production calls from the real app have no prefix, so a simple `LIKE 'LOADTEST_%'` or `LIKE 'MANUALTEST_%'` filter separates test from production on any existing table — **with zero schema changes**.

### Persistence

A new Django endpoint (`POST /api/load-test/result/`) writes a `LoadTestResult` row after Step 5 completes. The `test_run_id` (UUID) is generated once per test run and shared across all rows belonging to that run.

---

## 10. Test Tool

**File:** `medisum/testing/web_app.py` — Flask web application, browser-based UI.

**Capabilities:**

- Multi-doctor, multi-patient session matrix
- Per-patient individual audio file upload (falls back to silent WAV)
- Per-patient language selection (sent individually to `/transcribe` and `/api/audio-data/`)
- Configurable global settings: LLM, STT model, translation model, template type, URLs
- Session mode toggle: Automated Test (`LOADTEST_`) / Manual Test (`MANUALTEST_`)
- Real-time SSE stream with per-row live status
- Excel export of timing results
- Config export / import (see Section 10.1)

**Deployment:** Render — `gunicorn --worker-class=gthread --threads=16 --timeout=120`

---

### 10.1 Test Configuration Management (Export / Import)

To reduce manual effort across repeated runs, the tool supports saving and restoring the full test setup as a portable JSON file.

#### What is saved

| Field                                  | Saved? | Notes                                                                   |
| -------------------------------------- | ------ | ----------------------------------------------------------------------- |
| Doctor phone numbers                   | Yes    |                                                                         |
| Doctor passwords                       | Yes    | Stored in plain text in the file — keep it secure                       |
| Patient IDs per doctor                 | Yes    |                                                                         |
| Language per patient                   | Yes    |                                                                         |
| Global settings (LLM, STT, URLs, etc.) | Yes    |                                                                         |
| Session mode (automated / manual)      | Yes    |                                                                         |
| Audio file contents                    | **No** | Browser security prevents this — filenames are saved as a reminder only |

#### Workflow

```
First run (setup)
  1. Fill in doctors, patients, audio, language, settings as usual
  2. Click "Export Config" → downloads  medsum_config_<date>.json
  3. Store the file alongside your test audio files (e.g. in a shared folder or git)

Subsequent runs
  1. Click "Import Config" → select the saved .json file
  2. All fields are pre-filled automatically
  3. Re-upload audio files for any patients that need them (filenames shown as reminders)
  4. Click "Preview Test Matrix" and run as normal
```

#### Config file schema

```json
{
  "version": 1,
  "saved_at": "2026-05-06T10:30:00",
  "global": {
    "llm": "OpenAI",
    "stt_model": "Bhasini",
    "translate_model": "Bhasini",
    "template_type": "soap",
    "template_id": "1",
    "duration": 2.0,
    "django_url": "https://medsum.amritaai.org",
    "flask_url": "https://test-medsum.amritaai.org/transcribe",
    "test_mode": "automated"
  },
  "doctors": [
    {
      "phone": "9876543210",
      "password": "secret",
      "patients": [
        {
          "patient_id": "101",
          "language": "en",
          "audio_filenames": ["nikita_en.wav"]
        },
        {
          "patient_id": "102",
          "language": "hi",
          "audio_filenames": ["nikita_hi.wav"]
        }
      ]
    }
  ]
}
```

> `audio_filenames` is informational only — it reminds the tester which files to re-upload. Audio bytes are never written to the file.

---

## 11. Reporting & Analysis

After each test run, compute the following from `load_test_result`:

| Analysis                       | Query basis                                                                             |
| ------------------------------ | --------------------------------------------------------------------------------------- |
| End-to-end latency per session | `to be decided`                                                                         |
| Batch throughput               | `Σ(audio_duration_s) / 60 ÷ batch_wall_time_min` per `test_run_id`                      |
| Latency degradation under load | Compare median `transcribe_rtt` across scenarios A1 → B2                                |
| Bottleneck identification      | Compare `transcription_time`, `translation_time`, `llm_time` as % of `flask_total_time` |
| Pass rate                      | `COUNT(status='pass') / COUNT(*) × 100` per `test_run_id`                               |

---