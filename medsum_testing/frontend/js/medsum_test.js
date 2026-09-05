const API = '/api/medsum-test';

let currentBatchId = null;
let currentBatchRef = '';
let currentTestId = null;
let batchPollInterval = null;
let accuracyChart = null;
let distributionChart = null;
let driveStats = { total: 0, with_transcript: 0 };
let latestResults = [];
let allResultsCache = null;
let currentDetailResult = null;
let ltRows = [];           // [{phone, password, patientId}]
let ltRunResults = [];     // timing_data objects per sessi on
let ltRunId = null;
let ltMode = 'drive';      // 'drive' | 'manual'
let ltPerRowFiles = {};    // { rowIndex: { audio: File|null, gt: File|null } }
let ltBulkFiles = {};      // { patientId: { audio: File|null, gt: File|null } }
let ltServiceEmail = '';
let accDoctors = [];       // [{phone, password, patients: [id, ...]}, ...]
let accSetupOpen = true;
let lastListView = 'dashboard';
let audioCatalog = [];
let audioSelectedKeys = [];
let audioSelectionReady = false;
let audioSourceTab = 'upload';
let pendingGtFiles = [];
let detailOpenGeneration = 0;
window._currentResults = [];
window._historyResults = [];

function testCaseViewApi() {
  return window.MedsumTestCaseView || {};
}

function resultStableId(r) {
  const api = testCaseViewApi();
  if (api.stableTestId) return api.stableTestId(r) || '';
  const tid = String((r && r.test_id) || '').trim();
  if (tid && !/^\d{1,12}$/.test(tid)) return tid;
  return '';
}

function sessionPersistApi() {
  return window.MedsumSessionPersist || {};
}

async function initPage() {
  updateDashboardStats([]);
  restoreTableTabs();
  renderEmptyResultsState();
  bindModelDescription();
  checkAIConfig();
  bindEvents();
  restoreDoctorForm();
  restoreLtForm();
  ltLoadServiceEmail();
  // File selection resets on refresh: File blobs cannot round-trip, and
  // restoring Drive keys alone would look selected but would not be submitted.
  await loadDriveStats();
  await loadDashboardFilters();
  try {
    updateDashboardStats(await fetchAllResults());
  } catch (err) {
    console.warn('Dashboard stats failed:', err);
  }
  await resumeWatchedBatch();
}

function restoreDoctorForm() {
  const api = sessionPersistApi();
  const snapshot = api.loadDoctors ? api.loadDoctors() : [];
  const addBlank = api.shouldAddBlankDoctorRow
    ? api.shouldAddBlankDoctorRow(snapshot)
    : !snapshot.length;
  if (addBlank) {
    accAddDoctor();
    return;
  }
  snapshot.forEach(row => accAddDoctor(row.phone, row.password, row.patients));
}

function restoreLtForm() {
  const api = sessionPersistApi();
  const snapshot = api.loadLtRows ? api.loadLtRows() : [];
  const addBlank = api.shouldAddBlankLtRow
    ? api.shouldAddBlankLtRow(snapshot)
    : !snapshot.length;
  if (addBlank) {
    ltAddRow();
    return;
  }
  snapshot.forEach(row => ltAddRow(row.phone, row.password, row.patientId));
}

function persistDoctorForm() {
  const api = sessionPersistApi();
  if (api.saveDoctors) api.saveDoctors(accDoctors);
}

function persistLtForm() {
  const api = sessionPersistApi();
  if (api.saveLtRows) api.saveLtRows(ltRows);
}

function restoreTableTabs() {
  const api = sessionPersistApi();
  const tabs = api.loadTableTabs ? api.loadTableTabs() : { dashboard: 'results', history: 'results' };
  const tableApi = resultsTableApi();
  if (!tableApi.setTab) return;
  tableApi.setTab('dashboard', tabs.dashboard);
  tableApi.setTab('history', tabs.history);
}

function persistSelectedBatchIds() {
  const api = sessionPersistApi();
  if (api.saveSelectedBatchIds) api.saveSelectedBatchIds(getSelectedBatchIds());
}

function armBatchPoll(batchId) {
  if (!batchId) return;
  if (batchPollInterval) return;
  batchPollInterval = setInterval(() => pollBatch(batchId), 5000);
}

function stopBatchPoll() {
  if (batchPollInterval) clearInterval(batchPollInterval);
  batchPollInterval = null;
}

function markBatchUiRunning(running) {
  const btn = document.getElementById('run-all-btn');
  if (btn) {
    if (running) {
      btn.dataset.running = '1';
      btn.disabled = true;
      btn.textContent = '⏳ Running...';
    } else {
      btn.dataset.running = '';
      btn.disabled = false;
      btn.textContent = RUN_BATCH_LABEL;
      updateRunButtonState();
    }
  }
  const live = document.getElementById('acc-live-section');
  if (live && running) live.style.display = '';
}

