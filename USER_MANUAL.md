# Medisum Load Test Console — User Manual

**Tool:** `medisum/testing/web_app.py`
**Version:** Current
**Audience:** QA engineers and developers running performance tests on the Medisum AI pipeline

---

## Table of Contents

- [Medisum Load Test Console — User Manual](#medisum-load-test-console--user-manual)
  - [Table of Contents](#table-of-contents)
  - [1. Introduction](#1-introduction)
    - [What the tool tests](#what-the-tool-tests)
    - [Key behaviours](#key-behaviours)
  - [2. Getting Started](#2-getting-started)
    - [Prerequisites](#prerequisites)
    - [Launching the application](#launching-the-application)
    - [First time checklist](#first-time-checklist)
  - [3. Screen 1 — Test Setup](#3-screen-1--test-setup)
    - [3.1 Adding Doctors](#31-adding-doctors)
    - [3.2 Adding Patients per Doctor](#32-adding-patients-per-doctor)
    - [3.3 Uploading Audio per Patient](#33-uploading-audio-per-patient)
    - [3.4 Setting Language per Patient](#34-setting-language-per-patient)
    - [3.5 Advanced Settings](#35-advanced-settings)
  - [4. Screen 2 — Preview \& Run](#4-screen-2--preview--run)
    - [4.1 Previewing the Test Matrix](#41-previewing-the-test-matrix)
    - [4.2 Running the Test](#42-running-the-test)
    - [4.3 Live Status \& Logs](#43-live-status--logs)
  - [5. Understanding Results](#5-understanding-results)
    - [5.1 Status Badges](#51-status-badges)
    - [5.2 Viewing Transcription \& Summary](#52-viewing-transcription--summary)
    - [5.3 Stats Bar](#53-stats-bar)
  - [6. Exporting Results](#6-exporting-results)
    - [6.1 Excel Column Reference](#61-excel-column-reference)
  - [7. Test Case Scenarios](#7-test-case-scenarios)
    - [7.1 Sequential Baseline (Phase A)](#71-sequential-baseline-phase-a)
      - [Scenario A1 — 1 doctor, 1 patient](#scenario-a1--1-doctor-1-patient)
      - [Scenario A2 — 1 doctor, 5 patients](#scenario-a2--1-doctor-5-patients)
    - [7.2 Concurrent Load (Phase B)](#72-concurrent-load-phase-b)
      - [Scenario B1 — 5 doctors, 1 patient each](#scenario-b1--5-doctors-1-patient-each)
      - [Scenario B2 — 10 doctors, 1 patient each](#scenario-b2--10-doctors-1-patient-each)
      - [Scenario B3 — 5 doctors, 2 patients each](#scenario-b3--5-doctors-2-patients-each)
    - [7.3 Summary table](#73-summary-table)
  - [8. Concurrency Model Explained](#8-concurrency-model-explained)
  - [9. What Gets Stored in the Database](#9-what-gets-stored-in-the-database)
  - [10. Tips, Warnings \& Common Mistakes](#10-tips-warnings--common-mistakes)
    - [Do's](#dos)
    - [Warnings](#warnings)
    - [Common mistakes](#common-mistakes)

---

## 1. Introduction

The **Medisum Load Test Console** is a browser-based tool that automates the full Medisum clinical documentation workflow for multiple doctors and patients simultaneously. It measures end-to-end latency across every step — login, patient data fetch, AI transcription, audio upload, and summary storage — and exports all timing data to Excel for analysis.

### What the tool tests

```
Doctor Login  →  Fetch Profile  →  [Per Patient, in parallel]
                                         │
                                         ├── Fetch Patient Metadata
                                         ├── Send audio (base64) → Flask /transcribe
                                         │       (STT → Translation → LLM Summary)
                                         ├── Upload audio file → Django /api/audio-data/
                                         └── Store summary → Django /api/summary-data/
```

### Key behaviours

| Behaviour | Detail |
|---|---|
| Doctors | Run in **parallel** with each other |
| Patients per doctor | Run in **parallel** with each other |
| Audio files per patient | Run **sequentially** (transcribe → upload → store must be in order) |
| Audio fallback | If no audio file is uploaded for a patient, a silent WAV is generated automatically |
| Language | Selected **per patient** — different patients in the same run can use different languages |

---

## 2. Getting Started

### Prerequisites

- Python 3.9+
- Required packages installed: `flask`, `requests`, `openpyxl`
- Network access to the Medisum Django and Flask servers
- Valid doctor credentials (phone + password) registered in the target environment
- Valid patient IDs registered under those doctors in the target environment

### Launching the application

```bash
cd medisum/testing
python web_app.py
```

Then open your browser at:

```
http://127.0.0.1:5050
```

> **Deployed on Render:** If the tool is hosted, open the Render URL directly. No local setup is needed.

### First time checklist

Before running any test, verify:

- [ ] You know which environment to test: `test-medsum.amritaai.org` or `medsum.bharatgen.dev`
- [ ] You have at least one valid doctor account (phone + password) on that environment
- [ ] You have patient IDs assigned to those doctors
- [ ] You have audio files ready (`.wav`, `.mp3`, or `.m4a`) — or plan to use the silent WAV fallback

---

## 3. Screen 1 — Test Setup

This is the main configuration screen. You build the test matrix here before running.

---

### 3.1 Adding Doctors

1. The page loads with **one doctor card** already present.
2. Fill in the **Phone Number** and **Password** for that doctor.
3. To add more doctors, click **+ Add Doctor** at the bottom of the Doctors section.
4. Each doctor card is numbered. To remove a doctor, click the **×** button in the top-right corner of the card.

> Doctors represent independent concurrent sessions. Each doctor logs in separately and runs its patients in parallel.

---

### 3.2 Adding Patients per Doctor

Each doctor card has a **Patient IDs** input box.

1. Click inside the input box under "Patient IDs".
2. Type a numeric patient ID and press **Enter** or **comma**.
3. The patient ID appears as a blue tag. Repeat for each patient.
4. To remove a patient, click the **×** on its tag. The corresponding audio and language row disappears automatically.

> Patient IDs must exist in the target environment and must be associated with the doctor's account.

---

### 3.3 Uploading Audio per Patient

When a patient tag is added, a **patient row** appears below the tag box. Each patient row has its own audio upload slot.

**To upload audio for a patient:**

1. Click **+ Upload Audio** on the patient row.
2. Select one or more audio files (`.wav`, `.mp3`, `.m4a`).
3. Selected files appear as green chips inside the patient row.
4. To remove a file, click the **×** on its chip.

**If no audio is uploaded:**

The row shows: *"No audio — silent WAV will be used"*. A 2-second silent WAV is generated automatically at run time. The duration of this fallback can be changed in Advanced Settings.

> If multiple audio files are uploaded for one patient, they are processed **sequentially** — each goes through the full pipeline (transcribe → upload → store) before the next begins.

---

### 3.4 Setting Language per Patient

Each patient row has a **Language** dropdown directly below the patient badge.

1. Click the language dropdown on the patient row.
2. Select the language of the audio for that patient.

Available options:

| Code | Language |
|---|---|
| `en` | English |
| `hi` | Hindi |
| `ta` | Tamil |
| `te` | Telugu |
| `kn` | Kannada |
| `ml` | Malayalam |
| `bn` | Bengali |
| `mr` | Marathi |

> The selected language is sent to both the Flask `/transcribe` endpoint and Django `/api/audio-data/`. Different patients in the same run can use different languages — this is by design.

---

### 3.5 Advanced Settings

Click **▶ Advanced Settings** to expand. These are global settings applied to all doctors and patients in the run.

| Setting | What it controls | Default |
|---|---|---|
| **LLM** | Which LLM backend generates the summary | OpenAI |
| **STT Model** | Speech-to-text engine | Bhasini |
| **Translate Model** | Translation engine | Bhasini |
| **Template Type** | Summary format — SOAP or Discharge Summary | SOAP |
| **Template ID** | Specific template version ID | 1 |
| **Audio Duration (s)** | Duration of the silent WAV fallback, used when no audio is uploaded | 2.0 s |
| **Django Base URL** | Which Django environment to hit | test-medsum.amritaai.org |
| **Flask Transcribe URL** | Which Flask environment to hit | test-medsum.amritaai.org/transcribe |

**Choosing the target environment:**

Both URL dropdowns offer two options:

| Environment | Django URL | Flask URL |
|---|---|---|
| Amrita Test | `https://test-medsum.amritaai.org` | `https://test-medsum.amritaai.org/transcribe` |
| Bharatgen | `https://medsum.bharatgen.dev` | `https://medsum.bharatgen.dev/transcribe` |

> Always ensure both URLs point to the **same environment**. Mixing them (e.g. Amrita Django + Bharatgen Flask) will cause authentication failures.

---

## 4. Screen 2 — Preview & Run

### 4.1 Previewing the Test Matrix

Click **Preview Test Matrix →** after filling in all doctors and patients.

The tool validates your input first:
- Every doctor must have a phone number and password.
- Every doctor must have at least one patient ID.

If validation passes, you move to Screen 2, which shows:

- **Stats bar**: number of doctors, total patient slots, total audio runs.
- **Run table**: one row per audio file per patient — this is exactly what will execute.

**Run table columns:**

| Column | Description |
|---|---|
| # | Row number |
| Doctor Phone | The doctor's phone number |
| Patient ID | The patient being processed |
| Audio File | Filename, or `(silent WAV)` if no file uploaded |
| Status | Pending (grey) before run starts |
| audio_id | Filled after Step 4 completes |
| summary_id | Filled after Step 5 completes |
| Details | Time + View button after passing |

---

### 4.2 Running the Test

Click **Confirm & Run** (green button).

What happens immediately:
- All doctor sessions start **in parallel**.
- Within each doctor, all patients start **in parallel**.
- Within each patient, audio files run **sequentially**.
- The run table updates in real time as each row completes.
- A progress bar at the top tracks overall completion.

> Do not close or refresh the browser tab while a run is in progress. Results are held in memory and will be lost.

**During a run:**

- The **Confirm & Run** button shows a spinner and is disabled.
- The **← Back** button is hidden.
- The Logs panel (bottom) streams step-by-step progress.

**After a run completes:**

- **↺ New Test** — returns to Screen 1 to set up a new run.
- **⬇ Export to Excel** — downloads the full timing data.
- **Confirm & Run** re-enables for running the same matrix again.

---

### 4.3 Live Status & Logs

**Status badges in the table:**

| Badge | Meaning |
|---|---|
| Pending (grey) | Not yet started |
| Running (blue, animated) | In progress |
| ✓ Pass (green) | All steps completed successfully |
| ✗ Fail (red) | One step failed; error shown in Details column |

**Logs panel (bottom of screen):**

The logs panel shows every step for every doctor/patient in real time. Each line is colour-coded:

| Colour | Meaning |
|---|---|
| Blue | Step start marker (e.g. `[Step 4] Transcribing...`) |
| Green | Step completed successfully (`[OK]`) |
| Amber | Warning — step returned non-critical error, run continues |
| Red | Step failed (`[FAIL]` or `[ERROR]`) |

Log controls:
- **Minimize / Expand** — toggle panel height.
- **Clear** — clears log text (does not affect results).

> Tip: Keep the logs panel visible when debugging failures. The error message in the log is more detailed than the truncated text in the Details column.

---

## 5. Understanding Results

### 5.1 Status Badges

A row is marked **Pass** only if all five steps complete without error:

1. Login (`/api/login/`)
2. Doctor profile fetch (`/api/user/update/{id}/`)
3. Patient metadata fetch (`/api/patient-data/{id}/`)
4. Transcription (`/transcribe` Flask)
5. Audio upload (`/api/audio-data/`)
6. Summary store (`/api/summary-data/`)

If any step fails, the row is marked **Fail** and subsequent steps are skipped for that audio.

---

### 5.2 Viewing Transcription & Summary

After a row passes, click **View ▾** in the Details column to expand an inline panel showing:
- **Transcription** — raw speech-to-text output from the Flask pipeline.
- **Summary** — the generated clinical summary (SOAP or Discharge format).

Click **Hide ▴** to collapse.

---

### 5.3 Stats Bar

| Stat | Description |
|---|---|
| Doctors | Number of doctor cards configured |
| Patient Slots | Total unique patients across all doctors |
| Total Runs | Total audio files to process (1 row per audio file per patient) |
| Passed | Rows that completed all steps successfully |
| Failed | Rows that encountered an error |

---

## 6. Exporting Results

After a run (or partial run), click **⬇ Export to Excel**.

This downloads a file named `medsum-results-YYYY-MM-DD_HH-MM-SS.xlsx` containing one row per audio run with all timing data.

---

### 6.1 Excel Column Reference

| Col | Header | Description |
|---|---|---|
| A | Doctor ID | Database ID of the doctor |
| B | Patient ID | Database ID of the patient |
| C | Audio ID | ID of the audio record created in Django |
| D | Summary ID | ID of the summary record created in Django |
| E | Audio Duration (s) | Actual audio length as reported by Flask (not the config default) |
| F | Language | Language code selected for this patient (`en`, `hi`, etc.) |
| G | Login Time (s) | Time for `POST /api/login/` |
| H | Doctor Profile Time (s) | Time for `GET /api/user/update/{id}/` |
| I | Patient Metadata Time (s) | Time for `GET /api/patient-data/{id}/` |
| J | Transcribe RTT (s) | Full round-trip time for `POST /transcribe` including network |
| K | Audio Upload Time (s) | Time for `POST /api/audio-data/` |
| L | Summary Store Time (s) | Time for `POST /api/summary-data/` |
| M | STT Time (s) | Speech-to-text time reported by Flask internally |
| N | Translation Time (s) | Translation time reported by Flask internally |
| O | LLM Time (s) | LLM summarisation time reported by Flask internally |
| P | Flask Total Time (s) | Total server-side time reported by Flask (`STT + Translation + LLM + Audio Processing`) |
| Q | Audio Processing Time (s) | Flask overhead beyond STT/Translation/LLM: `Flask Total − (STT + Translation + LLM)` |
| R | User Perceived Summary Latency (s) | End-to-end time from sending audio to receiving summary (Transcribe RTT ≈ this) |

> All timing values are rounded to 5 decimal places.
>
> **Relationship:** `M + N + O + Q = P` (Flask-internal) and `J ≥ P` (RTT ≥ server time due to network).

---

## 7. Test Case Scenarios

The spec defines two phases. This section shows exactly how to configure each one in the tool.

---

### 7.1 Sequential Baseline (Phase A)

Purpose: measure latency **without** concurrency pressure to establish a baseline.

#### Scenario A1 — 1 doctor, 1 patient

```
Setup:
  Doctors:  1
  Patients: 1 per doctor

Repeat: 3 times with 30-second gap between runs
Use the same doctor, patient, and audio file every time.
```

**How to set up:**
1. Add 1 doctor card with credentials.
2. Add 1 patient ID to that doctor.
3. Upload the test audio file to that patient row.
4. Select the correct language.
5. Click Preview → Confirm & Run.
6. After completion, wait 30 seconds, then click **Confirm & Run** again. Repeat 3 times total.

---

#### Scenario A2 — 1 doctor, 5 patients

```
Setup:
  Doctors:  1
  Patients: 5 per doctor

Repeat: 3 times with 30-second gap between runs
```

> Note: even though only 1 doctor is used, the 5 patients **run in parallel** within that session. This tests patient-level parallelism without doctor-level parallelism.

**How to set up:**
1. Add 1 doctor card.
2. Add 5 patient IDs to that doctor (type each ID, press Enter).
3. For each patient row, upload the appropriate audio and select the language.
4. Run 3 times with 30-second gaps.

---

### 7.2 Concurrent Load (Phase B)

Purpose: simulate real-world load by running multiple doctors simultaneously.

#### Scenario B1 — 5 doctors, 1 patient each

```
Setup:
  Doctors:  5 (each a separate card)
  Patients: 1 per doctor
  Total:    5 concurrent sessions

Repeat: 3 times with 30-second gap
```

**How to set up:**
1. Click **+ Add Doctor** to create 5 doctor cards.
2. Fill in credentials for each doctor.
3. Add 1 patient ID per doctor card.
4. Upload audio and select language for each patient row.
5. Preview — you should see **5 rows** in the run table.
6. Run 3 times with 30-second gaps.

---

#### Scenario B2 — 10 doctors, 1 patient each

```
Setup:
  Doctors:  10
  Patients: 1 per doctor
  Total:    10 concurrent sessions

Repeat: 3 times with 30-second gap
```

Same as B1 but with 10 doctor cards.

---

#### Scenario B3 — 5 doctors, 2 patients each

```
Setup:
  Doctors:  5
  Patients: 2 per doctor
  Total:    10 concurrent sessions (5 doctors × 2 patients each)

Repeat: 3 times with 30-second gap
```

**How to set up:**
1. Add 5 doctor cards.
2. For each doctor, add **2** patient IDs.
3. Each doctor card will show 2 patient rows — upload audio and set language for each.
4. Preview — you should see **10 rows** in the run table.
5. Run 3 times with 30-second gaps.

---

### 7.3 Summary table

| Scenario | Doctor Cards | Patients per Card | Rows in Preview | Parallelism |
|---|---|---|---|---|
| A1 | 1 | 1 | 1 | None (sequential baseline) |
| A2 | 1 | 5 | 5 | Patient-level only |
| B1 | 5 | 1 | 5 | Doctor-level |
| B2 | 10 | 1 | 10 | Doctor-level |
| B3 | 5 | 2 | 10 | Doctor-level + Patient-level |

---

## 8. Concurrency Model Explained

Understanding when things run in parallel vs. sequentially is important for interpreting timings.

```
Run starts
│
├── Doctor 1 thread ──────────────────────────────────────────── (parallel)
│       Step 1:  Login                          (sequential — must finish first)
│       Step 1b: Fetch profile                  (sequential — must finish first)
│       │
│       ├── Patient A thread ──────────────────────────────────── (parallel within doctor)
│       │       Step 2: Fetch patient metadata
│       │       Step 3: POST /transcribe        (sequential per audio)
│       │       Step 4: POST /api/audio-data/   (after Step 3 returns)
│       │       Step 5: POST /api/summary-data/ (after Step 4 returns)
│       │
│       └── Patient B thread ──────────────────────────────────── (parallel within doctor)
│               Step 2: Fetch patient metadata
│               Step 3 → 4 → 5 (sequential per audio)
│
└── Doctor 2 thread ──────────────────────────────────────────── (parallel)
        Step 1 → 1b → patients (same structure)
```

**Implications for timing interpretation:**

- **Login Time** and **Doctor Profile Time** are shared overhead paid once per doctor session, not per patient.
- **Patient Metadata Time**, **User Percieved Summary latency**, **Audio Upload Time**, and **Summary Store Time** are measured per patient thread and reflect actual concurrency on the server.
- Under high concurrency (B2: 10 doctors), if **User Percieved Summary latency** increases significantly compared to A1, it indicates the Flask AI pipeline is the bottleneck.
- If **Audio Upload Time** or **Summary Store Time** degrade, Django or the database is the bottleneck.

---

## 9. What Gets Stored in the Database

Every run creates real records in the production/test database:

| Step | Table | Record created |
|---|---|---|
| Step 4 (audio upload) | `audios` | One row per audio file per patient |
| Step 5 (summary store) | `summaries` | One row per audio file per patient |

These records are tagged with a special `session_id` prefix so they can be distinguished from real clinical data:

| Run type | `session_id` format |
|---|---|
| Load test (this tool) | `LOADTEST_<uuid>` |
| Manual test (planned toggle) | `MANUALTEST_<uuid>` |
| Real production use | `<uuid>` (no prefix) |

To exclude test data from production analytics:
```sql
-- Production records only
SELECT * FROM medisum_app_summaries
WHERE session_id NOT LIKE 'LOADTEST_%' AND session_id NOT LIKE 'MANUALTEST_%';
```

---

## 10. Tips, Warnings & Common Mistakes

### Do's

- **Use the same audio files and patient IDs across all repetitions** of a scenario. Varying inputs makes comparison meaningless.
- **Wait 30 seconds between repetitions** to allow server-side caches and connection pools to settle.
- **Check the Logs panel** when a row fails — the log shows which step failed and why (e.g. `audio-data 400: ...`).
- **Export to Excel immediately after a run** — results are held in memory only. Refreshing the page clears them.

### Warnings

- **Do not refresh the page during a run.** All in-progress results will be lost.
- **Passwords are sent in plain text** over HTTPS to the server. Do not use personal passwords — use dedicated test accounts.
- **Patient IDs must exist** in the target environment and be linked to the respective doctor. Using unknown patient IDs causes Step 2 (patient metadata fetch) to warn but does not stop the run.
- **Both URLs must point to the same environment.** Mixing Django from one environment with Flask from another will cause authentication to fail silently.

### Common mistakes

| Mistake | Symptom | Fix |
|---|---|---|
| Wrong patient ID for the doctor | Patient metadata returns 404 (WARN); summary may still be created with empty name/age | Use patient IDs registered under that doctor |
| Audio file too large | Step 3 (transcribe) times out after 180 s | Use shorter audio clips for load tests |
| Django and Flask URLs pointing to different environments | Login succeeds but transcription fails with 401 | Set both dropdowns to the same environment |
| No audio uploaded but duration left at 2 s | Transcription returns empty or error (silent audio) | Either upload a real audio file or increase fallback duration |
| Refreshing page mid-run | Results lost | Export before navigating away |
