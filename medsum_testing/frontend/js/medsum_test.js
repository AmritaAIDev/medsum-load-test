const API = '/api/medsum-test';

let currentBatchId = null;
let currentTestId = null;
let batchPollInterval = null;
let accuracyChart = null;
let distributionChart = null;
let driveStats = { total: 0, with_transcript: 0 };
let latestResults = [];
let currentDetailResult = null;

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
}

function getAccuracyDisplay(result) {
  const score = result.comparison?.similarity_score
    ?? result.similarity_score
    ?? result.transcription_comparison?.similarity_score
    ?? result.accuracy_score;

  if (score != null && score !== '') {
    const cssClass = score >= 95 ? 'high'
      : score >= 80 ? 'med'
      : score >= 60 ? 'warn'
      : 'low';
    return { text: `${Math.round(score)}%`, cssClass };
  }

  return { text: '—', cssClass: 'muted' };
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
    comparison: { ...comp, error: comp.error },
    comparison_error: comp.error,
    total_test_time_seconds: r.total_test_time_seconds,
    transcription_result: r.transcription_result,
  };
}

function computeStatsFromResults(results) {
  const completed = results.filter(r => r.status === 'complete' || r.final_result);
  const scores = completed
    .map(r => r.comparison?.similarity_score ?? r.accuracy_score ?? r.transcription_comparison?.similarity_score)
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
    const acc = getAccuracyDisplay(r);

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
      <td>
        <span class="accuracy-badge ${acc.cssClass}">${esc(acc.text)}</span>
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

  const comparison = result.comparison || result.transcription_comparison || {};
  const score = comparison.similarity_score ?? result.accuracy_score ?? result.similarity_score;
  const tr = result.transcription_result || {};

  renderAccuracySummary(result);

  document.getElementById('detail-title').textContent =
    `${result.audio_filename || 'Test Run'} — ${result.language || ''}`;

  const tcRefEl = document.getElementById('detail-tc-ref');
  const runRefEl = document.getElementById('detail-run-ref');
  if (tcRefEl) tcRefEl.textContent = result.tc_ref || '—';
  if (runRefEl) runRefEl.textContent = result.run_ref || '';

  const duration = result.total_test_time_seconds != null
    ? formatDuration(result.total_test_time_seconds)
    : '—';

  document.getElementById('detail-meta').innerHTML =
    `${esc(result.status || result.final_result || '')}` +
    ` · Score: ${score != null ? Math.round(score) + '%' : '—'}` +
    ` · Duration: ${esc(duration)}`;

  const groundTruth = result.ground_truth_transcription || result.ground_truth || '';
  const generated = result.generated_transcription || result.transcription || '';

  const transcriptionSection = document.getElementById('transcription-section');
  if (groundTruth || generated) {
    renderTranscriptionDiff(groundTruth, generated);
    if (transcriptionSection) transcriptionSection.style.display = 'block';
  } else if (transcriptionSection) {
    transcriptionSection.style.display = 'none';
  }

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

  const translation = tr?.debug?.translation || result.generated_translation || result.translation || '';
  const translationSection = document.getElementById('translation-section');
  if (translation || result.translation_ground_truth || result.translation_comparison) {
    renderTranslation(result);
    if (translationSection) translationSection.style.display = 'block';
  } else if (translationSection) {
    translationSection.style.display = 'none';
  }

  const hasSOAP = tr.subjective || tr.objective || tr.assessment || tr.plan;
  const soapSection = document.getElementById('soap-section');
  if (hasSOAP) {
    renderSOAPSummary(result);
    if (soapSection) soapSection.style.display = 'block';
  } else if (soapSection) {
    soapSection.style.display = 'none';
  }

  const medVal = result.medication_validation;
  const hasMeds = (medVal?.final_medications?.length > 0)
    || (medVal?.raw_medications?.length > 0)
    || (tr?.plan?.medications?.length > 0);
  const medicationSection = document.getElementById('medication-section');
  if (hasMeds) {
    renderMedicationValidation(result);
    if (medicationSection) medicationSection.style.display = 'block';
  } else if (medicationSection) {
    medicationSection.style.display = 'none';
  }

  const medicalDiffSection = document.getElementById('medical-diff-section');
  if (comparison?.similarity_score != null) {
    renderMedicalDifferences(comparison);
    if (medicalDiffSection) medicalDiffSection.style.display = 'block';
  } else if (medicalDiffSection) {
    medicalDiffSection.style.display = 'none';
  }

  const soapComparisonSection = document.getElementById('soap-comparison-section');
  if (hasSoapComparison(result.soap_comparison)) {
    renderSOAPComparison(result.soap_comparison);
    if (soapComparisonSection) soapComparisonSection.style.display = 'block';
  } else if (soapComparisonSection) {
    soapComparisonSection.style.display = 'none';
  }

  const errorsSection = document.getElementById('errors-section');
  if (result.errors?.length) {
    errorsSection.style.display = '';
    document.getElementById('errors-box').textContent = result.errors.join('\n\n');
  } else {
    errorsSection.style.display = 'none';
  }
}