async function resumeWatchedBatch() {
  const api = sessionPersistApi();
  const stored = api.loadCurrentBatchId ? api.loadCurrentBatchId() : '';
  if (!stored) return;
  currentBatchId = stored;
  await pollBatch(stored);
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

function accuracyTooltipApi() {
  return window.MedsumAccuracyTooltip || {};
}

function accuracyTooltipModel(result, focus) {
  const api = accuracyTooltipApi();
  if (!api.buildAccuracyTooltip) return null;
  return api.buildAccuracyTooltip(result || {}, { focus: focus || 'overall' });
}

/**
 * Score pill. Tooltip is per-case (compared / criteria / method / field status).
 * A missing score still gets a popover when the field is NOT_SCORED.
 */
function scorePill(score, reason, label, id, options) {
  const safeLabel = esc(label || '');
  const safeId = String(id || '').replace(/[^a-zA-Z0-9_-]/g, '');
  const api = accuracyTooltipApi();
  const tooltip = (options && options.tooltip)
    || (reason && typeof reason === 'object' && reason.pieces ? reason : null);

  const n = (score == null || score === '') ? NaN : Number(score);
  const scored = !Number.isNaN(n);
  const notScored = !scored && tooltip && (
    tooltip.overall_status === 'NOT_SCORED' || tooltip.not_scored_present
  );
  const hideLabel = Boolean(options && options.hideLabel);
  const extraClass = (options && options.extraClass) ? ` ${options.extraClass}` : '';

  if (!scored && !notScored) {
    return `<span class="score-pill muted${extraClass}" title="${safeLabel}">—${hideLabel ? '' : `<span class="pill-label">${safeLabel}</span>`}</span>`;
  }

  const cls = !scored
    ? 'muted'
    : n >= 90 ? 'high' : n >= 75 ? 'med' : n >= 60 ? 'warn' : 'low';
  const shown = scored
    ? ((options && options.shownText) || `${Math.round(n)}%`)
    : '—';
  const header = scored
    ? `WHY ${Math.round(n)}%? — ${safeLabel}`
    : `NOT_SCORED — ${safeLabel}`;

  let body;
  if (tooltip && api.tooltipHtml) {
    body = api.tooltipHtml(tooltip, header);
  } else {
    body = `<div class="reason-header">${esc(header)}</div>
            <div class="reason-text">${esc(aiSummary(reason) || 'No reasoning available.')}</div>`;
  }

  return `
        <span class="score-pill-wrapper" data-tooltip-trigger>
            <span class="score-pill ${cls}${extraClass}" id="pill-${safeId}" title="${safeLabel}">
                ${shown}
                ${hideLabel ? '' : `<span class="pill-label">${safeLabel}</span>`}
            </span>
            <div class="reason-popup" role="tooltip">${body}</div>
        </span>`;
}

function makeCollapsible(id, title, contentHtml, {
  defaultOpen = false,
  score = null,
  scoreReason = null,
  scoreLabel = '',
  headerRight = '',
  timeSeconds = null,
  timeLabel = '',
} = {}) {
  const arrow = defaultOpen ? '▼' : '▶';
  const scoreBadge = score != null
    ? scorePill(score, scoreReason, scoreLabel, `${id}-header`)
    : '';

  let timeChipHtml = '';
  if (timeSeconds != null && parseFloat(timeSeconds) > 0) {
    const t = parseFloat(timeSeconds);
    const display = t >= 60
      ? `${Math.floor(t / 60)}m ${Math.round(t % 60)}s`
      : `${t.toFixed(1)}s`;
    const label = timeLabel ? esc(timeLabel) : 'processing';
    timeChipHtml = `
            <span class="header-time-chip" title="${label} latency">
                ⏱ ${display}
            </span>`;
  }

  return `
        <div class="collapsible-section" id="section-${id}">
            <div class="collapsible-header" onclick="toggleSection('${id}')">
                <span class="collapsible-arrow" id="arrow-${id}">${arrow}</span>
                <span class="collapsible-title">${title}</span>
                <span class="collapsible-header-right" onclick="event.stopPropagation()">
                    ${timeChipHtml}
                    ${scoreBadge}
                </span>
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
  const backBtn = document.getElementById('back-btn');
  if (backBtn) {
    backBtn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      backToDashboardFromDetail();
    });
  }
  const pdfBtn = document.getElementById('pdf-btn');
  if (pdfBtn) pdfBtn.addEventListener('click', () => downloadReport('pdf'));
  const excelBtn = document.getElementById('excel-btn');
  if (excelBtn) excelBtn.addEventListener('click', () => downloadReport('excel'));
  const exportBtn = document.getElementById('export-btn');
  const exportMenu = document.getElementById('export-menu');
  if (exportBtn && exportMenu) {
    exportBtn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      const open = !exportMenu.classList.contains('open');
      exportMenu.classList.toggle('open', open);
      exportBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      const panel = exportMenu.querySelector('.export-menu');
      if (panel) panel.hidden = !open;
    });
    document.addEventListener('click', event => {
      if (exportMenu.contains(event.target)) return;
      exportMenu.classList.remove('open');
      exportBtn.setAttribute('aria-expanded', 'false');
      const panel = exportMenu.querySelector('.export-menu');
      if (panel) panel.hidden = true;
    });
    exportMenu.querySelectorAll('[data-soap-gt-export]').forEach(item => {
      item.addEventListener('click', () => {
        const format = item.getAttribute('data-soap-gt-export') || 'json';
        downloadSoapGtComparison(format);
        exportMenu.classList.remove('open');
        exportBtn.setAttribute('aria-expanded', 'false');
        const panel = exportMenu.querySelector('.export-menu');
        if (panel) panel.hidden = true;
      });
    });
  }

  if (window.MedsumPageNav && window.MedsumPageNav.bind) {
    window.MedsumPageNav.bind({
      onChange: handlePageChange,
      openDetail: testId => openTestDetail(testId, { fromRoute: true }),
    });
  }

  bindModelDescription();
  bindTestCaseViewClicks();
  bindAccuracyOverallTips();
  bindTotalReportDownloads();
  bindRunFileListClicks();
  bindUploadDropZone();
  bindGtEditModal();
  bindTooltipPositioning();
  bindBatchFilter();
}

function bindTooltipPositioning() {
  const api = window.MedsumTooltipPosition;
  if (api && api.bindTooltipPositioning) api.bindTooltipPositioning();
}

function bindAccuracyOverallTips() {
  const api = accuracyTooltipApi();
  if (!api.tooltipHtml || !api.overallAverageTooltip) return;
  const model = api.overallAverageTooltip();
  document.querySelectorAll('[data-accuracy-overall-tip] .stat-tip-popup').forEach(el => {
    el.innerHTML = api.tooltipHtml(model, 'Average Accuracy');
  });
}

function accuracyChartApi() {
  return window.MedsumAccuracyChart || {};
}

function getSelectedBatchIds() {
  const allCb = document.getElementById('batch-filter-all');
  if (allCb && allCb.checked) return [];
  return [...document.querySelectorAll('#batch-filter-options .batch-filter-cb:checked')]
    .map(cb => cb.value)
    .filter(id => id && id !== 'all');
}

function batchFilterPanelOpen() {
  const panel = document.getElementById('batch-filter-panel');
  return panel && !panel.hidden;
}

function setBatchFilterPanelOpen(open) {
  const panel = document.getElementById('batch-filter-panel');
  const toggle = document.getElementById('batch-filter-toggle');
  if (panel) panel.hidden = !open;
  if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function formatBatchFilterLabel(batch, labels) {
  const data = batch || {};
  const api = accuracyChartApi();
  const dated = api.batchDisplayLabel
    ? api.batchDisplayLabel(data, labels)
    : '';
  if (dated && /\d{2}-[A-Za-z]{3}-\d{4} \| /.test(dated)) return dated;
  const dateText = (api.formatBatchDate && api.formatBatchDate(data.timestamp))
    || String(data.timestamp || data.created_at || '').slice(0, 10);
  const fallback = displayBatchLabel(data);
  if (dateText && dateText !== 'Invalid Date' && !String(fallback).includes(dateText)) {
    return `${dateText} | ${fallback}`;
  }
  return dated || fallback || 'Batch';
}

function syncBatchFilterToggleLabel() {
  const textEl = document.getElementById('batch-filter-toggle-text')
    || document.getElementById('batch-filter-toggle');
  if (!textEl) return;
  const selected = getSelectedBatchIds();
  if (!selected.length) {
    textEl.textContent = 'All Batches';
    return;
  }
  const labels = [...document.querySelectorAll('#batch-filter-options .batch-filter-cb:checked')]
    .map(cb => cb.dataset.label || cb.closest('label')?.textContent?.trim() || cb.value);
  if (selected.length === 1) {
    textEl.textContent = labels[0] || formatBatchFilterLabel({ batch_id: selected[0] });
    return;
  }
  textEl.textContent = `${selected.length} batches`;
}

function onBatchFilterAllToggle() {
  const allCb = document.getElementById('batch-filter-all');
  if (!allCb) return;
  if (allCb.checked) {
    document.querySelectorAll('#batch-filter-options .batch-filter-cb').forEach(cb => {
      cb.checked = false;
    });
  } else if (!getSelectedBatchIds().length) {
    allCb.checked = true;
  }
  persistSelectedBatchIds();
  syncBatchFilterToggleLabel();
  onDashboardFilterChange();
}

function onBatchOptionToggle() {
  const allCb = document.getElementById('batch-filter-all');
  const selected = [...document.querySelectorAll('#batch-filter-options .batch-filter-cb:checked')];
  if (allCb) allCb.checked = selected.length === 0;
  persistSelectedBatchIds();
  syncBatchFilterToggleLabel();
  onDashboardFilterChange();
}

function bindBatchFilter() {
  const root = document.getElementById('batch-filter');
  if (!root || root.dataset.bound === '1') return;
  root.dataset.bound = '1';
  const toggle = document.getElementById('batch-filter-toggle');
  const allCb = document.getElementById('batch-filter-all');
  if (toggle) {
    toggle.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      setBatchFilterPanelOpen(!batchFilterPanelOpen());
    });
  }
  if (allCb) {
    allCb.addEventListener('change', onBatchFilterAllToggle);
  }
  document.addEventListener('click', event => {
    if (!root.contains(event.target)) setBatchFilterPanelOpen(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') setBatchFilterPanelOpen(false);
  });
}

function resultsActionsApi() {
  return window.MedsumResultsActions || {};
}

function rowsForView(rows) {
  const api = resultsActionsApi();
  return api.visibleResults ? api.visibleResults(rows) : (rows || []);
}

function rowActionsHtml(testId) {
  const api = resultsActionsApi();
  if (api.rowActionsHtml) return api.rowActionsHtml(testId, esc);
  return `<button type="button" class="view-btn"
                  data-row-action="download"
                  data-test-id="${esc(testId)}"
                  ${testId ? '' : 'disabled'}>Download</button>`;
}

function excludeResultRow(testId) {
  const api = resultsActionsApi();
  if (api.excludeFromView) api.excludeFromView(testId);
  if (lastListView === 'runs') {
    const raw = window._historyResultsUnfiltered || window._historyResults || [];
    accRenderSummaryTable(raw);
    applyAccRunStats(computeAccRunStats(raw));
  } else {
    const raw = window._currentResultsUnfiltered || window._currentResults || [];
    updateDashboardStats(raw);
    renderTestRunsTable(raw);
  }
  showToast('Removed from this view — audio and saved result were not deleted');
}

function downloadIndividualReport(testId, format) {
  if (!testId) return;
  window.location.href = `${API}/report/${testId}?format=${format || 'pdf'}`;
}

async function downloadTotalReport(format) {
  const rows = lastListView === 'runs'
    ? (window._historyResults || [])
    : (window._currentResults || []);
  const ids = rows.map(resultStableId).filter(Boolean);
  if (!ids.length) {
    showToast('No results to download');
    return;
  }
  try {
    const resp = await fetch(`${API}/report/total?format=${format || 'pdf'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        test_ids: ids,
        batch_id: rows[0]?.batch_id || '',
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = format === 'excel'
      ? 'medsum-batch.xlsx'
      : 'medsum-batch.pdf';
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    showToast(`Total report failed: ${err.message}`);
  }
}

function soapBatchFilename(format) {
  if (format === 'excel') return 'soap-batch-comparison.xlsx';
  if (format === 'csv') return 'soap-batch-comparison.csv';
  if (format === 'html') return 'soap-batch-comparison.html';
  if (format === 'json') return 'soap-batch-comparison.json';
  return 'soap-batch-comparison.pdf';
}

async function downloadSoapBatchReport(format) {
  const rows = lastListView === 'runs'
    ? (window._historyResults || [])
    : (window._currentResults || []);
  const ids = rows.map(resultStableId).filter(Boolean);
  if (!ids.length) {
    showToast('No results to download');
    return;
  }
  try {
    const resp = await fetch(`${API}/report/total/soap-comparison?format=${format || 'pdf'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        test_ids: ids,
        batch_id: rows[0]?.batch_id || '',
        format: format || 'pdf',
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = soapBatchFilename(format);
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    showToast(`SOAP batch report failed: ${err.message}`);
  }
}

function closeBatchExportMenu(root) {
  if (!root) return;
  root.classList.remove('open');
  const toggle = root.querySelector('[data-batch-export-toggle]');
  if (toggle) toggle.setAttribute('aria-expanded', 'false');
  const panel = root.querySelector('.export-menu');
  if (panel) panel.hidden = true;
}

function bindTotalReportDownloads() {
  document.querySelectorAll('[data-batch-export]').forEach(root => {
    if (root.dataset.bound) return;
    root.dataset.bound = '1';
    const toggle = root.querySelector('[data-batch-export-toggle]');
    const panel = root.querySelector('.export-menu');
    if (toggle && panel) {
      toggle.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const open = !root.classList.contains('open');
        document.querySelectorAll('[data-batch-export]').forEach(other => {
          if (other !== root) closeBatchExportMenu(other);
        });
        root.classList.toggle('open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        panel.hidden = !open;
      });
    }
  });
  document.addEventListener('click', event => {
    document.querySelectorAll('[data-batch-export]').forEach(root => {
      if (root.contains(event.target)) return;
      closeBatchExportMenu(root);
    });
  });
  document.querySelectorAll('[data-download-total-report]').forEach(btn => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      downloadTotalReport(btn.getAttribute('data-download-total-report') || 'pdf');
      closeBatchExportMenu(btn.closest('[data-batch-export]'));
    });
  });
  document.querySelectorAll('[data-download-soap-batch]').forEach(btn => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => {
      downloadSoapBatchReport(btn.getAttribute('data-download-soap-batch') || 'pdf');
      closeBatchExportMenu(btn.closest('[data-batch-export]'));
    });
  });
}

function bindTestCaseViewClicks() {
  document.addEventListener('click', event => {
    const actionBtn = event.target.closest('[data-row-action]');
    if (actionBtn) {
      event.preventDefault();
      event.stopPropagation();
      const kind = actionBtn.getAttribute('data-row-action');
      const testId = actionBtn.getAttribute('data-test-id')
        || actionBtn.getAttribute('data-open-test-id')
        || '';
      if (kind === 'remove') {
        excludeResultRow(testId);
        return;
      }
      if (kind === 'download') {
        downloadIndividualReport(testId);
        return;
      }
      if (kind === 'view' && testId) {
        openTestDetail(testId);
      }
      return;
    }

    const ignore = event.target.closest(
      '.score-pill-wrapper, [data-col="accuracy"], [data-col="soap-accuracy"], .reason-popup, #back-btn, #pdf-btn, #excel-btn, #export-btn, #export-menu'
    );
    if (ignore && !event.target.closest('tr[data-open-test-id], [data-row-action="view"]')) return;

    const opener = event.target.closest('[data-open-test-id]');
    if (!opener) return;
    const testId = opener.getAttribute('data-open-test-id') || '';
    const hostId = opener.id || '';
    const api = testCaseViewApi();
    const allowed = api.clickShouldOpenDetail
      ? api.clickShouldOpenDetail(hostId, testId)
      : (Boolean(testId) && hostId !== 'detail-view' && hostId !== 'back-btn');
    if (!allowed) return;
    event.preventDefault();
    openTestDetail(testId);
  });
}

function bindModelDescription() {
  const sel = document.getElementById('ai-model-select');
  if (!sel || sel.dataset.descBound) return;
  sel.dataset.descBound = '1';
  const apply = () => {
    const desc = sel.options[sel.selectedIndex]?.getAttribute('data-desc') || '';
    const el = document.getElementById('model-description');
    if (el) el.textContent = desc;
    updateRunModelLabels();
  };
  sel.addEventListener('change', apply);
  apply();
}

function updateRunModelLabels() {
  const sel = document.getElementById('ai-model-select');
  const label = sel?.options[sel.selectedIndex]?.textContent?.trim()
    || sel?.value
    || '—';
  document.querySelectorAll('[data-run-model-label]').forEach(el => {
    el.textContent = label;
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
    populateAudioSelectors(files);
    renderUnmatchedDriveGt(data);
  } catch (err) {
    console.warn('Drive stats failed:', err);
  }
}

function renderUnmatchedDriveGt(data) {
  const box = document.getElementById('drive-unmatched-gt');
  if (!box) return;
  const unmatched = (data && data.unmatched_ground_truth) || [];
  const count = Number(data && data.unmatched_ground_truth_count) || unmatched.length;
  if (!count) {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  const heading = (data && data.unmatched_ground_truth_heading)
    || `${count} Ground Truth file${count === 1 ? '' : 's'} in Drive didn't match any audio file`;
  const items = unmatched.map(row => {
    const name = row.filename || row.name || '';
    const kind = row.kind ? ` (${row.kind})` : '';
    const folder = row.folder_label ? ` — ${row.folder_label}` : '';
    return `<li>${esc(name)}${esc(kind)}${esc(folder)}</li>`;
  }).join('');
  box.hidden = false;
  box.innerHTML =
    `<p class="drive-orphan-heading">${esc(heading)}</p>`
    + (items ? `<ul class="drive-orphan-list">${items}</ul>` : '');
}

function setAudioSourceTab(tab) {
  const next = tab === 'drive' ? 'drive' : 'upload';
  const switched = next !== audioSourceTab;
  audioSourceTab = next;
  document.querySelectorAll('.source-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.sourceTab === audioSourceTab);
  });
  const uploadPane = document.getElementById('upload-manual-pane');
  const drivePane = document.getElementById('upload-drive-pane');
  if (uploadPane) uploadPane.hidden = audioSourceTab !== 'upload';
  if (drivePane) drivePane.hidden = audioSourceTab !== 'drive';
  if (!switched) return;
  const api = audioSelectionApi();
  if (!api.TAB_SWITCH_KEEPS_SELECTION) {
    audioSelectedKeys = api.selectionAfterSourceSwitch
      ? api.selectionAfterSourceSwitch(audioSelectedKeys, audioCatalog, audioSourceTab)
      : audioSelectedKeys.filter(key => {
          const item = audioCatalog.find(row => catalogItemId(row) === key);
          return item && (item.source || 'drive') === audioSourceTab;
        });
    syncDrivePickerToSelection();
  }
  renderRunFileList();
}

function syncDrivePickerToSelection() {
  const selectedIds = new Set(audioSelectedKeys);
  document.querySelectorAll('#multi-audio-checkboxes .audio-checkbox').forEach(cb => {
    let parsed;
    try {
      parsed = JSON.parse(cb.value);
    } catch (err) {
      cb.checked = false;
      return;
    }
    const item = audioCatalog.find(row =>
      (row.source || 'drive') === 'drive'
      && String(row.language || '') === String(parsed.language || '')
      && String(row.audio || '') === String(parsed.audio || '')
    );
    cb.checked = !!(item && selectedIds.has(catalogItemId(item)));
  });
}

function audioSelectionApi() {
  return window.MedsumAudioSelection || {};
}

function cachedDurationResults() {
  return []
    .concat(window._currentResultsUnfiltered || window._currentResults || [])
    .concat(window._historyResultsUnfiltered || window._historyResults || []);
}

function ingestDriveFiles(files) {
  const api = audioSelectionApi();
  const incoming = (files || [])
    .filter(f => (f.status === 'ready' || !f.status) && (f.audio || f.audio_filename))
    .map(f => ({
      source: 'drive',
      language: f.language || f.folder_label || '',
      audio: f.audio || f.audio_filename || '',
      folder_label: f.folder_label || '',
      duration: f.duration ?? f.audio_length ?? f.audio_duration_seconds ?? null,
      has_transcript: !!f.has_transcript,
      has_transcript_ground_truth: !!(f.has_transcript_ground_truth || f.has_transcript),
      has_soap_ground_truth: !!f.has_soap_ground_truth,
      has_summary_ground_truth: !!(f.has_summary_ground_truth || f.has_soap_ground_truth),
      has_translation_ground_truth: !!f.has_translation_ground_truth,
      has_json_ground_truth: !!f.has_json_ground_truth,
      transcript_filename: f.transcript_filename || '',
      soap_gt_filename: f.soap_gt_filename || '',
      translation_gt_filename: f.translation_gt_filename || '',
      is_english: !!f.is_english,
      file: null,
    }));
  audioCatalog = api.ingestIntoCatalog
    ? api.ingestIntoCatalog(audioCatalog, incoming)
    : audioCatalog.concat(incoming);
  applyGroundTruthMatches();
}

function catalogItemId(item) {
  const api = audioSelectionApi();
  return api.catalogId ? api.catalogId(item) : `${item.source}::${item.language}::${item.audio}`;
}

function populateAudioSelectors(files) {
  const multiDiv = document.getElementById('multi-audio-checkboxes');
  if (!multiDiv) return;

  ingestDriveFiles(files);
  const ready = audioCatalog.filter(item => (item.source || 'drive') === 'drive');

  multiDiv.innerHTML = '';

  const selectedIds = new Set(audioSelectedKeys);
  ready.forEach(f => {
    const language = f.language || '';
    const audio = f.audio || '';
    const payload = JSON.stringify({ language, audio });
    const labelText = `${language} — ${audio}`;

    const label = document.createElement('label');
    label.className = 'multi-audio-label';
    label.setAttribute('data-filter-text', labelText);
    label.setAttribute('data-language', language);
    label.setAttribute('data-audio', audio);
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'audio-checkbox';
    cb.value = payload;
    cb.checked = selectedIds.has(catalogItemId(f));
    cb.addEventListener('change', () => onDriveFileToggle(f, cb.checked));
    label.appendChild(cb);
    const name = document.createElement('span');
    name.textContent = labelText;
    label.appendChild(name);
    multiDiv.appendChild(label);
  });
  applyMultiAudioFilter(
    document.getElementById('multi-audio-filter')?.value || ''
  );

  audioSelectionReady = true;
  renderRunFileList();
}

function onDriveFileToggle(item, checked) {
  const id = catalogItemId(item);
  const api = audioSelectionApi();
  if (checked) {
    if (!audioSelectedKeys.includes(id)) audioSelectedKeys.push(id);
  } else {
    audioSelectedKeys = api.dropSelectedKey
      ? api.dropSelectedKey(audioSelectedKeys, id)
      : audioSelectedKeys.filter(key => key !== id);
  }
  renderRunFileList();
}

function multiAudioPickerRows() {
  return [...document.querySelectorAll('#multi-audio-checkboxes .multi-audio-label')];
}

function applyMultiAudioFilter(query) {
  const api = audioSelectionApi();
  const q = String(query || '');
  multiAudioPickerRows().forEach(row => {
    const item = {
      language: row.getAttribute('data-language') || '',
      audio: row.getAttribute('data-audio') || row.getAttribute('data-filter-text') || '',
    };
    const visible = api.filterMultiAudioItems
      ? api.filterMultiAudioItems([item], q).length > 0
      : !q.trim()
        || String(item.audio).toLowerCase().indexOf(q.trim().toLowerCase()) !== -1;
    row.hidden = !visible;
  });
}

function onMultiAudioFilterInput(input) {
  applyMultiAudioFilter(input?.value || '');
}

function setVisibleMultiAudioChecked(checked) {
  multiAudioPickerRows().forEach(row => {
    if (row.hidden) return;
    const cb = row.querySelector('.audio-checkbox');
    if (!cb) return;
    cb.checked = Boolean(checked);
    try {
      const parsed = JSON.parse(cb.value);
      const item = audioCatalog.find(rowItem =>
        (rowItem.source || 'drive') === 'drive'
        && rowItem.language === parsed.language
        && rowItem.audio === parsed.audio
      );
      if (item) onDriveFileToggle(item, Boolean(checked));
    } catch (_) { /* ignore */ }
  });
  renderRunFileList();
}

function selectAllMultiAudio() {
  setVisibleMultiAudioChecked(true);
}

function clearMultiAudio() {
  setVisibleMultiAudioChecked(false);
}

function selectedCatalogItems() {
  const allow = new Set(audioSelectedKeys);
  return audioCatalog.filter(item => allow.has(catalogItemId(item)));
}

function getSelectedAudios() {
  const api = audioSelectionApi();
  const selected = selectedCatalogItems().map(item => {
    if (item.language) return item;
    if ((item.source || 'drive') === 'upload') return item;
    const driveHit = audioCatalog.find(row =>
      (row.source || 'drive') === 'drive'
      && String(row.audio || '').toLowerCase() === String(item.audio || '').toLowerCase()
    );
    return driveHit ? { ...item, language: driveHit.language } : item;
  });
  if (api.runPayload) return api.runPayload(selected);
  if (api.drivePayload) return api.drivePayload(selected);
  return selected
    .filter(item => (item.source || 'drive') === 'drive')
    .map(item => ({ language: item.language, audio: item.audio }));
}

function applyGroundTruthMatches() {
  const api = audioSelectionApi();
  if (!api.attachGroundTruths) return;
  audioCatalog = audioCatalog.map(item => api.attachGroundTruths(item, pendingGtFiles));
}

let _manualUploadQueue = Promise.resolve();

function accHandleLocalAudio(input) {
  const files = [...(input.files || [])];
  input.value = '';
  _manualUploadQueue = _manualUploadQueue.then(() => ingestDroppedFiles(files));
}

async function uploadManualAudioFile(file) {
  // Choice (a): send bytes on drop so Run Batch Test stays JSON (Drive path
  // unchanged). The returned upload_id is what runPayload attaches.
  const fd = new FormData();
  fd.append('file', file);
  const resp = await fetch(`${API}/upload-audio`, { method: 'POST', body: fd });
  let data = {};
  try {
    data = await resp.json();
  } catch (_) { /* empty */ }
  if (!resp.ok) {
    throw new Error(data.error || `HTTP ${resp.status}`);
  }
  return data;
}

async function ingestDroppedFiles(files) {
  const api = audioSelectionApi();
  const list = files || [];
  const audioFiles = [];
  list.forEach(file => {
    const name = file && file.name ? file.name : '';
    const kind = api.classifyUpload ? api.classifyUpload(name) : 'unknown';
    if (kind === 'audio') {
      audioFiles.push(file);
    } else if (kind && kind !== 'unknown') {
      pendingGtFiles.push({ filename: name, name, file, kind });
    }
  });
  const audios = [];
  for (let i = 0; i < audioFiles.length; i++) {
    const file = audioFiles[i];
    const name = file && file.name ? file.name : '';
    try {
      const stored = await uploadManualAudioFile(file);
      audios.push({
        source: 'upload',
        language: stored.language || '',
        audio: stored.filename || name,
        upload_id: stored.upload_id,
        duration: api.durationSecondsForItem
          ? api.durationSecondsForItem({ audio: stored.filename || name }, cachedDurationResults())
          : null,
        file,
      });
    } catch (err) {
      showToast(`Could not upload ${name}: ${err.message}`, 'warning');
    }
  }
  if (audios.length) {
    audioCatalog = api.ingestIntoCatalog
      ? api.ingestIntoCatalog(audioCatalog, audios)
      : audioCatalog.concat(audios);
    audios.forEach(item => {
      const id = catalogItemId(item);
      if (!audioSelectedKeys.includes(id)) audioSelectedKeys.push(id);
    });
  }
  applyGroundTruthMatches();
  renderRunFileList();
}

function bindUploadDropZone() {
  const zone = document.getElementById('gt-drop-zone');
  const input = document.getElementById('acc-local-audio');
  if (!zone || zone.dataset.dropBound === '1') return;
  zone.dataset.dropBound = '1';
  let skipClick = false;
  ['dragenter', 'dragover'].forEach(type => {
    zone.addEventListener(type, event => {
      event.preventDefault();
      zone.classList.add('is-dragover');
    });
  });
  ['dragleave', 'drop'].forEach(type => {
    zone.addEventListener(type, event => {
      event.preventDefault();
      if (type === 'dragleave') zone.classList.remove('is-dragover');
    });
  });
  zone.addEventListener('drop', event => {
    event.preventDefault();
    zone.classList.remove('is-dragover');
    skipClick = true;
    const dropped = [...(event.dataTransfer?.files || [])];
    _manualUploadQueue = _manualUploadQueue.then(() => ingestDroppedFiles(dropped));
  });
  zone.addEventListener('click', () => {
    if (skipClick) {
      skipClick = false;
      return;
    }
    input?.click();
  });
}

const NO_RUN_FILES_MESSAGE =
  'No audio files left in this run. Add a file before running tests.';

const MISSING_LANGUAGE_RUN_MESSAGE =
  'Set a language for each uploaded audio file before running tests.';

const RUN_BATCH_LABEL = 'Run Batch Test';

function bindRunFileListClicks() {
  const hosts = [
    document.getElementById('selected-files-tbody'),
  ].filter(Boolean);
  hosts.forEach(list => {
    if (list.dataset.excludeBound === '1') return;
    list.dataset.excludeBound = '1';
    list.addEventListener('click', event => {
      const viewBtn = event.target.closest('[data-view-run-file]');
      if (viewBtn && list.contains(viewBtn)) {
        event.preventDefault();
        event.stopPropagation();
        viewSelectedRunFile(viewBtn.getAttribute('data-view-run-file') || '');
        return;
      }
      const editBtn = event.target.closest('[data-edit-gt], [data-enter-manual-gt]');
      if (editBtn && list.contains(editBtn)) {
        event.preventDefault();
        event.stopPropagation();
        openManualGtEditor(
          editBtn.getAttribute('data-edit-gt')
          || editBtn.getAttribute('data-enter-manual-gt')
          || ''
        );
        return;
      }
      const btn = event.target.closest('[data-exclude-audio]');
      if (!btn || !list.contains(btn)) return;
      event.preventDefault();
      event.stopPropagation();
      accExcludeRunAudio(btn.getAttribute('data-exclude-audio') || '');
    });
    list.addEventListener('change', event => {
      const select = event.target.closest('[data-upload-language]');
      if (!select || !list.contains(select)) return;
      accSetUploadLanguage(
        select.getAttribute('data-upload-language') || '',
        select.value
      );
    });
  });
}

function accSetUploadLanguage(id, language) {
  const api = audioSelectionApi();
  if (!api.setUploadLanguage) return;
  const next = api.setUploadLanguage(audioCatalog, audioSelectedKeys, id, language);
  audioCatalog = next.catalog || audioCatalog;
  audioSelectedKeys = next.selectedKeys || audioSelectedKeys;
  renderRunFileList();
}

function uncheckDriveFileInModeUi(item) {
  if (!item || (item.source && item.source !== 'drive')) return;
  const payload = JSON.stringify({
    language: item.language || '',
    audio: item.audio || '',
  });
  document.querySelectorAll('.audio-checkbox').forEach(cb => {
    if (cb.value === payload) cb.checked = false;
  });
}

function accExcludeRunAudio(id) {
  const targetId = String(id || '');
  if (!targetId) return;
  const api = audioSelectionApi();
  const target = audioCatalog.find(item => catalogItemId(item) === targetId);
  audioSelectedKeys = api.dropSelectedKey
    ? api.dropSelectedKey(audioSelectedKeys, targetId)
    : audioSelectedKeys.filter(key => key !== targetId);
  uncheckDriveFileInModeUi(target);
  renderRunFileList();
}

function clearAllSelectedFiles() {
  const api = audioSelectionApi();
  const keys = audioSelectedKeys.slice();
  keys.forEach(id => {
    const target = audioCatalog.find(item => catalogItemId(item) === id);
    uncheckDriveFileInModeUi(target);
    audioSelectedKeys = api.dropSelectedKey
      ? api.dropSelectedKey(audioSelectedKeys, id)
      : audioSelectedKeys.filter(key => key !== id);
  });
  renderRunFileList();
}

function resolveTestIdForAudio(filename) {
  const api = testCaseViewApi();
  const want = String(filename || '').trim().toLowerCase();
  if (!want) return '';
  const pools = [
    window._currentResultsUnfiltered || window._currentResults || [],
    window._historyResultsUnfiltered || window._historyResults || [],
  ];
  for (let p = 0; p < pools.length; p++) {
    const rows = pools[p] || [];
    for (let i = rows.length - 1; i >= 0; i--) {
      const name = String(rows[i].audio_filename || rows[i].filename || '').trim().toLowerCase();
      if (name !== want) continue;
      if (api.stableTestId) {
        const id = api.stableTestId(rows[i]);
        if (id) return id;
      }
      if (rows[i].test_id) return String(rows[i].test_id);
    }
  }
  return '';
}

function viewSelectedRunFile(catalogIdValue) {
  const item = audioCatalog.find(row => catalogItemId(row) === catalogIdValue);
  const testId = (item && item.test_id) || resolveTestIdForAudio(item && item.audio);
  if (testId) {
    openTestDetail(testId);
    return;
  }
  showToast('No saved result for this file yet. Run the test to open the detail view.');
}

function gtStatusPillHtml(status, label) {
  const mark = status === 'complete' ? '✓ '
    : (status === 'missing_gt_all' || status === 'missing_language' ? '× ' : '');
  return `<span class="gt-status-pill ${esc(status)}">${mark}${esc(label)}</span>`;
}

function audioFileCellHtml(row, id) {
  const name = esc(row.audio_file || '—');
  if (row.source !== 'upload') return `<td>${name}</td>`;
  const api = audioSelectionApi();
  const labels = api.supportedLanguageLabels ? api.supportedLanguageLabels() : [];
  const current = String(row.language || '');
  const options = ['<option value="">Select language</option>'].concat(
    labels.map(label => {
      const selected = current.toLowerCase() === String(label).toLowerCase()
        ? ' selected' : '';
      return `<option value="${esc(label)}"${selected}>${esc(label)}</option>`;
    })
  ).join('');
  return `<td>
    <div class="run-audio-file-cell">
      <span>${name}</span>
      <select class="run-lang-select" data-upload-language="${esc(id)}"
              aria-label="Language for ${name}">
        ${options}
      </select>
    </div>
  </td>`;
}

function groundTruthCellHtml(row, id) {
  if (row.has_manual_gt || row.ground_truth === 'Manual') {
    return `<td class="gt-cell"><span class="manual-gt-badge">Manual</span></td>`;
  }
  if (row.enter_manually || row.ground_truth === '—') {
    return `<td class="gt-cell">
      <span>—</span>
      <button type="button" class="enter-manual-gt" data-enter-manual-gt="${esc(id)}">
        + Enter manually
      </button>
    </td>`;
  }
  return `<td class="gt-cell">${esc(row.ground_truth)}</td>`;
}

function renderRunFileList() {
  bindRunFileListClicks();
  const api = audioSelectionApi();
  applyGroundTruthMatches();
  const selected = selectedCatalogItems();
  const empty = document.getElementById('run-file-empty');
  if (empty) empty.style.display = selected.length ? 'none' : '';
  const badge = document.getElementById('files-selected-badge');
  if (badge) {
    const n = selected.length;
    badge.textContent = `${n} File${n === 1 ? '' : 's'} Selected`;
  }

  const tbody = document.getElementById('selected-files-tbody');
  if (tbody) {
    const results = cachedDurationResults();
    tbody.innerHTML = selected.map((item, idx) => {
      const id = catalogItemId(item);
      const row = api.selectedFilesTableRow
        ? api.selectedFilesTableRow(item, idx + 1, results)
        : {
            index: idx + 1,
            audio_file: item.audio || '—',
            duration: '—',
            ground_truth: item.ground_truth_filename || '—',
            status: item.gt_status || 'missing_gt_all',
            status_label: item.gt_status_label || 'Missing GT (All)',
            source: item.source || 'drive',
            language: item.language || '',
          };
      const testId = resolveTestIdForAudio(item.audio);
      return `<tr data-run-file="${esc(id)}">
        <td>${row.index}</td>
        ${audioFileCellHtml(row, id)}
        <td>${esc(row.duration)}</td>
        ${groundTruthCellHtml(row, id)}
        <td>${gtStatusPillHtml(row.status, row.status_label)}</td>
        <td>
          <span class="selected-file-actions">
            <button type="button" class="icon-action view" data-view-run-file="${esc(id)}"
                    data-open-test-id="${esc(testId)}" title="View" aria-label="View">
              <svg viewBox="0 0 16 16" aria-hidden="true">
                <path d="M8 3C4.5 3 1.7 5.1 1 8c.7 2.9 3.5 5 7 5s6.3-2.1 7-5c-.7-2.9-3.5-5-7-5zm0 8a3 3 0 1 1 0-6 3 3 0 0 1 0 6z"/>
              </svg>
            </button>
            <button type="button" class="icon-action edit" data-edit-gt="${esc(id)}"
                    title="Edit Ground Truth" aria-label="Edit Ground Truth">
              <svg viewBox="0 0 16 16" aria-hidden="true">
                <path d="M11.7 1.3a1 1 0 0 1 1.4 0l1.6 1.6a1 1 0 0 1 0 1.4L6 13H3v-3l8.7-8.7zM4.5 11.5 11 5l1.5 1.5-6.5 6.5H4.5v-1.5z"/>
              </svg>
            </button>
            <button type="button" class="icon-action delete" data-exclude-audio="${esc(id)}"
                    title="Remove from this run" aria-label="Delete">
              <svg viewBox="0 0 16 16" aria-hidden="true">
                <path d="M6 2h4l.5 1H14v1H2V3h3.5L6 2zm1 4v6H6V6h1zm3 0v6H9V6h1zM3.5 5h9l-.6 8.2A1 1 0 0 1 11.9 14H4.1a1 1 0 0 1-1-.8L2.5 5z"/>
              </svg>
            </button>
          </span>
        </td>
      </tr>`;
    }).join('');
  }
  updateRunButtonState();
}

function updateRunButtonState() {
  const btn = document.getElementById('run-all-btn');
  if (!btn || btn.dataset.running === '1') return;
  const api = audioSelectionApi();
  const selected = selectedCatalogItems();
  const none = selected.length === 0;
  const missingLang = api.missingLanguageUploads
    ? api.missingLanguageUploads(selected).length > 0
    : selected.some(item => api.uploadNeedsLanguage && api.uploadNeedsLanguage(item));
  btn.disabled = none || missingLang;
  btn.title = none
    ? NO_RUN_FILES_MESSAGE
    : (missingLang ? MISSING_LANGUAGE_RUN_MESSAGE : '');
}

let gtEditCatalogId = '';
let gtSoapMode = 'form';
let gtSoapDraft = {};
let gtSoapCriticality = {};
let gtSoapJsonShape = 'facts';

function gtEditModal() {
  return document.getElementById('gt-edit-modal');
}

function resetSoapGtEditorState() {
  gtSoapMode = 'form';
  gtSoapDraft = {};
  gtSoapCriticality = {};
  gtSoapJsonShape = 'facts';
  const jsonInput = document.getElementById('gt-edit-soap-json');
  if (jsonInput) {
    jsonInput.value = '';
    jsonInput.classList.remove('is-invalid');
    jsonInput.removeAttribute('aria-invalid');
  }
  showSoapGtMessages([], []);
  applySoapGtModeUi('form');
}

function closeManualGtEditor() {
  const modal = gtEditModal();
  if (modal) modal.hidden = true;
  gtEditCatalogId = '';
  resetSoapGtEditorState();
}

function soapEditorHtml(soap) {
  const api = audioSelectionApi();
  const fields = api.soapEditorFields
    ? api.soapEditorFields(api.SOAP_CONSULT_TEMPLATE)
    : [];
  const data = soap || {};
  const bySection = {};
  fields.forEach(field => {
    const section = field.section || 'summary';
    if (!bySection[section]) bySection[section] = [];
    const value = api.getSoapAtPath ? api.getSoapAtPath(data, field.path) : '';
    const structured = value != null && typeof value === 'object';
    const shown = structured || value == null ? '' : String(value);
    bySection[section].push(
      `<div class="gt-edit-soap-field">
        <label for="gt-soap-${esc(field.path)}">${esc(field.label)}</label>
        <textarea id="gt-soap-${esc(field.path)}" class="gt-edit-soap-input" rows="2"
                  data-soap-path="${esc(field.path)}"${structured ? ' data-soap-structured="1"' : ''}>${esc(shown)}</textarea>
      </div>`
    );
  });
  return Object.keys(bySection).map(section => `
    <div class="gt-edit-soap-section">
      <h4>${esc(section)}</h4>
      ${bySection[section].join('')}
    </div>
  `).join('');
}

function fillSoapFormFromSoap(soap) {
  const soapHost = document.getElementById('gt-edit-soap-fields');
  if (soapHost) soapHost.innerHTML = soapEditorHtml(soap);
}

function collectSoapNested() {
  const api = audioSelectionApi();
  const soap = api.cloneSoap ? api.cloneSoap(gtSoapDraft) || {} : Object.assign({}, gtSoapDraft || {});
  document.querySelectorAll('#gt-edit-soap-fields [data-soap-path]').forEach(input => {
    if (!api.setSoapAtPath) return;
    const path = input.getAttribute('data-soap-path');
    const existing = api.getSoapAtPath ? api.getSoapAtPath(soap, path) : null;
    const typed = input.value;
    if (
      existing != null
      && typeof existing === 'object'
      && !String(typed || '').trim()
    ) {
      return;
    }
    api.setSoapAtPath(soap, path, typed);
  });
  return soap;
}

function showSoapGtMessages(errors, warnings) {
  const host = document.getElementById('gt-edit-soap-messages');
  const jsonInput = document.getElementById('gt-edit-soap-json');
  const errs = (errors || []).filter(Boolean);
  const warns = (warnings || []).filter(Boolean);
  if (jsonInput) {
    jsonInput.classList.toggle('is-invalid', !!errs.length);
    if (errs.length) jsonInput.setAttribute('aria-invalid', 'true');
    else jsonInput.removeAttribute('aria-invalid');
  }
  if (!host) return;
  if (!errs.length && !warns.length) {
    host.hidden = true;
    host.innerHTML = '';
    return;
  }
  host.hidden = false;
  let html = '';
  if (errs.length) {
    html += `<ul class="gt-edit-soap-errors">${errs.map(msg => `<li>${esc(msg)}</li>`).join('')}</ul>`;
  }
  if (warns.length) {
    html += `<ul class="gt-edit-soap-warnings">${warns.map(msg => `<li>${esc(msg)}</li>`).join('')}</ul>`;
  }
  host.innerHTML = html;
}

function applySoapGtModeUi(mode) {
  const formPane = document.getElementById('gt-edit-soap-form-pane');
  const jsonPane = document.getElementById('gt-edit-soap-json-pane');
  if (formPane) formPane.hidden = mode !== 'form';
  if (jsonPane) jsonPane.hidden = mode !== 'json';
  document.querySelectorAll('[data-soap-gt-mode]').forEach(btn => {
    const active = btn.getAttribute('data-soap-gt-mode') === mode;
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function setSoapGtMode(next) {
  const wanted = next === 'json' ? 'json' : 'form';
  const api = audioSelectionApi();
  if (wanted === gtSoapMode) {
    applySoapGtModeUi(wanted);
    return true;
  }
  if (gtSoapMode === 'form' && wanted === 'json') {
    const soap = collectSoapNested();
    gtSoapDraft = soap;
    const converted = api.soapToFactsJson
      ? api.soapToFactsJson(soap, gtSoapCriticality, null, gtSoapJsonShape === 'nested')
      : { text: JSON.stringify({ facts: [] }, null, 2), warnings: [] };
    if (converted.shape) gtSoapJsonShape = converted.shape;
    const jsonInput = document.getElementById('gt-edit-soap-json');
    if (jsonInput) jsonInput.value = converted.text;
    showSoapGtMessages([], converted.warnings || []);
  } else if (gtSoapMode === 'json' && wanted === 'form') {
    const jsonInput = document.getElementById('gt-edit-soap-json');
    const parsed = api.parseSoapFactsJson
      ? api.parseSoapFactsJson(jsonInput ? jsonInput.value : '')
      : { ok: true, soap: null, warnings: [], errors: [], criticalityByPath: {} };
    if (!parsed.ok) {
      showSoapGtMessages(parsed.errors || [], parsed.warnings || []);
      applySoapGtModeUi('json');
      jsonInput?.focus();
      return false;
    }
    gtSoapDraft = parsed.soap || {};
    gtSoapCriticality = parsed.criticalityByPath || {};
    gtSoapJsonShape = parsed.shape || 'facts';
    fillSoapFormFromSoap(gtSoapDraft);
    showSoapGtMessages([], parsed.warnings || []);
  }
  gtSoapMode = wanted;
  applySoapGtModeUi(wanted);
  return true;
}

function openManualGtEditor(catalogIdValue) {
  const id = String(catalogIdValue || '');
  const item = audioCatalog.find(row => catalogItemId(row) === id);
  const modal = gtEditModal();
  if (!id || !item || !modal) return;
  const api = audioSelectionApi();
  gtEditCatalogId = id;
  resetSoapGtEditorState();
  const title = document.getElementById('gt-edit-title');
  const existing = (api.hasManualGt && api.hasManualGt(item))
    || String(item.ground_truth_filename || '').trim();
  if (title) title.textContent = existing ? 'Edit Ground Truth' : 'Enter Ground Truth';
  const filename = document.getElementById('gt-edit-filename');
  if (filename) filename.textContent = item.audio || item.audio_filename || '';
  const mg = api.normalizeManualGt ? api.normalizeManualGt(item.manual_gt) : {};
  const transcription = document.getElementById('gt-edit-transcription');
  const translation = document.getElementById('gt-edit-translation');
  if (transcription) {
    transcription.value = mg.transcription
      || item.ground_truth_transcription
      || item.ground_truth
      || '';
  }
  if (translation) {
    translation.value = mg.translation || item.translation_ground_truth || '';
  }
  const soap = mg.soap || item.soap_ground_truth || {};
  gtSoapDraft = api.cloneSoap ? (api.cloneSoap(soap) || {}) : Object.assign({}, soap || {});
  gtSoapJsonShape = (api.soapHasStructuredLeaves && api.soapHasStructuredLeaves(gtSoapDraft))
    ? 'nested'
    : 'facts';
  fillSoapFormFromSoap(gtSoapDraft);
  modal.hidden = false;
  transcription?.focus();
}

function collectManualGtForm() {
  const transcription = document.getElementById('gt-edit-transcription')?.value || '';
  const translation = document.getElementById('gt-edit-translation')?.value || '';
  const api = audioSelectionApi();
  if (gtSoapMode === 'json') {
    const jsonInput = document.getElementById('gt-edit-soap-json');
    const parsed = api.parseSoapFactsJson
      ? api.parseSoapFactsJson(jsonInput ? jsonInput.value : '')
      : { ok: false, errors: ['SOAP JSON parser is unavailable'] };
    if (!parsed.ok) {
      return { error: true, errors: parsed.errors || [], warnings: parsed.warnings || [] };
    }
    gtSoapDraft = parsed.soap || {};
    gtSoapCriticality = parsed.criticalityByPath || {};
    gtSoapJsonShape = parsed.shape || 'facts';
    return {
      transcription,
      translation,
      soap: parsed.soap,
      warnings: parsed.warnings || [],
    };
  }
  return {
    transcription,
    translation,
    soap: collectSoapNested(),
  };
}

function saveManualGtEditor() {
  const id = gtEditCatalogId;
  const item = audioCatalog.find(row => catalogItemId(row) === id);
  const api = audioSelectionApi();
  if (!id || !item || !api.applyManualGroundTruth) return;
  const collected = collectManualGtForm();
  if (collected.error) {
    showSoapGtMessages(collected.errors || [], collected.warnings || []);
    return;
  }
  const updated = api.applyManualGroundTruth(item, {
    transcription: collected.transcription,
    translation: collected.translation,
    soap: collected.soap,
  });
  audioCatalog = audioCatalog.map(row => (catalogItemId(row) === id ? updated : row));
  closeManualGtEditor();
  renderRunFileList();
}

function bindGtEditModal() {
  const modal = gtEditModal();
  if (!modal || modal.dataset.gtBound === '1') return;
  modal.dataset.gtBound = '1';
  const form = document.getElementById('gt-edit-form');
  form?.addEventListener('submit', event => {
    event.preventDefault();
    saveManualGtEditor();
  });
  modal.querySelectorAll('[data-gt-edit-cancel]').forEach(btn => {
    btn.addEventListener('click', event => {
      event.preventDefault();
      closeManualGtEditor();
    });
  });
  modal.querySelectorAll('[data-soap-gt-mode]').forEach(btn => {
    btn.addEventListener('click', event => {
      event.preventDefault();
      setSoapGtMode(btn.getAttribute('data-soap-gt-mode') || 'form');
    });
  });
  modal.addEventListener('click', event => {
    if (event.target === modal) closeManualGtEditor();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && modal && !modal.hidden) {
      event.stopPropagation();
      closeManualGtEditor();
    }
  });
}


const RESULTS_TABLE_COLSPAN = 6;

function resultsTableApi() {
  return window.MedsumResultsTable || {};
}

function resultsTableColspan(source) {
  const api = resultsTableApi();
  const tab = api.getState ? api.getState(source).tab : 'results';
  const headers = api.visibleHeaders
    ? api.visibleHeaders(tab)
    : ['Test Case ID', 'Audio File', 'Language', 'SOAP accuracy', 'Clinical Quality', 'Execution Status'];
  return headers.length || RESULTS_TABLE_COLSPAN;
}

function cachedRowsForSource(source) {
  if (source === 'history') {
    return window._historyResultsUnfiltered || window._historyResults || [];
  }
  return window._currentResultsUnfiltered || window._currentResults || [];
}

function emptyStateRow(colspan, subtext) {
  return `
            <tr>
                <td colspan="${colspan}" class="empty-state-cell">
                    <div class="empty-state">
                        <span class="empty-icon">📋</span>
                        <p>No tests run yet. Select audio files and click Run All Tests.</p>
                        <p class="empty-sub">${subtext}</p>
                    </div>
                </td>
            </tr>`;
}

function formatRowStatus(r) {
  if (r && r.execution_display && r.soap_evaluation_display) {
    const status = String(r.status || '').trim().toLowerCase();
    const verdict = String(r.final_result || '').trim().toLowerCase();
    const unscored = status === 'complete' && (
      r.accuracy_skipped || verdict === 'complete_no_accuracy'
    );
    return {
      execution: unscored
        ? { label: 'NOT_SCORED', css: 'not-scored' }
        : r.execution_display,
      evaluation: r.soap_evaluation_display,
      quality: r.clinical_quality_display || qualityFromEvaluation(r.soap_evaluation_display, r),
    };
  }
  return computeRowDisplay(r);
}

function computeRowDisplay(r) {
  const status = String(r?.status || '').trim().toLowerCase();
  const verdict = String(r?.final_result || '').trim().toLowerCase();
  const executionMap = {
    pending: { label: 'Not evaluated', css: 'not-evaluated' },
    running: { label: 'Not evaluated', css: 'not-evaluated' },
    complete: { label: 'Completed', css: 'completed' },
    failed: { label: 'Error', css: 'error' },
    skipped: { label: 'Not evaluated', css: 'not-evaluated' },
  };
  let execution = executionMap[status]
    || { label: 'Not evaluated', css: 'not-evaluated' };
  if (status === 'complete' && (r?.accuracy_skipped || verdict === 'complete_no_accuracy')) {
    execution = { label: 'NOT_SCORED', css: 'not-scored' };
  }

  const emptyEval = { label: '—', css: 'no-eval', percent: '', facts: '', text: '—', show: false };
  const emptyQuality = { label: '—', css: 'empty', show: false };
  if (status === 'failed' || verdict === 'failed' || status === 'skipped' || verdict === 'skipped') {
    return { execution, evaluation: emptyEval, quality: emptyQuality };
  }
  if (status === 'pending' || status === 'running' || verdict === 'pending') {
    return {
      execution,
      evaluation: { label: '—', css: 'muted', percent: '', facts: '', text: '—', show: true },
      quality: emptyQuality,
    };
  }
  const bands = {
    pass: { label: 'High accuracy', css: 'high-accuracy' },
    review: { label: 'Needs review', css: 'needs-review' },
    fail: { label: 'Low accuracy', css: 'low-accuracy' },
    complete_no_accuracy: { label: 'NOT_SCORED', css: 'not-scored' },
  };
  const band = bands[verdict] || (r?.accuracy_skipped
    ? bands.complete_no_accuracy
    : { label: 'NOT_SCORED', css: 'not-scored' });
  const soap = soapEvalFromRow(r);
  const percent = band.css === 'not-scored' ? '' : soap.percent;
  const facts = band.css === 'not-scored' ? '' : soap.facts;
  const parts = [percent, band.label].filter(Boolean);
  let text = parts.join(' · ') || band.label;
  if (facts) text = `${text} · ${facts}`;
  const evaluation = {
    label: band.label,
    css: band.css,
    percent,
    facts,
    text,
    show: true,
    percent_value: band.css === 'not-scored' ? null : soap.value,
  };
  return {
    execution,
    evaluation,
    quality: qualityFromEvaluation(evaluation, r),
  };
}

const CLINICAL_QUALITY_BANDS = { acceptable: 90, minor: 80, moderate: 70 };

function qualityFromEvaluation(evaluation, r) {
  const empty = { label: '—', css: 'empty', show: false };
  const ev = evaluation || {};
  if (!ev.show || ev.css === 'not-scored' || ev.css === 'muted' || ev.css === 'no-eval') {
    return empty;
  }
  const score = ev.percent_value != null ? Number(ev.percent_value) : NaN;
  if (!Number.isFinite(score)) return empty;
  let key = 'major';
  if (score >= CLINICAL_QUALITY_BANDS.acceptable) key = 'acceptable';
  else if (score >= CLINICAL_QUALITY_BANDS.minor) key = 'minor';
  else if (score >= CLINICAL_QUALITY_BANDS.moderate) key = 'moderate';
  const map = {
    acceptable: { label: 'Clinically Acceptable', css: 'acceptable' },
    minor: { label: 'Minor Deviation', css: 'minor' },
    moderate: { label: 'Moderate Deviation', css: 'moderate' },
    major: { label: 'Major Deviation', css: 'major' },
  };
  return { ...map[key], show: true };
}

function soapEvalFromRow(r) {
  const soap = r?.soap_comparison || {};
  const pair = soap.gt_vs_generated && typeof soap.gt_vs_generated === 'object'
    ? soap.gt_vs_generated
    : soap;
  const metrics = pair.metrics && typeof pair.metrics === 'object' ? pair.metrics : {};
  const scores = soap.scores && typeof soap.scores === 'object' ? soap.scores : {};
  let pct = pair.overall_weighted_clinical_score ?? pair.similarity_score
    ?? scores.gt_vs_generated ?? metrics.overall_weighted_clinical_score
    ?? r?.accuracy_score ?? r?.similarity_score;
  const n = pct == null || pct === '' ? NaN : Number(pct);
  const percent = Number.isFinite(n) ? `${Math.round(n)}%` : '';
  const facts = soapFactLine(pair, metrics);
  return { percent, facts, value: Number.isFinite(n) ? n : null };
}

function soapFactLine(pair, metrics) {
  const viewApi = testCaseViewApi();
  if (typeof viewApi.soapFactCounts === 'function') {
    const counts = viewApi.soapFactCounts({ soap_comparison: pair || {} });
    if (counts) {
      return ['Correct', 'Incorrect', 'Missing', 'Hallucination']
        .map(key => `${key} ${counts[key]}`)
        .join(' · ');
    }
    return '';
  }
  const facts = pair && Array.isArray(pair.facts) ? pair.facts : [];
  const counts = { Correct: 0, Incorrect: 0, Missing: 0, Hallucination: 0 };
  let found = false;
  facts.forEach(row => {
    const kind = row && (row.result || row.type);
    const key = String(kind || '').replace(/_/g, ' ');
    const titled = key.charAt(0).toUpperCase() + key.slice(1).toLowerCase();
    const mapped = titled === 'Extra' ? 'Hallucination'
      : (titled === 'Missing detail' || titled === 'Removed in final' ? 'Missing' : titled);
    if (mapped === 'NA' || mapped === 'N/A') return;
    const label = mapped || 'Correct';
    if (label in counts) {
      counts[label] += 1;
      found = true;
    }
  });
  if (!found && metrics && (metrics.correct_count != null || metrics.missing_count != null
      || metrics.hallucination_count != null || metrics.captured_count != null)) {
    counts.Correct = Number(metrics.correct_count) || 0;
    counts.Missing = Number(metrics.missing_count) || 0;
    counts.Hallucination = Number(metrics.hallucination_count) || 0;
    counts.Incorrect = metrics.incorrect_count != null
      ? Number(metrics.incorrect_count) || 0
      : Math.max(0, (Number(metrics.captured_count) || 0) - counts.Correct
        - counts.Hallucination);
    found = true;
  }
  if (!found) return '';
  return ['Correct', 'Incorrect', 'Missing', 'Hallucination']
    .map(key => `${key} ${counts[key]}`)
    .join(' · ');
}

function chipSpan(chip, kind) {
  const label = chip && chip.label != null ? chip.label : '—';
  const css = chip && chip.css ? chip.css : 'muted';
  return `<span class="status-chip status-${esc(css)}" data-chip="${esc(kind)}">${esc(label)}</span>`;
}

function statusChipHtml(r) {
  return executionChipHtml(r);
}

function executionChipHtml(r) {
  const shown = formatRowStatus(r);
  const reason = String(
    r?.accuracy_skip_reason
    || (Array.isArray(r?.errors) ? r.errors[0] : '')
    || ''
  ).trim();
  const chip = shown.execution || {};
  const title = reason && (chip.css === 'error' || r?.accuracy_skipped)
    ? ` title="${esc(reason)}"`
    : '';
  const label = chip.label != null ? chip.label : '—';
  const css = chip.css ? chip.css : 'muted';
  return `<span class="status-chip status-${esc(css)}" data-chip="execution"${title}>${esc(label)}</span>`;
}

function clinicalQualityChipHtml(r) {
  const shown = formatRowStatus(r);
  const q = shown.quality || { label: '—', css: 'empty', show: false };
  if (!q.show) {
    return `<span class="table-empty" data-chip="clinical-quality">—</span>`;
  }
  return `<span class="quality-chip quality-${esc(q.css)}" data-chip="clinical-quality">${esc(q.label)}</span>`;
}

function soapAccuracyScore(r) {
  if (r && r.soap_accuracy_display && r.soap_accuracy_display.percent_value != null) {
    const n = Number(r.soap_accuracy_display.percent_value);
    return Number.isFinite(n) ? n : null;
  }
  const soap = r?.soap_comparison || {};
  const pair = soap.gt_vs_generated && typeof soap.gt_vs_generated === 'object'
    ? soap.gt_vs_generated
    : soap;
  const metrics = pair.metrics && typeof pair.metrics === 'object' ? pair.metrics : {};
  const scores = soap.scores && typeof soap.scores === 'object' ? soap.scores : {};
  const pct = pair.overall_weighted_clinical_score ?? pair.similarity_score
    ?? scores.gt_vs_generated ?? metrics.overall_weighted_clinical_score;
  const n = pct == null || pct === '' ? NaN : Number(pct);
  return Number.isFinite(n) ? n : null;
}

function soapAccuracyPillHtml(r, pillId) {
  const score = soapAccuracyScore(r);
  const tip = accuracyTooltipModel(r, 'soap');
  if (score == null) {
    const soap = (r && r.soap_comparison) || {};
    const display = r && r.soap_accuracy_display;
    const reason = String(
      (display && display.skipped && display.label)
      || soap.skip_reason
      || ''
    ).trim();
    if (reason) {
      return `<span class="table-empty soap-acc-skipped" title="${esc(reason)}">${esc(reason)}</span>`;
    }
    return `<span class="table-empty" title="SOAP accuracy">—</span>`;
  }
  const n = Math.round(Number(score));
  return scorePill(score, tip, 'SOAP accuracy', pillId, {
    tooltip: tip,
    shownText: `<span class="acc-value">${n}%</span><span class="acc-unit">accuracy</span>`,
    hideLabel: true,
    extraClass: 'soap-acc-chip',
  });
}

function renderEmptyResultsState() {
  paintResultsTable('dashboard', []);
  paintResultsTable('history', []);
}

function resultTimestampMs(r) {
  const raw = r?.created_at || r?.completed_at || r?.timestamp || 0;
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? 0 : d.getTime();
}

async function fetchAllResults(force = false) {
  if (!force && allResultsCache !== null) {
    return allResultsCache;
  }
  const resp = await fetch(`${API}/results`);
  if (!resp.ok) return allResultsCache || [];
  const items = await resp.json();
  allResultsCache = items;
  latestResults = items;
  return items;
}

async function onHistoryFilterChange() {
  const filter = document.getElementById('history-filter')?.value || 'current';

  if (filter === 'current') {
    if (currentBatchId) {
      await pollBatch(currentBatchId);
    } else {
      paintResultsTable('history', []);
      applyAccRunStats({ total: 0, passed: 0, failed: 0, avg: null, rate: 0 }, false);
    }
    return;
  }

  const data = await fetchAllResults();
  const now = Date.now();
  const day = 86400000;
  let filtered = data;
  if (filter === 'today') {
    filtered = data.filter(r => now - resultTimestampMs(r) < day && resultTimestampMs(r) > 0);
  } else if (filter === 'last7') {
    filtered = data.filter(r => now - resultTimestampMs(r) < 7 * day && resultTimestampMs(r) > 0);
  }

  applyAccRunStats(computeAccRunStats(filtered));
  accRenderSummaryTable(filtered.map(normalizeResultSummary));
  const status = document.getElementById('acc-detail-batch-status');
  if (status) {
    status.textContent = filtered.length
      ? `${filtered.length} result${filtered.length === 1 ? '' : 's'}`
      : '';
  }
}

async function loadDashboardFilters() {
  try {
    const fetched = await fetchAllResults(true);
    const data = Array.isArray(fetched) ? fetched : (fetched?.results || []);
    const options = document.getElementById('batch-filter-options');
    if (!options) return;

    const api = accuracyChartApi();
    const batches = api.collectBatches
      ? api.collectBatches(data)
      : [...new Map(
        data
          .filter(r => r.batch_id)
          .map(r => [r.batch_id, {
            batch_id: r.batch_id,
            batch_ref: r.batch_ref || '',
            timestamp: String(r.created_at || r.timestamp || ''),
          }])
      ).values()];
    const persist = sessionPersistApi();
    const stored = persist.loadSelectedBatchIds ? persist.loadSelectedBatchIds() : [];
    const previous = persist.restoreSelectedBatchIds
      ? persist.restoreSelectedBatchIds(stored, batches.map(b => b.batch_id))
      : stored;
    const allWasChecked = previous.length === 0;

    const labels = api.assignBatchLabels ? api.assignBatchLabels(batches) : {};
    batches.sort((a, b) => String(b.timestamp || '').localeCompare(String(a.timestamp || '')));
    options.innerHTML = '';
    if (!batches.length) {
      const empty = document.createElement('div');
      empty.className = 'batch-filter-empty';
      empty.textContent = 'No saved batches yet';
      options.appendChild(empty);
    }
    batches.forEach(b => {
      const row = document.createElement('label');
      row.className = 'multi-audio-label batch-filter-option';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'batch-filter-cb';
      cb.value = b.batch_id;
      cb.checked = previous.includes(b.batch_id);
      const text = formatBatchFilterLabel(b, labels);
      cb.dataset.label = text;
      cb.addEventListener('change', onBatchOptionToggle);
      row.appendChild(cb);
      row.appendChild(document.createTextNode(' ' + text));
      options.appendChild(row);
    });
    const allCb = document.getElementById('batch-filter-all');
    if (allCb) {
      allCb.checked = allWasChecked || getSelectedBatchIds().length === 0;
    }
    persistSelectedBatchIds();
    syncBatchFilterToggleLabel();
  } catch (err) {
    console.warn('Dashboard filters failed:', err);
  }
}

async function onDashboardFilterChange() {
  const selectedIds = getSelectedBatchIds();
  const typeFilter = document.getElementById('type-filter')?.value || 'all';
  const api = accuracyChartApi();

  let data = await fetchAllResults();

  data = api.filterResultsByBatchIds
    ? api.filterResultsByBatchIds(data, selectedIds)
    : (selectedIds.length
      ? data.filter(r => selectedIds.includes(r.batch_id))
      : data);
  if (typeFilter === 'load') {
    data = [];
  }

  updateDashboardStats(data, selectedIds);
  renderTestRunsTable(data.map(normalizeResultSummary));
}

function selectedModelFromUi() {
  const sel = document.getElementById('ai-model-select');
  return sel?.options[sel.selectedIndex]?.textContent?.trim()
    || sel?.value
    || '';
}

function runSummaryApi() {
  return window.MedsumRunSummary || {};
}

function fillRunSummary(data, source) {
  const api = runSummaryApi();
  if (!api.summaryDisplay) return;
  const display = api.summaryDisplay(data, selectedModelFromUi());
  document.querySelectorAll(
    `[data-section="run-summary"][data-results-source="${source}"]`
  ).forEach(section => {
    section.querySelectorAll('[data-summary-field]').forEach(el => {
      const key = el.getAttribute('data-summary-field');
      const val = display[key];
      if (key === 'meta') {
        el.textContent = val || '';
        el.style.display = val ? '' : 'none';
        return;
      }
      el.textContent = val != null && val !== '' ? val : '—';
    });
  });
}

function updateDashboardStats(data, selectedBatchIds) {
  const rows = rowsForView(data);
  const api = runSummaryApi();
  const summary = api.computeRunSummary
    ? api.computeRunSummary(rows, selectedModelFromUi())
    : {};
  const batches = new Set(rows.map(r => r.batch_id).filter(Boolean)).size;
  const reports = rows.filter(r => r.report_pdf_path || r.report_excel_path).length;
  const selected = selectedBatchIds == null ? getSelectedBatchIds() : selectedBatchIds;

  const set = (id, val, suffix = '') => {
    const el = document.getElementById(id);
    if (el) el.textContent = val != null && val !== '' ? `${val}${suffix}` : '—';
  };

  set('stat-batches', batches || 0);
  set('stat-total-cases', summary.total_test_cases ?? rows.length);
  set('stat-passed', summary.done_tests ?? summary.passed_tests ?? 0);
  set(
    'stat-accuracy',
    summary.average_accuracy != null ? summary.average_accuracy : null,
    '%'
  );
  set(
    'stat-latency',
    summary.average_latency != null
      ? Number(summary.average_latency).toFixed(2)
      : null,
    's'
  );
  set('stat-reports', reports);
  fillRunSummary(rows, 'dashboard');

  renderAccuracyChart(rows, selected);
  renderDistributionChart(rows);
}

function setStatCard(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value ?? '—';
}

function latencyAnalysisApi() {
  return window.MedsumLatencyAnalysis || {};
}

function switchTableTab(tabId, btn) {
  const panel = btn?.closest('.table-panel');
  if (!panel) return;
  const source = panel.dataset.resultsSource || 'dashboard';
  const api = resultsTableApi();
  if (api.setTab) api.setTab(source, tabId);
  const persist = sessionPersistApi();
  if (persist.saveTableTab) persist.saveTableTab(source, tabId);
  paintResultsTable(source, cachedRowsForSource(source));
}

const DETAIL_CMP_TABS = ['summary', 'detail', 'soap-gt-report'];

const detailCmpUi = {
  tab: 'summary',
  scroll: { summary: 0, detail: 0, 'soap-gt-report': 0 },
};

function wrapDetailCmpSection(id, html) {
  if (!html) return '';
  return `<div data-detail-cmp-section="${esc(id)}">${html}</div>`;
}

function switchDetailComparisonTab(tabId, btn) {
  const root = document.getElementById('detail-comparison-tabs');
  if (!root) return;
  const next = DETAIL_CMP_TABS.includes(tabId) ? tabId : 'summary';
  detailCmpUi.scroll[detailCmpUi.tab] = window.scrollY;
  detailCmpUi.tab = next;
  root.querySelectorAll('[data-detail-cmp-tab]').forEach(el => {
    el.classList.toggle('active', el.getAttribute('data-detail-cmp-tab') === next);
  });
  DETAIL_CMP_TABS.forEach(id => {
    const panel = document.getElementById(
      id === 'summary' ? 'detail-cmp-summary'
        : id === 'detail' ? 'detail-cmp-detail'
          : 'detail-cmp-soap-gt-report'
    );
    if (panel) panel.hidden = next !== id;
  });
  window.scrollTo(0, detailCmpUi.scroll[next] || 0);
  if (btn && btn.focus) btn.focus();
}

function resetDetailComparisonTabs() {
  detailCmpUi.tab = 'summary';
  detailCmpUi.scroll = { summary: 0, detail: 0, 'soap-gt-report': 0 };
  const root = document.getElementById('detail-comparison-tabs');
  if (!root) return;
  root.querySelectorAll('[data-detail-cmp-tab]').forEach(el => {
    el.classList.toggle('active', el.getAttribute('data-detail-cmp-tab') === 'summary');
  });
  const summary = document.getElementById('detail-cmp-summary');
  const detail = document.getElementById('detail-cmp-detail');
  const report = document.getElementById('detail-cmp-soap-gt-report');
  if (summary) summary.hidden = false;
  if (detail) detail.hidden = true;
  if (report) report.hidden = true;
}

function onResultsTableFilter(input) {
  const panel = input?.closest('.table-panel');
  if (!panel) return;
  const source = panel.dataset.resultsSource || 'dashboard';
  const api = resultsTableApi();
  if (api.setFilter) api.setFilter(source, input.value);
  if (api.setPage) api.setPage(source, 1);
  paintResultsTable(source, cachedRowsForSource(source));
}

function onResultsTableSort(source, sortKey) {
  const api = resultsTableApi();
  if (api.setSort) api.setSort(source, sortKey);
  paintResultsTable(source, cachedRowsForSource(source));
}

function onResultsTablePage(source, page) {
  const api = resultsTableApi();
  if (api.setPage) api.setPage(source, page);
  paintResultsTable(source, cachedRowsForSource(source));
}

function resultsLatencyCells(r) {
  const api = latencyAnalysisApi();
  const headers = api.LATENCY_ANALYSIS_HEADERS || [
    'Audio File', 'Audio Length', 'Transcription', 'Translation', 'SOAP', 'Total Time',
  ];
  const cells = api.latencyAnalysisValues
    ? api.latencyAnalysisValues(r)
    : headers.map(() => '—');
  return cells.map((val, i) => {
    const header = headers[i] || '';
    const unavailable = val === (api.UNAVAILABLE || 'unavailable');
    const cls = unavailable
      ? ' class="latency-unavailable"'
      : (header === 'Total Time' ? ' class="total-latency-cell"' : '');
    return `<td${cls} data-col="${esc(header)}">${esc(val)}</td>`;
  }).join('');
}

function resultsTabCells(r, pillPrefix) {
  const testId = resultStableId(r);
  const pillId = `${pillPrefix}-${String(testId).replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 24)}`;
  const langClass = (r.language || '').toLowerCase().replace(/\s+/g, '-');
  return `
      <td>${esc(r.audio_filename || '—')}</td>
      <td>
        <span class="lang-badge lang-${esc(langClass)}">
          ${esc(r.language || '—')}
        </span>
      </td>
      <td data-col="soap-accuracy" onclick="event.stopPropagation()">
        ${soapAccuracyPillHtml(r, pillId)}
      </td>
      <td data-col="clinical-quality">${clinicalQualityChipHtml(r)}</td>
      <td data-col="execution">${executionChipHtml(r)}</td>`;
}

function resultsIdCell(r) {
  const api = resultsTableApi();
  const testId = resultStableId(r);
  const displayId = api.displayTestCaseId
    ? api.displayTestCaseId(r)
    : (r.tc_ref || r.test_case_id || (testId ? `${String(testId).slice(0, 8)}…` : '—'));
  return `
      <td class="run-id-cell" data-col="Test Case ID">
        <span class="tc-ref">${esc(displayId)}</span>
        ${rowActionsHtml(testId)}
      </td>`;
}

function paintResultsThead(table, view, source) {
  const thead = table?.querySelector('thead');
  if (!thead) return;
  thead.innerHTML = `<tr>${(view.headers || []).map(h => {
    const sorted = view.sortKey === h;
    const arrow = sorted ? (view.sortDir === 'desc' ? ' ↓' : ' ↑') : '';
    return `<th data-sort-key="${esc(h)}"
                class="${sorted ? 'is-sorted' : ''}"
                onclick="onResultsTableSort('${esc(source)}','${esc(h)}')">${esc(h)}${arrow}</th>`;
  }).join('')}</tr>`;
}

function paintResultsPager(panel, source, view) {
  const el = panel?.querySelector('[data-table-pager]');
  if (!el) return;
  if (!view || view.totalPages <= 1) {
    el.innerHTML = '';
    return;
  }
  el.innerHTML = `
    <button type="button" ${view.page <= 1 ? 'disabled' : ''}
            onclick="onResultsTablePage('${esc(source)}', ${view.page - 1})">Prev</button>
    <span>Page ${view.page} of ${view.totalPages}</span>
    <button type="button" ${view.page >= view.totalPages ? 'disabled' : ''}
            onclick="onResultsTablePage('${esc(source)}', ${view.page + 1})">Next</button>`;
}

function paintResultsTable(source, results) {
  const api = resultsTableApi();
  const key = source === 'history' ? 'history' : 'dashboard';
  const visible = rowsForView(results);
  if (key === 'history') {
    window._historyResultsUnfiltered = results || [];
    window._historyResults = visible;
  } else {
    window._currentResultsUnfiltered = results || [];
    window._currentResults = visible;
  }

  const tbodyId = key === 'history' ? 'acc-summary-tbody' : 'test-runs-tbody';
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  const table = tbody.closest('table');
  const panel = tbody.closest('.table-panel');
  const state = api.getState ? api.getState(key) : { tab: 'results', page: 1, pageSize: 50 };
  const view = api.applyTableView
    ? api.applyTableView(visible, state)
    : {
        tab: 'results',
        headers: ['Test Case ID', 'Audio File', 'Language', 'SOAP accuracy', 'Clinical Quality', 'Execution Status'],
        rows: visible.slice(0, 50),
        page: 1,
        totalPages: 1,
        sortKey: '',
        sortDir: 'asc',
      };

  const tab = view.tab === 'latency' ? 'latency' : 'results';
  if (panel) {
    panel.querySelectorAll('.table-tab').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tableTab === tab);
    });
    const note = panel.querySelector('[data-latency-note]');
    if (note) note.hidden = tab !== 'latency';
    const filterInput = panel.querySelector('[data-table-filter]');
    const liveFilter = (state && state.filter) || '';
    if (filterInput && filterInput.value !== liveFilter) {
      filterInput.value = liveFilter;
    }
  }

  paintResultsThead(table, view, key);
  paintResultsPager(panel, key, view);

  const emptyHint = key === 'history'
    ? 'To view previous results, use the dropdown above.'
    : 'Use the batch filter above to review previous dashboard results.';
  if (!view.rows.length) {
    tbody.innerHTML = emptyStateRow(view.headers.length || RESULTS_TABLE_COLSPAN, emptyHint);
    fillRunSummary(visible, key);
    return;
  }

  const pillPrefix = key === 'history' ? 'hist' : 'table';
  tbody.innerHTML = view.rows.map(r => {
    const testId = resultStableId(r);
    const rowKey = testId || (api.displayTestCaseId ? api.displayTestCaseId(r) : '');
    const rest = tab === 'latency'
      ? resultsLatencyCells(r)
      : resultsTabCells(r, pillPrefix);
    return `<tr data-open-test-id="${esc(testId)}"
                data-test-id="${esc(testId)}"
                data-row-key="${esc(rowKey)}"
                style="cursor:pointer"
                title="${esc(testId || rowKey)}">
      ${resultsIdCell(r)}
      ${rest}
    </tr>`;
  }).join('');
  fillRunSummary(visible, key);
}

function displayBatchLabel(r) {
  const id = String((r && r.batch_id) || '').trim();
  const ref = String((r && r.batch_ref) || '').trim();
  if (/^\d{2}-[A-Za-z]{3}-\d{4} \| \d+$/.test(id)) return id;
  if (ref) return ref;
  if (!id) return '—';
  return /^[0-9a-f-]{36}$/i.test(id) ? id.slice(0, 8) : id;
}

function renderBatchStatus(data) {
  const el = document.getElementById('batch-status-bar');
  if (!el) return;
  if (!data || data.total == null) {
    el.textContent = '';
    el.dataset.batchPhase = '';
    return;
  }
  const done = data.passed ?? data.done ?? 0;
  const failed = data.failed ?? 0;
  const pending = data.pending ?? 0;
  const skipped = data.skipped ?? 0;
  const persist = sessionPersistApi();
  const phase = persist.batchWatchPhase
    ? persist.batchWatchPhase({
        pending,
        total: data.total,
        results: data.results || [],
      })
    : '';
  el.dataset.batchPhase = phase;
  const phaseLabel = phase === 'running' ? 'Running'
    : phase === 'done' ? 'Done'
    : phase === 'pending' ? 'Pending'
    : '';
  const label = displayBatchLabel({
    batch_id: data.batch_id || currentBatchId,
    batch_ref: data.batch_ref || currentBatchRef,
  });
  const skipBit = skipped ? `, ${skipped} skipped` : '';
  const prefix = phaseLabel ? `${phaseLabel} — ` : '';
  el.textContent = `${prefix}${label}: ${done} done, ${failed} errors, ${pending} pending${skipBit}`;
}

function isExecutionFailed(r) {
  const status = String(r?.status || '').trim().toLowerCase();
  const verdict = String(r?.final_result || '').trim().toLowerCase();
  return status === 'failed' || verdict === 'failed';
}

function computeAccRunStats(items) {
  const rows = rowsForView(items);
  const api = runSummaryApi();
  const summary = api.computeRunSummary
    ? api.computeRunSummary(rows, selectedModelFromUi())
    : {};
  const failed = rows.filter(isExecutionFailed).length;
  const done = summary.done_tests ?? 0;
  return {
    total: summary.total_test_cases ?? rows.length,
    passed: done,
    done,
    failed,
    avg: summary.average_accuracy ?? null,
    rate: statsTotalRate(summary.total_test_cases ?? rows.length, done),
  };
}

function statsTotalRate(total, done) {
  return total ? Math.round(done / total * 100) : 0;
}

function applyAccRunStats(stats, rateReady = true) {
  const totalEl = document.getElementById('acc-stat-total');
  if (!totalEl) return;
  totalEl.textContent = String(stats.total);
  document.getElementById('acc-stat-passed').textContent = String(stats.passed);
  document.getElementById('acc-stat-rate').textContent =
    rateReady && stats.total ? `${stats.rate}%` : '—';
  document.getElementById('acc-stat-accuracy').textContent =
    stats.avg != null ? `${stats.avg}%` : '—';
}

function fillRunsHistory(results) {
  if (batchPollInterval) return;
  const filter = document.getElementById('history-filter')?.value || 'current';
  if (filter === 'current' && !results) {
    onHistoryFilterChange();
    return;
  }
  const items = results || [];
  const section = document.getElementById('acc-live-section');
  if (!section) return;
  section.style.display = '';

  applyAccRunStats(computeAccRunStats(items));
  accRenderSummaryTable(items);

  const status = document.getElementById('acc-detail-batch-status');
  if (status) {
    status.textContent = items.length
      ? `${items.length} result${items.length === 1 ? '' : 's'}`
      : '';
  }
}

function resultScore(r) {
  return r?.comparison?.similarity_score
    ?? r?.transcription_comparison?.similarity_score
    ?? r?.accuracy_score
    ?? r?.similarity_score
    ?? null;
}

function refreshDashboard(results) {
  updateDashboardStats(results || latestResults || []);
}

function normalizeResultSummary(r) {
  const comp = r.comparison || r.transcription_comparison || {};
  return {
    test_id: resultStableId(r),
    tc_ref: r.tc_ref,
    run_ref: r.run_ref,
    batch_id: r.batch_id || '',
    batch_ref: r.batch_ref || '',
    created_at: r.created_at || r.timestamp || '',
    completed_at: r.completed_at || r.timestamp || '',
    timestamp: r.timestamp || r.created_at || '',
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
    transcription_comparison: r.transcription_comparison || comp,
    translation_comparison: r.translation_comparison || null,
    soap_comparison: r.soap_comparison || null,
    accuracy_skipped: r.accuracy_skipped,
    accuracy_skip_reason: r.accuracy_skip_reason || '',
    has_ground_truth: r.has_ground_truth,
    has_translation_ground_truth: r.has_translation_ground_truth,
    has_soap_ground_truth: r.has_soap_ground_truth,
    total_test_time_seconds: r.total_test_time_seconds,
    audio_duration_seconds: r.audio_duration_seconds,
    transcription_result: r.transcription_result,
    patient_id: r.patient_id || r.patientId || '',
    phone: r.phone || '',
    doctor_id: r.doctor_id || '',
    doctor_name: r.doctor_name
      || r.transcription_result?.doctor_details?.doctor_name
      || '',
    report_pdf_path: r.report_pdf_path || '',
    report_excel_path: r.report_excel_path || '',
    ai_model: r.ai_model || '',
    ai_model_used: r.ai_model_used || r.ai_model || '',
    llm_model: r.llm_model || '',
  };
}

function accToggleSetup() {
  accSetupOpen = !accSetupOpen;
  document.getElementById('acc-setup-body').style.display =
    accSetupOpen ? '' : 'none';
  document.getElementById('acc-setup-arrow').textContent =
    accSetupOpen ? '▼' : '▶';
}

function accDoctorPatientApi() {
  return window.MedsumDoctorPatient || {};
}

function accAddDoctor(phone = '', password = '', patients = []) {
  const idx = accDoctors.length;
  const dp = accDoctorPatientApi();
  const ids = dp.normalizePatientIds
    ? dp.normalizePatientIds(patients)
    : [...patients];
  accDoctors.push({
    phone,
    password,
    patients: ids,
    changing: false,
  });

  const tr = document.createElement('tr');
  tr.id = `acc-doc-row-${idx}`;
  tr.className = 'doctor-row';
  tr.setAttribute('data-acc-credentials-saved', 'false');
  tr.innerHTML = `
    <td>
      <input type="text" data-field="phone" value="${esc(phone)}"
             placeholder="9876543210"
             oninput="accDoctors[${idx}].phone=this.value; persistDoctorForm()"
             onblur="accSyncDoctorCredentials(${idx})"
             style="width:100%;padding:8px;
                    border:1px solid var(--border);
                    border-radius:6px;font-size:14px">
    </td>
    <td>
      <div class="acc-password-cell">
        <input type="password" data-field="password" value="${esc(password)}"
               id="acc-pwd-${idx}"
               placeholder="Password"
               oninput="accDoctors[${idx}].password=this.value; persistDoctorForm()"
               onblur="accSyncDoctorCredentials(${idx})"
               style="flex:1;padding:8px;
                      border:1px solid var(--border);
                      border-radius:6px;font-size:14px">
        <button type="button"
                onclick="accTogglePwd(${idx})"
                style="background:none;border:none;
                       cursor:pointer;font-size:16px;
                       color:var(--text-secondary)">👁</button>
        <span class="acc-credentials-saved-badge"
              data-acc-credentials-saved-badge hidden>Saved</span>
      </div>
    </td>
    <td>
      <div class="acc-patient-cell" id="acc-patient-cell-${idx}"
           data-doctor-idx="${idx}"></div>
    </td>
    <td>
      <button type="button"
              onclick="accRemoveDoctor(${idx})"
              style="background:none;border:none;
                     cursor:pointer;color:var(--danger);
                     font-size:18px">🗑</button>
    </td>`;

  document.getElementById('acc-doctor-tbody').appendChild(tr);
  accRenderPatientCell(idx);
  accApplyDoctorSavedState(idx);
  accUpdateSummary();
}

function accSyncDoctorCredentials(idx) {
  const doctor = accDoctors[idx];
  const row = document.getElementById(`acc-doc-row-${idx}`);
  if (!doctor || !row) return;
  const phoneEl = row.querySelector('[data-field="phone"]');
  const pwdEl = row.querySelector('[data-field="password"]');
  if (phoneEl) doctor.phone = phoneEl.value.trim();
  if (pwdEl) doctor.password = pwdEl.value;
  accApplyDoctorSavedState(idx);
  accUpdateSummary();
}

function accApplyDoctorSavedState(idx) {
  const doctor = accDoctors[idx];
  const row = document.getElementById(`acc-doc-row-${idx}`);
  if (!doctor || !row) return;
  const dp = accDoctorPatientApi();
  const saved = dp.credentialsLookSaved
    ? dp.credentialsLookSaved(doctor.phone, doctor.password)
    : !!(String(doctor.phone || '').trim() && String(doctor.password || ''));
  row.classList.toggle('acc-doctor-credentials-saved', saved);
  row.setAttribute('data-acc-credentials-saved', saved ? 'true' : 'false');
  row.querySelectorAll('[data-field="phone"], [data-field="password"]').forEach(el => {
    el.classList.toggle('acc-field-saved', saved);
  });
  const badge = row.querySelector('[data-acc-credentials-saved-badge]');
  if (badge) badge.hidden = !saved;
}

function accShowPatientEditorError(idx, message) {
  const input = document.getElementById(`acc-patient-input-${idx}`);
  const cell = document.getElementById(`acc-patient-cell-${idx}`);
  if (input) {
    input.classList.add('acc-patient-input-error');
    input.setAttribute('aria-invalid', 'true');
    input.setAttribute('aria-describedby', `acc-patient-error-${idx}`);
  }
  if (cell) {
    let err = cell.querySelector('[data-acc-patient-error]');
    if (!err) {
      err = document.createElement('p');
      err.className = 'acc-patient-error';
      err.id = `acc-patient-error-${idx}`;
      err.setAttribute('data-acc-patient-error', 'true');
      err.setAttribute('role', 'alert');
      cell.appendChild(err);
    }
    err.textContent = message;
  }
  showToast(message, 'error');
}

function accFlashPatientSaved(idx, patientId) {
  const dp = accDoctorPatientApi();
  const msg = dp.patientSavedMessage
    ? dp.patientSavedMessage(patientId)
    : `Patient ${patientId} saved`;
  showToast(msg, 'success');
  const chip = document.querySelector(
    `#acc-patient-cell-${idx} [data-acc-assigned-patient]`
  );
  if (chip) {
    chip.classList.add('acc-patient-just-saved');
    chip.setAttribute('data-acc-just-saved', 'true');
  }
}

function accReadPatientInput(idx) {
  const input = document.getElementById(`acc-patient-input-${idx}`);
  if (!input) return '';
  return input.value.trim().replace(/,/g, '');
}

function accPatientKeydown(e, idx) {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault();
    if (accDoctors[idx]?.changing) accCommitChangePatient(idx);
    else accAddPatient(idx);
  }
}

