const API = '/api/medsum-test';

let currentBatchId = null;
let currentTestId = null;
let batchPollInterval = null;
let accuracyChart = null;
let distributionChart = null;
let driveStats = { total: 0, with_transcript: 0 };
let latestResults = [];
let currentDetailResult = null;
let ltRows = [];           // [{phone, password, patientId}]
let ltRunResults = [];     // timing_data objects per sessi on
let ltRunId = null;
let ltMode = 'drive';      // 'drive' | 'manual'
let ltPerRowFiles = {};    // { rowIndex: { audio: File|null, gt: File|null } }
let ltBulkFiles = {};      // { patientId: { audio: File|null, gt: File|null } }
let ltServiceEmail = '';

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
  checkAIConfig();
  bindEvents();
  ltAddRow();           // start with one blank row
  ltLoadServiceEmail();
}

/**
 * Extract the AI comparison summary string. Never use difference lists/objects.
 */
function aiSummary(compOrText) {
  if (compOrText == null || compOrText === '') return '';
  if (typeof compOrText === 'string') return compOrText;
  if (Array.isArray(compOrText)) return '';
  if (typeof compOrText === 'object') {
    if (typeof compOrText.summary === 'string' && compOrText.summary.trim()) {
      return compOrText.summary;
    }
    if (typeof compOrText.error === 'string' && compOrText.error.trim()) {
      return compOrText.error;
    }
  }
  return '';
}

/**
 * Creates a score pill that shows the AI summary on click.
 * @param {number|null} score - 0-100 or null
 * @param {string|object} reason - AI summary string, or comparison object with .summary
 * @param {string} label - short label shown in pill
 * @param {string} id - unique id for this pill
 */
function scorePill(score, reason, label, id) {
  const safeLabel = esc(label || '');
  const safeId = String(id).replace(/[^a-zA-Z0-9_-]/g, '');

  if (score == null || score === '') {
    return `<span class="score-pill muted" title="${safeLabel}">—<span class="pill-label">${safeLabel}</span></span>`;
  }

  const n = Math.round(Number(score));
  if (Number.isNaN(n)) {
    return `<span class="score-pill muted" title="${safeLabel}">—<span class="pill-label">${safeLabel}</span></span>`;
  }

  const cls = n >= 90 ? 'high' : n >= 75 ? 'med' :
              n >= 60 ? 'warn' : 'low';

  const displayReason = esc(
    aiSummary(reason) || 'No reasoning available from AI comparison.'
  );

  return `
        <span class="score-pill-wrapper">
            <span class="score-pill ${cls}"
                  id="pill-${safeId}"
                  onclick="event.stopPropagation(); toggleReason('${safeId}')"
                  title="Click to see why">
                ${n}%
                <span class="pill-label">${safeLabel}</span>
            </span>
            <div class="reason-box" id="reason-${safeId}" style="display:none">
                <div class="reason-header">
                    <span>WHY ${n}%? — ${safeLabel}</span>
                    <button type="button" class="reason-close"
                            onclick="event.stopPropagation(); toggleReason('${safeId}')">✕</button>
                </div>
                <div class="reason-text">${displayReason}</div>
            </div>
        </span>`;
}

function toggleReason(id) {
  const box = document.getElementById(`reason-${id}`);
  if (!box) return;

  const isOpen = box.style.display !== 'none';

  document.querySelectorAll('.reason-box').forEach(b => {
    b.style.display = 'none';
  });

  if (!isOpen) {
    box.style.display = 'block';
    const rect = box.getBoundingClientRect();
    if (rect.right > window.innerWidth - 20) {
      box.classList.add('align-right');
    } else {
      box.classList.remove('align-right');
    }
  }
}

function makeCollapsible(id, title, contentHtml, {
  defaultOpen = false,
  score = null,
  scoreReason = null,
  scoreLabel = '',
  headerRight = '',
} = {}) {
  const arrow = defaultOpen ? '▼' : '▶';
  const scoreBadge = score != null
    ? scorePill(score, scoreReason, scoreLabel, `${id}-header`)
    : '';

  return `
        <div class="collapsible-section" id="section-${id}">
            <div class="collapsible-header" onclick="toggleSection('${id}')">
                <span class="collapsible-arrow" id="arrow-${id}">${arrow}</span>
                <span class="collapsible-title">${title}</span>
                <span onclick="event.stopPropagation()">${scoreBadge}</span>
                ${headerRight}
            </div>
            <div class="collapsible-body" id="body-${id}"
                 style="display:${defaultOpen ? 'block' : 'none'}">
                ${contentHtml}
            </div>
        </div>`;
}

function toggleSection(id) {
  const body = document.getElementById(`body-${id}`);
  const arrow = document.getElementById(`arrow-${id}`);
  if (!body) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  if (arrow) arrow.textContent = open ? '▶' : '▼';
}

function comparisonSummary(comp) {
  return aiSummary(comp);
}

function hasKeys(obj) {
  return !!obj && typeof obj === 'object' && Object.keys(obj).length > 0;
}

async function checkAIConfig() {
  try {
    const res = await fetch(`${API}/health`);
    if (!res.ok) return;
    const data = await res.json();
    const banner = document.getElementById('ai-warning-banner');
    if (data.ai_warning && banner) {
      banner.style.display = 'block';
      document.getElementById('ai-warning-text').textContent = data.ai_warning;
    }
  } catch (err) {
    console.warn('AI config check failed:', err);
  }
}