function renderAccuracySummary(result) {
  const container = document.getElementById('accuracy-summary');
  if (!container) return;

  const transScore = result.comparison?.similarity_score
    ?? result.transcription_comparison?.similarity_score;
  const transScore2 = result.translation_comparison?.similarity_score;
  const soapScore = result.soap_comparison?.scores?.gt_vs_generated
    ?? result.soap_comparison?.gt_vs_generated?.similarity_score;
  const soapRawScore = result.soap_comparison?.scores?.gt_vs_raw
    ?? result.soap_comparison?.gt_vs_raw?.similarity_score;
  const soapDelta = result.soap_comparison?.scores?.raw_vs_generated
    ?? result.soap_comparison?.raw_vs_generated?.similarity_score;
  const medDiffs = result.medication_validation?.difference_count;

  function scoreChip(label, score, tooltip = '') {
    if (score == null) return `
            <div class="acc-chip muted" title="${tooltip}">
                <span class="acc-label">${label}</span>
                <span class="acc-score">—</span>
            </div>`;
    const cls = score >= 90 ? 'high' : score >= 75 ? 'med'
      : score >= 60 ? 'warn' : 'low';
    return `
            <div class="acc-chip ${cls}" title="${tooltip}">
                <span class="acc-label">${label}</span>
                <span class="acc-score">${Math.round(score)}%</span>
            </div>`;
  }

  function medChip(diffs) {
    if (diffs == null) return `
            <div class="acc-chip muted">
                <span class="acc-label">Medications</span>
                <span class="acc-score">—</span>
            </div>`;
    const cls = diffs === 0 ? 'high' : diffs <= 2 ? 'med' : 'warn';
    return `
            <div class="acc-chip ${cls}">
                <span class="acc-label">Medications</span>
                <span class="acc-score">${diffs} diff${diffs !== 1 ? 's' : ''}</span>
            </div>`;
  }

  container.innerHTML = `
        <div class="accuracy-bar">
            ${scoreChip('Transcription', transScore,
    'Ground truth transcript vs generated transcription')}
            ${scoreChip('Translation', transScore2,
    'Ground truth translation vs generated translation')}
            ${scoreChip('SOAP (GT→Gen)', soapScore,
    'Ground truth SOAP vs generated SOAP')}
            ${scoreChip('SOAP (GT→Raw)', soapRawScore,
    'Ground truth SOAP vs raw LLM SOAP')}
            ${scoreChip('SOAP (Raw→Gen)', soapDelta,
    'Raw LLM SOAP vs final generated SOAP (post-processing delta)')}
            ${medChip(medDiffs)}
        </div>`;
}

function hasSoapComparison(soapComp) {
  if (!soapComp) return false;
  if (soapComp.scores) {
    return Object.values(soapComp.scores).some(s => s != null);
  }
  return soapComp.similarity_score != null
    || soapComp.gt_vs_generated
    || soapComp.raw_vs_generated
    || soapComp.gt_vs_raw;
}