function accAddPatient(idx) {
  const doctor = accDoctors[idx];
  if (!doctor) return;
  const val = accReadPatientInput(idx);
  const dp = accDoctorPatientApi();
  const error = dp.patientAddValidationError
    ? dp.patientAddValidationError(val, doctor.patients, { replace: false })
    : (!val
      ? 'Patient ID is required'
      : (isNaN(val) ? 'Patient ID must be numeric' : ''));
  if (error) {
    accShowPatientEditorError(idx, error);
    return;
  }
  const result = dp.assignPatientToDoctor(
    doctor.patients, val, { replace: false }
  );
  if (!result || !result.ok) {
    accShowPatientEditorError(
      idx, (result && result.error) || 'Could not assign patient'
    );
    return;
  }
  doctor.patients = result.patients;
  doctor.changing = false;
  accRenderPatientCell(idx);
  accFlashPatientSaved(idx, val);
  accApplyDoctorSavedState(idx);
  accUpdateSummary();
}

function accStartChangePatient(idx) {
  const doctor = accDoctors[idx];
  if (!doctor || doctor.patients.length !== 1) return;
  doctor.changing = true;
  accRenderPatientCell(idx);
  const input = document.getElementById(`acc-patient-input-${idx}`);
  if (input) {
    input.value = doctor.patients[0];
    input.focus();
    input.select();
  }
}

