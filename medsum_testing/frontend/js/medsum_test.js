const API = '/api/medsum-test';

let currentBatchId = null;
let currentTestId = null;
let batchPollInterval = null;
let accuracyChart = null;
let distributionChart = null;
let driveStats = { total: 0, with_transcript: 0 };
let latestResults = [];
let currentDetailResult = null;
let activeSoapTab = 'subjective';

const STAT_CARDS = [
  { id: 'stat-input', icon: '📤', label: 'Input Upload', sublabel: 'Requests Sent', key: 'total' },
  { id: 'stat-gt', icon: '📋', label: 'Ground Truth Comparison', sublabel: 'Compared', key: 'with_transcript' },
  { id: 'stat-output', icon: '✅', label: 'Output Validation', sublabel: 'Passed', key: 'passed' },
  { id: 'stat-accuracy', icon: '🎯', label: 'Accuracy', sublabel: 'Overall Accuracy', key: 'avg_accuracy', suffix: '%' },
  { id: 'stat-time', icon: '⏱', label: 'Time Taken', sublabel: 'Total Time', key: 'total_time' },
  { id: 'stat-reports', icon: '📄', label: 'Basic Report', sublabel: 'Generated', key: 'report_count' },
];

function initPage() {
  renderStatCards({});
  loadDriveStats();
  loadRecentResults();
  bindEvents();
}

function bindEvents() {
  document.getElementById('run-all-btn').addEventListener('click', runAllTests);
  document.getElementById('back-btn').addEventListener('click', showDashboard);
  document.getElementById('pdf-btn').addEventListener('click', () => downloadReport('pdf'));
  document.getElementById('excel-btn').addEventListener('click', () => downloadReport('excel'));

  document.querySelectorAll('.soap-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      activeSoapTab = btn.dataset.tab;
      document.querySelectorAll('.soap-tab').forEach(b => b.classList.toggle('active', b === btn));
      if (currentDetailResult) renderSOAPTabs(currentDetailResult.transcription_result);
    });
  });

  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', e => {
      e.preventDefault();
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      item.classList.add('active');
      if (item.dataset.nav !== 'dashboard') showDashboard();
    });
  });
}

async function loadDriveStats() {
  try {
    const resp = await fetch(`${API}/drive-files`);
    if (!resp.ok) return;
    const data = await resp.json();
    const files = data.files || [];
    driveStats.total = files.length;
    driveStats.with_transcript = files.filter(f => f.has_transcript).length;
    renderStatCards({ total: driveStats.total, with_transcript: driveStats.with_transcript });
  } catch (err) {
    console.warn('Drive stats failed:', err);
  }
}

async function loadRecentResults() {
  try {
    const resp = await fetch(`${API}/results`);
    if (!resp.ok) return;
    const items = await resp.json();
    latestResults = items;
    renderTestRunsTable(items.map(normalizeResultSummary));
    renderStatCards(computeStatsFromResults(items));
  } catch (err) {
    document.getElementById('test-runs-tbody').innerHTML =
      `<tr><td colspan="6" class="empty-row">Error: ${esc(err.message)}</td></tr>`;
  }
}

function normalizeResultSummary(r) {
  return {
    test_id: r.test_id || r.id,
    audio_filename: r.audio_filename || r.filename,
    language: r.language,
    status: r.status || 'complete',
    final_result: r.final_result,
    comparison: r.accuracy_score != null
      ? { similarity_score: r.accuracy_score }
      : (r.transcription_comparison || r.comparison),
    transcription_result: r.transcription_result,
  };
}

function computeStatsFromResults(results) {
  const completed = results.filter(r => r.status === 'complete' || r.final_result);
  const scores = completed
    .map(r => r.accuracy_score ?? r.transcription_comparison?.similarity_score)
    .filter(s => s != null);
  const passed = completed.filter(r => r.final_result === 'pass').length;
  return {
    total: driveStats.total || results.length,
    with_transcript: driveStats.with_transcript,
    passed,
    avg_accuracy: scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length * 10) / 10 : 0,
    total_time: '--',
    report_count: completed.length,
  };
}