function renderSOAPComparison(soapComp) {
  const container = document.getElementById('soap-comparison-section');
  if (!container) return;

  const isThreeWay = !!(soapComp.scores || soapComp.gt_vs_generated || soapComp.raw_vs_generated || soapComp.gt_vs_raw);
  if (!isThreeWay) {
    const score = soapComp.similarity_score;
    const scoreClass = score >= 95 ? 'high' : score >= 80 ? 'med' : score >= 60 ? 'warn' : 'low';
    container.innerHTML = `
    <div class="section-card">
      <div class="section-title">
        📋 SOAP Comparison
        ${score != null
    ? `<span class="accuracy-pill ${scoreClass}">${Math.round(score)}% match</span>`
    : ''}
      </div>
      <p class="comparison-summary">${esc(soapComp.summary || '')}</p>
    </div>`;
    return;
  }

  const panels = [
    { key: 'gt_vs_generated', label: 'SOAP GT → Generated' },
    { key: 'gt_vs_raw', label: 'SOAP GT → Raw LLM' },
    { key: 'raw_vs_generated', label: 'SOAP Raw → Generated' },
  ];
  const scores = soapComp.scores || {};

  const panelHtml = panels.map(({ key, label }) => {
    const detail = soapComp[key];
    const score = scores[key] ?? detail?.similarity_score;
    if (score == null && !detail) {
      return `<div class="soap-panel muted">
        <div class="soap-panel-label">${esc(label)}</div>
        <div class="soap-panel-score">—</div>
        <p class="comparison-summary">Not available</p>
      </div>`;
    }
    const cls = score == null ? 'muted'
      : score >= 90 ? 'high'
        : score >= 75 ? 'med'
          : score >= 60 ? 'warn' : 'low';
    return `<div class="soap-panel ${cls}">
        <div class="soap-panel-label">${esc(label)}</div>
        <div class="soap-panel-score">${score != null ? Math.round(score) + '%' : '—'}</div>
        <p class="comparison-summary">${esc(detail?.summary || '')}</p>
      </div>`;
  }).join('');

  container.innerHTML = `
    <div class="section-card">
      <div class="section-title">📋 SOAP Comparison</div>
      <div class="soap-three-way">${panelHtml}</div>
    </div>`;
}

function computeWordDiff(groundTruth, generated) {
  if (!groundTruth || !generated) {
    return {
      gtHtml: esc(groundTruth || ''),
      genHtml: esc(generated || ''),
    };
  }

  const normalize = str => str
    .toLowerCase()
    .replace(/[.,\-–—;:!?()]/g, '')
    .replace(/\s+/g, ' ')
    .trim();

  const gtWords = (groundTruth || '').split(/\s+/);
  const genWords = (generated || '').split(/\s+/);
  const gtNorm = new Set(gtWords.map(normalize));

  const gtHtml = gtWords.map(w => `<span>${esc(w)}</span>`).join(' ');

  const genHtml = genWords.map(w => {
    const norm = normalize(w);
    if (gtNorm.has(norm)) {
      return `<span>${esc(w)}</span>`;
    }
    const strippedW = norm.replace(/[^a-z0-9]/g, '');
    const matchesPunctVariant = [...gtNorm].some(gt =>
      gt.replace(/[^a-z0-9]/g, '') === strippedW
    );
    if (matchesPunctVariant) {
      return `<span>${esc(w)}</span>`;
    }
    return `<span class="diff-highlight">${esc(w)}</span>`;
  }).join(' ');

  return { gtHtml, genHtml };
}

function renderTranscriptionDiff(groundTruth, generated) {
  const container = document.getElementById('transcription-diff');
  if (!container) return;

  const { gtHtml, genHtml } = computeWordDiff(groundTruth, generated);

  container.innerHTML = `
    <div class="diff-grid">
      <div class="diff-col">
        <div class="diff-col-header">Ground Truth</div>
        <div class="diff-text">${gtHtml || '<em>No ground truth available</em>'}</div>
      </div>
      <div class="diff-col">
        <div class="diff-col-header">Generated</div>
        <div class="diff-text generated-col">${genHtml || '<em>No transcription</em>'}</div>
      </div>
    </div>`;
}

