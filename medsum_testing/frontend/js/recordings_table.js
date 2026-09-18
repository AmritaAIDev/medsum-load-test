/**
 * Recordings table under Clinical Fact Accuracy.
 * Clinical View matches the recordings review list; row click opens fact-level review.
 */
(function (root) {
  const API_BASE = '/api/batches';
  const FILTERS = [
    { id: 'all', label: 'All' },
    { id: 'safety', label: 'Safety flag', flag: true },
    { id: 'review', label: 'Needs review' },
    { id: 'pass', label: 'Passed' },
    { id: 'invented', label: 'Has invented fact' },
    { id: 'allergy', label: 'Allergy error' },
    { id: 'dose', label: 'Dose / frequency error' },
    { id: 'hindi', label: 'Hindi numeral error' },
    { id: 'brand', label: 'Brand / sound-alike drug' },
  ];

  let rows = [];
  let filterId = 'all';
  let view = 'clinical';
  let abortController = null;

  function sectionEl() {
    return document.getElementById('recordings-section')
      || document.querySelector('[data-recordings-table]');
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

  function matchesFilter(row, id) {
    if (!row) return false;
    if (id === 'all') return true;
    if (id === 'safety') return !!row.safety_flag;
    if (id === 'review') return row.status === 'review';
    if (id === 'pass') return row.status === 'pass';
    if (id === 'invented') return !!row.has_invented;
    if (id === 'allergy') return !!row.allergy_error;
    if (id === 'dose') return !!row.dose_frequency_error;
    if (id === 'hindi') return !!row.hindi_numeral_error;
    if (id === 'brand') return !!row.brand_error;
    return true;
  }

  function filterCount(id) {
    return rows.filter((row) => matchesFilter(row, id)).length;
  }

  function filteredRows() {
    return rows.filter((row) => matchesFilter(row, filterId));
  }

  function flagSvg() {
    return `<svg class="rec-flag-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path fill="currentColor" d="M3.2 1.5h1.4v13H3.2zm2.2.6 8.2 2.3v5.3L5.4 7.4z"/>
    </svg>`;
  }

  function viewToggleHtml() {
    return `
      <div class="rec-view-toggle" role="group" aria-label="Recordings view">
        <button type="button" class="rec-view-btn${view === 'clinical' ? ' is-active' : ''}" data-rec-view="clinical">Clinical View</button>
        <button type="button" class="rec-view-btn${view === 'detailed' ? ' is-active' : ''}" data-rec-view="detailed">Detailed View</button>
      </div>`;
  }

  function chipsHtml() {
    return FILTERS.map((item) => {
      const count = filterCount(item.id);
      const label = item.id === 'all' ? `${item.label} ${count}` : item.label;
      const flag = item.flag ? flagSvg() : '';
      return `<button type="button" class="rec-chip${filterId === item.id ? ' is-active' : ''}" data-rec-filter="${esc(item.id)}">${flag}${esc(label)}</button>`;
    }).join('');
  }

  function rowHtml(row) {
    const status = String(row.status || 'na');
    const detailed = view === 'detailed';
    return `
      <tr class="rec-row" data-open-test-id="${esc(row.test_id || '')}">
        <td class="rec-flag-cell">${row.safety_flag ? flagSvg() : ''}</td>
        <td class="rec-id">${esc(row.recording || '—')}</td>
        ${detailed ? `<td>${esc(row.audio_filename || '—')}</td><td>${esc(row.language || '—')}</td>` : ''}
        <td>${esc(row.duration || '—')}</td>
        <td>${esc(row.correct)} / ${esc(row.ground_truth)}</td>
        <td>${esc(row.missed)}</td>
        <td>${esc(row.wrong)}</td>
        <td>${esc(row.invented)}</td>
        <td>${esc(row.latency || '—')}</td>
        <td><span class="status-chip status-${esc(status === 'review' ? 'needs-review' : status)}">${esc(row.status_label || 'N/A')}</span></td>
      </tr>`;
  }

  function tableHtml() {
    const items = filteredRows();
    const detailed = view === 'detailed';
    const body = items.length
      ? items.map(rowHtml).join('')
      : `<tr><td colspan="${detailed ? 11 : 9}" class="rec-empty">No recordings match this filter.</td></tr>`;
    return `
      <div class="rec-head">
        <div>
          <h3>Recordings</h3>
          <p class="rec-sub">Click any row to open the fact-level review.</p>
        </div>
        ${viewToggleHtml()}
      </div>
      <div class="rec-chips">${chipsHtml()}</div>
      <p class="rec-count">Showing ${items.length} of ${rows.length}.</p>
      <div class="rec-table-wrap">
        <table class="rec-table">
          <thead>
            <tr>
              <th class="rec-flag-col">${flagSvg()}</th>
              <th>Recording</th>
              ${detailed ? '<th>Audio File</th><th>Language</th>' : ''}
              <th>Duration (mins)</th>
              <th>Correct / GT</th>
              <th>Missed</th>
              <th>Wrong</th>
              <th>Invented</th>
              <th>Latency</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function bindSection(el) {
    el.querySelectorAll('[data-rec-filter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        filterId = btn.getAttribute('data-rec-filter') || 'all';
        paint();
      });
    });
    el.querySelectorAll('[data-rec-view]').forEach((btn) => {
      btn.addEventListener('click', () => {
        view = btn.getAttribute('data-rec-view') === 'detailed' ? 'detailed' : 'clinical';
        paint();
      });
    });
  }

  function paint() {
    const el = sectionEl();
    if (!el) return;
    el.hidden = false;
    el.innerHTML = tableHtml();
    bindSection(el);
  }

  function renderLoading() {
    const el = sectionEl();
    if (!el) return;
    el.hidden = false;
    el.innerHTML = `
      <div class="rec-head">
        <div>
          <h3>Recordings</h3>
          <p class="rec-sub">Loading recordings…</p>
        </div>
      </div>`;
  }

  function renderError(message) {
    const el = sectionEl();
    if (!el) return;
    el.hidden = false;
    el.innerHTML = `
      <div class="rec-head">
        <div>
          <h3>Recordings</h3>
          <p class="rec-sub rec-error">${esc(message || 'Could not load recordings.')}</p>
        </div>
      </div>`;
  }

  function metricsUrl(batchId, testType, model, batchIds) {
    const id = encodeURIComponent(batchId || 'all');
    const params = new URLSearchParams();
    params.set('test_type', testType || 'All');
    params.set('model', model || 'All');
    if (batchIds && batchIds.length) params.set('batch_ids', batchIds.join(','));
    return `${API_BASE}/${id}/recordings/?${params.toString()}`;
  }

  async function refresh(options) {
    const el = sectionEl();
    if (!el) return;
    const opts = options || {};
    const batchIds = opts.batchIds || [];
    const batchId = batchIds.length === 1 ? batchIds[0] : (opts.batchId || 'all');
    const extraIds = batchIds.length > 1 ? batchIds : [];
    renderLoading();
    if (abortController) abortController.abort();
    abortController = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    try {
      const resp = await fetch(
        metricsUrl(batchId, opts.testType, opts.model, extraIds),
        { signal: abortController ? abortController.signal : undefined }
      );
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error((body && body.error) || 'Could not load recordings.');
      rows = ((body.data || body).recordings) || [];
      paint();
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      renderError(err && err.message ? err.message : 'Could not load recordings.');
    }
  }

  function refreshFromDashboard(options) {
    return refresh(options || {});
  }

  const api = { refresh, refreshFromDashboard };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.MedsumRecordingsTable = api;
})(typeof window !== 'undefined' ? window : globalThis);