function renderStatCards(data) {
  const grid = document.getElementById('stat-grid');
  const stats = {
    total: data.total ?? driveStats.total ?? '--',
    with_transcript: data.with_transcript ?? driveStats.with_transcript ?? '--',
    passed: data.passed ?? '--',
    avg_accuracy: data.avg_accuracy ?? '--',
    total_time: data.total_time ?? '--',
    report_count: data.report_count ?? '--',
  };

  grid.innerHTML = STAT_CARDS.map(card => {
    const val = stats[card.key] ?? '--';
    const display = val === '--' ? '--' : `${val}${card.suffix || ''}`;
    return `
      <div class="stat-card" id="${card.id}">
        <div class="stat-icon">${card.icon}</div>
        <div>
          <div class="stat-value">${esc(String(display))}</div>
          <div class="stat-label">${esc(card.label)}</div>
          <div class="stat-sublabel">${esc(card.sublabel)}</div>
        </div>
      </div>`;
  }).join('');
}

async function runAllTests() {
  const btn = document.getElementById('run-all-btn');
  const model = document.getElementById('ai-model-select').value;

  btn.disabled = true;
  btn.textContent = '⏳ Running...';

  try {
    const resp = await fetch(`${API}/run-all`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ai_model: model }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);

    showToast(`Started ${data.total} tests — batch ${data.batch_id.slice(0, 8)}…`);
    currentBatchId = data.batch_id;
    document.getElementById('batch-status').textContent =
      `Batch running: 0 / ${data.total} complete`;

    if (batchPollInterval) clearInterval(batchPollInterval);
    batchPollInterval = setInterval(() => pollBatch(data.batch_id), 5000);
    pollBatch(data.batch_id);
  } catch (err) {
    showToast(`Run failed: ${err.message}`);
    btn.disabled = false;
    btn.textContent = '▶ Run All Tests';
  }
}

async function pollBatch(batchId) {
  try {
    const resp = await fetch(`${API}/results/batch/${batchId}`);
    if (!resp.ok) return;
    const data = await resp.json();

    renderStatCards({
      total: data.total,
      with_transcript: driveStats.with_transcript,
      passed: data.passed,
      avg_accuracy: data.avg_accuracy,
      report_count: data.completed,
    });
    renderTestRunsTable(data.results);
    renderAccuracyChart(data.results);
    renderDistributionChart(data.results);

    document.getElementById('batch-status').textContent =
      `Batch: ${data.completed} complete, ${data.failed} failed, ${data.pending} pending`;

    if (data.pending === 0) {
      clearInterval(batchPollInterval);
      batchPollInterval = null;
      const btn = document.getElementById('run-all-btn');
      btn.disabled = false;
      btn.textContent = '▶ Run All Tests';
      showToast(`All ${data.total} tests complete — avg accuracy: ${data.avg_accuracy}%`);
    }
  } catch (err) {
    console.warn('Batch poll failed:', err);
  }
}

function renderTestRunsTable(results) {
  const tbody = document.getElementById('test-runs-tbody');
  if (!results || !results.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-row">No test runs yet</td></tr>';
    return;
  }

  tbody.innerHTML = results.slice(0, 30).map(r => {
    const comp = r.transcription_comparison || r.comparison || {};
    const score = comp.similarity_score ?? r.accuracy_score;
    const accuracy = score != null ? `${Math.round(score)}%` : '--';
    const accuracyClass = score >= 95 ? 'high' : score >= 80 ? 'med' :
      score >= 60 ? 'warn' : score != null ? 'low' : '';
    const statusBadge = r.status === 'complete' ? '✅ Completed' :
      r.status === 'failed' ? '❌ Failed' :
      r.status === 'running' ? '⏳ Running' : '⏸ Pending';
    const time = formatDuration(r.transcription_result?.['total-time']);

    return `<tr onclick="openTestDetail('${esc(r.test_id)}')" style="cursor:pointer">
      <td>${esc((r.test_id || '').slice(0, 8))}…</td>
      <td>${esc(r.audio_filename || '--')}</td>
      <td>${esc(r.language || '--')}</td>
      <td><span class="accuracy-badge ${accuracyClass}">${esc(accuracy)}</span></td>
      <td>${esc(time)}</td>
      <td>${statusBadge}</td>
    </tr>`;
  }).join('');
}

