# Changelog — 2026-05-06

**Project:** Medisum Load Test Console (`medisum/testing/`)
**Session scope:** Bug fixes, UX improvements, new metrics, spec updates, and user manual creation

---

## Table of Changes

| # | Change Group | Files Affected | Type |
|---|---|---|---|
| 1 | Excel export — stale key names causing empty columns | `web_app.py` | Bug Fix |
| 2 | Excel export — timing precision + 4 new Flask sub-timing columns | `web_app.py` | Enhancement |
| 3 | Per-patient language dropdown (moved from global) | `web_app.py` | Feature |
| 4 | URL inputs converted to environment dropdowns | `web_app.py` | UX Improvement |
| 5 | `audio_processing_time` — new computed metric | `web_app.py` | Feature |
| 6 | `audio_duration` — use actual value from Flask, not config default | `web_app.py` | Bug Fix |
| 7 | Language column added to Excel export | `web_app.py` | Enhancement |
| 8 | Excel column layout — final clean alignment (18 columns) | `web_app.py` | Refactor |
| 9 | LOAD_TEST_SPEC — test data isolation strategy (Option A) | `LOAD_TEST_SPEC.md` | Documentation |
| 10 | LOAD_TEST_SPEC — per-patient language + config export/import spec | `LOAD_TEST_SPEC.md` | Documentation |
| 11 | User manual created | `USER_MANUAL.md` | Documentation |

---

## Change 1 — Excel Export: Stale Key Names Causing Empty Columns

### Problem

When exporting results to Excel, four columns were always empty regardless of whether the test ran successfully:

- Patient Data Time (s)
- Step 4 Time (s)
- Step 5 Time (s)
- Step 6 Time (s)

### Root Cause

The export route (`/export`) was reading timing keys by **old names** that no longer existed in `timing_data`. The keys had been renamed in a previous session when the timing data structure was restructured, but the export function was never updated to match.

| Excel header | Key export was reading (wrong) | Actual key in `timing_data` (correct) |
|---|---|---|
| Patient Data Time | `patient_data_time` | `patient_metadata_time` |
| Step 4 Time | `step4_time` | `transcribe_rtt` |
| Step 5 Time | `step5_time` | `audio_upload_time` |
| Step 6 Time | `step6_time` | `summary_store_time` |

`result.get("step4_time")` always returns `None` when the key does not exist — silently writing a blank cell with no error.

### Files Changed

- `medisum/testing/web_app.py` — `/export` route, data row assignment block

### Diff

```diff
# /export route — data row assignments

- ws.cell(row=row_idx, column=8).value  = result.get("patient_data_time")
- ws.cell(row=row_idx, column=9).value  = result.get("step4_time")
- ws.cell(row=row_idx, column=10).value = result.get("step5_time")
- ws.cell(row=row_idx, column=11).value = result.get("step6_time")

+ ws.cell(row=row_idx, column=9).value  = _t(result, "patient_metadata_time")
+ ws.cell(row=row_idx, column=10).value = _t(result, "transcribe_rtt")
+ ws.cell(row=row_idx, column=11).value = _t(result, "audio_upload_time")
+ ws.cell(row=row_idx, column=12).value = _t(result, "summary_store_time")
```

---

## Change 2 — Excel Export: Timing Precision + 4 New Flask Sub-timing Columns

### Problem

1. Timing values were exported with arbitrary floating-point precision (e.g. `12.384729473821`).
2. Flask sub-timings (`transcription_time`, `translation_time`, `llm_time`, `flask_total_time`) were already being collected in `timing_data` but never written to the Excel file.

### Root Cause

1. No rounding was applied at the export stage — values came through as raw Python floats.
2. When the Flask response sub-timings were added to `timing_data`, the export route was not updated to include the new keys.

### Files Changed

- `medisum/testing/web_app.py` — `/export` route

### Diff