function accCancelChangePatient(idx) {
  if (!accDoctors[idx]) return;
  accDoctors[idx].changing = false;
  accRenderPatientCell(idx);
}

function accCommitChangePatient(idx) {
  const doctor = accDoctors[idx];
  if (!doctor) return;
  const val = accReadPatientInput(idx);
  const dp = accDoctorPatientApi();
  const error = dp.patientAddValidationError
    ? dp.patientAddValidationError(val, doctor.patients, { replace: true })
    : (!val
      ? 'Patient ID is required'
      : (isNaN(val) ? 'Patient ID must be numeric' : ''));
  if (error) {
    accShowPatientEditorError(idx, error);
    return;
  }
  const result = dp.assignPatientToDoctor(
    doctor.patients, val, { replace: true }
  );
  if (!result || !result.ok) {
    accShowPatientEditorError(
      idx, (result && result.error) || 'Could not change patient'
    );
    return;
  }
  doctor.patients = result.patients;
  doctor.changing = false;
  accRenderPatientCell(idx);
  accFlashPatientSaved(idx, val);
  accApplyDoctorSavedState(idx);
  accUpdateSummary();
}

function accRemovePatient(idx, patientId) {
  const doctor = accDoctors[idx];
  if (!doctor) return;
  doctor.patients = doctor.patients.filter(p => p !== String(patientId));
  doctor.changing = false;
  accRenderPatientCell(idx);
  accUpdateSummary();
}