function renderAccuracyChart(results) {
  const canvas = document.getElementById('accuracy-chart');
  if (!canvas) return;

  const completed = (results || [])
    .filter(r => r.status === 'complete')
    .map(r => ({
      name: r.audio_filename,
      score: (r.transcription_comparison || r.comparison || {}).similarity_score
        ?? r.accuracy_score,
    }))
    .filter(r => r.score != null)
    .slice(-10);

  if (accuracyChart) accuracyChart.destroy();
  if (!completed.length) return;

  accuracyChart = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: completed.map(r => (r.name || '').slice(0, 12)),
      datasets: [{
        data: completed.map(r => r.score),
        borderColor: '#6C5CE7',
        backgroundColor: 'rgba(108,92,231,0.1)',
        tension: 0.4,
        pointBackgroundColor: '#6C5CE7',
        fill: true,
      }],
    },
    options: {
      responsive: true,
      scales: { y: { min: 0, max: 100 } },
      plugins: { legend: { display: false } },
    },
  });
}

function renderDistributionChart(results) {
  const canvas = document.getElementById('distribution-chart');
  const legend = document.getElementById('distribution-legend');
  if (!canvas) return;

  const scores = (results || [])
    .map(r => (r.transcription_comparison || r.comparison || {}).similarity_score ?? r.accuracy_score)
    .filter(s => s != null);

  const high = scores.filter(s => s >= 95).length;
  const med = scores.filter(s => s >= 80 && s < 95).length;
  const warn = scores.filter(s => s >= 60 && s < 80).length;
  const low = scores.filter(s => s < 60).length;
  const total = scores.length || 1;

  if (distributionChart) distributionChart.destroy();

  if (!scores.length) {
    legend.innerHTML = '<span class="legend-item">No completed scores yet</span>';
    return;
  }

  distributionChart = new Chart(canvas.getContext('2d'), {
    type: 'doughnut',
    data: {
      labels: ['>95%', '80-95%', '60-80%', '<60%'],
      datasets: [{
        data: [high, med, warn, low],
        backgroundColor: ['#00B894', '#6C5CE7', '#FDCB6E', '#E17055'],
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
    },
  });

  const colors = ['#00B894', '#6C5CE7', '#FDCB6E', '#E17055'];
  const counts = [high, med, warn, low];
  const labels = ['>95%', '80-95%', '60-80%', '<60%'];
  legend.innerHTML = labels.map((label, i) => {
    const pct = Math.round(counts[i] / total * 100);
    return `<div class="legend-item">
      <span class="legend-dot" style="background:${colors[i]}"></span>
      ${label} — ${counts[i]} (${pct}%)
    </div>`;
  }).join('');
}

async function openTestDetail(testId) {
  try {
    const resp = await fetch(`${API}/results/${testId}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    currentTestId = testId;
    currentDetailResult = data;
    renderDetailPage(data);
  } catch (err) {
    showToast(`Failed to load: ${err.message}`);
  }
}

function renderDetailPage(result) {
  document.getElementById('dashboard-view').style.display = 'none';
  document.getElementById('detail-view').style.display = 'block';

  const score = (result.transcription_comparison || {}).similarity_score;
  document.getElementById('detail-title').textContent =
    `Test ${result.test_id?.slice(0, 8)}… — ${result.audio_filename || ''}`;
  document.getElementById('detail-meta').textContent =
    `${result.language || ''} · ${result.status || ''} · ` +
    `Accuracy: ${score != null ? Math.round(score) + '%' : 'N/A'} · ` +
    `Duration: ${formatDuration(result.transcription_result?.['total-time'])}`;

  document.getElementById('ground-truth-text').textContent =
    result.ground_truth_transcription || '(none)';
  document.getElementById('generated-text').textContent =
    result.generated_transcription || '(empty)';

  renderMedicalDifferences(result.transcription_comparison);
  renderMedicationValidation(result.medication_validation);
  renderSOAPTabs(result.transcription_result);

  const errorsSection = document.getElementById('errors-section');
  if (result.errors?.length) {
    errorsSection.style.display = '';
    document.getElementById('errors-box').textContent = result.errors.join('\n\n');
  } else {
    errorsSection.style.display = 'none';
  }
}

function showDashboard() {
  document.getElementById('dashboard-view').style.display = '';
  document.getElementById('detail-view').style.display = 'none';
  currentTestId = null;
  currentDetailResult = null;
}

function renderMedicalDifferences(comp) {
  const el = document.getElementById('medical-diffs');
  const items = [
    ...(comp?.medical_differences || []),
    ...(comp?.general_differences || []),
  ];
  el.innerHTML = items.length
    ? items.map(d => `<li>${esc(d)}</li>`).join('')
    : '<li>No medical differences flagged</li>';
}

function renderMedicationValidation(medVal) {
  const el = document.getElementById('med-validation');
  if (!medVal) {
    el.innerHTML = '<p style="color:var(--text-secondary)">No medication validation data</p>';
    return;
  }

  const diffRows = (medVal.differences || []).map(d => `
    <div class="med-diff ${d.severity}">
      <span class="severity-badge ${d.severity}">${esc((d.severity || '').toUpperCase())}</span>
      ${esc(d.detail || '')}
    </div>
  `).join('');

  const finalMeds = medVal.final_medications || [];
  const rawMeds = medVal.raw_medications || [];
  const tableRows = finalMeds.map((med, i) => {
    const raw = rawMeds[i] || {};
    return `
      <tr>
        <td>${esc(med.drug_name || '--')}</td>
        <td class="${raw.dose !== med.dose ? 'changed' : ''}">${esc(String(raw.dose ?? 'NA'))}</td>
        <td class="${raw.dose !== med.dose ? 'changed' : ''}">${esc(String(med.dose ?? 'NA'))}</td>
        <td class="${raw.schedule !== med.schedule ? 'changed' : ''}">${esc(String(raw.schedule ?? 'NA'))}</td>
        <td class="${raw.schedule !== med.schedule ? 'changed' : ''}">${esc(String(med.schedule ?? 'NA'))}</td>
      </tr>`;
  }).join('');

  el.innerHTML = `
    <h3>Medication Validation
      ${medVal.has_critical_differences
        ? '<span class="badge danger">⚠ Critical Differences</span>'
        : '<span class="badge success">✓ OK</span>'}
    </h3>
    <table class="med-table">
      <thead>
        <tr>
          <th>Drug</th>
          <th>Raw Dose</th>
          <th>Final Dose</th>
          <th>Raw Schedule</th>
          <th>Final Schedule</th>
        </tr>
      </thead>
      <tbody>${tableRows || '<tr><td colspan="5">No medications</td></tr>'}</tbody>
    </table>
    <div class="med-differences">${diffRows || '<p>No differences</p>'}</div>
  `;
}

function renderSOAPTabs(tr) {
  const el = document.getElementById('soap-content');
  if (!tr) {
    el.textContent = '(no SOAP data)';
    return;
  }

  const section = tr[activeSoapTab];
  if (typeof section === 'string') {
    el.textContent = section || '(empty)';
  } else if (section && typeof section === 'object') {
    el.textContent = JSON.stringify(section, null, 2);
  } else if (activeSoapTab === 'summary') {
    el.textContent = tr.summary || tr.medical_summary || '(empty)';
  } else {
    el.textContent = '(empty)';
  }
}

function downloadReport(format) {
  if (!currentTestId) return;
  window.location.href = `${API}/report/${currentTestId}?format=${format}`;
}

function formatDuration(seconds) {
  if (seconds == null || seconds === '') return '--';
  const s = Number(seconds);
  if (Number.isNaN(s)) return '--';
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  return `${m}m ${rem}s`;
}

function showToast(msg) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.style.display = '';
  setTimeout(() => { toast.style.display = 'none'; }, 4000);
}

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

window.openTestDetail = openTestDetail;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initPage);
} else {
  initPage();
}