function bindEvents() {
  document.getElementById('run-all-btn').addEventListener('click', runAllTests);
  document.getElementById('back-btn').addEventListener('click', showDashboard);
  document.getElementById('pdf-btn').addEventListener('click', () => downloadReport('pdf'));
  document.getElementById('excel-btn').addEventListener('click', () => downloadReport('excel'));

  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', e => {
      e.preventDefault();
      document.querySelectorAll('.nav-item').forEach(n =>
        n.classList.remove('active'));
      item.classList.add('active');

      const nav = item.dataset.nav;
      if (nav === 'load-testing') {
        showLoadTesting();
      } else {
        // Hide load testing view if open
        const ltView = document.getElementById('load-testing-view');
        if (ltView) ltView.style.display = 'none';
        if (nav === 'dashboard') {
          showDashboard();
        } else {
          showDashboard();
        }
      }
    });
  });

  document.addEventListener('click', e => {
    if (!e.target.closest('.score-pill-wrapper') && !e.target.closest('.score-pill-wrap')) {
      document.querySelectorAll('.reason-box').forEach(b => {
        b.style.display = 'none';
      });
    }
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
  const comp = r.comparison || r.transcription_comparison || {};
  return {
    test_id: r.test_id || r.id,
    tc_ref: r.tc_ref,
    run_ref: r.run_ref,
    audio_filename: r.audio_filename || r.filename,
    language: r.language,
    status: r.status || 'complete',
    final_result: r.final_result,
    similarity_score: r.comparison?.similarity_score
      ?? r.similarity_score
      ?? r.accuracy_score
      ?? comp.similarity_score,
    comparison: { ...comp, error: comp.error, summary: comp.summary },
    comparison_error: comp.error,
    comparison_summary: comp.summary || '',
    total_test_time_seconds: r.total_test_time_seconds,
    transcription_result: r.transcription_result,
  };
}

function computeStatsFromResults(results) {
  const completed = results.filter(r => r.status === 'complete' || r.final_result);
  const scores = completed
    .map(r => r.comparison?.similarity_score ?? r.accuracy_score ?? r.transcription_comparison?.similarity_score)
    .filter(s => s != null);
  const passed = completed.filter(r => r.final_result === 'pass' || r.final_result === 'complete_no_accuracy').length;
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

    showToast(`Batch ${data.batch_ref || data.batch_id.slice(0, 8)} — ${data.total} tests started`);
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

  tbody.innerHTML = results.slice(0, 50).map(r => {
    const score = r.comparison?.similarity_score
      ?? r.similarity_score
      ?? r.transcription_comparison?.similarity_score
      ?? r.accuracy_score;
    const reason = aiSummary(r.comparison)
      || r.comparison_summary
      || aiSummary(r.transcription_comparison)
      || '';
    const pillId = `table-${String(r.test_id || '').replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 24)}`;

    const timeDisplay = r.total_test_time_seconds != null
      ? formatDuration(r.total_test_time_seconds)
      : '—';

    const statusBadge = r.status === 'complete' ? '✅ Completed' :
      r.status === 'failed' ? '❌ Failed' :
      r.status === 'running' ? '⏳ Running' : '⏸ Pending';

    const displayId = r.tc_ref
      || r.run_ref
      || `${(r.test_id || '').slice(0, 8)}…`;

    const langClass = (r.language || '').toLowerCase().replace(/\s+/g, '-');

    return `<tr onclick="openTestDetail('${esc(r.test_id)}')"
                style="cursor:pointer"
                title="${esc(r.run_ref || r.test_id || '')}">
      <td class="run-id-cell">
        <span class="tc-ref">${esc(displayId)}</span>
      </td>
      <td>${esc(r.audio_filename || '—')}</td>
      <td>
        <span class="lang-badge lang-${esc(langClass)}">
          ${esc(r.language || '—')}
        </span>
      </td>
      <td onclick="event.stopPropagation()">
        ${scorePill(score, reason, 'Accuracy', pillId)}
      </td>
      <td>${esc(timeDisplay)}</td>
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
      score: (r.comparison || r.transcription_comparison || {}).similarity_score
        ?? r.accuracy_score
        ?? r.similarity_score,
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
    .map(r => (r.comparison || r.transcription_comparison || {}).similarity_score ?? r.accuracy_score ?? r.similarity_score)
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

  document.getElementById('detail-tc-ref').textContent = result.tc_ref || '—';
  document.getElementById('detail-run-ref').textContent = result.run_ref || '—';
  document.getElementById('detail-title').textContent =
    `${result.audio_filename || ''} — ${result.language || ''}`;

  const duration = result.total_test_time_seconds != null
    ? formatDuration(result.total_test_time_seconds)
    : '—';
  const meta = document.getElementById('detail-meta');
  if (meta) {
    meta.textContent = `${result.status || result.final_result || ''} · Duration: ${duration}`;
  }

  renderAccuracySummary(result);

  const flaskError = result.flask_error || result.transcription_result?.error;
  const flaskErrorText = flaskError && typeof flaskError === 'object'
    ? JSON.stringify(flaskError)
    : flaskError;
  const errEl = document.getElementById('flask-error-banner');
  if (errEl) {
    if (flaskErrorText) {
      errEl.style.display = 'block';
      errEl.textContent = `⚠ LLM Error: ${flaskErrorText}`;
    } else {
      errEl.style.display = 'none';
      errEl.textContent = '';
    }
  }

  const sections = document.getElementById('detail-sections');
  if (sections) {
    sections.innerHTML = [
      renderTranscriptionComparison(result),
      renderTranslationComparison(result),
      renderSOAPComparison(result),
      renderPrescriptionComparison(result),
      renderMedicationValidation(result),
    ].filter(Boolean).join('');
  }

  const errorsSection = document.getElementById('errors-section');
  if (result.errors?.length) {
    errorsSection.style.display = '';
    document.getElementById('errors-box').textContent = result.errors.join('\n\n');
  } else if (errorsSection) {
    errorsSection.style.display = 'none';
  }
}

function renderAccuracySummary(result) {
  const container = document.getElementById('accuracy-summary');
  if (!container) return;

  const transComp = result.comparison || result.transcription_comparison || {};
  const transComp2 = result.translation_comparison || {};
  const soapComp = result.soap_comparison || {};
  const soapScores = soapComp.scores || {};
  const medVal = result.medication_validation || {};

  const soapGenScore = soapScores.gt_vs_generated ?? soapComp.gt_vs_generated?.similarity_score;
  const soapRawScore = soapScores.gt_vs_raw ?? soapComp.gt_vs_raw?.similarity_score;
  const soapDeltaScore = soapScores.raw_vs_generated ?? soapComp.raw_vs_generated?.similarity_score;

  const medCount = medVal.difference_count || 0;
  const medCls = medCount === 0 ? 'high' : (medVal.has_critical_differences ? 'low' : 'warn');
  const medLabel = medCount === 0
    ? '✓ Meds'
    : `${medCount} Med Diff${medCount !== 1 ? 's' : ''}`;

  container.innerHTML = `
        <div class="acc-bar-title">Accuracy Overview</div>
        <div class="acc-bar-chips">
            ${scorePill(
              transComp.similarity_score,
              transComp.summary,
              'Transcription',
              'acc-transcription'
            )}
            ${scorePill(
              transComp2.similarity_score,
              transComp2.summary,
              'Translation',
              'acc-translation'
            )}
            ${scorePill(
              soapGenScore,
              soapComp.gt_vs_generated?.summary,
              'SOAP GT→Gen',
              'acc-soap-gen'
            )}
            ${scorePill(
              soapRawScore,
              soapComp.gt_vs_raw?.summary,
              'SOAP GT→Raw',
              'acc-soap-raw'
            )}
            ${scorePill(
              soapDeltaScore,
              soapComp.raw_vs_generated?.summary,
              'SOAP Raw→Gen',
              'acc-soap-delta'
            )}
            <span class="score-pill ${medCls}"
                  onclick="toggleSection('medication')"
                  title="Click to see medication validation">
                ${esc(medLabel)}
            </span>
        </div>`;
}

function formatDiffItem(d, { showType = true } = {}) {
  if (d == null) return '';
  if (typeof d === 'string') {
    return `<div class="diff-item-plain">• ${esc(d)}</div>`;
  }
  const sev = d.severity || 'medium';
  const typeHtml = showType && d.type
    ? `<span class="diff-type">${esc(String(d.type).replace(/_/g, ' '))}</span>`
    : '';
  return `
            <div class="diff-item ${esc(sev)}">
                <span class="sev-badge ${esc(sev)}">${esc(sev)}</span>
                ${typeHtml}
                <span class="diff-gt">GT: ${esc(d.ground_truth || '—')}</span>
                <span class="arrow">→</span>
                <span class="diff-gen">Generated: ${esc(d.generated || '—')}</span>
            </div>`;
}

function renderTranscriptionComparison(result) {
  const gt = result.ground_truth || result.ground_truth_transcription || '';
  const gen = result.transcription || result.generated_transcription || '';
  if (!gt && !gen) return '';

  const comp = result.comparison || result.transcription_comparison || {};
  const score = comp.similarity_score;

  const { gtHtml, genHtml } = computeWordDiff(gt, gen);

  const medDiffs = (comp.medical_difference_details || []).length
    ? comp.medical_difference_details
    : (comp.medical_differences || []);
  const genDiffs = comp.general_differences || [];

  const medDiffHtml = medDiffs.length === 0 ? '' : `
        <div class="diff-section-label">Medical Differences</div>
        ${medDiffs.map(d => formatDiffItem(d)).join('')}`;

  const genDiffHtml = genDiffs.length === 0 ? '' : `
        <div class="diff-section-label" style="margin-top:0.75rem">General Differences</div>
        ${genDiffs.map(d => formatDiffItem(d, { showType: false })).join('')}`;

  const scoreHtml = score != null ? `
        <div class="section-score-row">
            ${scorePill(score, comp.summary, 'Transcription Accuracy', 'trans-score')}
        </div>` : '';

  const content = `
        ${scoreHtml}
        <div class="diff-legend-row">
            <span><span class="legend-swatch missing"></span> Missing from generated</span>
            <span><span class="legend-swatch wrong"></span> Not in ground truth</span>
        </div>
        <div class="diff-grid">
            <div class="diff-col">
                <div class="diff-col-header">Ground Truth</div>
                <div class="diff-text">${gtHtml || '<em>No ground truth</em>'}</div>
            </div>
            <div class="diff-col">
                <div class="diff-col-header">Generated</div>
                <div class="diff-text">${genHtml || '<em>No transcription</em>'}</div>
            </div>
        </div>
        ${medDiffHtml}
        ${genDiffHtml}`;

  return makeCollapsible('transcription', '📝 Transcription Comparison', content, {
    defaultOpen: false,
    score,
    scoreReason: comp.summary,
    scoreLabel: 'Transcription',
  });
}

function renderTranslationComparison(result) {
  const lang = (result.language || '').toLowerCase();
  const gtTrans = result.ground_truth_translation
    || result.translation_ground_truth
    || (lang === 'english' ? (result.ground_truth || result.ground_truth_transcription || '') : '');
  const genTrans = result.generated_translation
    || result.translation
    || result.text_translation
    || result.transcription_result?.debug?.translation
    || '';
  if (!gtTrans && !genTrans) return '';

  const comp = result.translation_comparison || {};
  const score = comp.similarity_score;

  const { gtHtml, genHtml } = computeWordDiff(gtTrans, genTrans);

  const diffs = comp.differences || comp.medical_differences || [];
  const diffsHtml = diffs.length === 0 ? '' : `
        <div class="diff-section-label" style="margin-top:0.75rem">Differences Found</div>
        ${diffs.map(d => formatDiffItem(d, { showType: false })).join('')}`;

  const scoreHtml = score != null ? `
        <div class="section-score-row">
            ${scorePill(score, comp.summary, 'Translation Accuracy', 'trans2-score')}
            ${lang === 'english'
              ? '<span class="note-text">Ground truth = _script file (English audio)</span>'
              : ''}
        </div>` : '';

  const content = `
        ${scoreHtml}
        <div class="diff-grid">
            <div class="diff-col">
                <div class="diff-col-header">Ground Truth Translation</div>
                <div class="diff-text">${gtHtml || '<em>No ground truth</em>'}</div>
            </div>
            <div class="diff-col">
                <div class="diff-col-header">Generated Translation</div>
                <div class="diff-text">${genHtml || '<em>No translation</em>'}</div>
            </div>
        </div>
        ${diffsHtml}`;

  return makeCollapsible('translation', '🌐 Translation Comparison', content, {
    defaultOpen: false,
    score,
    scoreReason: comp.summary,
    scoreLabel: 'Translation',
  });
}

function soapFieldTable(gtSection, genSection, sectionKey) {
  if (!gtSection && !genSection) return '<em>No data</em>';

  const gt = typeof gtSection === 'object' && gtSection && !Array.isArray(gtSection) ? gtSection : {};
  const gen = typeof genSection === 'object' && genSection && !Array.isArray(genSection) ? genSection : {};
  const allKeys = [...new Set([...Object.keys(gt), ...Object.keys(gen)])];
  const norm = v => String(v || '').toLowerCase().replace(/[.,\-–—\s]/g, '').trim();

  const rows = allKeys.map(field => {
    const gtVal = gt[field];
    const genVal = gen[field];

    if (field === 'medications' && (Array.isArray(gtVal) || Array.isArray(genVal))) {
      const gtMeds = Array.isArray(gtVal) ? gtVal : [];
      const genMeds = Array.isArray(genVal) ? genVal : [];
      const maxLen = Math.max(gtMeds.length, genMeds.length);
      if (maxLen === 0) return '';

      const medRows = Array.from({ length: maxLen }, (_, i) => {
        const gm = gtMeds[i] || {};
        const gn = genMeds[i] || {};
        const MED_FIELDS = ['drug_name', 'dose', 'schedule', 'duration', 'instructions'];
        return MED_FIELDS.map(mf => {
          const gv = String(gm[mf] || '—');
          const nv = String(gn[mf] || '—');
          const diff = norm(gv) !== norm(nv) && gv !== '—' && nv !== '—';
          const label = (i > 0 && mf === 'drug_name')
            ? `Drug ${i + 1}: ${mf.replace(/_/g, ' ')}`
            : mf.replace(/_/g, ' ');
          return `<tr class="${diff ? 'soap-diff-row' : ''}">
                        <td class="soap-field-name" style="padding-left:1.5rem">${esc(label)}</td>
                        <td class="${diff ? 'diff-cell' : ''}">${esc(gv)}</td>
                        <td class="${diff ? 'diff-cell diff-cell-gen' : ''}">${esc(nv)}</td>
                        <td class="diff-flag">${diff ? '⚠' : ''}</td>
                    </tr>`;
        }).join('');
      }).join('');

      return `<tr>
                <td class="soap-field-name" colspan="4"
                    style="background:var(--bg);font-weight:600;padding-top:0.5rem">
                    Medications (${maxLen})
                </td>
            </tr>${medRows}`;
    }

    const gtStr = gtVal != null && typeof gtVal !== 'object' ? String(gtVal) : (gtVal == null ? '—' : JSON.stringify(gtVal));
    const genStr = genVal != null && typeof genVal !== 'object' ? String(genVal) : (genVal == null ? '—' : JSON.stringify(genVal));
    const isDiff = norm(gtStr) !== norm(genStr)
      && gtStr !== 'NA' && genStr !== 'NA'
      && gtStr !== '—' && genStr !== '—';

    return `<tr class="${isDiff ? 'soap-diff-row' : ''}">
            <td class="soap-field-name">${esc(field.replace(/_/g, ' '))}</td>
            <td class="${isDiff ? 'diff-cell' : ''}">${esc(gtStr)}</td>
            <td class="${isDiff ? 'diff-cell diff-cell-gen' : ''}">${esc(genStr)}</td>
            <td class="diff-flag">${isDiff ? '⚠' : ''}</td>
        </tr>`;
  }).join('');

  return `<table class="soap-compare-table">
        <thead><tr>
            <th>Field</th>
            <th>Ground Truth</th>
            <th>Generated</th>
            <th></th>
        </tr></thead>
        <tbody>${rows}</tbody>
    </table>`;
}

function generatedSOAPFromResult(result) {
  if (hasKeys(result.soap_generated)) return result.soap_generated;
  const tr = result.transcription_result || {};
  if (tr.subjective || tr.objective || tr.assessment || tr.plan || tr.summary) {
    return {
      subjective: tr.subjective,
      objective: tr.objective,
      assessment: tr.assessment,
      plan: tr.plan,
      summary: tr.summary,
    };
  }
  return {};
}

function renderSOAPComparison(result) {
  const comp = result.soap_comparison || {};
  const scores = comp.scores || {};
  const gtSOAP = result.soap_ground_truth || {};
  const genSOAP = generatedSOAPFromResult(result);

  if (!hasKeys(gtSOAP) && !hasKeys(genSOAP) && !hasKeys(comp)) return '';

  const SECTIONS = ['subjective', 'objective', 'assessment', 'plan', 'summary'];

  const sectionsHtml = SECTIONS.map(sec => {
    const gtSec = gtSOAP[sec];
    const genSec = genSOAP[sec];
    if (!gtSec && !genSec) return '';

    let content;
    if (typeof gtSec === 'string' || typeof genSec === 'string') {
      const norm = v => String(v || '').toLowerCase().replace(/[.,\-–]/g, '').trim();
      const isDiff = norm(gtSec) !== norm(genSec);
      content = `
                <div class="diff-grid">
                    <div class="diff-col">
                        <div class="diff-col-header">Ground Truth</div>
                        <div class="diff-text ${isDiff ? 'diff-text-warn' : ''}">${esc(gtSec || '—')}</div>
                    </div>
                    <div class="diff-col">
                        <div class="diff-col-header">Generated</div>
                        <div class="diff-text ${isDiff ? 'diff-text-warn' : ''}">${esc(genSec || '—')}</div>
                    </div>
                </div>`;
    } else {
      content = soapFieldTable(gtSec, genSec, sec);
    }

    return makeCollapsible(
      `soap-sec-${sec}`,
      sec.charAt(0).toUpperCase() + sec.slice(1),
      content,
      { defaultOpen: false }
    );
  }).join('');

  const soapGenScore = scores.gt_vs_generated ?? comp.gt_vs_generated?.similarity_score;
  const soapRawScore = scores.gt_vs_raw ?? comp.gt_vs_raw?.similarity_score;
  const soapDeltaScore = scores.raw_vs_generated ?? comp.raw_vs_generated?.similarity_score;

  const scoreRow = `
        <div class="soap-three-scores">
            <div class="soap-score-item">
                <div class="soap-score-label">GT → Generated</div>
                ${scorePill(soapGenScore, comp.gt_vs_generated?.summary, 'GT vs Generated', 'soap-gt-gen')}
            </div>
            <div class="soap-score-item">
                <div class="soap-score-label">GT → Raw LLM</div>
                ${scorePill(soapRawScore, comp.gt_vs_raw?.summary, 'GT vs Raw', 'soap-gt-raw')}
            </div>
            <div class="soap-score-item">
                <div class="soap-score-label">Raw → Generated</div>
                ${scorePill(soapDeltaScore, comp.raw_vs_generated?.summary, 'Raw vs Generated', 'soap-raw-gen')}
            </div>
        </div>`;

  const content = scoreRow + sectionsHtml;

  return makeCollapsible('soap', '📋 SOAP Comparison', content, {
    defaultOpen: true,
    score: soapGenScore,
    scoreReason: comp.gt_vs_generated?.summary,
    scoreLabel: 'SOAP GT→Gen',
  });
}

function rxFieldText(val) {
  if (val == null || val === '') return '';
  if (Array.isArray(val)) {
    return val.map(v => (typeof v === 'object' && v ? (v.diagnosis || v.name || JSON.stringify(v)) : String(v))).join(', ');
  }
  if (typeof val === 'object') {
    return val.diagnosis || val.chief_complaint || val.name || '';
  }
  return String(val);
}

function renderPrescriptionComparison(result) {
  const gtSOAP = result.soap_ground_truth || {};
  const genSOAP = generatedSOAPFromResult(result);

  const gtComplaint = rxFieldText(gtSOAP.subjective?.chief_complaint);
  const genComplaint = rxFieldText(genSOAP.subjective?.chief_complaint);
  const gtDiagnosis = rxFieldText(gtSOAP.assessment?.diagnosis);
  const genDiagnosis = rxFieldText(genSOAP.assessment?.diagnosis);
  const gtMeds = gtSOAP.plan?.medications || [];
  const genMeds = genSOAP.plan?.medications || [];

  if (!gtComplaint && !genComplaint &&
      !gtDiagnosis && !genDiagnosis &&
      !gtMeds.length && !genMeds.length) {
    return '';
  }

  const norm = s => String(s || '')
    .toLowerCase()
    .replace(/[.,\-–—;:!?()[\]{}"“”‘’'`/\\]/g, '')
    .replace(/\s+/g, ' ')
    .trim();

  const rxRow = (label, gtVal, genVal) => {
    const gtStr = gtVal || '—';
    const genStr = genVal || '—';
    const isDiff = norm(gtStr) !== norm(genStr)
      && gtStr !== '—' && genStr !== '—';
    return {
      isDiff,
      html: `<tr class="${isDiff ? 'soap-diff-row' : ''}">
                <td class="med-field">${esc(label)}</td>
                <td class="med-gt-col">${esc(gtStr)}</td>
                <td class="${isDiff ? 'diff-cell-gen' : ''}">${esc(genStr)}</td>
            </tr>`,
    };
  };

  const complaint = rxRow('Chief complaint', gtComplaint, genComplaint);
  const diagnosis = rxRow('Diagnosis', gtDiagnosis, genDiagnosis);

  const summaryTable = `
            <table class="med-compare-table">
                <thead>
                    <tr>
                        <th>Field</th>
                        <th>Ground Truth</th>
                        <th>Generated</th>
                    </tr>
                </thead>
                <tbody>
                    ${complaint.html}
                    ${diagnosis.html}
                </tbody>
            </table>`;

  const MED_FIELDS = ['drug_name', 'dose', 'schedule', 'duration', 'instructions'];
  const maxMeds = Math.max(gtMeds.length, genMeds.length);

  let medDiffs = 0;
  const medsHtml = Array.from({ length: maxMeds }, (_, i) => {
    const gt = gtMeds[i] || {};
    const gen = genMeds[i] || {};
    const name = gt.drug_name || gen.drug_name || `Drug ${i + 1}`;

    let drugDiffs = 0;
    const rows = MED_FIELDS.map(field => {
      const gtVal = String(gt[field] ?? '—');
      const genVal = String(gen[field] ?? '—');
      const isDiff = norm(gtVal) !== norm(genVal) && gtVal !== '—' && genVal !== '—';
      if (isDiff) {
        drugDiffs++;
        medDiffs++;
      }
      return `<tr class="${isDiff ? 'soap-diff-row' : ''}">
                    <td class="med-field">${esc(field.replace(/_/g, ' '))}</td>
                    <td class="med-gt-col">${esc(gtVal)}</td>
                    <td class="${isDiff ? 'diff-cell-gen' : ''}">${esc(genVal)}</td>
                </tr>`;
    }).join('');

    const drugBadge = drugDiffs === 0
      ? '<span class="score-pill high" style="font-size:11px;padding:2px 8px">✓</span>'
      : `<span class="score-pill ${drugDiffs <= 1 ? 'warn' : 'low'}" style="font-size:11px;padding:2px 8px">${drugDiffs} diff${drugDiffs !== 1 ? 's' : ''}</span>`;

    const table = `
            <table class="med-compare-table">
                <thead>
                    <tr>
                        <th>Field</th>
                        <th>Ground Truth</th>
                        <th>Generated</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>`;

    return makeCollapsible(`rx-med-${i}`, `💊 ${esc(name)}`, table, {
      defaultOpen: true,
      headerRight: `<span onclick="event.stopPropagation()">${drugBadge}</span>`,
    });
  }).join('');

  const content = summaryTable
    + (maxMeds
      ? `<div class="rx-meds-heading">Medications</div>${medsHtml}`
      : '<p class="na" style="margin-top:0.75rem">No medications</p>');

  const totalDiffs = (complaint.isDiff ? 1 : 0) + (diagnosis.isDiff ? 1 : 0) + medDiffs;
  const badge = totalDiffs === 0
    ? '<span class="score-pill high" style="font-size:11px;padding:2px 8px">✓ Match</span>'
    : `<span class="score-pill ${totalDiffs <= 3 ? 'warn' : 'low'}" style="font-size:11px;padding:2px 8px">${totalDiffs} diff${totalDiffs !== 1 ? 's' : ''}</span>`;

  return makeCollapsible('prescription', '📋 Prescription Comparison', content, {
    defaultOpen: true,
    headerRight: `<span onclick="event.stopPropagation()">${badge}</span>`,
  });
}

function renderMedicationValidation(result) {
  const tr = result.transcription_result || {};
  const debug = tr.debug || {};
  const rawSoap = debug.raw_soap || debug['raw soap'] || {};
  const medVal = result.medication_validation || {};

  const finalMeds = tr.plan?.medications || medVal.final_medications || [];
  const rawMeds = rawSoap.plan?.medications || medVal.raw_medications || [];
  const gtMeds = result.soap_ground_truth?.plan?.medications || [];

  if (!finalMeds.length && !rawMeds.length && !gtMeds.length) return '';

  const FIELDS = ['drug_name', 'dose', 'schedule', 'duration', 'instructions', 'generic_name'];
  const norm = v => String(v || '').toLowerCase().replace(/[.,\-–—\s]/g, '').trim();
  const maxLen = Math.max(finalMeds.length, rawMeds.length, gtMeds.length);
  const hasGT = gtMeds.length > 0;
  const colHeaders = hasGT
    ? '<th>Field</th><th>Raw LLM</th><th>Ground Truth</th><th>Final Generated</th>'
    : '<th>Field</th><th>Raw LLM</th><th>Final Generated</th>';

  const medsHtml = Array.from({ length: maxLen }, (_, i) => {
    const final = finalMeds[i] || {};
    const raw = rawMeds[i] || {};
    const gt = gtMeds[i] || {};
    const name = gt.drug_name || final.drug_name || raw.drug_name || `Drug ${i + 1}`;

    let drugDiffs = 0;
    const rows = FIELDS.map(field => {
      const rawVal = String(raw[field] ?? '—');
      const gtVal = String(gt[field] ?? '—');
      const finalVal = String(final[field] ?? '—');

      const rawDiffFinal = norm(rawVal) !== norm(finalVal) && rawVal !== '—' && finalVal !== '—';
      const gtDiffFinal = hasGT && norm(gtVal) !== norm(finalVal) && gtVal !== '—' && finalVal !== '—';
      const gtDiffRaw = hasGT && norm(gtVal) !== norm(rawVal) && gtVal !== '—' && rawVal !== '—';

      if (rawDiffFinal || gtDiffFinal) drugDiffs++;

      if (hasGT) {
        return `<tr>
                    <td class="med-field">${esc(field.replace(/_/g, ' '))}</td>
                    <td class="${gtDiffRaw ? 'diff-cell-raw' : ''}">${esc(rawVal)}</td>
                    <td class="med-gt-col">${esc(gtVal)}</td>
                    <td class="${gtDiffFinal ? 'diff-cell-gen' : rawDiffFinal ? 'diff-cell-raw' : ''}">${esc(finalVal)}</td>
                </tr>`;
      }
      return `<tr>
                    <td class="med-field">${esc(field.replace(/_/g, ' '))}</td>
                    <td class="${rawDiffFinal ? 'diff-cell-raw' : ''}">${esc(rawVal)}</td>
                    <td class="${rawDiffFinal ? 'diff-cell-gen' : ''}">${esc(finalVal)}</td>
                </tr>`;
    }).join('');

    const badge = drugDiffs === 0
      ? '<span class="score-pill high" style="font-size:11px;padding:2px 8px">✓</span>'
      : `<span class="score-pill ${drugDiffs <= 1 ? 'warn' : 'low'}" style="font-size:11px;padding:2px 8px">${drugDiffs} diff${drugDiffs !== 1 ? 's' : ''}</span>`;

    const content = `
            <table class="med-compare-table">
                <thead><tr>${colHeaders}</tr></thead>
                <tbody>${rows}</tbody>
            </table>`;

    return makeCollapsible(`med-drug-${i}`, `💊 ${esc(name)}`, content, {
      defaultOpen: true,
      headerRight: `<span onclick="event.stopPropagation()">${badge}</span>`,
    });
  }).join('');

  const totalDiffs = medVal.difference_count || 0;
  const hasCritical = medVal.has_critical_differences;
  const overallBadge = totalDiffs === 0
    ? '<span class="score-pill high" style="font-size:12px">✓ All Match</span>'
    : `<span class="score-pill ${hasCritical ? 'low' : 'warn'}" style="font-size:12px">${totalDiffs} diff${totalDiffs !== 1 ? 's' : ''}</span>`;

  return makeCollapsible('medication', '💊 Medication Validation', medsHtml, {
    defaultOpen: true,
    headerRight: `<span onclick="event.stopPropagation()">${overallBadge}</span>`,
  });
}

function computeWordDiff(a, b) {
  if (!a && !b) return { gtHtml: '', genHtml: '' };
  if (!a) return { gtHtml: '', genHtml: esc(b) };
  if (!b) return { gtHtml: esc(a), genHtml: '' };

  const norm = s => String(s)
    .toLowerCase()
    .replace(/[.,\-–—;:!?()[\]{}"“”‘’'`/\\]/g, '')
    .replace(/\s+/g, '')
    .trim();

  const aWords = String(a).split(/\s+/).filter(Boolean);
  const bWords = String(b).split(/\s+/).filter(Boolean);

  const aNorm = new Set(aWords.map(norm).filter(Boolean));
  const bNorm = new Set(bWords.map(norm).filter(Boolean));

  const bigrams = words => {
    const bg = new Set();
    for (let i = 0; i < words.length - 1; i++) {
      const pair = norm(words[i] + words[i + 1]);
      if (pair) bg.add(pair);
    }
    return bg;
  };
  const aBigrams = bigrams(aWords);
  const bBigrams = bigrams(bWords);

  const isInOther = (word, idx, words, otherNorm, otherBigrams) => {
    const n = norm(word);
    if (!n) return true;
    if (otherNorm.has(n)) return true;
    if (idx < words.length - 1) {
      const pair = norm(word + words[idx + 1]);
      if (pair && (otherBigrams.has(pair) || otherNorm.has(pair))) return true;
    }
    if (idx > 0) {
      const pair = norm(words[idx - 1] + word);
      if (pair && (otherBigrams.has(pair) || otherNorm.has(pair))) return true;
    }
    return false;
  };

  const gtHtml = aWords.map((w, i) =>
    !isInOther(w, i, aWords, bNorm, bBigrams)
      ? `<span class="diff-missing" title="Missing in generated">${esc(w)}</span>`
      : `<span>${esc(w)}</span>`
  ).join(' ');

  const genHtml = bWords.map((w, i) =>
    !isInOther(w, i, bWords, aNorm, aBigrams)
      ? `<span class="diff-wrong" title="Not in ground truth">${esc(w)}</span>`
      : `<span>${esc(w)}</span>`
  ).join(' ');

  return { gtHtml, genHtml };
}

function showDashboard() {
  document.getElementById('dashboard-view').style.display = '';
  document.getElementById('detail-view').style.display = 'none';
  currentTestId = null;
  currentDetailResult = null;
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
window.toggleReason = toggleReason;
window.toggleSection = toggleSection;

// ── Load Testing ─────────────────────────────────────────────────────────────

function showLoadTesting() {
  document.getElementById('dashboard-view').style.display = 'none';
  document.getElementById('detail-view').style.display = 'none';
  document.getElementById('load-testing-view').style.display = '';
  ltUpdateRowCount();
}

function showDashboardFromLT() {
  document.getElementById('load-testing-view').style.display = 'none';
  showDashboard();
}

function ltAddRow(phone = '', password = '', patientId = '') {
  const tbody = document.getElementById('lt-doctor-tbody');
  const idx = ltRows.length;
  ltRows.push({ phone, password, patientId });

  const tr = document.createElement('tr');
  tr.id = `lt-row-${idx}`;
  tr.innerHTML = `
    <td>
      <input type="text" value="${esc(phone)}"
             placeholder="9876543210"
             onchange="ltRows[${idx}].phone=this.value"
             style="width:100%;padding:8px;border:1px solid var(--border);
                    border-radius:6px;font-size:14px">
    </td>
    <td>
      <div style="display:flex;align-items:center;gap:6px">
        <input type="password" value="${esc(password)}"
               placeholder="Password"
               id="lt-pwd-${idx}"
               onchange="ltRows[${idx}].password=this.value"
               style="flex:1;padding:8px;border:1px solid var(--border);
                      border-radius:6px;font-size:14px">
        <button type="button"
                onclick="ltTogglePwd(${idx})"
                style="background:none;border:none;cursor:pointer;
                       color:var(--text-secondary);font-size:16px"
                title="Show/hide password">👁</button>
      </div>
    </td>
    <td>
      <input type="text" value="${esc(patientId)}"
             placeholder="101"
             onchange="ltRows[${idx}].patientId=this.value; if (ltMode === 'manual') ltRenderPerRowUploads();"
             style="width:100%;padding:8px;border:1px solid var(--border);
                    border-radius:6px;font-size:14px">
    </td>
    <td>
      <button type="button"
              onclick="ltRemoveRow(${idx})"
              style="background:none;border:none;cursor:pointer;
                     color:var(--danger);font-size:18px"
              title="Remove row">🗑</button>
    </td>`;
  tbody.appendChild(tr);
  ltUpdateRowCount();
  if (ltMode === 'manual') ltRenderPerRowUploads();
}

function ltRemoveRow(idx) {
  const tr = document.getElementById(`lt-row-${idx}`);
  if (tr) tr.remove();
  ltRows[idx] = null;
  delete ltPerRowFiles[idx];
  ltUpdateRowCount();
  if (ltMode === 'manual') ltRenderPerRowUploads();
}

function ltTogglePwd(idx) {
  const input = document.getElementById(`lt-pwd-${idx}`);
  if (!input) return;
  input.type = input.type === 'password' ? 'text' : 'password';
}

function ltUpdateRowCount() {
  const active = ltRows.filter(r => r !== null);
  const el = document.getElementById('lt-row-count');
  if (el) el.textContent = `Total Rows: ${active.length}`;
}

function ltGetActiveRows() {
  return ltRows.filter(r => r !== null && r.phone && r.patientId);
}

function ltImportExcel() {
  document.getElementById('lt-excel-input').click();
}

async function ltHandleExcel(input) {
  const file = input.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append('file', file);

  try {
    const resp = await fetch('/api/load-test/upload-excel', {
      method: 'POST',
      body: formData,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);

    document.getElementById('lt-doctor-tbody').innerHTML = '';
    ltRows = [];
    ltPerRowFiles = {};

    data.rows.forEach(r => ltAddRow(r.phone, r.password, r.patient_id));
    ltRematchBulkToRows();
    if (ltMode === 'manual') ltRenderPerRowUploads();
    showToast(`Imported ${data.rows.length} rows from Excel`);
  } catch (err) {
    showToast(`Excel import failed: ${err.message}`);
  }
  input.value = '';
}

function ltExportConfig() {
  const driveLink = document.getElementById('lt-drive-link').value.trim();
  const config = {
    version: 1,
    saved_at: new Date().toISOString(),
    mode: ltMode,
    drive_link: driveLink,
    rows: ltGetActiveRows().map(r => ({
      phone: r.phone,
      password: r.password,
      patient_id: r.patientId,
    })),
    // Uploaded files are not saved (browser security). Filenames only.
    file_reminders: Object.entries(ltPerRowFiles).reduce(
      (acc, [idx, files]) => {
        acc[idx] = {
          audio: files.audio?.name || null,
          gt: files.gt?.name || null,
        };
        return acc;
      }, {}
    ),
  };
  const blob = new Blob([JSON.stringify(config, null, 2)],
                         { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `lt_config_${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function ltImportConfig() {
  document.getElementById('lt-config-input').click();
}

function ltHandleConfigImport(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    try {
      const config = JSON.parse(e.target.result);
      if (!config.rows) throw new Error('Invalid config file');

      if (config.drive_link) {
        document.getElementById('lt-drive-link').value = config.drive_link;
      }

      document.getElementById('lt-doctor-tbody').innerHTML = '';
      ltRows = [];
      ltPerRowFiles = {};
      config.rows.forEach(r =>
        ltAddRow(r.phone || '', r.password || '', r.patient_id || '')
      );
      ltRematchBulkToRows();
      if (config.mode) ltSetMode(config.mode);
      else if (ltMode === 'manual') ltRenderPerRowUploads();
      showToast(`Config imported — ${config.rows.length} rows restored`);
    } catch (err) {
      showToast(`Config import failed: ${err.message}`);
    }
  };
  reader.readAsText(file);
  input.value = '';
}

function ltDownloadTemplate() {
  window.location.href = '/api/load-test/excel-template';
}

function ltSetMode(mode) {
  ltMode = mode;

  document.getElementById('lt-drive-mode').style.display =
    mode === 'drive' ? '' : 'none';
  document.getElementById('lt-manual-mode').style.display =
    mode === 'manual' ? '' : 'none';

  const driveBtn = document.getElementById('lt-mode-drive');
  const manualBtn = document.getElementById('lt-mode-manual');
  driveBtn.style.background =
    mode === 'drive' ? 'var(--primary)' : 'white';
  driveBtn.style.color =
    mode === 'drive' ? 'white' : 'var(--text-secondary)';
  manualBtn.style.background =
    mode === 'manual' ? 'var(--primary)' : 'white';
  manualBtn.style.color =
    mode === 'manual' ? 'white' : 'var(--text-secondary)';

  if (mode === 'manual') ltRenderPerRowUploads();
}

function ltLoadServiceEmail() {
  ltServiceEmail =
    'medsum-google-drive@ancient-duality-453106-e2.iam.gserviceaccount.com';
  const el = document.getElementById('lt-service-email');
  if (el) el.textContent = ltServiceEmail;
}

function ltCopyServiceEmail() {
  if (!ltServiceEmail) {
    showToast('Service email not available');
    return;
  }
  navigator.clipboard.writeText(ltServiceEmail)
    .then(() => showToast('Service email copied to clipboard'))
    .catch(() => {
      const el = document.getElementById('lt-service-email');
      if (el) {
        const range = document.createRange();
        range.selectNode(el);
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
      }
      showToast('Select and copy the email address');
    });
}

async function ltVerifyDriveFolder() {
  const link = document.getElementById('lt-drive-link').value.trim();
  const statusEl = document.getElementById('lt-drive-status');

  if (!link) {
    showToast('Please enter a Drive folder link first.');
    return;
  }

  statusEl.style.display = '';
  statusEl.style.background = 'var(--primary-light)';
  statusEl.style.color = 'var(--primary)';
  statusEl.textContent = '⏳ Verifying access...';

  try {
    const resp = await fetch('/api/load-test/verify-drive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ drive_link: link }),
    });
    const data = await resp.json();

    if (!resp.ok || data.error) {
      statusEl.style.background = '#FEE2E2';
      statusEl.style.color = 'var(--danger)';
      statusEl.textContent =
        `✗ ${data.error || 'Could not access folder'}`;
    } else {
      statusEl.style.background = '#D1FAE5';
      statusEl.style.color = 'var(--success)';
      statusEl.textContent =
        `✓ Access confirmed — ${data.file_count} file(s) found`;
    }
  } catch (err) {
    statusEl.style.background = '#FEE2E2';
    statusEl.style.color = 'var(--danger)';
    statusEl.textContent = `✗ ${err.message}`;
  }
}

function ltRematchBulkToRows() {
  Object.entries(ltBulkFiles).forEach(([pid, files]) => {
    const rowIdx = ltRows.findIndex(r => r !== null && r.patientId === pid);
    if (rowIdx === -1) return;
    if (!ltPerRowFiles[rowIdx]) ltPerRowFiles[rowIdx] = {};
    if (files.audio) ltPerRowFiles[rowIdx].audio = files.audio;
    if (files.gt) ltPerRowFiles[rowIdx].gt = files.gt;
  });
}

function ltRenderPerRowUploads() {
  const container = document.getElementById('lt-per-row-uploads');
  if (!container) return;

  const activeRows = ltRows
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => r !== null);

  if (!activeRows.length) {
    container.innerHTML =
      '<p class="subtitle">No rows added yet. '
      + 'Add doctor–patient rows above first.</p>';
    return;
  }

  container.innerHTML = activeRows.map(({ r, i }) => {
    const files = ltPerRowFiles[i] || {};
    const audioName = files.audio?.name || null;
    const gtName = files.gt?.name || null;

    return `
      <div style="display:grid;
                  grid-template-columns:1fr 1fr 1fr 1fr;
                  gap:12px;align-items:center;
                  padding:10px 0;
                  border-bottom:1px solid var(--border)">

        <div style="font-size:13px;color:var(--text-primary)">
          <span style="font-weight:600">${esc(r.phone || '—')}</span>
          <br>
          <span style="font-size:12px;color:var(--text-secondary)">
            Patient: ${esc(r.patientId || '—')}
          </span>
        </div>

        <div>
          <label style="display:flex;align-items:center;gap:6px;
                        padding:6px 10px;border:1px solid var(--border);
                        border-radius:6px;cursor:pointer;
                        background:white;font-size:12px;
                        color:var(--text-secondary)">
            <input type="file" accept="audio/*,.wav,.mp3,.m4a"
                   style="display:none"
                   onchange="ltSetRowFile(${i},'audio',this)">
            🎙 ${audioName
              ? `<span style="color:var(--success)">${esc(audioName)}</span>`
              : 'Upload Audio'}
          </label>
        </div>

        <div>
          <label style="display:flex;align-items:center;gap:6px;
                        padding:6px 10px;border:1px solid var(--border);
                        border-radius:6px;cursor:pointer;
                        background:white;font-size:12px;
                        color:var(--text-secondary)">
            <input type="file" accept=".txt,.doc,.docx"
                   style="display:none"
                   onchange="ltSetRowFile(${i},'gt',this)">
            📋 ${gtName
              ? `<span style="color:var(--success)">${esc(gtName)}</span>`
              : 'Ground Truth'}
          </label>
        </div>

        <div style="font-size:11px;color:var(--text-muted)">
          ${!audioName ? '⚠ No audio — silent WAV fallback' : ''}
          ${!gtName ? '<br>⚠ No ground truth — skipped' : ''}
        </div>

      </div>`;
  }).join('');
}

function ltSetRowFile(rowIndex, type, input) {
  const file = input.files[0];
  if (!file) return;
  if (!ltPerRowFiles[rowIndex]) ltPerRowFiles[rowIndex] = {};
  ltPerRowFiles[rowIndex][type] = file;
  ltRenderPerRowUploads();
}

function ltHandleBulkUpload(input, type) {
  const files = Array.from(input.files);
  if (!files.length) return;

  let matched = 0;
  const unmatched = [];

  files.forEach(file => {
    const nameNoExt = file.name.replace(/\.[^.]+$/, '');
    const numMatch = nameNoExt.match(/\d+/);
    if (!numMatch) {
      unmatched.push(file.name);
      return;
    }
    const patientId = numMatch[0];

    if (!ltBulkFiles[patientId]) ltBulkFiles[patientId] = {};
    ltBulkFiles[patientId][type] = file;

    const rowIdx = ltRows.findIndex(
      r => r !== null && r.patientId === patientId
    );
    if (rowIdx !== -1) {
      if (!ltPerRowFiles[rowIdx]) ltPerRowFiles[rowIdx] = {};
      ltPerRowFiles[rowIdx][type] = file;
      matched++;
    }
  });

  const statusEl = document.getElementById('lt-bulk-status');
  const label = type === 'audio' ? 'audio' : 'ground truth';
  let msg = `✓ ${files.length} ${label} file(s) uploaded`;
  if (matched > 0) msg += ` — ${matched} matched to rows`;
  if (unmatched.length > 0) {
    msg += ` — ${unmatched.length} unmatched `
         + `(${unmatched.join(', ')})`;
  }
  if (statusEl) statusEl.textContent = msg;

  ltRenderPerRowUploads();
  showToast(msg);
}

async function ltStartTest() {
  const activeWithIdx = ltRows
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => r !== null && r.phone && r.patientId);
  const rows = activeWithIdx.map(({ r }) => r);
  if (rows.length === 0) {
    showToast('Add at least one doctor–patient row before starting.');
    return;
  }
  const driveLink = document.getElementById('lt-drive-link').value.trim();
  if (ltMode === 'drive' && !driveLink) {
    showToast('Please provide a Google Drive folder link.');
    return;
  }

  ltRunResults = [];
  ltRunId = null;
  document.getElementById('lt-results-section').style.display = '';
  document.getElementById('lt-timing-summary').style.display = 'none';
  document.getElementById('lt-export-btn').style.display = 'none';
  document.getElementById('lt-live-tbody').innerHTML = '';
  document.getElementById('lt-stat-total').textContent = rows.length;
  document.getElementById('lt-stat-passed').textContent = '0';
  document.getElementById('lt-stat-failed').textContent = '0';
  document.getElementById('lt-stat-passrate').textContent = '—';

  rows.forEach((r, i) => {
    const tr = document.createElement('tr');
    tr.id = `lt-live-${i}`;
    tr.innerHTML = `
      <td>${i + 1}</td>
      <td>${esc(r.phone)}</td>
      <td>${esc(r.patientId)}</td>
      <td id="lt-stage-${i}">
        <span style="color:var(--text-muted)">Pending</span>
      </td>
      <td id="lt-time-${i}">—</td>
      <td id="lt-status-${i}">
        <span style="color:var(--text-muted)">⏸</span>
      </td>`;
    document.getElementById('lt-live-tbody').appendChild(tr);
  });

  const btn = document.getElementById('lt-start-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Running...';

  try {
    const formData = new FormData();
    formData.append('rows', JSON.stringify(
      rows.map(r => ({
        phone: r.phone,
        password: r.password,
        patient_id: r.patientId,
      }))
    ));
    formData.append('mode', ltMode);
    formData.append('drive_link', driveLink);

    if (ltMode === 'manual') {
      activeWithIdx.forEach(({ r, i }, sendIdx) => {
        const files = ltPerRowFiles[i] || {};
        const bulkMatch = ltBulkFiles[r.patientId] || {};
        const audioFile = files.audio || bulkMatch.audio || null;
        const gtFile = files.gt || bulkMatch.gt || null;
        if (audioFile) formData.append(`audio_${sendIdx}`, audioFile);
        if (gtFile) formData.append(`gt_${sendIdx}`, gtFile);
      });
    }

    const resp = await fetch('/api/load-test/run', {
      method: 'POST',
      body: formData,
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data:')) continue;
        try {
          const evt = JSON.parse(line.slice(5).trim());
          ltHandleSSE(evt, rows);
        } catch (_) {}
      }
    }
  } catch (err) {
    showToast(`Run failed: ${err.message}`);
  }

  btn.disabled = false;
  btn.textContent = '▶ Start Load Test';
}

function ltHandleSSE(evt, rows) {
  if (evt.type === 'run_id') {
    ltRunId = evt.run_id;
    document.getElementById('lt-run-id-label').textContent =
      `Run ID: ${evt.run_id.slice(0, 8)}…`;
    return;
  }

  if (evt.type === 'row_update') {
    const { row_index, status, stage, elapsed, timing_data, error,
            transcription, summary } = evt;

    const stageEl = document.getElementById(`lt-stage-${row_index}`);
    if (stageEl) {
      if (status === 'running') {
        stageEl.innerHTML =
          `<span style="color:var(--primary)">⏳ ${esc(stage || 'Running')}</span>`;
      } else if (status === 'pass') {
        stageEl.innerHTML =
          `<span style="color:var(--success)">✓ Done</span>`;
      } else if (status === 'fail') {
        stageEl.innerHTML =
          `<span style="color:var(--danger)" title="${esc(error||'')}">
            ✗ Failed
          </span>`;
      }
    }

    const timeEl = document.getElementById(`lt-time-${row_index}`);
    if (timeEl && elapsed != null) {
      timeEl.textContent = `${Number(elapsed).toFixed(1)}s`;
    }

    const statusEl = document.getElementById(`lt-status-${row_index}`);
    if (statusEl) {
      if (status === 'running') {
        statusEl.innerHTML =
          `<span class="lang-badge" style="background:var(--primary-light);
                  color:var(--primary)">Running</span>`;
      } else if (status === 'pass') {
        statusEl.innerHTML =
          `<span class="lang-badge" style="background:#D1FAE5;
                  color:var(--success)">✓ Pass</span>`;
        if (transcription || summary) {
          ltAddExpandRow(row_index, transcription, summary);
        }
      } else if (status === 'fail') {
        statusEl.innerHTML =
          `<span class="lang-badge" style="background:#FEE2E2;
                  color:var(--danger)">✗ Fail</span>`;
        if (error) ltAddErrorRow(row_index, error);
      }
    }

    if (timing_data) {
      ltRunResults.push({
        row_index,
        phone: rows[row_index]?.phone,
        patient_id: rows[row_index]?.patientId,
        ...timing_data,
      });
    }

    ltUpdateStats();
    return;
  }

  if (evt.type === 'done') {
    ltUpdateStats();
    ltShowTimingSummary();
    document.getElementById('lt-export-btn').style.display = '';
    showToast(`Done — ${evt.passed}/${evt.total} passed`);
    return;
  }
}

function ltAddExpandRow(rowIndex, transcription, summary) {
  const tbody = document.getElementById('lt-live-tbody');
  const refRow = document.getElementById(`lt-live-${rowIndex}`);
  if (!refRow) return;

  const existingExpand = document.getElementById(`lt-expand-${rowIndex}`);
  if (existingExpand) existingExpand.remove();

  const summaryText = summary
    ? esc(typeof summary === 'object' ? JSON.stringify(summary, null, 2) : summary)
    : '<em>empty</em>';

  const tr = document.createElement('tr');
  tr.id = `lt-expand-${rowIndex}`;
  tr.style.background = 'var(--bg)';
  tr.innerHTML = `
    <td colspan="6" style="padding:12px 16px">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div>
          <div style="font-size:11px;font-weight:600;text-transform:uppercase;
                      color:var(--text-secondary);margin-bottom:6px;
                      letter-spacing:0.04em">
            🎙 Transcription
          </div>
          <div style="background:white;border:1px solid var(--border);
                      border-radius:8px;padding:12px;font-size:13px;
                      font-family:var(--font-mono);white-space:pre-wrap;
                      max-height:180px;overflow-y:auto">
            ${transcription ? esc(transcription) : '<em>empty</em>'}
          </div>
        </div>
        <div>
          <div style="font-size:11px;font-weight:600;text-transform:uppercase;
                      color:var(--text-secondary);margin-bottom:6px;
                      letter-spacing:0.04em">
            📋 Summary
          </div>
          <div style="background:white;border:1px solid var(--border);
                      border-radius:8px;padding:12px;font-size:13px;
                      font-family:var(--font-mono);white-space:pre-wrap;
                      max-height:180px;overflow-y:auto">
            ${summaryText}
          </div>
        </div>
      </div>
    </td>`;
  refRow.after(tr);
}

function ltAddErrorRow(rowIndex, error) {
  const refRow = document.getElementById(`lt-live-${rowIndex}`);
  if (!refRow) return;

  const existing = document.getElementById(`lt-err-${rowIndex}`);
  if (existing) existing.remove();

  const tr = document.createElement('tr');
  tr.id = `lt-err-${rowIndex}`;
  tr.innerHTML = `
    <td colspan="6" style="padding:8px 16px;background:#FEE2E2">
      <span style="font-size:13px;color:var(--danger)">✗ ${esc(error)}</span>
    </td>`;
  refRow.after(tr);
}

function ltUpdateStats() {
  const total = Number(document.getElementById('lt-stat-total').textContent) || 0;
  const passed = ltRunResults.filter(r => r.status === 'pass').length;
  const failed = ltRunResults.filter(r => r.status === 'fail').length;
  const rate = total > 0 ? Math.round(passed / total * 100) : 0;

  document.getElementById('lt-stat-passed').textContent = passed;
  document.getElementById('lt-stat-failed').textContent = failed;
  document.getElementById('lt-stat-passrate').textContent =
    passed + failed > 0 ? `${rate}%` : '—';
}

function ltShowTimingSummary() {
  const passResults = ltRunResults.filter(r => r.status === 'pass');
  if (!passResults.length) return;

  const avg = key => {
    const vals = passResults
      .map(r => r[key])
      .filter(v => v != null && !isNaN(v));
    return vals.length
      ? (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(2)
      : '—';
  };

  const metrics = [
    ['Login', 'step1_time'],
    ['Doctor Profile', 'step1b_time'],
    ['Patient Metadata', 'patient_metadata_time'],
    ['Transcription (STT)', 'transcription_time'],
    ['Translation', 'translation_time'],
    ['LLM Summary', 'llm_time'],
    ['Flask Total', 'flask_total_time'],
    ['Audio Upload', 'audio_upload_time'],
    ['Summary Store', 'summary_store_time'],
    ['User Perceived Latency', 'user_percieved_summary_latency'],
  ];

  const tbody = document.getElementById('lt-timing-tbody');
  tbody.innerHTML = metrics.map(([label, key]) => `
    <tr>
      <td>${esc(label)}</td>
      <td>${avg(key)}s</td>
    </tr>`).join('');

  document.getElementById('lt-timing-summary').style.display = '';
}

async function ltExportResults() {
  if (!ltRunResults.length) {
    showToast('No results to export yet.');
    return;
  }
  try {
    const resp = await fetch('/api/load-test/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ runs: ltRunResults }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `lt_results_${new Date().toISOString().slice(0,10)}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    showToast(`Export failed: ${err.message}`);
  }
}

window.ltAddRow = ltAddRow;
window.ltRemoveRow = ltRemoveRow;
window.ltTogglePwd = ltTogglePwd;
window.ltImportExcel = ltImportExcel;
window.ltHandleExcel = ltHandleExcel;
window.ltImportConfig = ltImportConfig;
window.ltHandleConfigImport = ltHandleConfigImport;
window.ltExportConfig = ltExportConfig;
window.ltDownloadTemplate = ltDownloadTemplate;
window.ltStartTest = ltStartTest;
window.ltExportResults = ltExportResults;
window.ltSetMode = ltSetMode;
window.ltCopyServiceEmail = ltCopyServiceEmail;
window.ltVerifyDriveFolder = ltVerifyDriveFolder;
window.ltSetRowFile = ltSetRowFile;
window.ltHandleBulkUpload = ltHandleBulkUpload;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initPage);
} else {
  initPage();
}
