# Medsum Load Test Web App

A Flask-based web application for load testing the Medsum medical transcription and summarization system. This tool simulates multiple doctors logging in, uploading audio files, transcribing them via AI models, and storing generated summaries.

## Features

- **Multi-Doctor Simulation**: Configure and run tests with multiple doctors in parallel.
- **Patient Management**: Assign multiple patient IDs per doctor.
- **Audio Upload & Processing**: Upload audio files or generate silent WAV fallbacks.
- **AI-Powered Transcription**: Integrates with STT (Speech-to-Text) and LLM models for summarization.
- **Real-Time Monitoring**: Web UI with live logs, progress tracking, and detailed results.
- **Configurable Templates**: Supports SOAP and Discharge Summary templates.
- **Timing Breakdown**: Logs detailed execution times for each processing stage.
- **Concurrency Control**: Optimized parallelism (doctors parallel, patients parallel, audios sequential per patient).
- **Excel Export**: Export test results to Excel with detailed timing breakdown for each step.

## Architecture

The app interacts with two backend services:
- **Django Backend**: Handles authentication, user profiles, patient data, audio uploads, and summary storage.
- **Flask Transcribe Service**: Performs audio transcription, translation, and AI summarization.

### Processing Stages

1. **Doctor Login** (Step 1): Authenticate doctor and get access token.
2. **Doctor Profile Fetch** (Step 1b): Retrieve doctor's details.
3. **Patient Data Fetch**: Get patient demographics.
4. **Audio Upload** (Step 4): Upload audio file to Django backend.
5. **Transcription** (Step 5): Process audio via Flask service (STT + LLM summarization).
6. **Summary Storage** (Step 6): Store generated summary in Django backend.

Each stage is timed and logged for performance analysis.

## Prerequisites

- Python 3.8+
- Flask
- requests
- wave (standard library)
- A running instance of the Medsum Django and Flask backends

## Installation

1. Clone or download the project:
   ```
   git clone <repository-url>
   cd medsum-load-test
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Ensure the backend services are running and accessible.

## Usage

### Running the App

1. Start the Flask app:
   ```
   python web_app.py
   ```

2. Open your browser and navigate to `http://127.0.0.1:5050`.

### Configuring a Test

1. **Add Doctors**: Click "Add Doctor" to configure doctor accounts.
   - Enter phone number and password.
   - Add patient IDs (press Enter or comma after each) (Primary key).

2. **Upload Audio Files**: Drag and drop audio files (WAV, MP3, etc.) or leave empty for silent WAV generation.

3. **Advanced Settings**:
   - Language (e.g., en, hi, ta)
   - LLM Model (OpenAI, OpenAI, Param)
   - STT Model (Bhasini, Bharatgen)
   - Translate Model (Bhasini, Bharatgen)
   - Template Type (SOAP or Discharge Summary)
   - Template ID
   - Audio Duration (for silent WAV fallback)
   - Base URLs for Django and Flask services

4. **Preview & Run**:
   - Click "Preview Test Matrix" to see the test plan.
   - Click "Confirm & Run" to start the load test.
   - Monitor progress in the UI and logs panel.

### Logs and Timing

- **Real-Time Logs**: View live updates in the collapsible logs panel.
- **Stage Timings**: Each step logs its execution time (e.g., `time=0.45s`).
- **Progress Tracking**: UI shows overall progress, passed/failed counts, and per-row status.

Example log output:
```
[Dr 1] [Step 1 OK] doctor_id=123 free_minutes=100 time=0.45s
[Dr 1|Pt 456] [Step 5 OK] total-time=15.5s step-time=15.8s
```

### Export Results

After a test completes successfully:

1. Click the **"⬇ Export to Excel"** button in the action bar.
2. An Excel file will be downloaded with the filename `medsum-results-YYYY-MM-DD.xlsx`.

The exported spreadsheet contains the following columns:
- **Doctor ID**: ID of the doctor created during authentication.
- **Patient ID**: Patient ID used in the current test run.
- **Audio ID**: Backend-generated audio ID from the upload step.
- **Summary ID**: Backend-generated summary ID from the summary storage step.
- **Audio Duration (s)**: Audio duration used for the upload/processing step.
- **Step 1 Time (s)**: Login duration.
- **Step 1b Time (s)**: Doctor profile fetch duration.
- **Patient Data Time (s)**: Patient metadata fetch duration.
- **Step 4 Time (s)**: Audio upload duration.
- **Step 5 Time (s)**: Transcription & summarization duration.
- **Step 6 Time (s)**: Summary storage duration.

This export is designed for detailed performance analysis of each processing stage.