function accRenderPatientCell(idx) {
  const cell = document.getElementById(`acc-patient-cell-${idx}`);
  const doctor = accDoctors[idx];
  if (!cell || !doctor) return;
  const dp = accDoctorPatientApi();
  const patients = doctor.patients || [];
  const changing = !!doctor.changing;
  const showAdd = dp.addPatientControlsVisible
    ? dp.addPatientControlsVisible(patients.length, changing)
    : patients.length === 0 || changing;

  let html = '';
  if (patients.length > 1) {
    html += `<p class="acc-patient-legacy-note" data-acc-legacy-note>
      This doctor has ${patients.length} patients from a previous config.
      Only one patient is allowed per doctor in a test run.
      Remove extras to continue — none were dropped.
    </p>`;
    html += `<div id="acc-patients-${idx}" class="acc-patient-list">`;
    html += patients.map(p => {
      const pid = JSON.stringify(String(p));
      return `<div class="acc-legacy-patient-row">
        <span class="acc-assigned-patient">${esc(String(p))}</span>
        <button type="button" class="btn-outline"
                data-acc-remove-patient
                style="padding:4px 8px;font-size:12px"
                onclick="accRemovePatient(${idx},${pid})">
          Remove
        </button>
      </div>`;
    }).join('');
    html += `</div>`;
  } else if (patients.length === 1 && !changing) {
    const pid = JSON.stringify(String(patients[0]));
    html += `<div id="acc-patients-${idx}" class="acc-patient-chip-row">
      <span class="acc-assigned-patient acc-field-saved" data-acc-assigned-patient>
        ${esc(String(patients[0]))}
      </span>
      <span class="acc-credentials-saved-badge acc-patient-saved-cue"
            data-acc-patient-saved-cue>Saved</span>
      <button type="button" class="acc-patient-link" data-acc-change-patient
              aria-label="Change Patient"
              onclick="accStartChangePatient(${idx})">Change</button>
      <button type="button" class="acc-patient-link acc-patient-link-danger"
              data-acc-remove-patient aria-label="Remove Patient"
              onclick="accRemovePatient(${idx},${pid})">Remove</button>
    </div>`;
  } else {
    html += `<div id="acc-patients-${idx}"></div>`;
  }

  if (showAdd) {
    const isChange = changing && patients.length === 1;
    html += `<div class="acc-patient-add" data-acc-patient-editor
                  data-mode="${isChange ? 'change' : 'add'}">
      <input type="text"
             data-field="patient"
             class="patient-id-input"
             id="acc-patient-input-${idx}"
             placeholder="Patient ID"
             onkeydown="accPatientKeydown(event,${idx})">
      <button type="button"
              class="${isChange ? 'btn-outline' : 'btn-primary acc-add-patient-btn'}"
              style="padding:6px 10px;font-size:12px"
              data-acc-add-patient="${isChange ? 'save' : 'add'}"
              onclick="${isChange
                ? `accCommitChangePatient(${idx})`
                : `accAddPatient(${idx})`}">
        ${isChange ? 'Save Patient' : 'Add Patient'}
      </button>
      ${isChange
        ? `<button type="button" class="btn-outline"
                   style="padding:6px 10px;font-size:12px"
                   onclick="accCancelChangePatient(${idx})">Cancel</button>`
        : ''}
    </div>`;
  }

  cell.innerHTML = html;
}

