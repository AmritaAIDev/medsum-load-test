/**
 * Clinical Fact Accuracy table — 7 SOAP categories + All Categories totals.
 * Fetches GET /api/batches/{batchId}/accuracy-by-category/?test_type=&model=
 */
(function (root) {
  const CATEGORIES = [
    'Symptoms & History',
    'Diagnosis',
    'Medicines',
    'Medication Instructions',
    'Investigation',
    'Vitals and measurements',
    'Allergies & Follow-up Plan',
  ];
  const API_BASE = '/api/batches';
  const STATUS_META = {
    pass: { label: 'Pass', icon: '✓' },
    review: { label: 'Review', icon: '⚠' },
    fail: { label: 'Fail', icon: '✗' },
    na: { label: 'N/A', icon: '' },
  };

  let abortController = null;
  let lastPayload = null;
  let expanded = new Set();
  let expandAll = false;
  let onRowClick = null;

  function sectionEl() {
    return document.getElementById('accuracy-table-section')
      || document.getElementById('clinical-accuracy-section')
      || document.querySelector('[data-accuracy-table]');
  }

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function emptyMetrics() {
    return {
      ground_truth: 0,
      correct: 0,
      missed: 0,
      wrong: 0,
      invented: 0,
      accuracy_percent: null,
      runs_evaluated: 0,
      status: 'na',
      has_ground_truth: false,
    };
  }

  function statusKey(row) {
    const raw = String((row && row.status) || 'na').toLowerCase();
    return STATUS_META[raw] ? raw : 'na';
  }

  function formatCount(value) {
    const n = Number(value);
    return Number.isFinite(n) ? String(n) : '0';
  }

  function formatPct(value, hasGt) {
    if (!hasGt || value == null || value === '') return 'N/A';
    const n = Number(value);
    if (!Number.isFinite(n)) return 'N/A';
    return `${n.toFixed(1)}%`;
  }

  function collectModels(rows) {
    const names = new Set();
    (rows || []).forEach((row) => {
      const name = String(
        (row && (row.ai_model_used || row.ai_model || row.llm_model)) || ''
      ).trim();
      if (name) names.add(name);
    });
    return [...names].sort((a, b) => a.localeCompare(b));
  }

  function dashboardFilters() {
    const params = new URLSearchParams(root.location ? root.location.search : '');
    const typeEl = document.getElementById('type-filter');
    const modelEl = document.getElementById('model-filter');
    const selected = root.getSelectedBatchIds ? root.getSelectedBatchIds() : [];
    return {
      batchIds: Array.isArray(selected) ? selected : [],
      testType: (typeEl && typeEl.value) || params.get('test_type') || params.get('testType') || 'All',
      model: (modelEl && modelEl.value) || params.get('model') || 'All',
    };
  }

  function syncFilterQuery(testType, model) {
    if (!root.history || !root.location) return;
    const params = new URLSearchParams(root.location.search);
    const typeVal = String(testType || 'all').toLowerCase();
    const modelVal = String(model || 'all');
    if (typeVal && typeVal !== 'all') params.set('test_type', typeVal);
    else params.delete('test_type');
    if (modelVal && modelVal.toLowerCase() !== 'all') params.set('model', modelVal);
    else params.delete('model');
    const qs = params.toString();
    const hash = root.location.hash || '#dashboard';
    const next = `${root.location.pathname}${qs ? `?${qs}` : ''}${hash}`;
    const current = `${root.location.pathname}${root.location.search}${root.location.hash}`;
    if (next !== current) root.history.replaceState(null, '', next);
  }

  function applyQueryToFilters() {
    if (!root.location) return;
    const params = new URLSearchParams(root.location.search);
    const typeVal = params.get('test_type') || params.get('testType');
    const modelVal = params.get('model');
    const typeEl = document.getElementById('type-filter');
    const modelEl = document.getElementById('model-filter');
    if (typeEl && typeVal) {
      const wanted = String(typeVal).toLowerCase();
      const match = [...typeEl.options].find((opt) => opt.value.toLowerCase() === wanted);
      if (match) typeEl.value = match.value;
    }
    if (modelEl && modelVal) {
      const wanted = String(modelVal);
      if (![...modelEl.options].some((opt) => opt.value === wanted)) {
        const extra = document.createElement('option');
        extra.value = wanted;
        extra.textContent = wanted;
        modelEl.appendChild(extra);
      }
      modelEl.value = wanted;
    }
  }

  function populateModelFilter(rows) {
    const sel = document.getElementById('model-filter');
    if (!sel) return;
    const previous = sel.value || 'all';
    const models = collectModels(rows);
    sel.innerHTML = '';
    const all = document.createElement('option');
    all.value = 'all';
    all.textContent = 'All';
    sel.appendChild(all);
    models.forEach((name) => {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    });
    if (previous && previous !== 'all' && !models.includes(previous)) {
      const extra = document.createElement('option');
      extra.value = previous;
      extra.textContent = previous;
      sel.appendChild(extra);
    }
    sel.value = [...sel.options].some((opt) => opt.value === previous) ? previous : 'all';
  }

  function metricsUrl(batchId, testType, model, batchIds) {
    const id = encodeURIComponent(batchId || 'all');
    const params = new URLSearchParams();
    params.set('test_type', testType || 'All');
    params.set('model', model || 'All');
    if (batchIds && batchIds.length) params.set('batch_ids', batchIds.join(','));
    return `${API_BASE}/${id}/accuracy-by-category/?${params.toString()}`;
  }

  function statusBadge(row) {
    const key = statusKey(row);
    const meta = STATUS_META[key];
    const icon = meta.icon ? `<span class="status-icon" aria-hidden="true">${meta.icon}</span>` : '';
    return `<span class="status ${esc(key)} status-${esc(key)}">${icon}${esc(meta.label)}</span>`;
  }

  function thresholdLines(name, threshold, reviewRatio) {
    const spec = threshold || {};
    const ratio = Math.round((reviewRatio || 0.8) * 100);
    const lines = [
      `Missed limit: ${spec.max_missed_pct != null ? spec.max_missed_pct : 0}% of ground-truth facts`,
      `Wrong limit: ${spec.max_wrong_pct != null ? spec.max_wrong_pct : 0}% of ground-truth facts`,
    ];
    if (spec.max_invented != null) {
      lines.push(`Invented limit: ${spec.max_invented} (safety-critical)`);
    } else {
      lines.push('Invented facts are counted but have no hard cap');
    }
    if (spec.zero_missed) lines.push('Any missed fact fails this category');
    if (spec.safety_critical) lines.push('Safety-critical: invented facts are a safety fail');
    lines.push(`REVIEW when a rate is at least ${ratio}% of its limit`);
    if (name === 'All Categories') {
      lines.unshift('Overall status is the worst category status (Fail > Review > Pass).');
    }
    return lines;
  }

  function detailHtml(name, row, thresholds, reviewRatio) {
    const spec = (thresholds && thresholds[name]) || {};
    const gt = Number(row.ground_truth) || 0;
    const missedPct = gt ? ((Number(row.missed) || 0) * 100 / gt).toFixed(1) : '0.0';
    const wrongPct = gt ? ((Number(row.wrong) || 0) * 100 / gt).toFixed(1) : '0.0';
    const lines = thresholdLines(name, spec, reviewRatio)
      .map((line) => `<li>${esc(line)}</li>`)
      .join('');
    const note = !row.has_ground_truth
      ? '<p class="detail-note">Accuracy was not calculated — no SOAP ground truth in the selected runs.</p>'
      : `<p class="detail-note">This category: missed ${esc(missedPct)}%, wrong ${esc(wrongPct)}%, invented ${esc(formatCount(row.invented))}. Runs evaluated: ${esc(formatCount(row.runs_evaluated))}.</p>`;
    return `
      <div class="detail-row-inner">
        <p class="detail-title">${esc(name)} thresholds</p>
        <ul>${lines}</ul>
        ${note}
      </div>`;
  }

  function rowHtml(name, row, options) {
    const data = row || emptyMetrics();
    const hasGt = !!data.has_ground_truth;
    const isTotal = !!options.total;
    const key = name;
    const open = expandAll || expanded.has(key);
    return `
      <tr class="category-row${isTotal ? ' is-total' : ''}" data-category="${esc(key)}">
        <td>
          <div class="col-category">
            <span class="category-name">${esc(name)}</span>
          </div>
        </td>
        <td class="metric-value">${hasGt ? esc(formatCount(data.ground_truth)) : 'N/A'}</td>
        <td class="metric-value correct">${esc(formatCount(data.correct))}</td>
        <td class="metric-value missed">${esc(formatCount(data.missed))}</td>
        <td class="metric-value wrong">${esc(formatCount(data.wrong))}</td>
        <td class="metric-value invented">${esc(formatCount(data.invented))}</td>
        <!--
        <td class="metric-value accuracy">${esc(formatPct(data.accuracy_percent, hasGt))}</td>
        -->
        <td>${statusBadge(data)}</td>
      </tr>
      <!--
      <tr class="detail-row${open ? ' is-open' : ''}" data-detail-for="${esc(key)}" ${open ? '' : 'hidden'}>
        <td colspan="8">${detailHtml(name, data, options.thresholds, options.reviewRatio)}</td>
      </tr>
      -->`;
  }

  function summaryHtml(overall) {
    const row = overall || emptyMetrics();
    const passed = Number(row.categories_passed) || 0;
    const total = Number(row.categories_total) || 0;
    const facts = Number(row.ground_truth) || 0;
    const acc = formatPct(row.accuracy_percent, !!row.has_ground_truth);
    return `
      <div class="accuracy-summary-stats" aria-label="Clinical accuracy summary">
        <div class="accuracy-stat">
          <div class="accuracy-stat-value">${esc(passed)} / ${esc(total)}</div>
          <div class="accuracy-stat-label">Categories Passed</div>
        </div>
        <div class="accuracy-stat">
          <div class="accuracy-stat-value">${esc(acc)}</div>
          <div class="accuracy-stat-label">Overall Accuracy</div>
        </div>
        <div class="accuracy-stat">
          <div class="accuracy-stat-value">${esc(facts)}</div>
          <div class="accuracy-stat-label">Total Facts</div>
        </div>
      </div>`;
  }

  function passRuleHtml() {
    return `
      <div class="pass-rule">
        <p><strong>Pass rule.</strong> PASS meets every category threshold. REVIEW is at least 80% of a limit. FAIL exceeds a limit or a safety-critical rule (Diagnosis / Medicines: 0 invented; Allergies &amp; Follow-up Plan: 0 invented and 0 missed).</p>
      </div>`;
  }

  function tableHtml(payload) {
    const data = payload || {};
    const categories = data.categories || {};
    const overall = data.overall || emptyMetrics();
    const thresholds = data.thresholds || {};
    const reviewRatio = Number(data.review_ratio) || 0.8;
    const rows = CATEGORIES.map((name) => rowHtml(name, categories[name], {
      thresholds,
      reviewRatio,
    })).join('');
    const totals = rowHtml('All Categories', overall, {
      total: true,
      thresholds: { 'All Categories': {} },
      reviewRatio,
    });
    return `
      <div class="accuracy-table-head">
        <div>
          <h3>Clinical Fact Accuracy</h3>
          <p class="accuracy-table-sub">Extracted facts vs SOAP ground truth, by clinical category</p>
        </div>
        <!--
        <button type="button" class="btn-outline accuracy-details-toggle accuracy-details-btn" data-accuracy-toggle>
          ${expandAll ? 'Hide Details' : 'Show Details'}
        </button>
        -->
      </div>
      <!-- KPI cards hidden for now
      ${summaryHtml(overall)}
      -->
      ${data.note ? `<p class="accuracy-table-note">${esc(data.note)}</p>` : ''}
      <div class="accuracy-table-wrap">
        <table class="clinical-accuracy-table">
          <thead>
            <tr>
              <th>Category</th>
              <th>Ground Truth</th>
              <th>Correct</th>
              <th>Missed</th>
              <th>Wrong</th>
              <th>Invented</th>
              <!--
              <th>Accuracy %</th>
              -->
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
            ${totals}
          </tbody>
        </table>
      </div>
      ${passRuleHtml()}`;
  }

  function renderLoading() {
    const el = sectionEl();
    if (!el) return;
    el.hidden = false;
    el.innerHTML = `
      <div class="accuracy-table-head">
        <div>
          <h3>Clinical Fact Accuracy</h3>
          <p class="accuracy-table-sub">Loading category metrics…</p>
        </div>
      </div>
      <div class="accuracy-table-state" role="status">Loading clinical fact accuracy…</div>`;
  }

  function renderError(message) {
    const el = sectionEl();
    if (!el) return;
    el.hidden = false;
    el.innerHTML = `
      <div class="accuracy-table-head">
        <div>
          <h3>Clinical Fact Accuracy</h3>
          <p class="accuracy-table-sub">Could not load category metrics</p>
        </div>
      </div>
      <div class="accuracy-table-state is-error" role="alert">${esc(message || 'Accuracy metrics are unavailable.')}</div>`;
  }

  function bindSection(el) {
    /* Row expand and Show Details are commented out.
    el.querySelector('[data-accuracy-toggle]')?.addEventListener('click', () => {
      expandAll = !expandAll;
      expanded = expandAll
        ? new Set(CATEGORIES.concat(['All Categories']))
        : new Set();
      if (lastPayload) paint(lastPayload);
    });
    el.querySelectorAll('.category-row').forEach((row) => {
      const toggle = () => {
        const name = row.getAttribute('data-category') || '';
        const allNames = CATEGORIES.concat(['All Categories']);
        if (expandAll) {
          expandAll = false;
          expanded = new Set(allNames.filter((item) => item !== name));
        } else if (expanded.has(name)) {
          expanded.delete(name);
        } else {
          expanded.add(name);
        }
        if (typeof onRowClick === 'function') onRowClick(name);
        if (lastPayload) paint(lastPayload);
      };
      row.addEventListener('click', toggle);
      row.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          toggle();
        }
      });
    });
    */
  }

  function paint(payload) {
    const el = sectionEl();
    if (!el) return;
    el.hidden = false;
    lastPayload = payload;
    el.innerHTML = tableHtml(payload);
    bindSection(el);
  }

  async function fetchMetrics(batchId, testType, model, batchIds) {
    if (abortController) abortController.abort();
    abortController = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    const url = metricsUrl(batchId, testType, model, batchIds);
    const resp = await fetch(url, { signal: abortController ? abortController.signal : undefined });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = new Error((body && body.error) || `Could not load accuracy (${resp.status})`);
      err.status = resp.status;
      throw err;
    }
    return body.data || body;
  }

  async function refresh(options) {
    const el = sectionEl();
    if (!el) return;
    const opts = options || {};
    const filters = dashboardFilters();
    const batchIds = opts.batchIds != null ? opts.batchIds : filters.batchIds;
    const testType = opts.testType != null ? opts.testType : filters.testType;
    const model = opts.model != null ? opts.model : filters.model;
    if (opts.onRowClick) onRowClick = opts.onRowClick;
    const batchId = batchIds && batchIds.length === 1 ? batchIds[0] : (opts.batchId || 'all');
    const extraIds = batchIds && batchIds.length > 1 ? batchIds : [];
    syncFilterQuery(testType, model);
    renderLoading();
    try {
      const data = await fetchMetrics(batchId, testType, model, extraIds);
      paint(data);
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      renderError(err && err.message ? err.message : 'Could not load clinical fact accuracy.');
    }
  }

  function refreshFromDashboard(options) {
    const el = sectionEl();
    if (!el) return;
    const opts = options || {};
    if (opts.hidden) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    return refresh(opts);
  }

  function mount(options) {
    const opts = options || {};
    if (opts.onRowClick) onRowClick = opts.onRowClick;
    applyQueryToFilters();
    return refresh(opts);
  }

  const api = {
    CATEGORIES,
    collectModels,
    populateModelFilter,
    applyQueryToFilters,
    dashboardFilters,
    refresh,
    refreshFromDashboard,
    mount,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumAccuracyTable = api;
})(typeof window !== 'undefined' ? window : globalThis);