function renderTranslation(result) {
  const container = document.getElementById('translation-section');
  if (!container) return;

  const generated = result?.generated_translation
    || result?.transcription_result?.debug?.translation
    || result?.translation
    || result?.text_translation
    || '';
  const groundTruth = result?.translation_ground_truth
    || result?.ground_truth_translation
    || '';

  if (!generated && !groundTruth) {
    container.style.display = 'none';
    return;
  }

  const score = result?.translation_comparison?.similarity_score;
  const scoreClass = score >= 90 ? 'high' : score >= 75 ? 'med' : score >= 60 ? 'warn' : 'low';
  const scorePill = score != null
    ? `<span class="accuracy-pill ${scoreClass}">${Math.round(score)}% match</span>`
    : '';

  container.style.display = 'block';
  if (groundTruth) {
    const { gtHtml, genHtml } = computeWordDiff(groundTruth, generated);
    container.innerHTML = `
    <div class="section-card">
      <div class="section-title">🌐 Translation Comparison ${scorePill}</div>
      <div class="diff-grid">
        <div class="diff-col">
          <div class="diff-col-header">Ground Truth</div>
          <div class="diff-text">${gtHtml || '<em>No ground truth available</em>'}</div>
        </div>
        <div class="diff-col">
          <div class="diff-col-header">Generated</div>
          <div class="diff-text generated-col">${genHtml || '<em>No translation</em>'}</div>
        </div>
      </div>
    </div>`;
    return;
  }

  container.innerHTML = `
    <div class="section-card">
      <div class="section-title">🌐 Translation (Debug) ${scorePill}</div>
      <div class="translation-text">${esc(generated)}</div>
    </div>`;
}

function renderSOAPSummary(result) {
  const container = document.getElementById('soap-section');
  if (!container) return;

  const tr = result?.transcription_result || {};
  const subj = tr.subjective || result?.soap_subjective || {};
  const obj = tr.objective || result?.soap_objective || {};
  const asmt = tr.assessment || result?.soap_assessment || {};
  const plan = tr.plan || result?.soap_plan || {};
  const summary = tr.summary || result?.soap_summary || '';

  const tabs = [
    { id: 'subj', label: 'Subjective', content: subj },
    { id: 'obj', label: 'Objective', content: obj },
    { id: 'asmnt', label: 'Assessment', content: asmt },
    { id: 'plan', label: 'Plan', content: plan },
  ];

  const tabHeaders = tabs.map((t, i) =>
    `<button class="soap-tab ${i === 0 ? 'active' : ''}" type="button"
             onclick="switchSOAPTab('${t.id}', this)">${esc(t.label)}</button>`
  ).join('');

  const tabContents = tabs.map((t, i) => `
    <div id="soap-${t.id}" class="soap-content ${i === 0 ? 'active' : ''}">
      ${renderSOAPFields(t.content)}
    </div>
  `).join('');

  container.innerHTML = `
    <div class="section-card">
      <div class="section-title">📋 SOAP Summary</div>
      ${summary ? `<div class="soap-summary-text">${esc(summary)}</div>` : ''}
      <div class="soap-tabs">${tabHeaders}</div>
      ${tabContents}
    </div>`;
}

function renderSOAPFields(obj) {
  if (!obj || typeof obj !== 'object') return '<em>No data</em>';
  return Object.entries(obj).map(([key, val]) => {
    const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    let valueHtml = '';
    if (Array.isArray(val)) {
      valueHtml = val.map(v =>
        typeof v === 'object'
          ? `<div class="soap-nested">${renderSOAPFields(v)}</div>`
          : `<span>${esc(String(v))}</span>`
      ).join(', ');
    } else if (typeof val === 'object' && val !== null) {
      valueHtml = `<div class="soap-nested">${renderSOAPFields(val)}</div>`;
    } else {
      valueHtml = val === 'NA' || val === '' || val == null
        ? '<span class="soap-na">NA</span>'
        : `<span>${esc(String(val))}</span>`;
    }
    return `
      <div class="soap-field">
        <div class="soap-field-label">${esc(label)}</div>
        <div class="soap-field-value">${valueHtml}</div>
      </div>`;
  }).join('');
}

