"""
Medisum Load Test Web UI
Run:  python web_app.py
Open: http://127.0.0.1:5050
"""

import base64
import io
import json
import queue
import threading
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Flask, Response, request, stream_with_context

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Templates
# ─────────────────────────────────────────────────────────────────────────────

SOAP_TEMPLATE = {
    "subjective": {
        "chief_complaint": "", "history_of_present_illness": "",
        "past_medical_history": "", "medications": "", "allergies": "",
        "social_history": "", "family_history": "",
    },
    "objective": {
        "vitals": {"blood_pressure": "", "heart_rate": "", "respiratory_rate": "", "temperature": ""},
        "physical_exam": {"heart": []},
    },
    "assessment": [],
    "plan": {
        "medications": [{"drug_name": "", "dose": "", "schedule": "", "duration": "", "instructions": "", "snomed_ct_id": ""}],
        "activity": "", "investigations": "", "education": "", "follow_up": "",
    },
    "summary": "",
}

DISCHARGE_TEMPLATE = {
    "Patient_Details": {"Name": "", "Age": "", "Gender": "", "Blood_Group": "", "ABHA_ID": ""},
    "Admission_Details": {"Date_of_Admission": "", "Date_of_Discharge": "", "Ward": "", "Bed_Number": "", "Consultant": "", "Department": ""},
    "Presenting_Complaints": "", "History_of_Presenting_Illness": "", "Past_Medical_History": "",
    "Personal_History": "", "Family_History": "",
    "Examination_Findings": {"General_Examination": "", "Systemic_Examination": ""},
    "Investigations": "",
    "Diagnosis": {"Primary_Diagnosis": "", "Secondary_Diagnosis": ""},
    "Course_in_the_Hospital_and_Discussion": "", "Operative_or_Procedure_Findings": "",
    "Prognosis_on_Discharge": "", "Diet_Recommendation": "", "Physiscal_Activity": "",
    "Discharge_Medication": {"Serial_Number": [], "Drug_Name": [], "Dose": [], "Schedule": [], "Duration": [], "Days": [], "Instructions": []},
    "Follow_Up": "", "summary": "",
}

KNOWN_META_KEYS = {
    "doctor_details", "patient_demographics", "session",
    "transcription", "transcription-time", "translation-time",
    "llm-time", "audio_length", "total-time", "medicines_backend",
}