```diff
+ # Helper: round to 5 decimal places; return None if value is absent
+ def _t(result, key):
+     v = result.get(key)
+     return round(v, 5) if v is not None else None

  # Previously: no rounding, only 11 columns
- ws.cell(row=row_idx, column=6).value  = result.get("step1_time")
- ws.cell(row=row_idx, column=7).value  = result.get("step1b_time")
  # ...

  # After: all timing values rounded via _t(), 4 new columns added
+ ws.cell(row=row_idx, column=7).value  = _t(result, "step1_time")
+ ws.cell(row=row_idx, column=8).value  = _t(result, "step1b_time")
+ # ...
+ ws.cell(row=row_idx, column=13).value = _t(result, "transcription_time")
+ ws.cell(row=row_idx, column=14).value = _t(result, "translation_time")
+ ws.cell(row=row_idx, column=15).value = _t(result, "llm_time")
+ ws.cell(row=row_idx, column=16).value = _t(result, "flask_total_time")
```

**New headers added to Excel:**

| Col | Header | Key |
|---|---|---|
| M | STT Time (s) | `transcription_time` |
| N | Translation Time (s) | `translation_time` |
| O | LLM Time (s) | `llm_time` |
| P | Flask Total Time (s) | `flask_total_time` |

---

## Change 3 — Per-Patient Language Dropdown

### Problem

Language was a single global setting in Advanced Settings. Every doctor and every patient in a run used the same language. This made it impossible to test multilingual scenarios in a single run — e.g. one patient speaking Hindi while another speaks Tamil.

### Root Cause

Language was designed as a global run parameter from the start. No per-patient configuration existed.

### Files Changed

- `medisum/testing/web_app.py` — HTML Advanced Settings, CSS, `addPatientAudioRow()`, FormData build, `/run` route Python, `_run_patient()`

### Architecture Change

```
BEFORE
─────────────────────────────────────────────────────────
Advanced Settings
  ├── LLM
  ├── STT Model
  ├── Translate Model
  ├── Template Type
  ├── Template ID
  ├── Audio Duration
  ├── Language  ◄── global, one value for all patients
  ├── Django URL
  └── Flask URL

AFTER
─────────────────────────────────────────────────────────
Advanced Settings
  ├── LLM
  ├── STT Model
  ├── Translate Model
  ├── Template Type
  ├── Template ID
  ├── Audio Duration
  ├── Django URL
  └── Flask URL

Doctor Card → Patient Row (per patient)
  ├── Patient ID badge
  ├── + Upload Audio
  ├── Language dropdown  ◄── per patient, independent
  └── Audio file chips
```

### Diff

**HTML — Advanced Settings (language field removed):**

```diff
  <div class="adv-grid">
-   <div class="field">
-     <label>Language</label>
-     <select id="cfg-language">
-       <option value="en">English (en)</option>
-       ...
-     </select>
-   </div>
    <div class="field">
      <label>LLM</label>
      ...
```

**CSS — new patient language styles added:**

```diff
+ .pat-lang-row    { display: flex; align-items: center; gap: 6px; margin-bottom: 5px; }
+ .pat-lang-label  { font-size: 11px; color: var(--muted); white-space: nowrap; }
+ .pat-lang-select { font-size: 11px; background: var(--bg); border: 1px solid var(--border);
+                    border-radius: 4px; color: var(--text); padding: 3px 6px; }
+ .pat-lang-select:focus { border-color: var(--accent); }
```

**JS — `addPatientAudioRow()`: language dropdown injected into each patient row:**

```diff
  row.innerHTML = `
    <div class="pat-audio-header">
      <span class="pat-pid-badge">Patient ${patientId}</span>
      <label class="pat-upload-label">...</label>
    </div>
+   <div class="pat-lang-row">
+     <span class="pat-lang-label">Language:</span>
+     <select class="pat-lang-select">
+       <option value="en">English (en)</option>
+       <option value="hi">Hindi (hi)</option>
+       <option value="ta">Tamil (ta)</option>
+       <option value="te">Telugu (te)</option>
+       <option value="kn">Kannada (kn)</option>
+       <option value="ml">Malayalam (ml)</option>
+       <option value="bn">Bengali (bn)</option>
+       <option value="mr">Marathi (mr)</option>
+     </select>
+   </div>
    <div class="pat-file-list">...</div>`;
```

**JS — FormData build: per-patient language keys:**

