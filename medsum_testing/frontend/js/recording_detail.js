/**
 * Per-recording Clinical Facts panel (detail page).
 * GET /api/batches/{batchId}/recordings/{recordingId}/details/
 *
 * Filter dropdowns match MedSum v2.3 Case Details:
 *   All categories | All results | All error tags
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

  const CATEGORY_LABELS = {
    'Medication Instructions': 'Medication instructions',
  };

  const RESULT_OPTIONS = [
    { id: '', label: 'All results' },
    { id: 'Correct', label: 'Correct' },
    { id: 'Missing', label: 'Missing' },
    { id: 'Wrong', label: 'Wrong' },
    { id: 'Partial', label: 'Partial' },
    { id: 'Invented', label: 'Invented' },
  ];

  const DEFAULT_ERROR_TAGS = {
    presence: [
      { id: 'omission', label: 'Omission' },
      { id: 'hallucination', label: 'Hallucination / Invented' },
      { id: 'duplicate', label: 'Duplicate entry' },
      { id: 'misclassified', label: 'Misclassified category' },
    ],
    value: [
      { id: 'wrong_value', label: 'Wrong value/substitution' },
      { id: 'numeric_dose', label: 'Numeric/dose error' },
      { id: 'unit', label: 'Unit error' },
      { id: 'laterality', label: 'Laterality error' },
      { id: 'temporal', label: 'Temporal error' },
      { id: 'negation', label: 'Negation error' },
      { id: 'certainty', label: 'Certainty/hedging error' },
      { id: 'experiencer', label: 'Subject/experiencer error' },
      { id: 'brand', label: 'Brand–generic mapping error' },
      { id: 'abbreviation', label: 'Abbreviation misexpansion' },
      { id: 'partial', label: 'Partial capture' },
    ],
  };

  const API_BASE = '/api/batches';

  const TABLE_COLUMNS = [
    { key: 'category', label: 'Category', minWidth: 96 },
    { key: 'ground_truth', label: 'Ground truth', minWidth: 120 },
    { key: 'generated', label: 'MedSum output', minWidth: 120 },
    { key: 'result', label: 'Result', minWidth: 88 },
    { key: 'safety', label: 'Safety concern?', minWidth: 110 },
    { key: 'error_tag', label: 'Error tag', minWidth: 110 },
  ];
  const DEFAULT_COL_WIDTHS = [130, 200, 200, 96, 120, 140];
  const MIN_COL_WIDTH = 72;

  let abortController = null;
  let lastPayload = null;
  let filters = { category: '', result: '', errorTag: '' };
  let openFilter = null; // category | result | errorTag | null
  let columnWidths = DEFAULT_COL_WIDTHS.slice();
  let columnsUserSized = false;
  let resizeState = null;

  function hostEl() {
    return document.getElementById('recording-clinical-detail')
      || document.querySelector('[data-recording-clinical-detail]');
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

  function formatDuration(seconds) {
    if (seconds == null || seconds === '') return '—';
    const n = Number(seconds);
    if (!Number.isFinite(n) || n < 0) return '—';
    if (n < 60) return `${Math.round(n)}s`;
    const m = Math.floor(n / 60);
    const rem = Math.round(n % 60);
    return `${m}m ${rem}s`;
  }

  function formatCount(value) {
    const n = Number(value);
    return Number.isFinite(n) ? String(n) : '0';
  }

  function categoryLabel(name) {
    return CATEGORY_LABELS[name] || name;
  }

  function errorTagOptions(payload) {
    const opts = (payload && payload.error_tag_options) || DEFAULT_ERROR_TAGS;
    return {
      presence: (opts.presence && opts.presence.length) ? opts.presence : DEFAULT_ERROR_TAGS.presence,
      value: (opts.value && opts.value.length) ? opts.value : DEFAULT_ERROR_TAGS.value,
    };
  }

  function allErrorTagItems(payload) {
    const opts = errorTagOptions(payload);
    return []
      .concat(opts.presence || [])
      .concat(opts.value || []);
  }

  function errorTagLabel(id, payload) {
    if (!id) return 'All error tags';
    const hit = allErrorTagItems(payload).find((item) => item.id === id);
    return (hit && hit.label) || id;
  }

  function resultFilterLabel(id) {
    const hit = RESULT_OPTIONS.find((item) => item.id === id);
    return (hit && hit.label) || 'All results';
  }

  function categoryFilterLabel(id) {
    if (!id) return 'All categories';
    return categoryLabel(id);
  }

  function comparisonRows(payload) {
    const rows = (payload && payload.comparison_rows) || [];
    if (rows.length) return rows;
    // Fallback: expand legacy missed/wrong/invented lists into table rows.
    const cats = (payload && payload.categories) || {};
    const out = [];
    Object.keys(cats).forEach((name) => {
      const cat = cats[name] || {};
      (cat.wrong_facts || []).forEach((text, i) => {
        out.push({
          id: `legacy-wrong-${name}-${i}`,
          category: name,
          ground_truth: String(text || ''),
          generated: '',
          result: 'Wrong',
          error_tag: 'wrong_value',
          error_tag_label: 'Wrong value/substitution',
          error_source: 'Summarisation',
          safety_flagged: false,
          safety_auto_label: '',
        });
      });
      (cat.missed_facts || []).forEach((text, i) => {
        out.push({
          id: `legacy-miss-${name}-${i}`,
          category: name,
          ground_truth: String(text || ''),
          generated: '',
          result: 'Missing',
          error_tag: 'omission',
          error_tag_label: 'Omission',
          error_source: 'Summarisation',
          safety_flagged: /allerg/i.test(name) || /allerg/i.test(String(text || '')),
          safety_auto_label: /allerg/i.test(name) ? 'Missed allergy' : '',
        });
      });
      (cat.invented_facts || []).forEach((text, i) => {
        out.push({
          id: `legacy-inv-${name}-${i}`,
          category: name,
          ground_truth: '',
          generated: String(text || ''),
          result: 'Invented',
          error_tag: 'hallucination',
          error_tag_label: 'Hallucination / Invented',
          error_source: 'Summarisation',
          safety_flagged: true,
          safety_auto_label: 'Invented fact',
        });
      });
    });
    return out;
  }

  function filteredRows(payload) {
    return comparisonRows(payload).filter((row) => {
      if (filters.category && row.category !== filters.category) return false;
      if (filters.result && row.result !== filters.result) return false;
      if (filters.errorTag && row.error_tag !== filters.errorTag) return false;
      return true;
    });
  }

  function filtersActive() {
    return !!(filters.category || filters.result || filters.errorTag);
  }

  function dropdownOptionHtml(value, label, selected) {
    return `
      <button type="button" role="option"
              class="cf-filter-option${selected ? ' is-selected' : ''}"
              data-cf-option="${esc(value)}"
              aria-selected="${selected ? 'true' : 'false'}">${esc(label)}</button>`;
  }

  function filterDropdownHtml(key, label, options, selectedValue) {
    const open = openFilter === key;
    const selectedLabel = options.find((o) => o.value === selectedValue);
    const display = (selectedLabel && selectedLabel.label) || label;
    const opts = options.map((o) => dropdownOptionHtml(o.value, o.label, o.value === selectedValue)).join('');
    return `
      <div class="cf-filter-dd${open ? ' is-open' : ''}" data-cf-filter="${esc(key)}">
        <button type="button" class="cf-filter-trigger"
                data-cf-trigger="${esc(key)}"
                aria-haspopup="listbox"
                aria-expanded="${open ? 'true' : 'false'}">
          <span class="cf-filter-trigger-text">${esc(display)}</span>
          <span class="cf-filter-caret" aria-hidden="true"></span>
        </button>
        <div class="cf-filter-menu" role="listbox" ${open ? '' : 'hidden'}>
          ${opts}
        </div>
      </div>`;
  }

  function filtersHtml(payload) {
    const categoryOpts = [{ value: '', label: 'All categories' }].concat(
      CATEGORIES.map((name) => ({ value: name, label: categoryLabel(name) }))
    );
    const resultOpts = RESULT_OPTIONS.map((item) => ({
      value: item.id,
      label: item.label,
    }));
    const tagOpts = errorTagOptions(payload);

    // Group headers rendered as non-selectable options.
    const errorMenu = `
      <div class="cf-filter-dd${openFilter === 'errorTag' ? ' is-open' : ''}" data-cf-filter="errorTag">
        <button type="button" class="cf-filter-trigger"
                data-cf-trigger="errorTag"
                aria-haspopup="listbox"
                aria-expanded="${openFilter === 'errorTag' ? 'true' : 'false'}">
          <span class="cf-filter-trigger-text">${esc(errorTagLabel(filters.errorTag, payload))}</span>
          <span class="cf-filter-caret" aria-hidden="true"></span>
        </button>
        <div class="cf-filter-menu cf-filter-menu-tags" role="listbox"
             ${openFilter === 'errorTag' ? '' : 'hidden'}>
          ${dropdownOptionHtml('', 'All error tags', !filters.errorTag)}
          <div class="cf-filter-group" role="presentation">Presence errors</div>
          ${tagOpts.presence.map((item) => dropdownOptionHtml(item.id, item.label, filters.errorTag === item.id)).join('')}
          <div class="cf-filter-group" role="presentation">Value errors</div>
          ${tagOpts.value.map((item) => dropdownOptionHtml(item.id, item.label, filters.errorTag === item.id)).join('')}
        </div>
      </div>`;

    return `
      <div class="cf-filters" aria-label="Clinical fact filters">
        <span class="cf-filters-label">Filter:</span>
        ${filterDropdownHtml('category', categoryFilterLabel(filters.category), categoryOpts, filters.category)}
        ${filterDropdownHtml('result', resultFilterLabel(filters.result), resultOpts, filters.result)}
        ${errorMenu}
        <button type="button" class="cf-clear-filters${filtersActive() ? '' : ' is-disabled'}"
                data-cf-clear ${filtersActive() ? '' : 'disabled'}>Clear filters</button>
      </div>`;
  }

  function summaryLineHtml(payload, visible) {
    const s = (payload && payload.summary) || {};
    const total = Number(s.total_ground_truth) || visible.length
      || (Number(s.total_correct) || 0) + (Number(s.total_missed) || 0)
      + (Number(s.total_wrong) || 0) + (Number(s.total_invented) || 0);
    const correct = Number(s.total_correct) || 0;
    const missed = Number(s.total_missed) || 0;
    const wrong = Number(s.total_wrong) || 0;
    const partial = Number(s.total_partial) || 0;
    const invented = Number(s.total_invented) || 0;
    const filterNote = filtersActive()
      ? ` Showing ${visible.length} of ${comparisonRows(payload).length}.`
      : ' Errors are listed first.';
    return `
      <p class="cf-summary-line">
        ${esc(formatCount(total))} clinical facts compared —
        ${esc(formatCount(correct))} correct,
        ${esc(formatCount(missed))} missed,
        ${esc(formatCount(wrong))} wrong,
        ${esc(formatCount(partial))} partial,
        ${esc(formatCount(invented))} invented.${esc(filterNote)}
      </p>`;
  }

  function resultBadge(result) {
    const key = String(result || '').toLowerCase();
    return `<span class="cf-result cf-result-${esc(key)}"><span class="cf-result-dot" aria-hidden="true"></span>${esc(result || '—')}</span>`;
  }

  function safetyCell(row) {
    const flagged = !!(row && row.safety_flagged);
    const auto = (row && row.safety_auto_label) || '';
    return `
      <div class="cf-safety">
        <label class="cf-safety-check">
          <input type="checkbox" ${flagged ? 'checked' : ''} disabled
                 aria-label="Safety concern">
        </label>
        ${auto ? `<span class="cf-safety-auto">auto: ${esc(auto)}</span>` : ''}
        <select class="cf-safety-select" disabled aria-label="Safety flag action">
          <option>${flagged ? 'Clear flag…' : 'Flag…'}</option>
        </select>
      </div>`;
  }

  function errorTagCell(row) {
    const label = (row && (row.error_tag_label || row.error_tag)) || '';
    const source = (row && row.error_source) || '';
    if (!label) return '—';
    return `
      <div class="cf-error-tag">
        <span class="cf-error-tag-name">${esc(label)}</span>
        ${source ? `<span class="cf-error-source">${esc(source)}</span>` : ''}
      </div>`;
  }

  function rowClass(result) {
    const key = String(result || '').toLowerCase();
    if (key === 'wrong') return 'cf-row-wrong';
    if (key === 'missing') return 'cf-row-missing';
    if (key === 'invented') return 'cf-row-invented';
    if (key === 'partial') return 'cf-row-partial';
    return '';
  }

  function colWidthStyle(index) {
    const width = Number(columnWidths[index]) || DEFAULT_COL_WIDTHS[index] || 120;
    return `width:${width}px;min-width:${width}px;max-width:${width}px`;
  }

  function tableHtml(payload) {
    const rows = filteredRows(payload);
    const colgroup = TABLE_COLUMNS.map((col, index) => (
      `<col data-cf-col="${esc(col.key)}" style="${colWidthStyle(index)}">`
    )).join('');
    const headers = TABLE_COLUMNS.map((col, index) => {
      const resizer = index < TABLE_COLUMNS.length - 1
        ? `<span class="cf-col-resizer" data-cf-resize="${index}" role="separator"
                 aria-orientation="vertical" aria-label="Resize ${esc(col.label)} column"
                 title="Drag to resize"></span>`
        : '';
      return `<th scope="col" data-cf-col="${esc(col.key)}" style="${colWidthStyle(index)}">
        <span class="cf-th-label">${esc(col.label)}</span>${resizer}
      </th>`;
    }).join('');
    const body = rows.length
      ? rows.map((row) => {
        const gt = (row.ground_truth && String(row.ground_truth).trim()) || '—';
        const gen = (row.generated && String(row.generated).trim()) || '—';
        return `
          <tr class="${rowClass(row.result)}">
            <td style="${colWidthStyle(0)}">${esc(categoryLabel(row.category || ''))}</td>
            <td style="${colWidthStyle(1)}">${esc(gt)}</td>
            <td style="${colWidthStyle(2)}">${esc(gen)}</td>
            <td style="${colWidthStyle(3)}">${resultBadge(row.result)}</td>
            <td style="${colWidthStyle(4)}">${safetyCell(row)}</td>
            <td style="${colWidthStyle(5)}">${errorTagCell(row)}</td>
          </tr>`;
      }).join('')
      : `<tr class="cf-empty"><td colspan="${TABLE_COLUMNS.length}">${filtersActive()
        ? 'No clinical facts match the current filters.'
        : 'No clinical facts available for this recording.'}</td></tr>`;

    return `
      <div class="cf-table-wrap">
        <table class="cf-table">
          <colgroup>${colgroup}</colgroup>
          <thead>
            <tr>${headers}</tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function columnWidthsTotal() {
    return columnWidths.reduce((sum, w) => sum + (Number(w) || 0), 0);
  }

  function fillDefaultColumnWidths() {
    // Content-sized defaults — do not stretch to the panel width (that left
    // large empty gaps between compact columns like Result / Safety).
    columnWidths = DEFAULT_COL_WIDTHS.slice();
  }

  function ensureTableFillsWidth() {
    if (columnsUserSized) return;
    fillDefaultColumnWidths();
  }

  function applyColumnWidths(el) {
    const table = el.querySelector('.cf-table');
    if (!table) return;
    ensureTableFillsWidth();
    const total = columnWidthsTotal();
    table.style.width = `${total}px`;
    table.style.minWidth = `${total}px`;
    table.style.maxWidth = 'none';
    table.querySelectorAll('col[data-cf-col]').forEach((col, index) => {
      const width = Number(columnWidths[index]) || DEFAULT_COL_WIDTHS[index] || 120;
      col.style.width = `${width}px`;
      col.style.minWidth = `${width}px`;
      col.style.maxWidth = `${width}px`;
    });
    table.querySelectorAll('th[data-cf-col], td').forEach((cell) => {
      if (cell.closest('tr.cf-empty')) return;
      const row = cell.parentElement;
      if (!row) return;
      const index = Array.prototype.indexOf.call(row.children, cell);
      if (index < 0 || index >= columnWidths.length) return;
      const width = Number(columnWidths[index]) || DEFAULT_COL_WIDTHS[index] || 120;
      cell.style.width = `${width}px`;
      cell.style.minWidth = `${width}px`;
      cell.style.maxWidth = `${width}px`;
    });
  }

  function bindColumnResize(el) {
    el.querySelectorAll('[data-cf-resize]').forEach((handle) => {
      handle.addEventListener('mousedown', (event) => {
        event.preventDefault();
        event.stopPropagation();
        const index = Number(handle.getAttribute('data-cf-resize'));
        if (!Number.isFinite(index) || index < 0 || index >= columnWidths.length - 1) return;
        const minWidth = (TABLE_COLUMNS[index] && TABLE_COLUMNS[index].minWidth) || MIN_COL_WIDTH;
        resizeState = {
          index,
          startX: event.clientX,
          startWidth: Number(columnWidths[index]) || DEFAULT_COL_WIDTHS[index] || 120,
          minWidth,
        };
        document.body.classList.add('cf-col-resizing');
      });
    });

    if (!bindColumnResize._docBound) {
      bindColumnResize._docBound = true;
      document.addEventListener('mousemove', (event) => {
        if (!resizeState) return;
        columnsUserSized = true;
        const delta = event.clientX - resizeState.startX;
        const next = Math.max(resizeState.minWidth, Math.round(resizeState.startWidth + delta));
        columnWidths[resizeState.index] = next;
        const host = hostEl();
        if (host) applyColumnWidths(host);
      });
      document.addEventListener('mouseup', () => {
        if (!resizeState) return;
        resizeState = null;
        document.body.classList.remove('cf-col-resizing');
      });
      window.addEventListener('resize', () => {
        if (columnsUserSized) return;
        const host = hostEl();
        if (host && host.querySelector('.cf-table')) applyColumnWidths(host);
      });
    }
  }

  function panelHtml(payload) {
    const visible = filteredRows(payload);
    return `
      <div class="cf-panel recording-clinical-panel">
        ${filtersHtml(payload)}
        ${summaryLineHtml(payload, visible)}
        ${tableHtml(payload)}
      </div>`;
  }

  function closeMenus() {
    openFilter = null;
  }

  function bind(el, payload) {
    el.querySelectorAll('[data-cf-trigger]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const key = btn.getAttribute('data-cf-trigger') || '';
        openFilter = openFilter === key ? null : key;
        paint(payload);
      });
    });

    el.querySelectorAll('[data-cf-option]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const dd = btn.closest('[data-cf-filter]');
        const key = dd ? dd.getAttribute('data-cf-filter') : '';
        const value = btn.getAttribute('data-cf-option') || '';
        if (key === 'category') filters.category = value;
        if (key === 'result') filters.result = value;
        if (key === 'errorTag') filters.errorTag = value;
        closeMenus();
        paint(payload);
      });
    });

    const clearBtn = el.querySelector('[data-cf-clear]');
    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        filters = { category: '', result: '', errorTag: '' };
        closeMenus();
        paint(payload);
      });
    }

    bindColumnResize(el);

    if (!bind._docBound) {
      bind._docBound = true;
      document.addEventListener('click', (event) => {
        if (!openFilter) return;
        const host = hostEl();
        if (host && host.contains(event.target)) {
          if (event.target.closest && event.target.closest('[data-cf-filter]')) return;
        }
        closeMenus();
        if (lastPayload) paint(lastPayload);
      });
      document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !openFilter) return;
        closeMenus();
        if (lastPayload) paint(lastPayload);
      });
    }
  }

  function paint(payload) {
    const el = hostEl();
    if (!el) return;
    lastPayload = payload;
    el.hidden = false;
    el.innerHTML = panelHtml(payload);
    bind(el, payload);
    applyColumnWidths(el);
    // Remeasure after layout in case the panel was just shown.
    requestAnimationFrame(() => {
      if (hostEl() === el && el.querySelector('.cf-table')) applyColumnWidths(el);
    });
  }

  function renderLoading() {
    const el = hostEl();
    if (!el) return;
    el.hidden = false;
    el.innerHTML = '<div class="recording-clinical-state" role="status">Loading clinical fact details…</div>';
  }

  function renderError(message) {
    const el = hostEl();
    if (!el) return;
    el.hidden = false;
    el.innerHTML = `<div class="recording-clinical-state is-error">${esc(message || 'Could not load clinical fact details.')}</div>`;
  }

  function renderEmpty() {
    const el = hostEl();
    if (!el) return;
    el.hidden = true;
    el.innerHTML = '';
  }

  function detailsUrl(batchId, recordingId) {
    const batch = encodeURIComponent(batchId || 'all');
    const id = encodeURIComponent(recordingId || '');
    return `${API_BASE}/${batch}/recordings/${id}/details/`;
  }

  async function mount(options) {
    const opts = options || {};
    const el = hostEl();
    if (!el) return null;
    const recordingId = opts.recordingId || opts.testId || '';
    const batchId = opts.batchId || 'all';
    if (!recordingId) {
      renderEmpty();
      return null;
    }
    filters = { category: '', result: '', errorTag: '' };
    openFilter = null;
    renderLoading();
    if (abortController) abortController.abort();
    abortController = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    try {
      const resp = await fetch(detailsUrl(batchId, recordingId), {
        signal: abortController ? abortController.signal : undefined,
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error((body && (body.error || body.message)) || `HTTP ${resp.status}`);
      }
      const data = body.data || body;
      paint(data);
      return data;
    } catch (err) {
      if (err && err.name === 'AbortError') return null;
      if (batchId && batchId !== 'all') {
        try {
          const resp = await fetch(detailsUrl('all', recordingId));
          const body = await resp.json().catch(() => ({}));
          if (resp.ok) {
            const data = body.data || body;
            paint(data);
            return data;
          }
        } catch (_retry) {
          /* fall through */
        }
      }
      renderError(err.message || 'Could not load clinical fact details.');
      return null;
    }
  }

  function mountFromResult(result) {
    const data = result || {};
    return mount({
      recordingId: data.test_id || data.id || '',
      batchId: data.batch_id || 'all',
    });
  }

  const api = {
    CATEGORIES,
    RESULT_OPTIONS,
    DEFAULT_ERROR_TAGS,
    mount,
    mountFromResult,
    clear: renderEmpty,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumRecordingDetail = api;
})(typeof window !== 'undefined' ? window : globalThis);