function accRemoveDoctor(idx) {
  const tr = document.getElementById(`acc-doc-row-${idx}`);
  if (tr) tr.remove();
  accDoctors[idx] = null;
  accUpdateSummary();
}

function accTogglePwd(idx) {
  const input = document.getElementById(`acc-pwd-${idx}`);
  if (!input) return;
  input.type = input.type === 'password' ? 'text' : 'password';
}

function accUpdateSummary() {
  const active = accDoctors.filter(d => d !== null);
  const totalPatients = active.reduce(
    (sum, d) => sum + d.patients.length, 0
  );
  const el = document.getElementById('acc-setup-summary');
  if (el) {
    el.textContent = active.length > 0
      ? `${active.length} doctor${active.length !== 1 ? 's' : ''} · ${totalPatients} patient${totalPatients !== 1 ? 's' : ''}`
      : 'Not configured';
  }
  persistDoctorForm();
}

function accExportConfig() {
  const active = accGetActiveDoctors();
  const config = {
    version: 1,
    saved_at: new Date().toISOString(),
    doctors: active.map(d => ({
      phone: d.phone,
      password: d.password,
      patients: d.patients,
    })),
  };
  const blob = new Blob(
    [JSON.stringify(config, null, 2)],
    { type: 'application/json' }
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download =
    `acc_config_${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function accImportConfig() {
  document.getElementById('acc-config-input').click();
}

function accHandleConfigImport(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    try {
      const config = JSON.parse(e.target.result);
      if (!config.doctors) throw new Error('Invalid config');
      document.getElementById('acc-doctor-tbody').innerHTML = '';
      accDoctors = [];
      config.doctors.forEach(d =>
        accAddDoctor(
          d.phone || '',
          d.password || '',
          d.patients || []
        )
      );
      showToast(
        `Imported — ${config.doctors.length} doctor(s) restored`
      );
    } catch (err) {
      showToast(`Import failed: ${err.message}`);
    }
  };
  reader.readAsText(file);
  input.value = '';
}

function accGetActiveDoctors() {
  return accDoctors.filter(
    d => d !== null && d.phone && d.password && d.patients.length > 0
  );
}

function getConfiguredDoctors() {
  const fromState = accDoctors
    .filter(d => d !== null)
    .map(d => ({
      phone: (d.phone || '').trim(),
      password: d.password || '',
      patients: accDoctorPatientApi().normalizePatientIds
        ? accDoctorPatientApi().normalizePatientIds(d.patients)
        : [...(d.patients || [])],
    }))
    .filter(d => d.phone && d.password && d.patients.length > 0);
  if (fromState.length) return fromState;

  const doctors = [];
  const rows = document.querySelectorAll(
    '.doctor-row, #acc-doctor-tbody tr, [data-doctor-row]'
  );
  rows.forEach(row => {
    const phone = row.querySelector("input[data-field='phone']")?.value?.trim();
    const password = (
      row.querySelector("input[data-field='password']")
      || row.querySelector('input[type="password"]')
    )?.value?.trim();
    const assigned = [...row.querySelectorAll('[data-acc-assigned-patient]')]
      .map(el => el.textContent.trim())
      .filter(Boolean);
    const patients = accDoctorPatientApi().normalizePatientIds
      ? accDoctorPatientApi().normalizePatientIds(assigned)
      : assigned;
    if (phone && password && patients.length > 0) {
      doctors.push({ phone, password, patients });
    }
  });
  return doctors;
}

async function runAllTests() {
  await _manualUploadQueue;
  const doctors = getConfiguredDoctors();
  if (doctors.length === 0) {
    showToast(
      'Add at least one doctor with phone, password, and one Patient ID',
      'warning'
    );
    if (!accSetupOpen) accToggleSetup();
    return;
  }
  const extra = doctors.find(d => d.patients.length > 1);
  if (extra) {
    showToast(
      'Each doctor can have only one patient in a test run. Remove extra patients first.'
    );
    if (!accSetupOpen) accToggleSetup();
    return;
  }

  if (!selectedCatalogItems().length) {
    showToast(NO_RUN_FILES_MESSAGE, 'warning');
    updateRunButtonState();
    return;
  }

  const api = audioSelectionApi();
  const missingLang = api.missingLanguageUploads
    ? api.missingLanguageUploads(selectedCatalogItems())
    : [];
  if (missingLang.length) {
    showToast(MISSING_LANGUAGE_RUN_MESSAGE, 'warning');
    updateRunButtonState();
    return;
  }

  const selectedAudios = getSelectedAudios();
  if (!selectedAudios.length) {
    showToast(NO_RUN_FILES_MESSAGE, 'warning');
    updateRunButtonState();
    return;
  }

  const btn = document.getElementById('run-all-btn');
  const model = document.getElementById('ai-model-select')?.value
    || document.getElementById('ai-model-select')?.options[
      document.getElementById('ai-model-select')?.selectedIndex
    ]?.value
    || '';
  updateRunModelLabels();

  btn.disabled = true;
  btn.dataset.running = '1';
  btn.textContent = '⏳ Running...';

  document.getElementById('acc-live-section').style.display = '';
  document.getElementById('acc-stat-total').textContent = '—';
  document.getElementById('acc-stat-passed').textContent = '0';
  document.getElementById('acc-stat-rate').textContent = '—';
  document.getElementById('acc-stat-accuracy').textContent = '—';
  window._historyResults = [];
  fillRunSummary([], 'history');
  paintResultsTable('history', []);

  try {
    const payload = {
      ai_model: model,
      doctors: doctors.map(d => ({
        phone: d.phone,
        password: d.password,
        patients: d.patients,
      })),
      selected_audios: selectedAudios,
    };

    const resp = await fetch(`${API}/run-all`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok)
      throw new Error(data.error || `HTTP ${resp.status}`);

    showToast(
      `Batch ${displayBatchLabel(data)}`
      + ` — ${data.total} tests started`
    );
    currentBatchId = data.batch_id;
    currentBatchRef = data.batch_id || '';
    const persist = sessionPersistApi();
    if (persist.saveCurrentBatchId) persist.saveCurrentBatchId(data.batch_id);

    const statusText =
      `Batch running: 0 / ${data.total} complete`;
    document.getElementById('batch-status').textContent =
      statusText;
    document.getElementById('acc-detail-batch-status')
      .textContent = statusText;

    stopBatchPoll();
    armBatchPoll(data.batch_id);
    pollBatch(data.batch_id);

  } catch (err) {
    showToast(`Run failed: ${err.message}`);
    btn.disabled = false;
    btn.dataset.running = '';
    btn.textContent = RUN_BATCH_LABEL;
    updateRunButtonState();
    onHistoryFilterChange();
  }
}

async function pollBatch(batchId) {
  const persist = sessionPersistApi();
  const wasPolling = !!batchPollInterval;
  try {
    const resp = await fetch(`${API}/results/batch/${encodeURIComponent(batchId)}`);
    let data = { batch_id: batchId, results: [], pending: 0, total: 0 };
    if (resp.ok) {
      data = await resp.json();

      const batchResults = (audioSelectionApi().resultsKeepFailures
        ? audioSelectionApi().resultsKeepFailures(data.results || [])
        : (data.results || []));
      const accStats = computeAccRunStats(batchResults);
      updateDashboardStats(batchResults);
      renderTestRunsTable(batchResults);
      const skipped = (batchResults || []).filter(r => String(r.status || '').toLowerCase() === 'skipped').length;
      renderBatchStatus({
        total: data.total,
        batch_id: data.batch_id,
        batch_ref: data.batch_ref || currentBatchRef,
        passed: accStats.done ?? accStats.passed,
        failed: accStats.failed,
        pending: data.pending,
        skipped,
        results: batchResults,
      });

      applyAccRunStats(accStats, data.pending === 0);

      const historyFilter = document.getElementById('history-filter')?.value || 'current';
      if (historyFilter === 'current') {
        accRenderSummaryTable(batchResults);
      }

      const phase = persist.batchWatchPhase ? persist.batchWatchPhase(data) : '';
      const phaseLabel = phase === 'running' ? 'Running'
        : phase === 'done' ? 'Done'
        : phase === 'pending' ? 'Pending'
        : '';
      const statusText =
        `${phaseLabel ? `${phaseLabel} — ` : ''}`
        + `${displayBatchLabel({ batch_id: data.batch_id, batch_ref: data.batch_ref || currentBatchRef })}: `
        + `${accStats.done ?? accStats.passed} done, `
        + `${accStats.failed} errors, ${data.pending} pending`
        + (skipped ? `, ${skipped} skipped` : '');
      const batchStatus = document.getElementById('batch-status');
      if (batchStatus) {
        batchStatus.textContent = statusText;
        batchStatus.dataset.batchPhase = phase;
      }
      const detailStatus = document.getElementById('acc-detail-batch-status');
      if (detailStatus) detailStatus.textContent = statusText;
    } else {
      data = { batch_id: batchId, results: [], pending: 1, total: 0 };
      renderBatchStatus(data);
      const pendingText =
        `Pending — ${displayBatchLabel({ batch_id: batchId })}`;
      const batchStatus = document.getElementById('batch-status');
      if (batchStatus) {
        batchStatus.textContent = pendingText;
        batchStatus.dataset.batchPhase = 'pending';
      }
      const detailStatus = document.getElementById('acc-detail-batch-status');
      if (detailStatus) detailStatus.textContent = pendingText;
    }

    const payload = resp.ok ? data : {};
    if (persist.shouldClearStoredBatch && persist.shouldClearStoredBatch(payload, resp.status)) {
      if (persist.clearCurrentBatchId) persist.clearCurrentBatchId();
      stopBatchPoll();
      markBatchUiRunning(false);
      if (resp.ok && wasPolling) {
        showToast(`All ${data.total} tests complete — avg accuracy: ${data.avg_accuracy}%`);
        allResultsCache = null;
        loadDashboardFilters();
      }
      return;
    }

    if (persist.saveCurrentBatchId) persist.saveCurrentBatchId(batchId);
    markBatchUiRunning(true);
    armBatchPoll(batchId);
  } catch (err) {
    console.warn('Batch poll failed:', err);
    if (persist.saveCurrentBatchId) persist.saveCurrentBatchId(batchId);
    markBatchUiRunning(true);
    armBatchPoll(batchId);
  }
}

function formatAudioLength(seconds) {
  const api = testCaseViewApi();
  if (api.formatAudioLength) return api.formatAudioLength(seconds);
  if (seconds == null || seconds === '') return '—';
  const n = Number(seconds);
  if (!Number.isFinite(n) || n <= 0) return '—';
  if (n >= 60) return `${Math.floor(n / 60)}m ${Math.round(n % 60)}s`;
  return `${Math.round(n)}s`;
}

function renderTestRunsTable(results) {
  paintResultsTable('dashboard', results);
}

function resultDoctorName(r) {
  const name = r?.doctor_name
    || r?.transcription_result?.doctor_details?.doctor_name
    || '';
  const phone = r?.phone || '';
  return String(name || phone || '—');
}

function resultPatientId(r) {
  const v = r?.patient_id
    || r?.patientId
    || r?.transcription_result?.patient_demographics?.abha_id
    || r?.transcription_result?.patient_demographics?.patient_id
    || '';
  if (v === null || v === undefined || v === '') return '—';
  return String(v);
}

function accRenderSummaryTable(results) {
  paintResultsTable('history', results);
}

function renderAccuracyChart(results, selectedBatchIds) {
  const canvas = document.getElementById('accuracy-chart');
  const titleEl = document.getElementById('accuracy-chart-title');
  const noteEl = document.getElementById('accuracy-chart-note');
  const legendEl = document.getElementById('accuracy-chart-legend');
  if (!canvas || typeof Chart === 'undefined') return;

  const api = accuracyChartApi();
  const selected = selectedBatchIds == null ? getSelectedBatchIds() : selectedBatchIds;
  const model = api.buildAccuracyChart
    ? api.buildAccuracyChart(results, selected)
    : {
      mode: 'cases',
      title: 'Accuracy Over Time',
      labels: [],
      values: [],
      note: '',
      legend: [],
    };

  if (titleEl) titleEl.textContent = model.title || 'Accuracy Over Time';
  if (noteEl) {
    noteEl.textContent = model.note || '';
    noteEl.hidden = !model.note;
  }
  if (legendEl) {
    if (model.mode === 'batches' && (model.legend || []).length) {
      legendEl.innerHTML = model.legend.map((item, i) => {
        const color = api.barColor ? api.barColor(i) : '#6C5CE7';
        const pct = item.value == null ? '—' : `${item.value}%`;
        return `<div class="legend-item">
          <span class="legend-dot" style="background:${color}"></span>
          ${esc(item.label)} — ${pct}
        </div>`;
      }).join('');
    } else {
      legendEl.innerHTML = '';
    }
  }

  canvas.style.minWidth = model.mode === 'batches' && model.labels.length > 8
    ? `${model.labels.length * 88}px`
    : '';

  if (accuracyChart) {
    accuracyChart.destroy();
    accuracyChart = null;
  }
  if (model.mode === 'cases' && !model.values.length) return;
  if (model.mode === 'batches' && !(model.labels || []).length) return;

  const colors = (model.labels || []).map((_, i) => (
    api.barColor ? api.barColor(i) : '#6C5CE7'
  ));

  if (model.mode === 'batches') {
    accuracyChart = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: model.labels,
        datasets: [{
          label: 'Batch accuracy',
          data: model.values,
          backgroundColor: colors,
          maxBarThickness: 48,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        scales: {
          y: { min: 0, max: 100 },
          x: {
            ticks: {
              maxRotation: 45,
              minRotation: model.labels.length > 4 ? 30 : 0,
              autoSkip: false,
            },
          },
        },
        plugins: {
          legend: { display: false },
        },
      },
    });
    return;
  }

  accuracyChart = new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: model.labels,
      datasets: [{
        data: model.values,
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
  if (!canvas || typeof Chart === 'undefined') return;

  const scores = (results || []).map(resultScore).filter(s => s != null);

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

function cachedResultById(testId) {
  const api = testCaseViewApi();
  const pools = [
    window._currentResults,
    window._historyResults,
    latestResults,
    allResultsCache,
  ];
  for (const pool of pools) {
    const found = api.findResultByTestId
      ? api.findResultByTestId(pool, testId)
      : (pool || []).find(r => resultStableId(r) === testId);
    if (found) return found;
  }
  return null;
}

async function openTestDetail(testId, opts) {
  const api = testCaseViewApi();
  const id = api.stableTestId
    ? api.stableTestId({ test_id: testId })
    : String(testId || '').trim();
  if (!id) {
    showToast('Cannot open: this row has no stable test case id');
    return;
  }

  const fromRoute = !!(opts && opts.fromRoute);
  if (!fromRoute && window.MedsumPageNav && window.MedsumPageNav.navigate) {
    window.MedsumPageNav.navigate('detail', { testId: id });
    return;
  }

  const detailView = document.getElementById('detail-view');
  if (fromRoute && currentTestId === id && currentDetailResult
      && detailView && detailView.classList.contains('is-active')) {
    return;
  }

  const generation = ++detailOpenGeneration;
  currentTestId = id;

  const cached = cachedResultById(id);
  if (cached && generation === detailOpenGeneration) {
    currentDetailResult = cached;
    renderDetailPage(cached);
  }

  try {
    const resp = await fetch(`${API}/results/${encodeURIComponent(id)}`);
    if (generation !== detailOpenGeneration) return;
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    if (generation !== detailOpenGeneration) return;
    if (api.isStaleOpen && api.isStaleOpen(id, data)) {
      return;
    }
    if (resultStableId(data) && resultStableId(data) !== id) {
      showToast('Opened result did not match the selected test case');
      return;
    }
    currentTestId = id;
    currentDetailResult = data;
    renderDetailPage(data);
  } catch (err) {
    if (generation !== detailOpenGeneration) return;
    if (!cached) showToast(`Failed to load: ${err.message}`);
  }
}

function renderDetailPage(result) {
  if (window.MedsumPageNav && window.MedsumPageNav.setActiveView) {
    window.MedsumPageNav.setActiveView('detail');
  }
  const detailView = document.getElementById('detail-view');
  updateDetailBackLabel();

  const model = testCaseViewApi().detailViewModel
    ? testCaseViewApi().detailViewModel(result)
    : result;
  const openedId = model.test_id || resultStableId(result);
  detailView.setAttribute('data-open-test-id', openedId || '');
  detailView.setAttribute('data-open-audio', model.audio_filename || '');
  detailView.setAttribute('data-gt-source', model.ground_truth_source || '');

  renderInfoBar(result, model);
  const cmpHost = document.getElementById('gt-comparison-host');
  if (cmpHost && window.MedsumSoapSummaryNav) {
    window.MedsumSoapSummaryNav.mount(cmpHost, result);
  }
  const soapGtHost = document.getElementById('soap-gt-report-host');
  if (soapGtHost && window.MedsumSoapGtComparisonReport) {
    window.MedsumSoapGtComparisonReport.mount(soapGtHost, result);
  }
  resetDetailComparisonTabs();
  renderAccuracySummary(result, model);
  const legend = document.getElementById('detail-status-legend');
  if (legend) {
    legend.hidden = true;
    legend.innerHTML = '';
  }
  const latency = document.getElementById('latency-section');
  if (latency) {
    latency.style.display = 'none';
    latency.innerHTML = '';
  }
  const materialsHost = document.getElementById('detail-case-materials');
  if (materialsHost) {
    materialsHost.hidden = true;
    materialsHost.innerHTML = '';
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

  const sections = document.getElementById('detail-sections');
  if (sections) {
    sections.innerHTML = [
      wrapDetailCmpSection('transcription', renderTranscriptionComparison(result)),
      wrapDetailCmpSection('translation', renderTranslationComparison(result)),
      wrapDetailCmpSection('soap', renderSOAPComparison(result)),
      wrapDetailCmpSection('prescription', renderPrescriptionComparison(result)),
      wrapDetailCmpSection('medicine', renderMedicineComparison(result)),
      wrapDetailCmpSection('medication-validation', renderMedicationValidation(result)),
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

function sourceLabel(source) {
  const api = testCaseViewApi();
  if (api.sourceLabel) return api.sourceLabel(source);
  if (source === 'google_drive') return 'Google Drive';
  if (source === 'upload') return 'Frontend upload';
  return source || 'Unknown source';
}

function renderInfoBar(result, model) {
  const box = document.getElementById('detail-info-pairs');
  if (!box) return;
  const fields = (model.info_fields || []).map(field => {
    if (field.key === 'batch') {
      return Object.assign({}, field, { value: displayBatchLabel(result) });
    }
    return field;
  });
  box.innerHTML = fields.map(field => {
    const idAttr = field.id ? ` id="${esc(field.id)}"` : '';
    const metric = field.key === 'audio-length' ? ' data-metric="audio-length"' : '';
    return `<div class="detail-info-pair" data-field="${esc(field.key)}">
      <dt class="detail-info-label">${esc(field.label)}</dt>
      <dd class="detail-info-value"${idAttr}${metric}>${esc(field.value || '—')}</dd>
    </div>`;
  }).join('');
}

function caseMaterialsCardHtml(result, model) {
  const view = model || {};
  const player = view.audio_player && view.audio_url
    ? `<audio id="detail-audio-player" class="detail-audio-player" controls
              src="${esc(view.audio_url)}" preload="metadata">
         Your browser does not support audio playback.
       </audio>`
    : `<p class="empty-sub" data-field="audio-player-empty">
         Playback is available when this case has a Drive audio file.
       </p>`;
  return `
      <div class="detail-stat-card detail-materials-card score-pill muted"
           data-detail-materials data-field="case-materials">
        <span class="detail-stat-label">Case materials</span>
        <div class="detail-audio-block" data-field="audio">
          <div class="detail-audio-meta">
            <div>
              <div class="detail-field-label">Audio file</div>
              <div class="detail-field-value" data-field="audio-filename"
                   title="${esc(view.audio_filename || '—')}">
                ${esc(view.audio_filename || '—')}
              </div>
            </div>
            <div>
              <div class="detail-field-label">Audio source</div>
              <div class="detail-field-value" data-field="audio-source">
                ${esc(sourceLabel(view.audio_source))}
              </div>
            </div>
          </div>
          <div class="detail-audio-player-wrap" data-field="audio-player">
            ${player}
          </div>
        </div>
      </div>`;
}

function renderCaseMaterials(result, model) {
  return caseMaterialsCardHtml(result, model);
}

function latencyShortLabel(item) {
  const labels = {
    translation_time: 'Translation',
    transcription_time: 'Transcription',
    llm_time: 'LLM',
    total_time: 'Total',
    end_to_end: 'End-to-end',
  };
  return labels[item && item.key] || (item && item.label) || '';
}

function latencyCardHtml(result, model) {
  const view = model || (testCaseViewApi().detailViewModel
    ? testCaseViewApi().detailViewModel(result)
    : null);
  const latency = (view && view.latency) || (testCaseViewApi().latencyFigures
    ? testCaseViewApi().latencyFigures(result)
    : { visible: [], has_any: false });
  const visible = latency.visible || [];
  const body = visible.length
    ? `<div class="latency-mini-grid">
          ${visible.map(item => `
            <div class="latency-mini" data-latency-key="${esc(item.key)}"
                 title="${esc(item.label)}">
              <span class="latency-label">${esc(latencyShortLabel(item))}</span>
              <span class="latency-value">${esc(item.display)}</span>
            </div>`).join('')}
        </div>`
    : `<div class="latency-mini-empty">—</div>`;
  return `
      <div class="detail-stat-card detail-latency-card" data-field="latency">
        <span class="detail-stat-label">Latency</span>
        ${body}
      </div>`;
}

function evaluationMetricsCardHtml(acc, pct) {
  const metrics = (acc && acc.metrics) || {};
  const rows = [
    { key: 'fill_rate', label: 'Fill Rate' },
    { key: 'clinical_fact_recall', label: 'Clinical Fact Recall' },
    { key: 'clinical_fact_precision', label: 'Clinical Fact Precision' },
    { key: 'hallucination_rate', label: 'Hallucination Rate' },
    { key: 'critical_fact_accuracy', label: 'Critical-Fact Accuracy' },
  ];
  const body = `<div class="eval-metrics-mini-grid">
          ${rows.map(item => `
            <div class="eval-metrics-mini" data-eval-metric="${esc(item.key)}"
                 title="${esc(item.label)}">
              <span class="eval-metrics-label">${esc(item.label)}</span>
              <span class="eval-metrics-value">${esc(pct(metrics[item.key]))}</span>
            </div>`).join('')}
        </div>`;
  return `
      <div class="detail-stat-card detail-eval-metrics-card" data-field="evaluation-metrics">
        <span class="detail-stat-label">Evaluation Metrics</span>
        ${body}
      </div>`;
}

function renderAccuracySummary(result, model) {
  const container = document.getElementById('accuracy-summary');
  if (!container) return;
  const view = model || (testCaseViewApi().detailViewModel
    ? testCaseViewApi().detailViewModel(result)
    : result);
  const api = testCaseViewApi();
  const pct = api.formatPct || (v => (v == null || v === '' ? '—' : `${Math.round(Number(v))}%`));
  const acc = view.accuracy || {};
  const shown = formatRowStatus(result);
  const evalChip = shown.evaluation && shown.evaluation.show
    ? chipSpan(shown.evaluation, 'evaluation')
    : '';
  const soapPct = acc.skipped
    ? esc(acc.skip_reason || 'No ground truth available')
    : esc(pct(acc.soap_score));

  container.innerHTML = `
    <div class="detail-stat-row">
      <div class="detail-stat-card detail-stat-overall" data-field="accuracy">
        <div class="detail-stat-label">SOAP Accuracy</div>
        <div class="detail-overall-head">
          <div class="detail-stat-value" data-metric="soap-accuracy">${soapPct}</div>
          <div class="detail-meta-chips">
            ${executionChipHtml(result)}${evalChip}
          </div>
        </div>
      </div>
      ${evaluationMetricsCardHtml(acc, pct)}
      ${latencyCardHtml(result, view)}
      ${caseMaterialsCardHtml(result, view)}
    </div>`;
}

function latencyPillHtml(result, model) {
  const view = model || (testCaseViewApi().detailViewModel
    ? testCaseViewApi().detailViewModel(result)
    : null);
  const latency = (view && view.latency) || (testCaseViewApi().latencyFigures
    ? testCaseViewApi().latencyFigures(result)
    : { visible: [], has_any: false });
  const visible = latency.visible || [];
  if (!latency.has_any) return '';
  return `
      <div class="detail-latency-strip" data-field="latency">
        <div class="detail-stat-label">Latency</div>
        <div class="latency-strip-items">
          ${visible.map(item => `
            <span class="latency-inline" data-latency-key="${esc(item.key)}">
              <span class="latency-label">${esc(item.label)}</span>
              <span class="latency-value">${esc(item.display)}</span>
            </span>`).join('')}
        </div>
      </div>`;
}

function renderLatencySection(result, model) {
  const container = document.getElementById('latency-section');
  if (!container) return '';
  const html = latencyPillHtml(result, model);
  if (!html) {
    container.style.display = 'none';
    container.innerHTML = '';
    return '';
  }
  container.style.display = '';
  container.innerHTML = html;
  return html;
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

function stripCaseHeader(text) {
  if (!text) return text;
  const lines = String(text).split('\n');
  const cleaned = [];
  let skipNextBlank = false;
  for (const line of lines) {
    const stripped = line.trim();
    if (/^[Cc]ase\s*\d*\s*:/.test(stripped)) {
      skipNextBlank = true;
      continue;
    }
    if (skipNextBlank && stripped === '') {
      skipNextBlank = false;
      continue;
    }
    skipNextBlank = false;
    cleaned.push(line);
  }
  return cleaned.join('\n').trim();
}

function renderTranscriptionComparison(result) {
  const gt = stripCaseHeader(
    result.ground_truth || result.ground_truth_transcription || ''
  );
  const gen = result.transcription || result.generated_transcription || '';
  const transComp = result.comparison || result.transcription_comparison || {};
  if (!gt && !gen) {
    const reason = transComp.skip_reason || result.accuracy_skip_reason || '';
    if (!reason) return '';
    return makeCollapsible('transcription', '📝 Transcription Comparison',
      `<p class="skip-reason-banner">${esc(reason)}</p>`, {
      defaultOpen: true,
      score: null,
      scoreReason: reason,
      scoreLabel: 'Transcription',
    });
  }

  const comp = result.comparison || result.transcription_comparison || {};
  const score = comp.similarity_score;
  const tr = result.transcription_result || {};
  const sttTime = tr['transcription-time'] ?? tr?.time?.ASR;

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

  const skipReason = comp.skip_reason || result.accuracy_skip_reason || '';
  const gtEmpty = gtHtml || `<em>${esc(skipReason || 'No ground truth transcript found for this audio')}</em>`;
  const genEmpty = genHtml || '<em>No transcription</em>';

  const content = `
        ${scoreHtml}
        <div class="diff-legend-row">
            <span><span class="legend-swatch missing"></span> Missing from generated</span>
            <span><span class="legend-swatch wrong"></span> Not in ground truth</span>
        </div>
        <div class="diff-grid">
            <div class="diff-col">
                <div class="diff-col-header">Ground Truth</div>
                <div class="diff-text">${gtHtml ? gtHtml : gtEmpty}</div>
            </div>
            <div class="diff-col">
                <div class="diff-col-header">Generated</div>
                <div class="diff-text">${genHtml ? genHtml : genEmpty}</div>
            </div>
        </div>
        ${medDiffHtml}
        ${genDiffHtml}`;

  return makeCollapsible('transcription', '📝 Transcription Comparison', content, {
    defaultOpen: true,
    score,
    scoreReason: comp.summary,
    scoreLabel: 'Transcription',
    timeSeconds: sttTime,
    timeLabel: 'STT',
  });
}

function renderTranslationComparison(result) {
  const lang = (result.language || '').toLowerCase();
  const gtTrans = stripCaseHeader(
    result.ground_truth_translation
    || result.translation_ground_truth
    || (lang === 'english' ? (result.ground_truth || result.ground_truth_transcription || '') : '')
  );
  const genTrans = result.generated_translation
    || result.translation
    || result.text_translation
    || result.transcription_result?.debug?.translation
    || '';
  const transCompEarly = result.translation_comparison || {};
  if (!gtTrans && !genTrans) {
    const reason = transCompEarly.skip_reason || '';
    if (!reason) return '';
    return makeCollapsible('translation', '🌐 Translation Comparison',
      `<p class="skip-reason-banner">${esc(reason)}</p>`, {
      defaultOpen: true,
      score: null,
      scoreReason: reason,
      scoreLabel: 'Translation',
    });
  }

  const comp = result.translation_comparison || {};
  const score = comp.similarity_score;
  const tr = result.transcription_result || {};
  const translationTime = tr['translation-time'] ?? tr?.time?.Translation;

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

  const skipReason = comp.skip_reason || '';
  const gtEmpty = gtHtml || `<em>${esc(skipReason || 'No translation ground truth found for this audio')}</em>`;

  const content = `
        ${scoreHtml}
        <div class="diff-legend-row">
            <span><span class="legend-swatch missing"></span> Missing from generated</span>
            <span><span class="legend-swatch wrong"></span> Not in ground truth</span>
        </div>
        <div class="diff-grid">
            <div class="diff-col">
                <div class="diff-col-header">Ground Truth Translation</div>
                <div class="diff-text">${gtHtml ? gtHtml : gtEmpty}</div>
            </div>
            <div class="diff-col">
                <div class="diff-col-header">Generated Translation</div>
                <div class="diff-text">${genHtml || '<em>No translation</em>'}</div>
            </div>
        </div>
        ${diffsHtml}`;

  return makeCollapsible('translation', '🌐 Translation Comparison', content, {
    defaultOpen: true,
    score,
    scoreReason: comp.summary,
    scoreLabel: 'Translation',
    timeSeconds: translationTime,
    timeLabel: 'Translation',
  });
}

function getCellClasses(gtVal, genVal, resultLabel) {
  if (resultLabel) return cellClassesForResult(resultLabel, gtVal, genVal);
  const classified = classifyDisplayResult(gtVal, genVal);
  return { gtClass: classified.gtClass, genClass: classified.genClass, result: classified.result };
}

const SOAP_NA_MARKERS = {
  '': 1, '—': 1, '-': 1, NA: 1, na: 1, 'n/a': 1, 'N/A': 1,
  'Not applicable': 1, 'not applicable': 1,
  'Nothing to report': 1, 'not applicable/established': 1,
};

function isNaDisplayValue(value) {
  const s = String(value ?? '').trim();
  if (!s) return true;
  return !!SOAP_NA_MARKERS[s] || !!SOAP_NA_MARKERS[s.toLowerCase()];
}

const SOAP_ABSENCE_MARKERS = [
  'not present', 'absent', 'denies', 'not reported', 'no cough', 'none present',
];

function isAbsenceDisplayValue(value) {
  const s = String(value ?? '').trim().toLowerCase();
  if (!s) return false;
  return SOAP_ABSENCE_MARKERS.some(m => s === m || s.indexOf(m) !== -1);
}

function classifyDisplayResult(gtVal, genVal) {
  const gtEmpty = isNaDisplayValue(gtVal);
  const genEmpty = isNaDisplayValue(genVal);
  const norm = v => String(v || '').toLowerCase().replace(/[.,\-–—\s]/g, '').trim();
  if (gtEmpty && genEmpty) {
    return { result: 'Missing', gtClass: 'cell-missing-gt', genClass: 'cell-missing-gen' };
  }
  if (gtEmpty && !genEmpty) {
    return { result: 'Hallucination', gtClass: 'cell-hallucination-gt', genClass: 'cell-hallucination-gen' };
  }
  if (!gtEmpty && genEmpty) {
    return { result: 'Incorrect', gtClass: 'cell-correct-gt', genClass: 'cell-incorrect-gen' };
  }
  if (isAbsenceDisplayValue(gtVal) && !isAbsenceDisplayValue(genVal)) {
    return { result: 'Hallucination', gtClass: 'cell-hallucination-gt', genClass: 'cell-hallucination-gen' };
  }
  if (norm(gtVal) === norm(genVal)) {
    return { result: 'Correct', gtClass: '', genClass: '' };
  }
  return { result: 'Incorrect', gtClass: 'cell-correct-gt', genClass: 'cell-incorrect-gen' };
}

function cellClassesForResult(resultLabel, gtVal, genVal) {
  const key = String(resultLabel || '').trim();
  if (key === 'NA' || key === 'N/A') {
    return { result: 'NA', gtClass: 'cell-na', genClass: 'cell-na' };
  }
  if (key === 'Correct') return { result: 'Correct', gtClass: '', genClass: '' };
  if (key === 'Missing') {
    return { result: 'Missing', gtClass: 'cell-missing-gt', genClass: 'cell-missing-gen' };
  }
  if (key === 'Hallucination') {
    return { result: 'Hallucination', gtClass: 'cell-hallucination-gt', genClass: 'cell-hallucination-gen' };
  }
  if (key === 'Incorrect') {
    return { result: 'Incorrect', gtClass: 'cell-correct-gt', genClass: 'cell-incorrect-gen' };
  }
  return classifyDisplayResult(gtVal, genVal);
}

function encodedSoapCells(resultLabel, gtVal, genVal) {
  const key = String(resultLabel || '').trim();
  const gtText = soapDisplayValue(gtVal);
  const genText = soapDisplayValue(genVal);
  if (key === 'Missing') {
    return { ground_truth: gtText, generated: '—', gt_empty: false, gen_empty: true };
  }
  if (key === 'Hallucination') {
    return { ground_truth: '—', generated: genText, gt_empty: true, gen_empty: false };
  }
  return { ground_truth: gtText, generated: genText, gt_empty: false, gen_empty: false };
}

function soapFactsFromResult(result) {
  const api = window.MedsumGtComparisonTable || {};
  if (typeof api.soapFactsFromResult === 'function') {
    return api.soapFactsFromResult(result);
  }
  const soap = result?.soap_comparison || {};
  const pair = soap.gt_vs_generated && typeof soap.gt_vs_generated === 'object'
    ? soap.gt_vs_generated : soap;
  if (Array.isArray(pair.facts)) return pair.facts;
  if (Array.isArray(soap.facts)) return soap.facts;
  return [];
}

function normalizeSoapResult(raw) {
  const api = window.MedsumGtComparisonTable || {};
  if (typeof api.normalizeResultType === 'function') {
    return api.normalizeResultType(raw);
  }
  return String(raw || '').trim();
}

function displaySoapFilterResult(raw) {
  const nav = window.MedsumSoapSummaryNav || {};
  if (typeof nav.displayFilterResult === 'function') {
    return nav.displayFilterResult(raw);
  }
  const label = normalizeSoapResult(raw);
  if (label === 'NA' || label === 'N/A') return 'Missing';
  if (label === 'Missing') return 'Incorrect';
  return label || 'Correct';
}

function humanFieldName(fact) {
  const name = String(fact?.field || fact?.base_field || '').trim();
  if (!name) return 'Unknown';
  if (name.indexOf('_') !== -1 && name.indexOf(' ') === -1) {
    return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }
  return name;
}

function soapDisplayValue(value) {
  return isNaDisplayValue(value) ? '—' : String(value ?? '').trim();
}

function soapResultCss(label) {
  const key = String(label || '').toLowerCase();
  if (key === 'correct') return 'soap-result-correct';
  if (key === 'incorrect') return 'soap-result-incorrect';
  if (key === 'missing') return 'soap-result-missing';
  if (key === 'hallucination') return 'soap-result-hallucination';
  return 'soap-result-na';
}

function soapFactSection(fact) {
  const raw = String(fact?.section || '').trim();
  if (raw) return raw.charAt(0).toUpperCase() + raw.slice(1);
  return 'Other';
}

function soapFactTable(facts) {
  const order = ['Subjective', 'Objective', 'Assessment', 'Plan'];
  const buckets = { Subjective: [], Objective: [], Assessment: [], Plan: [], Other: [] };
  (facts || []).forEach(fact => {
    const prompt1 = normalizeSoapResult(fact.result || fact.type) || 'Correct';
    const result = displaySoapFilterResult(prompt1);
    const section = soapFactSection(fact);
    const titled = order.find(name => name.toLowerCase() === section.toLowerCase()) || 'Other';
    buckets[titled].push({
      field_name: humanFieldName(fact),
      ground_truth: fact.ground_truth,
      generated: fact.generated,
      result: result,
    });
  });
  const sections = order.concat(['Other']).filter(name => buckets[name].length);
  if (!sections.length) return '<em>No SOAP facts</em>';
  const blocks = sections.map(name => {
    const rows = buckets[name].map(row => {
      const css = soapResultCss(row.result);
      const cells = encodedSoapCells(row.result, row.ground_truth, row.generated);
      const classes = cellClassesForResult(row.result, row.ground_truth, row.generated);
      return `<tr data-soap-result="${esc(row.result)}" class="${css}">
            <td class="soap-field-name">${esc(row.field_name)}</td>
            <td class="${classes.gtClass}" data-soap-cell="gt">${esc(cells.ground_truth)}</td>
            <td class="${classes.genClass}" data-soap-cell="gen">${esc(cells.generated)}</td>
            <td class="soap-result-cell ${css}">${esc(row.result)}</td>
        </tr>`;
    }).join('');
    return `<h4 class="soap-section-heading">${esc(name)}</h4>
        <div class="detail-table-scroll"><table class="soap-compare-table soap-fact-table">
            <thead><tr>
                <th>Field Name</th>
                <th>Ground Truth</th>
                <th>Generated Output</th>
                <th>Result</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table></div>`;
  }).join('');
  return `<div data-soap-fact-root>${blocks}</div>`;
}

function soapFieldTable(gtSection, genSection, sectionKey) {
  if (!gtSection && !genSection) return '<em>No data</em>';

  const gt = typeof gtSection === 'object' && gtSection && !Array.isArray(gtSection) ? gtSection : {};
  const gen = typeof genSection === 'object' && genSection && !Array.isArray(genSection) ? genSection : {};
  const allKeys = [...new Set([...Object.keys(gt), ...Object.keys(gen)])];

  const rows = allKeys.map(field => {
    const gtVal = gt[field];
    const genVal = gen[field];
    if (typeof gtVal === 'object' || typeof genVal === 'object') {
      return '';
    }
    const classified = classifyDisplayResult(gtVal, genVal);
    const label = humanFieldName({ field });
    const cells = encodedSoapCells(classified.result, gtVal, genVal);
    const classes = cellClassesForResult(classified.result, gtVal, genVal);
    return `<tr data-soap-result="${esc(classified.result)}">
            <td class="soap-field-name">${esc(label)}</td>
            <td class="${classes.gtClass}" data-soap-cell="gt">${esc(cells.ground_truth)}</td>
            <td class="${classes.genClass}" data-soap-cell="gen">${esc(cells.generated)}</td>
            <td class="soap-result-cell ${soapResultCss(classified.result)}">${esc(classified.result)}</td>
        </tr>`;
  }).join('');

  return `<div class="detail-table-scroll"><table class="soap-compare-table soap-fact-table">
        <thead><tr>
            <th>Field Name</th>
            <th>Ground Truth</th>
            <th>Generated Output</th>
            <th>Result</th>
        </tr></thead>
        <tbody>${rows}</tbody>
    </table></div>`;
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
  const facts = soapFactsFromResult(result);
  detailCmpUi.soapFacts = facts;

  if (!hasKeys(gtSOAP) && !hasKeys(genSOAP) && !hasKeys(comp) && !facts.length) {
    const reason = comp.skip_reason || '';
    if (!reason) return '';
    return makeCollapsible('soap', '📋 SOAP Comparison',
      `<p class="skip-reason-banner">${esc(reason)}</p>`, {
      defaultOpen: true,
      score: null,
      scoreReason: reason,
      scoreLabel: 'SOAP',
    });
  }

  const skipBanner = comp.skip_reason
    ? `<p class="skip-reason-banner">${esc(comp.skip_reason)}</p>`
    : '';

  const content = `<div data-soap-fact-host>${soapFactTable(facts)}</div>`;

  const soapGenScore = scores.gt_vs_generated ?? comp.gt_vs_generated?.similarity_score
    ?? comp.gt_vs_generated?.overall_weighted_clinical_score
    ?? comp.overall_weighted_clinical_score;
  const scoreReason = comp.gt_vs_generated?.summary || comp.summary;

  const scoreRow = soapGenScore != null ? `
        <div class="section-score-row">
            ${scorePill(soapGenScore, scoreReason, 'SOAP', 'soap-gt-gen')}
        </div>` : '';

  const tr = result.transcription_result || {};
  const llmTime = tr['llm-time'] ?? tr?.time?.llm;

  return makeCollapsible('soap', '📋 SOAP Comparison', skipBanner + scoreRow + content, {
    defaultOpen: true,
    score: soapGenScore,
    scoreReason,
    scoreLabel: 'SOAP',
    timeSeconds: llmTime,
    timeLabel: 'LLM',
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

  const { gtClass: complaintGtClass, genClass: complaintGenClass } =
    getCellClasses(gtComplaint || '—', genComplaint || '—');
  const { gtClass: diagnosisGtClass, genClass: diagnosisGenClass } =
    getCellClasses(gtDiagnosis || '—', genDiagnosis || '—');
  const complaintDiff = !!(complaintGtClass || complaintGenClass);
  const diagnosisDiff = !!(diagnosisGtClass || diagnosisGenClass);

  const complaintHtml = `
    <div class="rx-row">
        <div class="rx-label">Chief Complaint</div>
        <div class="rx-cols">
            <div class="rx-col">
                <div class="rx-col-header">Ground Truth</div>
                <div class="rx-value ${complaintGtClass}">${esc(gtComplaint) || '<em class="na">—</em>'}</div>
            </div>
            <div class="rx-col">
                <div class="rx-col-header">Generated</div>
                <div class="rx-value ${complaintGenClass}">${esc(genComplaint) || '<em class="na">—</em>'}
                    ${complaintDiff ? '<span class="diff-flag-cell">⚠</span>' : ''}
                </div>
            </div>
        </div>
    </div>`;

  const diagnosisHtml = `
    <div class="rx-row">
        <div class="rx-label">Diagnosis</div>
        <div class="rx-cols">
            <div class="rx-col">
                <div class="rx-col-header">Ground Truth</div>
                <div class="rx-value ${diagnosisGtClass}">${esc(gtDiagnosis) || '<em class="na">—</em>'}</div>
            </div>
            <div class="rx-col">
                <div class="rx-col-header">Generated</div>
                <div class="rx-value ${diagnosisGenClass}">${esc(genDiagnosis) || '<em class="na">—</em>'}
                    ${diagnosisDiff ? '<span class="diff-flag-cell">⚠</span>' : ''}
                </div>
            </div>
        </div>
    </div>`;

  const MED_FIELDS = ['drug_name', 'dose', 'schedule', 'duration', 'instructions'];
  const maxMeds = Math.max(gtMeds.length, genMeds.length);

  let medDiffs = 0;
  const medsHtml = Array.from({ length: maxMeds }, (_, i) => {
    const gt = gtMeds[i] || {};
    const gen = genMeds[i] || {};
    const name = gt.drug_name || gen.drug_name || `Drug ${i + 1}`;

    let drugDiffs = 0;
    const fields = MED_FIELDS.map(f => {
      const gtVal = String(gt[f] || '—');
      const genVal = String(gen[f] || '—');
      const { gtClass, genClass } = getCellClasses(gtVal, genVal);
      const isDiff = !!(gtClass || genClass);
      if (isDiff) {
        drugDiffs++;
        medDiffs++;
      }
      return `
        <div class="rx-med-field">
            <span class="rx-med-key">${esc(f.replace(/_/g, ' '))}</span>
            <span class="rx-med-gt ${gtClass}">${esc(gtVal)}</span>
            <span class="rx-arrow">→</span>
            <span class="rx-med-gen ${genClass}">${esc(genVal)}</span>
            <span class="diff-flag-cell">${isDiff ? '⚠' : ''}</span>
        </div>`;
    }).join('');

    const drugBadge = drugDiffs === 0
      ? '<span class="score-pill high" style="font-size:11px;padding:2px 8px">✓</span>'
      : `<span class="score-pill ${drugDiffs <= 1 ? 'warn' : 'low'}" style="font-size:11px;padding:2px 8px">${drugDiffs} diff${drugDiffs !== 1 ? 's' : ''}</span>`;

    return makeCollapsible(`rx-med-${i}`, `💊 ${esc(name)}`, fields, {
      defaultOpen: true,
      headerRight: `<span onclick="event.stopPropagation()">${drugBadge}</span>`,
    });
  }).join('');

  const content = complaintHtml + diagnosisHtml
    + (maxMeds
      ? `<div class="rx-meds-heading">Medications</div>${medsHtml}`
      : '<p class="na" style="margin-top:0.75rem">No medications</p>');

  const totalDiffs = (complaintDiff ? 1 : 0) + (diagnosisDiff ? 1 : 0) + medDiffs;
  const badge = totalDiffs === 0
    ? '<span class="score-pill high" style="font-size:11px;padding:2px 8px">✓ Match</span>'
    : `<span class="score-pill ${totalDiffs <= 3 ? 'warn' : 'low'}" style="font-size:11px;padding:2px 8px">${totalDiffs} diff${totalDiffs !== 1 ? 's' : ''}</span>`;

  return makeCollapsible('prescription', '📋 Prescription Comparison', content, {
    defaultOpen: true,
    headerRight: `<span onclick="event.stopPropagation()">${badge}</span>`,
  });
}

function classifyMedNameDiff(gtName, genName) {
  const gt = String(gtName || '').trim() || '—';
  const gen = String(genName || '').trim() || '—';
  const empty = v => !v || v === '—';

  if (gt === gen) return { type: '', gtClass: '', genClass: '' };

  if (!empty(gt) && empty(gen)) {
    return { type: 'Missing', gtClass: 'cell-missing-gt', genClass: 'cell-missing-gen' };
  }
  if (empty(gt) && !empty(gen)) {
    return { type: 'Name difference', gtClass: '', genClass: 'cell-incorrect-gen' };
  }

  if (gt.toLowerCase() === gen.toLowerCase()) {
    return { type: 'Case difference', gtClass: 'cell-correct-gt', genClass: 'cell-incorrect-gen' };
  }

  const stripSpaces = s => s.toLowerCase().replace(/\s+/g, '');
  if (stripSpaces(gt) === stripSpaces(gen)) {
    return { type: 'Spacing difference', gtClass: 'cell-correct-gt', genClass: 'cell-incorrect-gen' };
  }

  const norm = s => s.toLowerCase().replace(/[\s.,\-]/g, '').trim();
  if (norm(gt) === norm(gen)) {
    return { type: 'Spacing difference', gtClass: 'cell-correct-gt', genClass: 'cell-incorrect-gen' };
  }

  return { type: 'Name difference', gtClass: 'cell-correct-gt', genClass: 'cell-incorrect-gen' };
}

function renderMedicineComparison(result) {
  const gtMeds = result.soap_ground_truth?.plan?.medications || [];
  const genSOAP = generatedSOAPFromResult(result);
  const genMeds = genSOAP.plan?.medications
    || result.transcription_result?.plan?.medications
    || [];

  if (!gtMeds.length && !genMeds.length) return '';

  const maxLen = Math.max(gtMeds.length, genMeds.length);

  let diffCount = 0;
  const rows = Array.from({ length: maxLen }, (_, i) => {
    const gtName = gtMeds[i]?.drug_name || '—';
    const genName = genMeds[i]?.drug_name || '—';
    const { type, gtClass, genClass } = classifyMedNameDiff(gtName, genName);
    if (type) diffCount++;

    return `<tr>
            <td class="med-field">${i + 1}</td>
            <td class="${gtClass}">${esc(gtName)}</td>
            <td class="${genClass}">${esc(genName)}</td>
            <td class="diff-flag-cell">
                ${type
                  ? `<span class="diff-type-badge">${esc(type)}</span>`
                  : '<span class="match-badge">✓</span>'}
            </td>
        </tr>`;
  }).join('');

  const badge = diffCount === 0
    ? '<span class="score-pill high" style="font-size:11px">✓ All Match</span>'
    : `<span class="score-pill low" style="font-size:11px">${diffCount} Name Diff${diffCount !== 1 ? 's' : ''}</span>`;

  const content = `
        <div class="detail-table-scroll"><table class="soap-compare-table">
            <thead><tr>
                <th style="width:6%">#</th>
                <th style="width:35%">Ground Truth</th>
                <th style="width:35%">Generated</th>
                <th style="width:24%">Difference</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table></div>`;

  return makeCollapsible('medicine-comparison', '💊 Medicine Comparison', content, {
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
  const maxLen = Math.max(finalMeds.length, rawMeds.length, gtMeds.length);
  const hasGT = gtMeds.length > 0;
  const colHeaders = hasGT
    ? '<th>Field</th><th>Ground Truth</th><th>Raw LLM</th><th>Final Generated</th>'
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

      if (hasGT) {
        const vsFinal = getCellClasses(gtVal, finalVal);
        const vsRaw = getCellClasses(gtVal, rawVal);
        if (vsFinal.gtClass || vsFinal.genClass) drugDiffs++;
        return `<tr>
                    <td class="med-field">${esc(field.replace(/_/g, ' '))}</td>
                    <td class="${vsFinal.gtClass}">${esc(gtVal)}</td>
                    <td class="${vsRaw.genClass}">${esc(rawVal)}</td>
                    <td class="${vsFinal.genClass}">${esc(finalVal)}</td>
                </tr>`;
      }
      const vsFinal = getCellClasses(rawVal, finalVal);
      if (vsFinal.gtClass || vsFinal.genClass) drugDiffs++;
      return `<tr>
                    <td class="med-field">${esc(field.replace(/_/g, ' '))}</td>
                    <td class="${vsFinal.gtClass}">${esc(rawVal)}</td>
                    <td class="${vsFinal.genClass}">${esc(finalVal)}</td>
                </tr>`;
    }).join('');

    const badge = drugDiffs === 0
      ? '<span class="score-pill high" style="font-size:11px;padding:2px 8px">✓</span>'
      : `<span class="score-pill ${drugDiffs <= 1 ? 'warn' : 'low'}" style="font-size:11px;padding:2px 8px">${drugDiffs} diff${drugDiffs !== 1 ? 's' : ''}</span>`;

    const content = `
            <div class="detail-table-scroll"><table class="med-compare-table">
                <thead><tr>${colHeaders}</tr></thead>
                <tbody>${rows}</tbody>
            </table></div>`;

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

  const norm = s => String(s)
    .toLowerCase()
    .replace(/[.,\-–—;:!?()'"""'']/g, '')
    .replace(/\s+/g, '')
    .trim();

  const aWords = a ? String(a).split(/\s+/).filter(Boolean) : [];
  const bWords = b ? String(b).split(/\s+/).filter(Boolean) : [];

  if (!aWords.length) {
    return { gtHtml: '', genHtml: esc(b) };
  }
  if (!bWords.length) {
    const gtHtml = aWords.map(w =>
      `<span class="diff-missing-gt" title="Missing in generated output">${esc(w)}</span>`
    ).join(' ');
    return {
      gtHtml,
      genHtml: '<span class="diff-missing-gen-placeholder">—</span>',
    };
  }

  const aNorm = new Set(aWords.map(norm).filter(Boolean));
  const bNorm = new Set(bWords.map(norm).filter(Boolean));

  const gtHtml = aWords.map(w => {
    const n = norm(w);
    if (n && !bNorm.has(n)) {
      return `<span class="diff-missing-gt" title="Missing in generated output">${esc(w)}</span>`;
    }
    return `<span>${esc(w)}</span>`;
  }).join(' ');

  const genHtml = bWords.map(w => {
    const n = norm(w);
    if (n && !aNorm.has(n)) {
      return `<span class="diff-incorrect-gen" title="Not present in ground truth">${esc(w)}</span>`;
    }
    return `<span>${esc(w)}</span>`;
  }).join(' ');

  return { gtHtml, genHtml };
}

function pageNavApi() {
  return window.MedsumPageNav || {};
}

function handlePageChange(route) {
  const page = route && route.page;
  if (page !== 'detail') {
    detailOpenGeneration += 1;
    currentTestId = null;
    currentDetailResult = null;
    const detailView = document.getElementById('detail-view');
    if (detailView) detailView.setAttribute('data-open-test-id', '');
  }
  if (page === 'dashboard' || page === 'runs' || page === 'load-testing') {
    lastListView = page;
  }
  if (page === 'runs') onHistoryFilterChange();
  if (page === 'load-testing') ltUpdateRowCount();
  updateDetailBackLabel();
}

function updateDetailBackLabel() {
  const btn = document.getElementById('back-btn');
  if (!btn) return;
  const labels = {
    dashboard: '← Back to Dashboard',
    runs: '← Back to Test Runs',
    'load-testing': '← Back to Load Testing',
  };
  btn.textContent = labels[lastListView] || labels.dashboard;
}

function showDashboard() {
  const nav = pageNavApi();
  if (nav.navigate) {
    nav.navigate('dashboard');
    return;
  }
  lastListView = 'dashboard';
  if (nav.setActiveView) nav.setActiveView('dashboard');
}

function backToDashboardFromDetail() {
  detailOpenGeneration += 1;
  const dest = lastListView === 'runs' || lastListView === 'load-testing'
    ? lastListView
    : 'dashboard';
  const nav = pageNavApi();
  if (nav.navigate) {
    nav.navigate(dest);
    return;
  }
  showDashboard();
}

function showTestRuns() {
  const nav = pageNavApi();
  if (nav.navigate) {
    nav.navigate('runs');
    return;
  }
  lastListView = 'runs';
  if (nav.setActiveView) nav.setActiveView('runs');
  onHistoryFilterChange();
}

function downloadReport(format) {
  if (!currentTestId) return;
  window.location.href = `${API}/report/${currentTestId}?format=${format}`;
}

function downloadSoapGtComparison(format) {
  if (!currentTestId) return;
  if (window.MedsumSoapGtComparisonReport && window.MedsumSoapGtComparisonReport.downloadUrl) {
    window.location.href = window.MedsumSoapGtComparisonReport.downloadUrl(currentTestId, format);
    return;
  }
  window.location.href = `${API}/report/${currentTestId}/soap-comparison?format=${format || 'json'}`;
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

function showToast(msg, type) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.className = 'toast' + (type ? ` toast-${type}` : '');
  toast.style.display = '';
  setTimeout(() => { toast.style.display = 'none'; }, 4000);
}

async function runSingleTest(language, audioFilename) {
  const doctors = accGetActiveDoctors();
  const patientId = doctors[0]?.patients?.[0];
  if (!patientId) {
    showToast(
      'Add a Patient ID in Doctor & Patient Setup first.',
      'warning'
    );
    if (!accSetupOpen) accToggleSetup();
    return;
  }

  const catalogHit = audioCatalog.find(row =>
    String(row.audio || row.audio_filename || '').toLowerCase()
    === String(audioFilename || '').toLowerCase()
  );
  const res = await fetch(`${API}/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      language,
      audio_filename: audioFilename,
      ai_model: document.getElementById('ai-model-select')?.value || 'gpt-4o-mini',
      patient_id: patientId,
      upload_id: (catalogHit && catalogHit.upload_id) || '',
      source: (catalogHit && catalogHit.source) || '',
    }),
  });
  const data = await res.json();
  if (!res.ok) {
    showToast(`Run failed: ${data.error || res.status}`);
    return;
  }
  showToast(`Test started: ${(data.test_id || '').slice(0, 8)}…`);
  return data;
}

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