```diff
  document.querySelectorAll('.doctor-card').forEach((card, di) => {
    card.querySelectorAll('.patient-audio-row').forEach(patRow => {
      const pid = patRow.dataset.patientId;
+     const lang = patRow.querySelector('.pat-lang-select').value;
+     fd.append(`plang_${di}_${pid}`, lang);
      (patRow._files || []).forEach((f, ai) => fd.append(`paudio_${di}_${pid}_${ai}`, f, f.name));
    });
  });
- fd.append('language', document.getElementById('cfg-language').value);
```

**Python `/run` route — parse `plang_*` keys from form:**

```diff
+ # Collect per-patient languages: plang_{di}_{pid}
+ patient_languages = {}
+ for key in request.form:
+     m = re.match(r'^plang_(\d+)_(\w+)$', key)
+     if m:
+         di, pid = int(m.group(1)), m.group(2)
+         patient_languages[(di, pid)] = request.form[key]
+ cfg["patient_languages"] = patient_languages

  cfg = {
      ...
-     "language": request.form.get("language", "en"),
+     "language": "en",   # fallback only — per-patient language overrides this
      ...
  }
```

**Python `_run_patient()` — resolve language per patient:**

```diff
- language = cfg["language"]
+ language = cfg.get("patient_languages", {}).get(
+     (doctor_idx, str(patient_id)), cfg.get("language", "en")
+ )
```

---

## Change 4 — URL Inputs Converted to Environment Dropdowns

### Problem

Django Base URL and Flask Transcribe URL were free-text `<input>` fields. Testers had to type the full URL each time, which was error-prone (typos, wrong protocol, missing `/transcribe`).

### Root Cause

Originally implemented as text inputs to allow maximum flexibility. In practice only two environments are used.

### Files Changed

- `medisum/testing/web_app.py` — HTML Advanced Settings

### Diff

```diff
- <div class="field">
-   <label>Django Base URL</label>
-   <input id="cfg-django" type="text" value="__DJANGO_BASE_URL__">
- </div>
- <div class="field">
-   <label>Flask Transcribe URL</label>
-   <input id="cfg-flask" type="text" value="__FLASK_BASE_URL__">
- </div>

+ <div class="field">
+   <label>Django Base URL</label>
+   <select id="cfg-django">
+     <option value="https://test-medsum.amritaai.org">https://test-medsum.amritaai.org</option>
+     <option value="https://medsum.bharatgen.dev">https://medsum.bharatgen.dev</option>
+   </select>
+ </div>
+ <div class="field">
+   <label>Flask Transcribe URL</label>
+   <select id="cfg-flask">
+     <option value="https://test-medsum.amritaai.org/transcribe">https://test-medsum.amritaai.org/transcribe</option>
+     <option value="https://medsum.bharatgen.dev/transcribe">https://medsum.bharatgen.dev/transcribe</option>
+   </select>
+ </div>
```

**Environment options:**

| Environment | Django URL | Flask URL |
|---|---|---|
| Amrita Test | `https://test-medsum.amritaai.org` | `https://test-medsum.amritaai.org/transcribe` |
| Bharatgen | `https://medsum.bharatgen.dev` | `https://medsum.bharatgen.dev/transcribe` |

---

## Change 5 — `audio_processing_time`: New Computed Metric

### Problem

Flask reports four timing values: `total-time`, `transcription-time`, `translation-time`, and `llm-time`. There was no way to quantify how much time Flask spends on audio decoding and pre-processing (base64 decode, WAV conversion, etc.) — the overhead that isn't STT, translation, or LLM.

### Root Cause

The metric did not previously exist. It is derived from existing fields but was never computed.

### Definition

```
audio_processing_time = flask_total_time − (transcription_time + translation_time + llm_time)
```

This captures: base64 decode, audio format conversion, and any other Flask overhead not attributed to the three named pipeline stages.

```
Flask Total Time
├── transcription_time   (STT)
├── translation_time     (Translation)
├── llm_time             (LLM)
└── audio_processing_time  ◄── NEW: everything else
```

### Files Changed

- `medisum/testing/web_app.py` — `_run_patient()`, `timing_data`, `/export` route

### Diff

**`_run_patient()` — compute after Flask responds:**