function switchSOAPTab(id, btn) {
  document.querySelectorAll('.soap-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.soap-content').forEach(c => c.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(`soap-${id}`)?.classList.add('active');
}

function renderMedicationValidation(result) {
  const container = document.getElementById('medication-section');
  if (!container) return;

  const medVal = result?.medication_validation;
  const tr = result?.transcription_result || {};

  const finalMeds = tr?.plan?.medications
    || medVal?.final_medications || [];
  const rawMeds = tr?.debug?.raw_soap?.plan?.medications
    || tr?.debug?.['raw soap']?.plan?.medications
    || medVal?.raw_medications || [];

  const differences = medVal?.differences || [];
  const diffMap = {};
  differences.forEach(d => {
    if (d.field) diffMap[`${d.drug}__${d.field}`] = d;
  });

  const FIELDS = ['drug_name', 'generic_name', 'dose', 'schedule',
    'duration', 'instructions', 'matched_drug_name'];

  const maxLen = Math.max(finalMeds.length, rawMeds.length, 0);

  const rows = Array.from({ length: maxLen }, (_, i) => {
    const final = finalMeds[i] || {};
    const raw = rawMeds[i] || {};
    const drugName = final.drug_name || raw.drug_name || `Drug ${i + 1}`;

    return FIELDS.map((field, fi) => {
      const finalVal = final[field] ?? '—';
      const rawVal = raw[field] ?? '—';
      const diff = diffMap[`${drugName}__${field}`];
      const changed = String(rawVal) !== String(finalVal);

      return `
        <tr>
          ${fi === 0 ? `<td class="med-drug-name" rowspan="${FIELDS.length}">${esc(drugName)}</td>` : ''}
          <td class="med-field-name">${esc(field.replace(/_/g, ' '))}</td>
          <td class="${changed ? 'med-changed' : ''}">${esc(String(rawVal))}</td>
          <td class="${changed ? 'med-changed' : ''}">
            ${esc(String(finalVal))}
            ${changed && diff?.severity
              ? `<span class="med-diff-badge ${esc(diff.severity)}">${esc(diff.severity)}</span>`
              : ''}
          </td>
        </tr>`;
    }).join('');
  }).join('');

  const diffSummary = differences.length > 0
    ? `<div class="med-diff-summary warning">
           ⚠ ${differences.length} difference${differences.length > 1 ? 's' : ''} found
           ${medVal?.has_critical_differences
      ? ' — <strong>critical differences present</strong>' : ''}
       </div>`
    : maxLen > 0
      ? '<div class="med-diff-summary success">✓ Raw and final medications match</div>'
      : '';

  container.innerHTML = `
    <div class="section-card">
      <div class="section-title">💊 Medication Validation</div>
      ${diffSummary}
      ${maxLen > 0 ? `
      <table class="med-table">
        <thead>
          <tr>
            <th>Drug</th>
            <th>Field</th>
            <th>Raw LLM Output</th>
            <th>Final Output (SNOMED)</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>` : '<p>No medications recorded for this consultation.</p>'}
    </div>`;
}

function renderMedicalDifferences(comparison) {
  const container = document.getElementById('medical-diff-section');
  if (!container) return;

  const structured = comparison?.medical_difference_details
    || (comparison?.medical_differences || []).filter(d => typeof d === 'object' && d.severity);
  const diffs = structured.filter(d => d.severity);

  if (diffs.length === 0) {
    container.innerHTML = `
      <div class="section-card">
        <div class="section-title">🏥 Medical Differences</div>
        <div class="no-diffs">✓ No significant medical differences found</div>
      </div>`;
    return;
  }

  const rows = diffs.map(d => `
    <div class="med-diff-row ${esc(d.severity || '')}">
      <div class="med-diff-type">
        <span class="severity-badge ${esc(d.severity || '')}">${esc(d.severity || '')}</span>
        <span class="diff-type-label">${esc((d.type || 'difference').replace(/_/g, ' '))}</span>
      </div>
      <div class="med-diff-values">
        <div class="diff-value ground-truth">
          <span class="diff-label">Ground Truth</span>
          <span>${esc(d.ground_truth || '—')}</span>
        </div>
        <div class="diff-arrow">→</div>
        <div class="diff-value generated">
          <span class="diff-label">Generated</span>
          <span class="diff-highlight">${esc(d.generated || '—')}</span>
        </div>
      </div>
    </div>
  `).join('');

  const score = comparison?.similarity_score;
  const scoreClass = score >= 95 ? 'high' : score >= 80 ? 'med' : score >= 60 ? 'warn' : 'low';
  container.innerHTML = `
    <div class="section-card">
      <div class="section-title">
        🏥 Medical Differences
        ${score != null
    ? `<span class="accuracy-pill ${scoreClass}">${Math.round(score)}% match</span>`
    : ''}
      </div>
      <p class="comparison-summary">${esc(comparison?.summary || '')}</p>
      ${rows}
    </div>`;
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
window.switchSOAPTab = switchSOAPTab;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initPage);
} else {
  initPage();
}