window.openTestDetail = openTestDetail;
window.renderDetailPage = renderDetailPage;
window.renderCaseMaterials = renderCaseMaterials;
window.toggleSection = toggleSection;
window.onHistoryFilterChange = onHistoryFilterChange;
window.onDashboardFilterChange = onDashboardFilterChange;
window.getSelectedBatchIds = getSelectedBatchIds;
window.setBatchFilterPanelOpen = setBatchFilterPanelOpen;
window.switchTableTab = switchTableTab;
window.onResultsTableFilter = onResultsTableFilter;
window.onResultsTableSort = onResultsTableSort;
window.onResultsTablePage = onResultsTablePage;

// ── Load Testing ─────────────────────────────────────────────────────────────

function showLoadTesting() {
  const nav = pageNavApi();
  if (nav.navigate) {
    nav.navigate('load-testing');
    return;
  }
  lastListView = 'load-testing';
  if (nav.setActiveView) nav.setActiveView('load-testing');
  ltUpdateRowCount();
}

function showDashboardFromLT() {
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
             onchange="ltRows[${idx}].phone=this.value; persistLtForm()"
             style="width:100%;padding:8px;border:1px solid var(--border);
                    border-radius:6px;font-size:14px">
    </td>
    <td>
      <div style="display:flex;align-items:center;gap:6px">
        <input type="password" value="${esc(password)}"
               placeholder="Password"
               id="lt-pwd-${idx}"
               onchange="ltRows[${idx}].password=this.value; persistLtForm()"
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
             onchange="ltRows[${idx}].patientId=this.value; persistLtForm(); if (ltMode === 'manual') ltRenderPerRowUploads();"
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
  persistLtForm();
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

window.persistDoctorForm = persistDoctorForm;
window.persistLtForm = persistLtForm;
window.resumeWatchedBatch = resumeWatchedBatch;
window.accToggleSetup = accToggleSetup;
window.accAddDoctor = accAddDoctor;
window.accRemoveDoctor = accRemoveDoctor;
window.accAddPatient = accAddPatient;
window.accSyncDoctorCredentials = accSyncDoctorCredentials;
window.accApplyDoctorSavedState = accApplyDoctorSavedState;
window.accShowPatientEditorError = accShowPatientEditorError;
window.accFlashPatientSaved = accFlashPatientSaved;
window.accRemovePatient = accRemovePatient;
window.accStartChangePatient = accStartChangePatient;
window.accCommitChangePatient = accCommitChangePatient;
window.accCancelChangePatient = accCancelChangePatient;
window.accPatientKeydown = accPatientKeydown;
window.accTogglePwd = accTogglePwd;
window.accExportConfig = accExportConfig;
window.accImportConfig = accImportConfig;
window.accHandleConfigImport = accHandleConfigImport;
window.showDashboard = showDashboard;
window.showTestRuns = showTestRuns;
window.showLoadTesting = showLoadTesting;
window.backToDashboardFromDetail = backToDashboardFromDetail;
window.runAllTests = runAllTests;
window.runSingleTest = runSingleTest;
window.setAudioSourceTab = setAudioSourceTab;
window.onMultiAudioFilterInput = onMultiAudioFilterInput;
window.selectAllMultiAudio = selectAllMultiAudio;
window.clearMultiAudio = clearMultiAudio;
window.clearAllSelectedFiles = clearAllSelectedFiles;
window.openManualGtEditor = openManualGtEditor;
window.closeManualGtEditor = closeManualGtEditor;
window.saveManualGtEditor = saveManualGtEditor;
window.accHandleLocalAudio = accHandleLocalAudio;
window.accExcludeRunAudio = accExcludeRunAudio;
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