```diff
  b4 = r4.json()
  total_time    = b4.get("total-time")
  transcription = b4.get("transcription", "")
  summary_text  = _extract_summary(b4)
  step4_time    = time.time() - start_time

+ # Compute audio_processing_time = flask_total - (stt + translation + llm)
+ _ft  = b4.get("total-time") or 0
+ _stt = b4.get("transcription-time") or 0
+ _tr  = b4.get("translation-time") or 0
+ _llm = b4.get("llm-time") or 0
+ audio_processing_time = round(_ft - (_stt + _tr + _llm), 5) if _ft else None
```

**`timing_data` — store it:**

```diff
  timing_data.update({
      ...
      "flask_total_time":       b4.get("total-time"),
+     "audio_processing_time":  audio_processing_time,
  })
```

**Excel — added as column Q:**

```diff
  headers = [
      ...
      "Flask Total Time (s)",          # P
+     "Audio Processing Time (s)",     # Q
      "User Perceived Summary Latency (s)",  # R
  ]
```

---

## Change 6 — `audio_duration`: Use Actual Value from Flask Instead of Config Default

### Problem

The `Audio Duration (s)` column in the Excel export always showed the **configured fallback duration** (default 2.0 s), even when a real audio file was uploaded. The actual duration of the uploaded file was never recorded.

### Root Cause

`timing_data["audio_duration"]` was being set to `float(cfg["duration"])` — the silent WAV fallback duration from Advanced Settings. The Flask `/transcribe` endpoint returns the actual audio length as `audio_length` in its response body, but this value was never extracted.

### Files Changed

- `medisum/testing/web_app.py` — `_run_patient()`

### Diff

```diff
  b4 = r4.json()
  total_time    = b4.get("total-time")
+ # Use actual audio length returned by Flask; fall back to configured duration if absent
+ audio_length  = b4.get("audio_length") or audio_duration

  timing_data = {
      "doctor_id":      doctor_id,
      "patient_id":     patient_id,
      "audio_id":       audio_id,
      "summary_id":     summary_id,
-     "audio_duration": audio_duration,   # was: always the config fallback value (e.g. 2.0)
+     "audio_duration": audio_length,     # now: actual duration from Flask response
      ...
  }
```

**Impact:**

| Scenario | Before | After |
|---|---|---|
| No audio uploaded (silent WAV) | `2.0` (correct — config value matches) | `2.0` (same, Flask returns length of generated WAV) |
| Real audio uploaded (e.g. 45 s clip) | `2.0` (wrong — config default) | `45.2` (correct — from Flask `audio_length`) |

---

## Change 7 — Language Column Added to Excel Export

### Problem

After making language per-patient (Change 3), the Excel export had no column showing which language was used for each row. Without it, a tester could not distinguish Hindi results from English results in the same spreadsheet.

### Root Cause

The `language` field was not included in `timing_data`, so it was not available for export.

### Files Changed

- `medisum/testing/web_app.py` — `_run_patient()` `timing_data` dict, `/export` headers and data rows

### Diff

**`timing_data` — add language:**

```diff
  timing_data = {
      "doctor_id":      doctor_id,
      "patient_id":     patient_id,
      "audio_id":       audio_id,
      "summary_id":     summary_id,
      "audio_duration": audio_length,
+     "language":       language,
  }
```

**Excel headers — insert Language at column F (after Audio Duration):**

```diff
  headers = [
      "Doctor ID",           # A
      "Patient ID",          # B
      "Audio ID",            # C
      "Summary ID",          # D
      "Audio Duration (s)",  # E
+     "Language",            # F  ◄── NEW
      "Login Time (s)",      # G  (was F)
      ...
  ]
```

**Excel data rows — write language value:**

```diff
  ws.cell(row=row_idx, column=5).value  = result.get("audio_duration")
+ ws.cell(row=row_idx, column=6).value  = result.get("language")
  ws.cell(row=row_idx, column=7).value  = _t(result, "step1_time")   # (was col 6)
  ...
```

---

## Change 8 — Excel Column Layout: Final Clean 18-Column Alignment

### Problem

Over multiple iterations, columns had been added, shifted, and some left commented out. The headers list, data row assignments, and column widths were out of sync — some columns had no data, column 9 was assigned twice (redundant write), and the User Perceived Summary Latency row was commented out.

