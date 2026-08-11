const API = '/api/medsum-test';

let driveFiles = [];
let currentTestId = null;
let pollTimer = null;

const languageSelect = document.getElementById('language-select');
const audioSelect = document.getElementById('audio-select');
const aiModelSelect = document.getElementById('ai-model-select');
const runBtn = document.getElementById('run-btn');
const transcriptHint = document.getElementById('transcript-hint');
const progressCard = document.getElementById('progress-card');
const progressList = document.getElementById('progress-list');
const resultsCard = document.getElementById('results-card');
const scoreBar = document.getElementById('score-bar');
const groundTruthText = document.getElementById('ground-truth-text');
const generatedText = document.getElementById('generated-text');
const medicalDiffs = document.getElementById('medical-diffs');
const summaryDiff = document.getElementById('summary-diff');
const medicationDiff = document.getElementById('medication-diff');
const regressionSection = document.getElementById('regression-section');
const regressionDiff = document.getElementById('regression-diff');
const errorsBox = document.getElementById('errors-box');
const pdfBtn = document.getElementById('pdf-btn');
const excelBtn = document.getElementById('excel-btn');
const pastResultsBody = document.getElementById('past-results-body');

document.getElementById('tab-run').addEventListener('click', () => showTab('run'));
document.getElementById('tab-past').addEventListener('click', () => showTab('past'));
languageSelect.addEventListener('change', populateAudioFiles);
runBtn.addEventListener('click', runTest);
pdfBtn.addEventListener('click', () => downloadReport('pdf'));
excelBtn.addEventListener('click', () => downloadReport('excel'));

function showTab(name) {
  document.getElementById('panel-run').style.display = name === 'run' ? '' : 'none';
  document.getElementById('panel-past').style.display = name === 'past' ? '' : 'none';
  document.getElementById('tab-run').classList.toggle('active', name === 'run');
  document.getElementById('tab-past').classList.toggle('active', name === 'past');
  if (name === 'past') loadPastResults();
}

async function loadDriveFiles() {
  try {
    const resp = await fetch(`${API}/drive-files`);
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    driveFiles = data.files || [];
    languageSelect.innerHTML = (data.languages || [])
      .map(l => `<option value="${esc(l)}">${esc(l)}</option>`)
      .join('');
    if (!data.languages?.length) {
      languageSelect.innerHTML = '<option value="">No languages found</option>';
    }
    populateAudioFiles();
  } catch (err) {
    languageSelect.innerHTML = '<option value="">Failed to load</option>';
    transcriptHint.textContent = `Drive error: ${err.message}`;
    transcriptHint.style.color = 'var(--red)';
  }
}

function populateAudioFiles() {
  const lang = languageSelect.value;
  const files = driveFiles.filter(f => f.language === lang);
  audioSelect.innerHTML = files.length
    ? files.map(f => `<option value="${esc(f.audio)}">${esc(f.audio)}${f.has_transcript ? '' : ' (no transcript)'}</option>`).join('')
    : '<option value="">No audio files</option>';

  const selected = files.find(f => f.audio === audioSelect.value) || files[0];
  if (selected) {
    audioSelect.value = selected.audio;
    transcriptHint.textContent = selected.has_transcript
      ? 'Ground truth transcript available'
      : 'No ground truth — accuracy scoring will be skipped';
    transcriptHint.style.color = selected.has_transcript ? 'var(--green)' : 'var(--yellow)';
  } else {
    transcriptHint.textContent = '';
  }
}

audioSelect.addEventListener('change', () => {
  const lang = languageSelect.value;
  const file = driveFiles.find(f => f.language === lang && f.audio === audioSelect.value);
  if (file) {
    transcriptHint.textContent = file.has_transcript
      ? 'Ground truth transcript available'
      : 'No ground truth — accuracy scoring will be skipped';
    transcriptHint.style.color = file.has_transcript ? 'var(--green)' : 'var(--yellow)';
  }
});