Example row:

| Doctor ID | Patient ID | Audio ID | Summary ID | Audio Duration (s) | Step 1 Time (s) | Step 1b Time (s) | Patient Data Time (s) | Step 4 Time (s) | Step 5 Time (s) | Step 6 Time (s) |
|-----------|------------|----------|------------|---------------------|-----------------|------------------|------------------------|----------------|----------------|----------------|
| 123 | 456 | 789 | 101 | 2.0 | 0.45 | 0.32 | 0.28 | 1.20 | 15.80 | 0.65 |

## Configuration

### Environment Variables

- `PORT`: Port to run the Flask app (default: 5050).

### Default URLs

- Django Base URL: `https://medsum.amritaai.org`
- Flask Transcribe URL: `https://test-medsum.amritaai.org/transcribe`

Modify these in the code or via the UI advanced settings.

### Templates

- **SOAP**: Subjective, Objective, Assessment, Plan.
- **Discharge Summary**: Patient details, admission info, diagnosis, etc.

## API Endpoints

The app exposes the following endpoints:

- `GET /`: Serves the main web UI.
- `POST /run`: Accepts form data for running the load test. Streams real-time events via Server-Sent Events (SSE).

## Performance Considerations

- **Concurrency Model**:
  - Doctors: Parallel threads.
  - Patients per doctor: Parallel threads.
  - Audios per patient: Sequential (to maintain order).
- **Timeouts**:
  - Login/Profile/Patient: 15-30 seconds.
  - Upload/Storage: 30 seconds.
  - Transcription: 180 seconds.
- **Bottlenecks**: Step 5 (transcription) is typically the slowest due to AI processing.

## Troubleshooting

- **Authentication Errors**: Verify doctor credentials and backend URLs.
- **Upload Failures**: Check file formats and network connectivity.
- **Transcription Timeouts**: Ensure the Flask service is responsive; increase timeout if needed.
- **Logs Not Showing**: Check browser console for JavaScript errors.

## Contributing

1. Fork the repository.
2. Create a feature branch.
3. Make changes and test thoroughly.
4. Submit a pull request.

## License

This project is licensed under the MIT License. See LICENSE file for details.

## Contact

For issues or questions, contact the development team at [your-email@example.com].

---

## MEDSUM Accuracy Testing Framework

A separate module for automated accuracy and regression testing against the MedSum backend. Runs on **port 5051** (load test stays on 5050).

```bash
pip install -r requirements_medsum.txt
copy config\medsum_config.example.yaml config\medsum_config.yaml
python run_medsum_test.py
```

Open http://127.0.0.1:5051

### Google Drive Setup (Service Account)

1. Go to Google Cloud Console → IAM & Admin → Service Accounts
2. Create a service account and download the JSON key → save as `credentials/service_account.json`
3. In Google Drive, share the root test folder with the service account email (Viewer access)
4. Copy the folder ID from the URL: `drive.google.com/drive/folders/THIS_PART`
5. Set `google_drive.root_folder_id` in `config/medsum_config.yaml`

**Expected folder structure:**
```
[Root Folder]
├── Hindi/
│   ├── hindi_05min_Cardiology.mp3
│   └── hindi_05min_Cardiology.txt
├── English/
│   ├── english_10min_Neurology.mp3
│   └── english_10min_Neurology.txt
└── Punjabi/
    └── punjabi_08min_Orthopedics.mp3   ← no .txt = runs without accuracy scoring
```

### Backend API Flow

Each test run:
1. **Django** — `POST /api/auth/login/` → `Token` auth
2. **Django** — `GET /api/patients/{id}/` → verify patient exists
3. **Django** — `POST /api/consultations/` with `"notes": "MEDSUM_AUTO_TEST"`
4. **Flask** — `POST /transcribe` (multipart audio upload) → `job_id`
5. **Flask** — poll `GET /transcribe/status/{job_id}` until complete
6. **Django** — `POST /api/consultations/{id}/transcription/` → triggers summary
7. **Django** — poll `GET /api/consultations/{id}/summary/`
8. **Django** — `GET /api/consultations/{id}/medications/`

### Scheduled Runs

Enable in `config/medsum_config.yaml`:
```yaml
scheduler:
  enabled: true
  cron: "0 2 * * *"
  ai_model: "deepseek"
  max_parallel_tests: 2
```

Or use the **Scheduled Runs** panel in the UI to enable/disable, change cron, or click **Run All Now**.</content>
<parameter name="filePath">c:\Users\guru.aswini\Desktop\Medsum-loadtesting\medsum-load-test\README.md