### Root Cause

Incremental additions without a full audit of the export block.

### Files Changed

- `medisum/testing/web_app.py` — entire `/export` route block

### Final Column Layout

| Col | Letter | Header | `timing_data` key |
|---|---|---|---|
| 1 | A | Doctor ID | `doctor_id` |
| 2 | B | Patient ID | `patient_id` |
| 3 | C | Audio ID | `audio_id` |
| 4 | D | Summary ID | `summary_id` |
| 5 | E | Audio Duration (s) | `audio_duration` (actual from Flask) |
| 6 | F | Language | `language` |
| 7 | G | Login Time (s) | `step1_time` |
| 8 | H | Doctor Profile Time (s) | `step1b_time` |
| 9 | I | Patient Metadata Time (s) | `patient_metadata_time` |
| 10 | J | Transcribe RTT (s) | `transcribe_rtt` |
| 11 | K | Audio Upload Time (s) | `audio_upload_time` |
| 12 | L | Summary Store Time (s) | `summary_store_time` |
| 13 | M | STT Time (s) | `transcription_time` |
| 14 | N | Translation Time (s) | `translation_time` |
| 15 | O | LLM Time (s) | `llm_time` |
| 16 | P | Flask Total Time (s) | `flask_total_time` |
| 17 | Q | Audio Processing Time (s) | `audio_processing_time` |
| 18 | R | User Perceived Summary Latency (s) | `user_percieved_summary_latency` |

**Timing relationships visible in the spreadsheet:**

```
M + N + O + Q  =  P          (Flask-internal decomposition)
J              ≥  P          (RTT ≥ server time; difference = network)
J              ≈  R          (Transcribe RTT ≈ user-perceived latency)
```

---

## Change 9 — LOAD_TEST_SPEC: Test Data Isolation Strategy (Option A)

### Problem

The spec had no defined strategy for distinguishing test-generated records from real production conversations in the `audios` and `summaries` tables. Both test and production records looked identical in the database.

### Decision

**Option A — zero schema changes, `session_id` prefix convention** was chosen over Option B (add `is_test` boolean column) because:

- No migration needed on existing tables
- `session_id` column already exists on both `audios` and `summaries`
- The `test_run_id` UUID embedded in the prefix provides a direct link back to `load_test_result`
- Zero risk to existing queries or application behaviour

### Linkage Diagram

```
load_test_result
  ├── test_run_id  (UUID, shared across the entire run)
  ├── audio_id     ──(integer ref, no FK)──► audios.audio_id
  └── summary_id   ──(integer ref, no FK)──► summaries.summary_id

audios.session_id    = "LOADTEST_<uuid>"  or  "MANUALTEST_<uuid>"
summaries.session_id = "LOADTEST_<uuid>"  or  "MANUALTEST_<uuid>"
Production records   = "<uuid>"  (no prefix)
```

### session_id Convention

| Run type | `session_id` written to DB | Filter query |
|---|---|---|
| Automated test (this tool) | `LOADTEST_<uuid>` | `session_id LIKE 'LOADTEST_%'` |
| Manual test (planned UI toggle) | `MANUALTEST_<uuid>` | `session_id LIKE 'MANUALTEST_%'` |
| Real production use | `<uuid>` | `session_id NOT LIKE 'LOAD%' AND session_id NOT LIKE 'MANUAL%'` |

### Files Changed

- `medisum/testing/LOAD_TEST_SPEC.md` — Section 8 (Database Schema): added Linkage subsection and Test Data Isolation subsection with SQL examples

---

## Change 10 — LOAD_TEST_SPEC: Per-Patient Language + Config Export/Import

### Problem

Two specification gaps existed after the implementation changes:

1. **Section 7** still listed `language` as a global parameter — it had become per-patient in code but the spec was not updated.
2. **No spec section existed** for the config export/import feature (save/reload test configuration as a JSON file).

### Files Changed

- `medisum/testing/LOAD_TEST_SPEC.md` — Section 7 restructured, Section 10.1 added

### Section 7 — Before vs After