# ─────────────────────────────────────────────────────────────────────────────
# HTML
# ─────────────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Medisum Load Test</title>
<style>
  :root {
    --bg: #f5f6fa;
    --surface: #ffffff;
    --surface2: #f0f1f5;
    --border: #d9dce6;
    --accent: #2563eb;
    --accent2: #7c3aed;
    --green: #16a34a;
    --red: #dc2626;
    --yellow: #d97706;
    --orange: #ea580c;
    --text: #111827;
    --muted: #6b7280;
    --mono: 'Menlo', 'Consolas', monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; min-height: 100vh; display: flex; flex-direction: column; }

  /* Header */
  header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 14px 28px; display: flex; align-items: center; gap: 12px; position: sticky; top: 0; z-index: 100; }
  header h1 { font-size: 17px; font-weight: 700; }
  .badge { background: var(--accent2); color: #fff; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 20px; }
  .header-right { margin-left: auto; display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--muted); }

  /* Screens */
  .screen { display: none; flex: 1; padding: 24px 28px 0; max-width: 1200px; width: 100%; margin: 0 auto; }
  .screen.active { display: block; }

  /* Cards */
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px; margin-bottom: 18px; }
  .card-title { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); flex-shrink: 0; }

  /* Fields */
  .field { margin-bottom: 12px; }
  .field:last-child { margin-bottom: 0; }
  .field label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
  .field input, .field select { width: 100%; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); padding: 7px 10px; font-size: 13px; outline: none; transition: border-color .15s; }
  .field input:focus, .field select:focus { border-color: var(--accent); }
  .field input::placeholder { color: #aaa; }
  select option { background: #fff; }

  /* Doctor cards grid */
  #doctors-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; margin-bottom: 14px; }
  .doctor-card { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 14px; position: relative; }
  .doctor-card-header { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; font-size: 12px; font-weight: 600; color: var(--muted); }
  .doctor-num { background: var(--accent); color: #fff; width: 20px; height: 20px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; flex-shrink: 0; }
  .remove-btn { margin-left: auto; background: none; border: none; color: #ccc; cursor: pointer; font-size: 16px; line-height: 1; padding: 0; }
  .remove-btn:hover { color: var(--red); }
  .doctor-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; }
  .add-btn { background: none; border: 1.5px dashed var(--border); border-radius: 8px; color: var(--muted); font-size: 13px; font-weight: 500; cursor: pointer; padding: 10px; width: 100%; text-align: center; transition: border-color .15s, color .15s; }
  .add-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* Patient tag input */
  .tag-box { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 5px 8px; display: flex; flex-wrap: wrap; gap: 5px; align-items: center; min-height: 36px; cursor: text; transition: border-color .15s; }
  .tag-box:focus-within { border-color: var(--accent); }
  .tag { background: #dbeafe; color: #1e40af; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 4px; display: flex; align-items: center; gap: 4px; }
  .tag button { background: none; border: none; color: #60a5fa; cursor: pointer; font-size: 13px; line-height: 1; padding: 0; }
  .tag button:hover { color: var(--red); }
  .tag-input { border: none; outline: none; background: transparent; font-size: 12px; color: var(--text); min-width: 80px; flex: 1; padding: 2px 0; }
  .tag-hint { font-size: 11px; color: #aaa; margin-top: 3px; }

  /* Audio upload */
  .upload-zone { border: 2px dashed var(--border); border-radius: 8px; padding: 28px; text-align: center; cursor: pointer; transition: border-color .2s, background .2s; position: relative; }
  .upload-zone:hover, .upload-zone.drag { border-color: var(--accent); background: #eff6ff; }
  .upload-zone input[type=file] { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; }
  .upload-zone .icon { font-size: 28px; margin-bottom: 6px; }
  .upload-zone p { color: var(--muted); font-size: 13px; }
  #audio-list { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
  .audio-item { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; padding: 7px 12px; display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .audio-item span { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .audio-item button { background: none; border: none; color: #aaa; cursor: pointer; font-size: 14px; }
  .audio-item button:hover { color: var(--red); }

  /* Advanced */
  .adv-toggle { background: none; border: none; color: var(--muted); font-size: 12px; cursor: pointer; display: flex; align-items: center; gap: 6px; padding: 0; margin-bottom: 12px; }
  .adv-toggle:hover { color: var(--text); }
  .adv-arrow { transition: transform .2s; display: inline-block; }
  .adv-toggle.open .adv-arrow { transform: rotate(90deg); }
  .adv-fields { display: none; }
  .adv-fields.open { display: block; }
  .adv-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }

  /* Action buttons */
  .btn { border: none; border-radius: 7px; padding: 10px 24px; font-size: 13px; font-weight: 600; cursor: pointer; transition: opacity .15s; display: inline-flex; align-items: center; gap: 8px; }
  .btn:hover { opacity: 0.88; }
  .btn:disabled { opacity: 0.45; cursor: not-allowed; }
  .btn-primary { background: var(--accent); color: #fff; }
  .btn-secondary { background: var(--surface2); color: var(--text); border: 1px solid var(--border); }
  .btn-green { background: var(--green); color: #fff; }
  .btn-danger { background: var(--red); color: #fff; }
  .action-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
  .spinner-sm { width: 14px; height: 14px; border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff; border-radius: 50%; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Stats bar */
  .stats-bar { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 18px; display: flex; gap: 28px; flex-wrap: wrap; margin-bottom: 18px; }
  .stat { display: flex; flex-direction: column; gap: 2px; }
  .stat-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); font-weight: 600; }
  .stat-value { font-size: 22px; font-weight: 700; color: var(--text); font-variant-numeric: tabular-nums; }
  .stat-value.green { color: var(--green); }
  .stat-value.red { color: var(--red); }
  .stat-value.blue { color: var(--accent); }

  /* Preview / Run table */
  .table-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; margin-bottom: 20px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { background: var(--surface2); padding: 10px 14px; text-align: left; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); border-bottom: 1px solid var(--border); white-space: nowrap; }
  td { padding: 10px 14px; border-bottom: 1px solid var(--border); vertical-align: middle; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: var(--bg); }
  .mono { font-family: var(--mono); font-size: 12px; }

  /* Status badges */
  .badge-status { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 20px; white-space: nowrap; }
  .badge-pending { background: #f3f4f6; color: var(--muted); }
  .badge-running { background: #dbeafe; color: #1d4ed8; }
  .badge-pass    { background: #dcfce7; color: #15803d; }
  .badge-fail    { background: #fee2e2; color: #dc2626; }
  .badge-skip    { background: #fef9c3; color: #92400e; }
  .dot-spin { width: 8px; height: 8px; border: 1.5px solid rgba(29,78,216,0.3); border-top-color: #1d4ed8; border-radius: 50%; animation: spin .7s linear infinite; }

  /* Error text */
  .err-text { font-size: 11px; color: var(--red); margin-top: 3px; font-family: var(--mono); }

  /* Expandable transcription/summary row */
  .expand-row td { padding: 0; background: #f8faff; border-bottom: 1px solid var(--border); }
  .expand-inner { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
  .expand-section { padding: 14px 18px; border-right: 1px solid var(--border); }
  .expand-section:last-child { border-right: none; }
  .expand-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px; color: var(--muted); margin-bottom: 8px; }
  .expand-text { font-family: var(--mono); font-size: 12px; line-height: 1.7; color: var(--text); white-space: pre-wrap; max-height: 200px; overflow-y: auto; }
  .view-btn { background: none; border: 1px solid var(--border); color: var(--accent); font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 4px; cursor: pointer; white-space: nowrap; }
  .view-btn:hover { background: #eff6ff; }

  /* Progress bar */
  .progress-wrap { background: #e5e7eb; border-radius: 99px; height: 6px; flex: 1; min-width: 100px; overflow: hidden; }
  .progress-bar { height: 100%; background: var(--accent); border-radius: 99px; transition: width .4s ease; }

  /* Status bar */
  #status-bar { padding: 10px 14px; border-radius: 8px; margin-bottom: 14px; font-size: 13px; display: none; align-items: center; gap: 10px; }
  #status-bar.info    { background: #eff6ff; border: 1px solid #bfdbfe; color: #1d4ed8; }
  #status-bar.success { background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d; }
  #status-bar.error   { background: #fef2f2; border: 1px solid #fecaca; color: #dc2626; }

  /* Logs */
  #logs-panel { position: sticky; bottom: 0; background: var(--surface); border: 1px solid var(--border); border-bottom: none; border-radius: 10px 10px 0 0; overflow: hidden; margin-top: 24px; }
  .logs-header { background: var(--surface2); padding: 9px 14px; display: flex; align-items: center; justify-content: space-between; cursor: pointer; user-select: none; border-bottom: 1px solid var(--border); }
  .logs-header-left { display: flex; align-items: center; gap: 10px; font-size: 12px; font-weight: 600; color: var(--muted); }
  .logs-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); transition: background .3s; }
  .logs-dot.active { background: var(--green); animation: pulse 1s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1}50%{opacity:.4} }
  .logs-actions { display: flex; gap: 8px; }
  .logs-btn { background: none; border: 1px solid var(--border); color: var(--muted); font-size: 11px; padding: 3px 10px; border-radius: 4px; cursor: pointer; }
  .logs-btn:hover { color: var(--text); }
  #logs-body { height: 180px; overflow-y: auto; padding: 10px 14px; font-family: var(--mono); font-size: 12px; line-height: 1.6; transition: height .25s ease, padding .25s ease; }
  #logs-body.minimized { height: 0; padding: 0 14px; overflow: hidden; }
  .log-line { margin-bottom: 1px; word-break: break-all; color: var(--muted); }
  .log-line.ok   { color: #15803d; }
  .log-line.warn { color: #b45309; }
  .log-line.fail { color: #dc2626; }
  .log-line.step { color: #2563eb; }
</style>
</head>
<body>

<header>
  <h1>Medisum Load Test Console</h1>
  <span class="badge">Load Testing</span>
  <div class="header-right" id="header-progress" style="display:none;">
    <span id="hdr-count">0 / 0</span>
    <div class="progress-wrap" style="width:160px;">
      <div class="progress-bar" id="hdr-bar" style="width:0%"></div>
    </div>
  </div>
</header>

<!-- ═══════════════════════════ SCREEN 1: INPUT ═══════════════════════════ -->
<div class="screen active" id="s-input">

  <!-- Doctors -->
  <div class="card">
    <div class="card-title"><span class="dot"></span> Doctors</div>
    <div id="doctors-list"></div>
    <button type="button" class="add-btn" id="add-doctor-btn">+ Add Doctor</button>
  </div>

  <!-- Audio -->
  <div class="card">
    <div class="card-title"><span class="dot" style="background:var(--green)"></span> Audio Files <span style="font-weight:400;color:var(--muted);text-transform:none;letter-spacing:0;">(shared across all patients)</span></div>
    <div class="upload-zone" id="upload-zone">
      <input type="file" id="audio-input" accept="audio/*,.wav,.mp3,.m4a" multiple>
      <div class="icon">🎙️</div>
      <p>Drop audio files here or click to browse — select multiple</p>
      <p style="font-size:11px;margin-top:4px;color:#aaa;">If no files uploaded, a silent WAV is generated per audio slot</p>
    </div>
    <div id="audio-list"></div>
  </div>

  <!-- Advanced -->
  <div class="card">
    <button type="button" class="adv-toggle" id="adv-toggle">
      <span class="adv-arrow">▶</span>&nbsp; Advanced Settings
    </button>
    <div class="adv-fields" id="adv-fields">
      <div class="adv-grid">
        <div class="field">
          <label>Language</label>
          <select id="cfg-language">
            <option value="en">English (en)</option>
            <option value="hi">Hindi (hi)</option>
            <option value="ta">Tamil (ta)</option>
            <option value="te">Telugu (te)</option>
            <option value="kn">Kannada (kn)</option>
            <option value="ml">Malayalam (ml)</option>
            <option value="bn">Bengali (bn)</option>
            <option value="mr">Marathi (mr)</option>
          </select>
        </div>
        <div class="field">
          <label>LLM</label>
          <select id="cfg-llm">
            <option value="OpenAI">OpenAI</option>
            <option value="Gemma">Gemma</option>
          </select>
        </div>
        <div class="field">
          <label>Template Type</label>
          <select id="cfg-template-type">
            <option value="soap">SOAP</option>
            <option value="discharge">Discharge Summary</option>
          </select>
        </div>
        <div class="field">
          <label>Template ID</label>
          <input id="cfg-template-id" type="text" value="1">
        </div>
        <div class="field">
          <label>Audio Duration (s) — silent WAV fallback</label>
          <input id="cfg-duration" type="number" value="2" step="0.1" min="0.5">
        </div>
        <div class="field">
          <label>Django Base URL</label>
          <input id="cfg-django" type="text" value="https://test-medsum.amritaai.org">
        </div>
        <div class="field">
          <label>Flask Transcribe URL</label>
          <input id="cfg-flask" type="text" value="https://test-medsum.amritaai.org/transcribe">
        </div>
      </div>
    </div>
  </div>

  <div id="status-bar"></div>
  <div style="margin-bottom:28px;">
    <button class="btn btn-primary" id="preview-btn" style="width:100%;justify-content:center;padding:12px;">
      Preview Test Matrix →
    </button>
  </div>
</div>

<!-- ═══════════════════════════ SCREEN 2: PREVIEW / RUN ═══════════════════════════ -->
<div class="screen" id="s-run">

  <div class="action-bar" id="action-bar">
    <button class="btn btn-secondary" id="back-btn">← Back</button>
    <button class="btn btn-green" id="run-btn">
      <span id="run-btn-text">Confirm &amp; Run</span>
      <div class="spinner-sm" id="run-spinner" style="display:none;"></div>
    </button>
    <button class="btn btn-secondary" id="new-test-btn" style="display:none;">↺ New Test</button>
    <div class="progress-wrap" id="run-progress-wrap" style="display:none;flex:1;min-width:80px;">
      <div class="progress-bar" id="run-bar" style="width:0%"></div>
    </div>
    <span id="run-counter" style="font-size:13px;color:var(--muted);display:none;"></span>
  </div>

  <!-- Stats -->
  <div class="stats-bar" id="stats-bar">
    <div class="stat"><span class="stat-label">Doctors</span><span class="stat-value blue" id="stat-doctors">0</span></div>
    <div class="stat"><span class="stat-label">Patient Slots</span><span class="stat-value blue" id="stat-patients">0</span></div>
    <div class="stat"><span class="stat-label">Audios / Patient</span><span class="stat-value blue" id="stat-audios">0</span></div>
    <div class="stat"><span class="stat-label">Total Runs</span><span class="stat-value blue" id="stat-total">0</span></div>
    <div class="stat" id="stat-pass-wrap" style="display:none;"><span class="stat-label">Passed</span><span class="stat-value green" id="stat-pass">0</span></div>
    <div class="stat" id="stat-fail-wrap" style="display:none;"><span class="stat-label">Failed</span><span class="stat-value red" id="stat-fail">0</span></div>
  </div>

  <!-- Table -->
  <div class="table-wrap">
    <table id="run-table">
      <thead>
        <tr>
          <th>#</th>
          <th>Doctor Phone</th>
          <th>Patient ID</th>
          <th>Audio File</th>
          <th>Status</th>
          <th>audio_id</th>
          <th>summary_id</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody id="run-tbody"></tbody>
    </table>
  </div>

</div>

<!-- Logs panel -->
<div id="logs-panel">
  <div class="logs-header" id="logs-header">
    <div class="logs-header-left">
      <div class="logs-dot" id="logs-dot"></div>
      <span>Logs</span>
      <span id="log-count" style="font-weight:400;">0 lines</span>
    </div>
    <div class="logs-actions">
      <button class="logs-btn" id="logs-clear" type="button">Clear</button>
      <button class="logs-btn" id="logs-toggle" type="button">Minimize</button>
    </div>
  </div>
  <div id="logs-body"></div>
</div>

<script>
// ── State ────────────────────────────────────────────────────────────────────
let doctorCount = 0;
let audioFiles  = [];   // Array of File objects
let matrix      = [];   // [{rowId, doctorIdx, phone, patientId, audioName, audioIdx}]
let runStats    = { total: 0, passed: 0, failed: 0 };

// ── Doctor cards ─────────────────────────────────────────────────────────────
function addDoctor() {
  const idx  = doctorCount++;
  const card = document.createElement('div');
  card.className = 'doctor-card';
  card.dataset.idx = idx;
  card.innerHTML = `
    <div class="doctor-card-header">
      <span class="doctor-num">${idx + 1}</span>
      <span>Doctor ${idx + 1}</span>
      <button class="remove-btn" onclick="removeDoctor(this)" title="Remove">×</button>
    </div>
    <div class="doctor-fields">
      <div class="field">
        <label>Phone Number</label>
        <input class="doc-phone" type="text" placeholder="9876543210" required>
      </div>
      <div class="field">
        <label>Password</label>
        <input class="doc-password" type="password" placeholder="••••••••" required>
      </div>
    </div>
    <div class="field">
      <label>Patient IDs</label>
      <div class="tag-box" onclick="this.querySelector('.tag-input').focus()">
        <input class="tag-input" type="text" placeholder="Type ID → Enter">
      </div>
      <div class="tag-hint">Press Enter or comma after each patient ID</div>
    </div>`;

  const tagInput = card.querySelector('.tag-input');
  tagInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      addTag(tagInput);
    } else if (e.key === 'Backspace' && !tagInput.value) {
      const box  = tagInput.closest('.tag-box');
      const tags = box.querySelectorAll('.tag');
      if (tags.length) tags[tags.length - 1].remove();
    }
  });
  tagInput.addEventListener('blur', () => { if (tagInput.value.trim()) addTag(tagInput); });

  document.getElementById('doctors-list').appendChild(card);
  renumberDoctors();
}

function removeDoctor(btn) {
  btn.closest('.doctor-card').remove();
  renumberDoctors();
}

function renumberDoctors() {
  document.querySelectorAll('.doctor-card').forEach((c, i) => {
    c.dataset.idx = i;
    c.querySelector('.doctor-num').textContent = i + 1;
    c.querySelector('.doctor-card-header span:nth-child(2)').textContent = `Doctor ${i + 1}`;
  });
}

function addTag(input) {
  const val = input.value.replace(/,/g, '').trim();
  if (!val || isNaN(Number(val))) { input.value = ''; return; }
  const box = input.closest('.tag-box');
  if ([...box.querySelectorAll('.tag span')].some(s => s.textContent === val)) { input.value = ''; return; }
  const tag = document.createElement('span');
  tag.className = 'tag';
  tag.innerHTML = `<span>${val}</span><button type="button" onclick="this.parentElement.remove()">×</button>`;
  box.insertBefore(tag, input);
  input.value = '';
}

function getDoctors() {
  return [...document.querySelectorAll('.doctor-card')].map(card => ({
    phone:      card.querySelector('.doc-phone').value.trim(),
    password:   card.querySelector('.doc-password').value.trim(),
    patientIds: [...card.querySelectorAll('.tag span')].map(s => s.textContent),
  }));
}

document.getElementById('add-doctor-btn').addEventListener('click', addDoctor);

// ── Audio upload ─────────────────────────────────────────────────────────────
const uploadZone = document.getElementById('upload-zone');
const audioInput = document.getElementById('audio-input');

audioInput.addEventListener('change', () => { addAudioFiles(audioInput.files); audioInput.value = ''; });
uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('drag'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag'));
uploadZone.addEventListener('drop', e => {
  e.preventDefault(); uploadZone.classList.remove('drag');
  addAudioFiles(e.dataTransfer.files);
});

function addAudioFiles(files) {
  for (const f of files) {
    if (!audioFiles.some(x => x.name === f.name)) audioFiles.push(f);
  }
  renderAudioList();
}

function renderAudioList() {
  const list = document.getElementById('audio-list');
  list.innerHTML = audioFiles.map((f, i) => `
    <div class="audio-item">
      <span>🎵 ${f.name}</span>
      <span style="color:var(--muted);font-size:11px;">${(f.size/1024).toFixed(1)} KB</span>
      <button onclick="removeAudio(${i})">×</button>
    </div>`).join('');
}

function removeAudio(i) { audioFiles.splice(i, 1); renderAudioList(); }

// ── Advanced toggle ──────────────────────────────────────────────────────────
document.getElementById('adv-toggle').addEventListener('click', function() {
  this.classList.toggle('open');
  document.getElementById('adv-fields').classList.toggle('open');
});

// ── Logs ─────────────────────────────────────────────────────────────────────
const logsBody   = document.getElementById('logs-body');
const logsToggle = document.getElementById('logs-toggle');
const logsDot    = document.getElementById('logs-dot');
const logCountEl = document.getElementById('log-count');
let logsMin = false, logCount = 0;

function toggleLogs() {
  logsMin = !logsMin;
  logsBody.classList.toggle('minimized', logsMin);
  logsToggle.textContent = logsMin ? 'Expand' : 'Minimize';
}
document.getElementById('logs-header').addEventListener('click', toggleLogs);
logsToggle.addEventListener('click', e => { e.stopPropagation(); toggleLogs(); });
document.getElementById('logs-clear').addEventListener('click', e => {
  e.stopPropagation(); logsBody.innerHTML = '';
  logCount = 0; logCountEl.textContent = '0 lines';
});

function appendLog(msg) {
  logCount++;
  logCountEl.textContent = logCount + ' lines';
  const d = document.createElement('div');
  d.className = 'log-line';
  const m = msg.trim();
  if      (m.includes('OK]'))   d.classList.add('ok');
  else if (m.includes('WARN'))  d.classList.add('warn');
  else if (m.includes('FAIL') || m.includes('ERROR')) d.classList.add('fail');
  else if (m.includes('[Step')) d.classList.add('step');
  d.textContent = msg;
  logsBody.appendChild(d);
  if (!logsMin) logsBody.scrollTop = logsBody.scrollHeight;
}

// ── Status bar (screen 1) ────────────────────────────────────────────────────
const statusBar = document.getElementById('status-bar');
function setStatus(msg, type) { statusBar.textContent = msg; statusBar.className = type; statusBar.style.display = 'flex'; }
function clearStatus() { statusBar.style.display = 'none'; }

// ── Validation & matrix build ────────────────────────────────────────────────
function buildMatrix() {
  const doctors = getDoctors();
  const audios  = audioFiles.length ? audioFiles.map(f => f.name) : ['(silent WAV)'];
  matrix = [];
  let rowNum = 1;
  doctors.forEach((doc, di) => {
    const patients = doc.patientIds.length ? doc.patientIds : ['(none)'];
    patients.forEach(pid => {
      audios.forEach((aName, ai) => {
        matrix.push({
          rowId: `d${di}_p${pid}_a${ai}`,
          rowNum: rowNum++,
          doctorIdx: di,
          phone: doc.phone,
          patientId: pid,
          audioName: aName,
          audioIdx: ai,
        });
      });
    });
  });
  return { doctors, audios, matrix };
}

// ── Preview button ────────────────────────────────────────────────────────────
document.getElementById('preview-btn').addEventListener('click', () => {
  clearStatus();
  const doctors = getDoctors();

  // Validate
  if (!doctors.length) return setStatus('Add at least one doctor.', 'error');
  for (const [i, d] of doctors.entries()) {
    if (!d.phone)  return setStatus(`Doctor ${i+1}: phone number is required.`, 'error');
    if (!d.password) return setStatus(`Doctor ${i+1}: password is required.`, 'error');
    if (!d.patientIds.length) return setStatus(`Doctor ${i+1}: add at least one patient ID.`, 'error');
  }

  const { matrix: mx, audios } = buildMatrix();
  const totalPatients = doctors.reduce((s, d) => s + d.patientIds.length, 0);

  // Update stats
  document.getElementById('stat-doctors').textContent  = doctors.length;
  document.getElementById('stat-patients').textContent = totalPatients;
  document.getElementById('stat-audios').textContent   = audios.length;
  document.getElementById('stat-total').textContent    = mx.length;

  // Build table
  const tbody = document.getElementById('run-tbody');
  tbody.innerHTML = mx.map(r => `
    <tr id="row-${r.rowId}">
      <td class="mono" style="color:var(--muted)">${r.rowNum}</td>
      <td class="mono">${r.phone}</td>
      <td class="mono">${r.patientId}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${r.audioName}">${r.audioName}</td>
      <td><span class="badge-status badge-pending">Pending</span></td>
      <td class="mono" id="aid-${r.rowId}">—</td>
      <td class="mono" id="sid-${r.rowId}">—</td>
      <td id="det-${r.rowId}" style="font-size:12px;color:var(--muted);">—</td>
    </tr>`).join('');

  // Reset run UI
  runStats = { total: mx.length, passed: 0, failed: 0 };
  document.getElementById('stat-pass-wrap').style.display = 'none';
  document.getElementById('stat-fail-wrap').style.display = 'none';
  document.getElementById('run-btn').style.display  = 'inline-flex';
  document.getElementById('run-btn-text').textContent = 'Confirm & Run';
  document.getElementById('run-btn').disabled = false;
  document.getElementById('back-btn').style.display = 'inline-flex';
  document.getElementById('new-test-btn').style.display = 'none';
  document.getElementById('run-progress-wrap').style.display = 'none';
  document.getElementById('run-counter').style.display = 'none';
  document.getElementById('header-progress').style.display = 'none';

  showScreen('s-run');
});

// ── Back button ───────────────────────────────────────────────────────────────
document.getElementById('back-btn').addEventListener('click', () => showScreen('s-input'));
document.getElementById('new-test-btn').addEventListener('click', () => {
  logsDot.classList.remove('active');
  showScreen('s-input');
});

// ── Screen switch ─────────────────────────────────────────────────────────────
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  window.scrollTo(0, 0);
}

// ── Run ───────────────────────────────────────────────────────────────────────
document.getElementById('run-btn').addEventListener('click', async () => {
  const runBtn    = document.getElementById('run-btn');
  const runSpinner = document.getElementById('run-spinner');
  runBtn.disabled = true;
  document.getElementById('run-btn-text').textContent = 'Running…';
  runSpinner.style.display = 'inline-block';
  document.getElementById('back-btn').style.display = 'none';
  document.getElementById('run-progress-wrap').style.display = 'flex';
  document.getElementById('run-counter').style.display = 'inline';
  document.getElementById('header-progress').style.display = 'flex';
  document.getElementById('stat-pass-wrap').style.display = '';
  document.getElementById('stat-fail-wrap').style.display = '';

  logsDot.classList.add('active');
  if (logsMin) toggleLogs();

  runStats.passed = 0; runStats.failed = 0;
  updateProgress();

  // Build form data
  const fd = new FormData();
  fd.append('doctors_json', JSON.stringify(getDoctors()));
  audioFiles.forEach((f, i) => fd.append(`audio_${i}`, f, f.name));
  fd.append('language',      document.getElementById('cfg-language').value);
  fd.append('llm',           document.getElementById('cfg-llm').value);
  fd.append('template_type', document.getElementById('cfg-template-type').value);
  fd.append('template_id',   document.getElementById('cfg-template-id').value);
  fd.append('duration',      document.getElementById('cfg-duration').value);
  fd.append('django_url',    document.getElementById('cfg-django').value);
  fd.append('flask_url',     document.getElementById('cfg-flask').value);

  try {
    const resp = await fetch('/run', { method: 'POST', body: fd });
    if (!resp.ok) {
      const errText = await resp.text();
      appendLog(`[ERROR] Server returned ${resp.status}: ${errText}`);
      throw new Error(`HTTP ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const raw = line.slice(5).trim();
        if (!raw) continue;
        let evt;
        try { evt = JSON.parse(raw); } catch { continue; }
        handleEvent(evt);
      }
    }
  } catch (err) {
    appendLog('[ERROR] ' + err.message);
  }

  runBtn.disabled = false;
  runSpinner.style.display = 'none';
  document.getElementById('run-btn-text').textContent = 'Run Again';
  document.getElementById('new-test-btn').style.display = 'inline-flex';
  logsDot.classList.remove('active');
});

function handleEvent(evt) {
  if (evt.type === 'log') {
    appendLog(evt.message);
  } else if (evt.type === 'row_update') {
    updateRow(evt);
  } else if (evt.type === 'done') {
    appendLog(`── All done: ${evt.passed}/${evt.total} passed ──`);
  }
}

function updateRow(evt) {
  const row = document.getElementById('row-' + evt.row_id);
  if (!row) return;
  const statusCell = row.querySelector('.badge-status');
  const aidCell    = document.getElementById('aid-' + evt.row_id);
  const sidCell    = document.getElementById('sid-' + evt.row_id);
  const detCell    = document.getElementById('det-' + evt.row_id);

  statusCell.className = 'badge-status badge-' + evt.status;
  if (evt.status === 'running') {
    statusCell.innerHTML = '<div class="dot-spin"></div> Running';
  } else if (evt.status === 'pass') {
    statusCell.textContent = '✓ Pass';
    runStats.passed++;
    if (aidCell) aidCell.textContent = evt.audio_id ?? '—';
    if (sidCell) sidCell.textContent = evt.summary_id ?? '—';
    if (detCell) {
      const timeStr = evt.total_time ? `${evt.total_time}s` : '';
      detCell.innerHTML = `${timeStr ? `<span style="color:var(--muted);font-size:11px;">${timeStr}</span> ` : ''}<button class="view-btn" onclick="toggleExpand('${evt.row_id}')">View ▾</button>`;
    }

    // Insert expandable sub-row after this row
    const expandRow = document.createElement('tr');
    expandRow.id = 'expand-' + evt.row_id;
    expandRow.className = 'expand-row';
    expandRow.style.display = 'none';
    const transcription = evt.transcription || '(empty)';
    const summary = typeof evt.summary === 'object'
      ? JSON.stringify(evt.summary, null, 2)
      : (evt.summary || '(empty)');
    expandRow.innerHTML = `<td colspan="8"><div class="expand-inner">
      <div class="expand-section">
        <div class="expand-label">🎙 Transcription</div>
        <div class="expand-text">${escHtml(transcription)}</div>
      </div>
      <div class="expand-section">
        <div class="expand-label">📋 Summary</div>
        <div class="expand-text">${escHtml(summary)}</div>
      </div>
    </div></td>`;
    row.insertAdjacentElement('afterend', expandRow);
    updateProgress();
  } else if (evt.status === 'fail') {
    statusCell.textContent = '✗ Fail';
    runStats.failed++;
    if (detCell) { detCell.innerHTML = `<span class="err-text">${evt.error || 'Unknown error'}</span>`; }
    updateProgress();
  }

  document.getElementById('stat-pass').textContent = runStats.passed;
  document.getElementById('stat-fail').textContent = runStats.failed;
}

function updateProgress() {
  const done  = runStats.passed + runStats.failed;
  const pct   = runStats.total ? Math.round(done / runStats.total * 100) : 0;
  document.getElementById('run-bar').style.width  = pct + '%';
  document.getElementById('hdr-bar').style.width  = pct + '%';
  document.getElementById('run-counter').textContent = `${done} / ${runStats.total}`;
  document.getElementById('hdr-count').textContent   = `${done} / ${runStats.total}`;
}

// ── Expand / collapse transcription+summary ───────────────────────────────────
function toggleExpand(rowId) {
  const expandRow = document.getElementById('expand-' + rowId);
  if (!expandRow) return;
  const visible = expandRow.style.display !== 'none';
  expandRow.style.display = visible ? 'none' : 'table-row';
  // Update button label
  const btn = document.querySelector(`#row-${rowId} .view-btn`);
  if (btn) btn.textContent = visible ? 'View ▾' : 'Hide ▴';
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Init ──────────────────────────────────────────────────────────────────────
addDoctor();  // start with one doctor row
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_wav(duration: float = 2.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * duration))
    return buf.getvalue()


def _extract_summary(body: dict) -> str:
    text = body.get("summary", "")
    if not text:
        for k in ("medical_summary", "subjective", "Diagnosis",
                  "Course_in_the_Hospital_and_Discussion", "Medicines"):
            v = body.get(k)
            if v:
                text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                break
    if not text:
        content = {k: v for k, v in body.items() if k not in KNOWN_META_KEYS}
        text = json.dumps(content, ensure_ascii=False)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Per-doctor worker
# ─────────────────────────────────────────────────────────────────────────────

def _run_patient(doctor_idx, patient_id, doctor_id, auth_token,
                 doctor_name, department, hospital,
                 audios, cfg, q):
    """
    Runs all audios for one patient sequentially (step 4→5→6 must be in order).
    Each call gets its own requests.Session — safe to run in parallel with other patients.
    """
    def log(msg):          q.put({"type": "log",        "message": msg})
    def row(row_id, **kw): q.put({"type": "row_update", "row_id": row_id, **kw})

    django    = cfg["django_url"].rstrip("/")
    flask_url = cfg["flask_url"].rstrip("/")
    if not flask_url.endswith("/transcribe"):
        flask_url += "/transcribe"

    language    = cfg["language"]
    llm         = cfg["llm"]
    template_id = cfg["template_id"]
    template    = SOAP_TEMPLATE if cfg["template_type"] == "soap" else DISCHARGE_TEMPLATE
    duration    = float(cfg["duration"])

    # Own session per patient thread — not shared with other threads
    sess = requests.Session()
    sess.headers["Authorization"] = f"Bearer {auth_token}"

    # Fetch patient details
    patient_name = age = gender = ""
    try:
        rpt = sess.get(f"{django}/api/patient-data/{patient_id}/", timeout=15)
        if rpt.status_code == 200:
            pd = rpt.json()
            patient_name = pd.get("patient_name", "")
            age          = str(pd.get("age", ""))
            gender       = pd.get("gender", "")
            log(f"  [Dr {doctor_idx+1}|Pt {patient_id}] {patient_name} | age={age} | gender={gender}")
        else:
            log(f"  [Dr {doctor_idx+1}|Pt {patient_id}] WARN: patient-data returned {rpt.status_code}")
    except Exception as e:
        log(f"  [Dr {doctor_idx+1}|Pt {patient_id}] WARN: {e}")

    # Audios are sequential per patient (upload → transcribe → store must be in order)
    for audio_idx, (audio_name, audio_bytes) in enumerate(audios):
        row_id = f"d{doctor_idx}_p{patient_id}_a{audio_idx}"
        row(row_id, status="running")
        log(f"  [Dr {doctor_idx+1}|Pt {patient_id}] Audio {audio_idx+1}/{len(audios)}: {audio_name}")

        try:
            # Step 4 — upload
            client_sid = str(uuid.uuid4())
            log(f"    [Dr {doctor_idx+1}|Pt {patient_id}] [Step 4] Uploading...")
            r4 = sess.post(f"{django}/api/audio-data/",
                data={"user_id": str(doctor_id), "patient_id": str(patient_id),
                      "language": language, "file_duration": str(duration), "session_id": client_sid},
                files={"audio": (audio_name, audio_bytes, "audio/wav")}, timeout=30)
            if r4.status_code != 201:
                raise RuntimeError(f"audio-data {r4.status_code}: {r4.text[:200]}")
            b4 = r4.json()
            if "audio_id" not in b4:
                raise RuntimeError(f"audio_id missing: {b4}")
            audio_id   = b4["audio_id"]
            session_id = b4.get("session_id") or client_sid
            log(f"    [Dr {doctor_idx+1}|Pt {patient_id}] [Step 4 OK] audio_id={audio_id}")

            # Step 5 — transcribe
            log(f"    [Dr {doctor_idx+1}|Pt {patient_id}] [Step 5] Transcribing ({language}/{llm})...")
            r5 = requests.post(flask_url, json={
                "audio_base64":      base64.b64encode(audio_bytes).decode(),
                "doctor_name":       doctor_name,
                "doctor_department": department,
                "hospital_name":     hospital,
                "patient_id":        str(patient_id),
                "patient_name":      patient_name,
                "age":               str(age),
                "gender":            gender,
                "template":          json.dumps(template),
                "template_id":       template_id,
                "language":          language,
                "llm":               llm,
            }, timeout=180)
            if r5.status_code != 200:
                raise RuntimeError(f"transcribe {r5.status_code}: {r5.text[:200]}")
            b5 = r5.json()
            total_time    = b5.get("total-time")
            transcription = b5.get("transcription", "")
            summary_text  = _extract_summary(b5)
            log(f"    [Dr {doctor_idx+1}|Pt {patient_id}] [Step 5 OK] total-time={total_time}s")

            # Step 6 — store summary
            log(f"    [Dr {doctor_idx+1}|Pt {patient_id}] [Step 6] Storing summary...")
            r6 = sess.post(f"{django}/api/summary-data/", json={
                "user_id": doctor_id, "patient_id": int(patient_id),
                "audio_id": audio_id, "session_id": session_id,
                "summary": summary_text, "summary_length": len(summary_text),
                "transcription": transcription, "template_id": template_id,
                "original_summary": summary_text, "is_approved": "No",
                "is_update": "False", "google_doc_link": None,
            }, timeout=30)
            if r6.status_code != 201:
                raise RuntimeError(f"summary-data {r6.status_code}: {r6.text[:200]}")
            summary_id = r6.json().get("summary_id")
            log(f"    [Dr {doctor_idx+1}|Pt {patient_id}] [Step 6 OK] summary_id={summary_id}")

            row(row_id, status="pass", audio_id=audio_id, summary_id=summary_id,
                total_time=round(total_time, 1) if total_time else None,
                transcription=transcription,
                summary=summary_text)

        except Exception as e:
            log(f"    [Dr {doctor_idx+1}|Pt {patient_id}] [FAIL] {e}")
            row(row_id, status="fail", error=str(e))


def _run_doctor(doctor_idx, doctor_cfg, audios, cfg, q):
    """
    Parallelism model:
      • Login + profile fetch  → sequential  (must complete before anything else)
      • Patients               → parallel    (each gets its own thread + session)
      • Audios within patient  → sequential  (step 4→5→6 must be in order)
    """
    def log(msg):          q.put({"type": "log",        "message": msg})
    def row(row_id, **kw): q.put({"type": "row_update", "row_id": row_id, **kw})

    django      = cfg["django_url"].rstrip("/")
    phone       = doctor_cfg["phone"]
    password    = doctor_cfg["password"]
    patient_ids = doctor_cfg["patientIds"]

    # ── Step 1: Login (sequential — need token before anything else) ──────────
    log(f"  [Dr {doctor_idx+1}] [Step 1] Login  phone={phone}")
    try:
        r = requests.post(f"{django}/api/login/",
                          json={"phone_number": phone, "password": password}, timeout=30)
        if r.status_code != 200:
            msg = f"Login failed {r.status_code}: {r.text[:150]}"
            log(f"  [Dr {doctor_idx+1}] [Step 1 FAIL] {msg}")
            for pid in patient_ids:
                for ai in range(len(audios)):
                    row(f"d{doctor_idx}_p{pid}_a{ai}", status="fail", error=msg)
            return
        body      = r.json()
        doctor_id = body["user"]["id"]
        token     = body["access"]
        log(f"  [Dr {doctor_idx+1}] [Step 1 OK] doctor_id={doctor_id}  free_minutes={body['user'].get('free_minutes')}")
    except Exception as e:
        for pid in patient_ids:
            for ai in range(len(audios)):
                row(f"d{doctor_idx}_p{pid}_a{ai}", status="fail", error=str(e))
        return

    # ── Step 1b: Doctor profile (sequential — shared across all patients) ─────
    doctor_name = department = hospital = ""
    try:
        rp = requests.get(f"{django}/api/user/update/{doctor_id}/",
                          headers={"Authorization": f"Bearer {token}"}, timeout=15)
        if rp.status_code == 200:
            prof        = rp.json()
            doctor_name = f"Dr {prof.get('firstname','')} {prof.get('lastname','')}".strip()
            department  = prof.get("department", "")
            hospital    = prof.get("hospital_name", "")
            log(f"  [Dr {doctor_idx+1}] [Step 1b OK] {doctor_name} | {department} | {hospital}")
        else:
            log(f"  [Dr {doctor_idx+1}] [Step 1b WARN] profile {rp.status_code} — using empty strings")
    except Exception as e:
        log(f"  [Dr {doctor_idx+1}] [Step 1b WARN] {e}")

    # ── Patients: all run in parallel, each with its own session ──────────────
    log(f"  [Dr {doctor_idx+1}] Launching {len(patient_ids)} patient thread(s) in parallel...")
    with ThreadPoolExecutor(max_workers=len(patient_ids)) as pool:
        futures = {
            pool.submit(
                _run_patient,
                doctor_idx, pid, doctor_id, token,
                doctor_name, department, hospital,
                audios, cfg, q
            ): pid
            for pid in patient_ids
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                pid = futures[future]
                log(f"  [Dr {doctor_idx+1}|Pt {pid}] Unhandled exception: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return HTML


@app.route("/run", methods=["POST"])
def run():
    try:
        doctors_raw = request.form.get("doctors_json", "[]")
        doctors     = json.loads(doctors_raw)
    except Exception as e:
        return {"error": f"Bad doctors_json: {e}"}, 400

    try:
        dur_raw  = request.form.get("duration", "")
        duration = float(dur_raw) if dur_raw.strip() else 2.0
    except Exception:
        duration = 2.0

    cfg = {
        "django_url":    request.form.get("django_url",    "https://medsum.amritaai.org"),
        "flask_url":     request.form.get("flask_url",     "https://test-medsum.amritaai.org/transcribe"),
        "language":      request.form.get("language",      "en"),
        "llm":           request.form.get("llm",           "OpenAI"),
        "template_type": request.form.get("template_type", "soap"),
        "template_id":   request.form.get("template_id",   "1"),
        "duration":      duration,
    }

    # Collect uploaded audio files; fall back to silent WAV
    try:
        audios = []
        i = 0
        while True:
            f = request.files.get(f"audio_{i}")
            if f is None:
                break
            audios.append((f.filename, f.read()))
            i += 1
        if not audios:
            audios = [("silent.wav", _make_wav(duration))]
    except Exception as e:
        return {"error": f"Audio file read error: {e}"}, 500

    if not doctors:
        return {"error": "No doctors provided"}, 400

    total = sum(len(d.get("patientIds", [])) for d in doctors) * len(audios)
    q     = queue.Queue()
    done_count = [0]
    passed     = [0]

    def run_all():
        total_patients = sum(len(d.get("patientIds", [])) for d in doctors)
        q.put({"type": "log", "message":
               f"  Starting load test: {len(doctors)} doctor(s) × {total_patients} patient slot(s) × {len(audios)} audio(s) = {total} runs"})
        q.put({"type": "log", "message":
               f"  Concurrency model: doctors=parallel  patients=parallel  audios=sequential per patient"})

        with ThreadPoolExecutor(max_workers=len(doctors)) as pool:
            futures = [pool.submit(_run_doctor, di, doc, audios, cfg, q)
                       for di, doc in enumerate(doctors)]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    q.put({"type": "log", "message": f"  [ERROR] Doctor thread: {e}"})

        q.put({"type": "done", "passed": passed[0], "total": total})
        q.put(None)

    threading.Thread(target=run_all, daemon=True).start()

    def generate():
        # Flush ngrok / reverse-proxy buffers (2 KB padding comment)
        yield ": " + " " * 2048 + "\n\n"
        while True:
            item = q.get()
            if item is None:
                break
            # Track pass count for done event
            if item.get("type") == "row_update" and item.get("status") == "pass":
                passed[0] += 1
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5050))
    print(f"\n  Medisum Load Test Console")
    print(f"  Open: http://127.0.0.1:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