async function runTest() {
  const language = languageSelect.value;
  const audio_filename = audioSelect.value;
  const ai_model = aiModelSelect.value;

  if (!language || !audio_filename) {
    alert('Select language and audio file.');
    return;
  }

  runBtn.disabled = true;
  resultsCard.style.display = 'none';
  progressCard.style.display = '';
  progressList.innerHTML = DEFAULT_STEPS.map(s =>
    `<li class="progress-item pending" data-step="${esc(s)}"><span class="pending-dot">○</span> ${esc(s)}</li>`
  ).join('');
  pdfBtn.disabled = true;
  excelBtn.disabled = true;
  currentTestId = null;

  if (pollTimer) clearInterval(pollTimer);

  try {
    const resp = await fetch(`${API}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ language, audio_filename, ai_model }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);

    currentTestId = data.test_id;
    pollTimer = setInterval(() => pollResult(data.test_id), 3000);
    pollResult(data.test_id);
  } catch (err) {
    runBtn.disabled = false;
    progressCard.style.display = 'none';
    alert(`Failed to start test: ${err.message}`);
  }
}

const DEFAULT_STEPS = [
  'Fetching audio from Drive',
  'Submitting to MedSum API',
  'Waiting for transcription',
  'Running AI comparison',
];

async function pollResult(testId) {
  try {
    const resp = await fetch(`${API}/results/${testId}`);
    if (!resp.ok) return;
    const result = await resp.json();

    renderProgress(result.progress_steps || []);
    if (result.status === 'running') return;

    clearInterval(pollTimer);
    pollTimer = null;
    runBtn.disabled = false;
    renderResults(result);
  } catch {
    /* keep polling */
  }
}

function renderProgress(steps) {
  progressList.querySelectorAll('.progress-item').forEach(item => {
    const stepName = item.dataset.step;
    const match = steps.find(s => s.step === stepName);
    const status = match ? match.status : 'pending';
    item.className = `progress-item ${status}`;
    const icon = status === 'done' ? '<span class="check">✓</span>'
      : status === 'active' ? '<span class="spinner"></span>'
      : status === 'failed' ? '<span style="color:var(--red)">✗</span>'
      : '<span class="pending-dot">○</span>';
    item.innerHTML = `${icon} ${esc(stepName)}`;
  });
}

function renderResults(result) {
  resultsCard.style.display = '';
  currentTestId = result.test_id;
  pdfBtn.disabled = false;
  excelBtn.disabled = false;

  const tc = result.transcription_comparison || {};
  const score = result.accuracy_skipped
    ? 'Skipped'
    : (tc.similarity_score != null ? `${Math.round(tc.similarity_score)}/100` : 'N/A');
  const severity = tc.severity || 'low';

  scoreBar.innerHTML = `
    <span>Accuracy Score: <span class="score-value">${esc(String(score))}</span></span>
    <span class="severity ${esc(severity)}">Severity: ${esc(severity)}</span>
    <span class="result-badge ${esc(result.final_result || '')}">${esc(formatResult(result.final_result))}</span>
    ${result.retry_count ? `<span style="color:var(--muted);font-size:12px;">Retries: ${result.retry_count}</span>` : ''}
  `;

  groundTruthText.textContent = result.ground_truth_transcription || '(none)';
  generatedText.innerHTML = highlightDiff(
    result.ground_truth_transcription || '',
    result.generated_transcription || '',
    tc.medical_differences || [],
    tc.general_differences || []
  );

  const medItems = [
    ...(tc.medical_differences || []),
    ...(result.medication_comparison?.medical_differences || []),
  ];
  medicalDiffs.innerHTML = medItems.length
    ? medItems.map(d => `<li>${esc(d)}</li>`).join('')
    : '<li style="border-left-color:var(--border);background:var(--bg);color:var(--muted);">No medical differences flagged</li>';

  summaryDiff.textContent = formatComparison(result.summary_comparison);
  medicationDiff.textContent = formatMedComparison(result.medication_comparison);

  if (result.regression_comparison && !result.regression_comparison.skipped) {
    regressionSection.style.display = '';
    regressionDiff.textContent = formatComparison(result.regression_comparison);
  } else {
    regressionSection.style.display = 'none';
  }

  if (result.errors?.length) {
    errorsBox.style.display = '';
    errorsBox.textContent = result.errors.join('\n\n');
  } else {
    errorsBox.style.display = 'none';
  }
}

function formatComparison(comp) {
  if (!comp) return 'N/A';
  if (comp.skipped) return comp.skip_reason || 'Skipped';
  const lines = [
    comp.summary || '',
    ...(comp.medical_differences || []).map(d => `[Medical] ${d}`),
    ...(comp.general_differences || []).map(d => `[General] ${d}`),
    comp.similarity_score != null ? `Similarity: ${comp.similarity_score}/100` : '',
    `Severity: ${comp.severity || 'low'}`,
  ].filter(Boolean);
  return lines.join('\n');
}

function formatMedComparison(comp) {
  if (!comp) return 'N/A';
  if (comp.skipped) return comp.skip_reason || 'Skipped';
  const lines = [
    comp.summary || '',
    ...(comp.added || []).map(d => `[Added] ${d}`),
    ...(comp.removed || []).map(d => `[Removed] ${d}`),
    ...(comp.changed || []).map(d => `[Changed] ${d}`),
    ...(comp.medical_differences || []).map(d => `[Medical] ${d}`),
  ].filter(Boolean);
  return lines.join('\n') || 'No medication differences';
}

function highlightDiff(ground, generated, medicalDiffs, generalDiffs) {
  let html = esc(generated || '(empty)');
  const terms = new Set();
  [...medicalDiffs, ...generalDiffs].forEach(d => {
    const matches = d.match(/[\w\u0900-\u097F]+(?:\s*[\d.]+\s*mg)?/gi);
    if (matches) matches.forEach(m => { if (m.length > 2) terms.add(m); });
  });
  terms.forEach(term => {
    const re = new RegExp(`(${term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    html = html.replace(re, '<span class="med-highlight">$1</span>');
  });
  return html;
}

async function loadPastResults() {
  pastResultsBody.innerHTML = '<tr><td colspan="7" class="empty-row">Loading…</td></tr>';
  try {
    const resp = await fetch(`${API}/results`);
    const items = await resp.json();
    if (!items.length) {
      pastResultsBody.innerHTML = '<tr><td colspan="7" class="empty-row">No past results</td></tr>';
      return;
    }
    pastResultsBody.innerHTML = items.map(item => `
      <tr>
        <td class="mono" style="font-size:11px;">${esc(item.id.slice(0, 8))}…</td>
        <td>${esc(item.language || '')}</td>
        <td>${esc(item.filename || '')}</td>
        <td style="font-size:12px;color:var(--muted);">${esc(formatTimestamp(item.timestamp))}</td>
        <td><span class="result-badge ${esc(item.final_result || '')}">${esc(formatResult(item.final_result))}</span></td>
        <td>${item.accuracy_score != null ? Math.round(item.accuracy_score) : '—'}</td>
        <td><button class="view-link" data-id="${esc(item.id)}" type="button">View</button></td>
      </tr>
    `).join('');

    pastResultsBody.querySelectorAll('.view-link').forEach(btn => {
      btn.addEventListener('click', () => loadPastResult(btn.dataset.id));
    });
  } catch (err) {
    pastResultsBody.innerHTML = `<tr><td colspan="7" class="empty-row">Error: ${esc(err.message)}</td></tr>`;
  }
}

async function loadPastResult(testId) {
  showTab('run');
  try {
    const resp = await fetch(`${API}/results/${testId}`);
    const result = await resp.json();
    progressCard.style.display = 'none';
    renderResults(result);
    renderProgress(result.progress_steps || DEFAULT_STEPS.map(s => ({ step: s, status: 'done' })));
  } catch (err) {
    alert(`Failed to load result: ${err.message}`);
  }
}

function downloadReport(format) {
  if (!currentTestId) return;
  window.location.href = `${API}/report/${currentTestId}?format=${format}`;
}

function formatResult(val) {
  const map = {
    pass: 'Pass',
    fail: 'Fail',
    review: 'Review',
    complete_no_accuracy: 'Complete (no GT)',
    failed: 'Failed',
    pending: 'Pending',
  };
  return map[val] || val || '—';
}

function formatTimestamp(ts) {
  if (!ts) return '—';
  try { return new Date(ts).toLocaleString(); } catch { return ts; }
}

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

loadDriveFiles();
loadScheduleState();

// ── Schedule panel ───────────────────────────────────────────────────────────

const scheduleCron = document.getElementById('schedule-cron');
const scheduleCronCustom = document.getElementById('schedule-cron-custom');
const customCronField = document.getElementById('custom-cron-field');
const scheduleModel = document.getElementById('schedule-model');
const scheduleEnabled = document.getElementById('schedule-enabled');
const saveScheduleBtn = document.getElementById('save-schedule-btn');
const runAllBtn = document.getElementById('run-all-btn');
const scheduleInfo = document.getElementById('schedule-info');
const scheduleStatusBadge = document.getElementById('schedule-status-badge');
const lastRunPanel = document.getElementById('last-run-panel');
const lastRunList = document.getElementById('last-run-list');
const toast = document.getElementById('toast');

if (scheduleCron) {
  scheduleCron.addEventListener('change', () => {
    customCronField.style.display = scheduleCron.value === 'custom' ? '' : 'none';
  });
  saveScheduleBtn.addEventListener('click', saveSchedule);
  runAllBtn.addEventListener('click', runAllNow);
}

function showToast(msg) {
  toast.textContent = msg;
  toast.style.display = '';
  setTimeout(() => { toast.style.display = 'none'; }, 3000);
}

function getSelectedCron() {
  if (scheduleCron.value === 'custom') {
    return scheduleCronCustom.value.trim() || '0 2 * * *';
  }
  return scheduleCron.value;
}

async function loadScheduleState() {
  if (!scheduleInfo) return;
  try {
    const res = await fetch(`${API}/schedule`);
    const data = await res.json();
    renderSchedulePanel(data);
  } catch (err) {
    scheduleInfo.textContent = `Failed to load schedule: ${err.message}`;
  }
}

function renderSchedulePanel(data) {
  scheduleEnabled.checked = !!data.enabled;
  scheduleModel.value = data.ai_model || 'deepseek';

  const cron = data.cron || '0 2 * * *';
  const known = [...scheduleCron.options].some(o => o.value === cron);
  if (known) {
    scheduleCron.value = cron;
    customCronField.style.display = 'none';
  } else {
    scheduleCron.value = 'custom';
    scheduleCronCustom.value = cron;
    customCronField.style.display = '';
  }

  scheduleStatusBadge.textContent = data.enabled ? 'ACTIVE' : 'INACTIVE';
  scheduleStatusBadge.className = `schedule-status ${data.enabled ? 'active' : 'inactive'}`;

  const nextRun = data.next_run ? formatTimestamp(data.next_run) : '—';
  const lastRun = data.last_run ? formatTimestamp(data.last_run) : '—';
  scheduleInfo.innerHTML = `
    Status: <strong>${data.enabled ? 'Active' : 'Disabled'}</strong>
    &nbsp;|&nbsp; Schedule: <strong>${esc(data.cron_human || cron)}</strong>
    &nbsp;|&nbsp; Next run: <strong>${esc(nextRun)}</strong>
    &nbsp;|&nbsp; Last run: <strong>${esc(lastRun)}</strong>
  `;

  if (data.last_run_status && data.last_run_status.length) {
    lastRunPanel.style.display = '';
    lastRunList.innerHTML = data.last_run_status.map(item => {
      const cls = item.status === 'failed' ? 'failed' : item.status === 'skipped' ? 'skipped' : '';
      const icon = item.status === 'complete' ? '✓' : item.status === 'skipped' ? '○' : '✗';
      return `<li class="${cls}">${icon} ${esc(item.audio)} — ${esc(item.status)}${
        item.error ? `<span class="run-error">${esc(item.error)}</span>` : ''
      }${item.reason ? `<span class="run-error">${esc(item.reason)}</span>` : ''}</li>`;
    }).join('');
  } else {
    lastRunPanel.style.display = 'none';
  }
}

async function saveSchedule() {
  const payload = {
    enabled: scheduleEnabled.checked,
    cron: getSelectedCron(),
    ai_model: scheduleModel.value,
  };
  const res = await fetch(`${API}/schedule`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json();
    showToast(`Save failed: ${err.error || res.status}`);
    return;
  }
  showToast('Schedule saved');
  loadScheduleState();
}

async function runAllNow() {
  const res = await fetch(`${API}/schedule/run-now`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ai_model: scheduleModel.value }),
  });
  const data = await res.json();
  if (!res.ok) {
    showToast(`Run failed: ${data.error || res.status}`);
    return;
  }
  showToast(`Running ${data.test_count} tests — check Results panel`);
  setTimeout(loadScheduleState, 5000);
}