```diff
## 7. Test Parameters

- | Parameter          | Default   |
- |---|---|
- | `language`         | `en`      |   ◄── was global
- | `llm`              | `OpenAI`  |
- | ...                | ...       |

+ ## 7.1 Global Parameters (Advanced Settings)
+ | Parameter          | Default   |
+ |---|---|
+ | `llm`              | `OpenAI`  |    ◄── language removed from here
+ | ...                | ...       |
+
+ ## 7.2 Per-Patient Parameters
+ | Parameter      | Default             |
+ |---|---|
+ | `language`     | `en`                |   ◄── now per-patient
+ | `audio_files`  | silent WAV fallback |
```

### Section 10.1 — Config Export/Import (Option B)

New section added covering:

- **What is saved** — doctor credentials, patient IDs, language per patient, global settings, session mode. Audio file *contents* are not saved (browser security); filenames are saved as reminders only.
- **Workflow** — first-run setup → Export Config → store file → future runs: Import Config → fields pre-filled → re-upload audio → run.
- **JSON schema** — documented structure including nested `doctors[].patients[].language` and `audio_filenames`.

**Config file schema:**

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
    "django_url": "https://test-medsum.amritaai.org",
    "flask_url": "https://test-medsum.amritaai.org/transcribe",
    "test_mode": "automated"
  },
  "doctors": [
    {
      "phone": "9876543210",
      "password": "secret",
      "patients": [
        { "patient_id": "101", "language": "en", "audio_filenames": ["nikita_en.wav"] },
        { "patient_id": "102", "language": "hi", "audio_filenames": ["nikita_hi.wav"] }
      ]
    }
  ]
}
```

---

## Change 11 — User Manual Created

### Problem

No user-facing documentation existed for the load testing tool. New testers had to read the source code to understand how to set up and run tests.

### Files Changed

- `medisum/testing/USER_MANUAL.md` — new file (~400 lines)

### Coverage

| Section | Content |
|---|---|
| 1. Introduction | Tool purpose, workflow diagram, key concurrency behaviours |
| 2. Getting Started | Prerequisites, launch command, first-time checklist |
| 3. Screen 1 — Setup | Step-by-step: doctors, patients, audio upload, per-patient language, advanced settings |
| 4. Screen 2 — Run | Preview matrix, running a test, live status badges, log panel |
| 5. Understanding Results | Pass/fail conditions (all 6 steps), inline transcription/summary view, stats bar |
| 6. Exporting Results | Excel download, full 18-column reference table with timing relationships |
| 7. Test Case Scenarios | Step-by-step setup for A1, A2, B1, B2, B3 with summary comparison table |
| 8. Concurrency Model | ASCII execution tree, bottleneck interpretation guidance |
| 9. Database Records | What gets created, session_id prefix convention, SQL filter query |
| 10. Tips & Common Mistakes | Do's, hard warnings, 5-row common mistakes table |

---

## Summary of Impact

### Files Modified

| File | Changes |
|---|---|
| `medisum/testing/web_app.py` | 8 independent changes — bug fixes, 2 new features, UX improvements, export overhaul |
| `medisum/testing/LOAD_TEST_SPEC.md` | Sections 7, 8, 10 updated — per-patient language, test isolation strategy, config export spec |
| `medisum/testing/USER_MANUAL.md` | New file — comprehensive 10-section user manual |

### Excel Export: Before vs After

| Metric | Before | After |
|---|---|---|
| Total columns | 11 (with 4 empty) | 18 (all populated) |
| Timing precision | Arbitrary float | 5 decimal places |
| Language recorded | No | Yes (col F) |
| Flask sub-timings | No | Yes (cols M–P) |
| Audio processing time | No | Yes (col Q) |
| Actual audio duration | No (always config default) | Yes (from Flask response) |

### Per-Patient Language: Before vs After

```
BEFORE                          AFTER
──────────────────────          ──────────────────────────────────────
Advanced Settings               Doctor Card
  Language: [English ▼]           Patient 101
                                    Language: [Hindi ▼]   ◄── per patient
                                    + Upload Audio
                                  Patient 102
One language for ALL patients.      Language: [English ▼]
                                    + Upload Audio
                                  
                                  Different language per patient, 
                                  sent independently to all endpoints.
```
