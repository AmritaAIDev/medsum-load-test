/**
 * Case-detail SOAP tab — field-by-field comparison table.
 * Categories ordered: Symptoms & History → Diagnosis → Medicines →
 * Medication instructions → Investigation → Vitals → Allergies & Follow-up.
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

  /** Preferred sub-field order within each clinical category. */
  const SUBFIELD_ORDER = {
    'Symptoms & History': [
      'Chief complaint',
      'History of present illness',
      'Past medical history',
      'Current medications',
      'Social history',
      'Family history',
    ],
    Diagnosis: [
      'Diagnosis',
      'Diagnosis type',
      'Diagnosis status',
      'Assessment reasoning',
    ],
    Medicines: [
      'Drug name',
      'Dose',
      'Schedule',
      'Duration',
    ],
    'Medication Instructions': [
      'Instructions',
    ],
    Investigation: [
      'Investigations',
    ],
    'Vitals and measurements': [
      'Blood pressure',
      'Pulse',
      'Heart rate',
      'Respiratory rate',
      'Temperature',
      'Heart exam',
      'Other findings',
    ],
    'Allergies & Follow-up Plan': [
      'Allergy',
      'Follow-up',
      'Activity',
      'Education',
      'Summary',
    ],
  };

  const API_BASE = '/api/batches';

  let abortController = null;
  let lastPayload = null;
  let filters = { category: '', subCategory: '' };
  let openFilter = null;

  function hostEl() {
    return document.getElementById('gt-comparison-host')
      || document.querySelector('[data-soap-comparison-host]');
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

  function categoryLabel(name) {
    return CATEGORY_LABELS[name] || name || '';
  }

  function categoryIndex(name) {
    const idx = CATEGORIES.indexOf(name);
    return idx >= 0 ? idx : CATEGORIES.length;
  }

  function bareFieldName(field) {
    return String(field || '')
      .replace(/\s*\[\d+\]\s*$/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function subfieldIndex(category, field) {
    const order = SUBFIELD_ORDER[category] || [];
    const bare = bareFieldName(field).toLowerCase();
    for (let i = 0; i < order.length; i++) {
      if (order[i].toLowerCase() === bare) return i;
    }
    return order.length;
  }

  function comparisonRows(payload) {
    return Array.isArray(payload && payload.comparison_rows)
      ? payload.comparison_rows.slice()
      : [];
  }

  function sortedRows(rows) {
    return rows.slice().sort((a, b) => {
      const ca = categoryIndex(a.category);
      const cb = categoryIndex(b.category);
      if (ca !== cb) return ca - cb;
      const fa = subfieldIndex(a.category, a.field);
      const fb = subfieldIndex(b.category, b.field);
      if (fa !== fb) return fa - fb;
      const la = String(a.field || '').localeCompare(String(b.field || ''), undefined, {
        numeric: true,
        sensitivity: 'base',
      });
      if (la !== 0) return la;
      return String(a.id || '').localeCompare(String(b.id || ''));
    });
  }

  function filteredRows(payload) {
    return sortedRows(comparisonRows(payload)).filter((row) => {
      if (filters.category && row.category !== filters.category) return false;
      if (filters.subCategory) {
        const field = String(row.field || '').trim();
        const bare = bareFieldName(field);
        if (field !== filters.subCategory && bare !== filters.subCategory) return false;
      }
      return true;
    });
  }

  function filtersActive() {
    return !!(filters.category || filters.subCategory);
  }

  function uniqueSubCategories(payload) {
    const rows = comparisonRows(payload).filter((row) => {
      if (filters.category && row.category !== filters.category) return false;
      return !!(row.field && String(row.field).trim());
    });
    const seen = new Map();
    sortedRows(rows).forEach((row) => {
      const label = String(row.field || '').trim();
      const bare = bareFieldName(label);
      const key = bare.toLowerCase();
      if (!seen.has(key)) seen.set(key, bare || label);
    });
    return [...seen.values()];
  }

  function dropdownOptionHtml(value, label, selected) {
    return `
      <button type="button" class="cf-filter-option${selected ? ' is-selected' : ''}"
              role="option" data-soap-cmp-option="${esc(value)}"
              aria-selected="${selected ? 'true' : 'false'}">${esc(label)}</button>`;
  }

  function filterDropdownHtml(key, label, options, selectedValue) {
    const open = openFilter === key;
    const selected = options.find((o) => o.value === selectedValue);
    const display = (selected && selected.label) || label;
    const opts = options.map((o) => dropdownOptionHtml(o.value, o.label, o.value === selectedValue)).join('');
    return `
      <div class="cf-filter-dd${open ? ' is-open' : ''}" data-soap-cmp-filter="${esc(key)}">
        <button type="button" class="cf-filter-trigger"
                data-soap-cmp-trigger="${esc(key)}"
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
    const subOpts = [{ value: '', label: 'All sub-categories' }].concat(
      uniqueSubCategories(payload).map((name) => ({ value: name, label: name }))
    );
    return `
      <div class="soap-cmp-filters cf-filters" aria-label="SOAP comparison filters">
        <span class="cf-filters-label">Filter:</span>
        ${filterDropdownHtml('category', 'All categories', categoryOpts, filters.category)}
        ${filterDropdownHtml('subCategory', 'All sub-categories', subOpts, filters.subCategory)}
        <button type="button" class="soap-cmp-clear${filtersActive() ? '' : ' is-disabled'}"
                data-soap-cmp-clear ${filtersActive() ? '' : 'disabled'}>Clear filters</button>
      </div>`;
  }

  function reviewBadge(result) {
    const label = String(result || '—').trim() || '—';
    const key = label.toLowerCase();
    const icon = key === 'correct'
      ? '<span class="soap-cmp-review-check" aria-hidden="true">✓</span>'
      : '<span class="soap-cmp-review-dot" aria-hidden="true"></span>';
    return `<span class="soap-cmp-review soap-cmp-review-${esc(key)}">${icon}${esc(label)}</span>`;
  }

  function rowClass(result) {
    const key = String(result || '').toLowerCase();
    if (key === 'correct') return 'soap-cmp-row-correct';
    if (key === 'wrong') return 'soap-cmp-row-wrong';
    if (key === 'missing') return 'soap-cmp-row-missing';
    if (key === 'invented') return 'soap-cmp-row-invented';
    if (key === 'partial') return 'soap-cmp-row-partial';
    return '';
  }

  function tableHtml(payload) {
    const rows = filteredRows(payload);
    const body = rows.length
      ? rows.map((row) => {
        const gt = (row.ground_truth && String(row.ground_truth).trim()) || '—';
        const gen = (row.generated && String(row.generated).trim()) || '—';
        const sub = (row.field && String(row.field).trim()) || '—';
        return `
          <tr class="${rowClass(row.result)}">
            <td class="soap-cmp-cat">${esc(categoryLabel(row.category || ''))}</td>
            <td class="soap-cmp-sub">${esc(sub)}</td>
            <td>${esc(gt)}</td>
            <td>${esc(gen)}</td>
            <td>${reviewBadge(row.result)}</td>
          </tr>`;
      }).join('')
      : `<tr class="soap-cmp-empty"><td colspan="5">${filtersActive()
        ? 'No SOAP fields match the current filters.'
        : 'No SOAP comparison fields available for this recording.'}</td></tr>`;

    return `
      <div class="soap-cmp-table-wrap">
        <table class="soap-cmp-table">
          <thead>
            <tr>
              <th>Category</th>
              <th>Sub-category</th>
              <th>Ground-truth SOAP</th>
              <th>MedSum SOAP</th>
              <th>Review</th>
            </tr>
          </thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function panelHtml(payload) {
    const visible = filteredRows(payload);
    return `
      <div class="soap-cmp-panel">
        <div class="soap-cmp-head">
          <h3 class="soap-cmp-title">SOAP comparison</h3>
          <span class="soap-cmp-badge">Full structured note, field by field</span>
        </div>
        ${filtersHtml(payload)}
        <p class="soap-cmp-count">${esc(String(visible.length))} field${visible.length === 1 ? '' : 's'} shown</p>
        ${tableHtml(payload)}
      </div>`;
  }

  function closeMenus() {
    openFilter = null;
  }

  function bind(el, payload) {
    el.querySelectorAll('[data-soap-cmp-trigger]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const key = btn.getAttribute('data-soap-cmp-trigger') || '';
        openFilter = openFilter === key ? null : key;
        paint(payload);
      });
    });

    el.querySelectorAll('[data-soap-cmp-option]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const dd = btn.closest('[data-soap-cmp-filter]');
        const key = dd ? dd.getAttribute('data-soap-cmp-filter') : '';
        const value = btn.getAttribute('data-soap-cmp-option') || '';
        if (key === 'category') {
          filters.category = value;
          filters.subCategory = '';
        }
        if (key === 'subCategory') filters.subCategory = value;
        closeMenus();
        paint(payload);
      });
    });

    const clearBtn = el.querySelector('[data-soap-cmp-clear]');
    if (clearBtn) {
      clearBtn.addEventListener('click', () => {
        filters = { category: '', subCategory: '' };
        closeMenus();
        paint(payload);
      });
    }

    if (!bind._docBound) {
      bind._docBound = true;
      document.addEventListener('click', (event) => {
        if (!openFilter) return;
        const host = hostEl();
        if (host && host.contains(event.target)) {
          if (event.target.closest && event.target.closest('[data-soap-cmp-filter]')) return;
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
    el.innerHTML = panelHtml(payload || {});
    bind(el, payload || {});
  }

  function renderLoading() {
    const el = hostEl();
    if (!el) return;
    el.innerHTML = '<div class="soap-cmp-state" role="status">Loading SOAP comparison…</div>';
  }

  function renderError(message) {
    const el = hostEl();
    if (!el) return;
    el.innerHTML = `<div class="soap-cmp-state is-error">${esc(message || 'Could not load SOAP comparison.')}</div>`;
  }

  function renderEmpty() {
    const el = hostEl();
    if (!el) return;
    el.innerHTML = '';
  }

  function detailsUrl(batchId, recordingId) {
    const batch = encodeURIComponent(batchId || 'all');
    const id = encodeURIComponent(recordingId || '');
    return `${API_BASE}/${batch}/recordings/${id}/details/`;
  }

  function mountWithPayload(payload) {
    filters = { category: '', subCategory: '' };
    openFilter = null;
    paint(payload || {});
    return payload || null;
  }

  async function mount(options) {
    const opts = options || {};
    const el = hostEl();
    if (!el) return null;
    if (opts.payload) return mountWithPayload(opts.payload);

    const recordingId = opts.recordingId || opts.testId || '';
    const batchId = opts.batchId || 'all';
    if (!recordingId) {
      renderEmpty();
      return null;
    }
    filters = { category: '', subCategory: '' };
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
      return mountWithPayload(body.data || body);
    } catch (err) {
      if (err && err.name === 'AbortError') return null;
      if (batchId && batchId !== 'all') {
        try {
          const resp = await fetch(detailsUrl('all', recordingId));
          const body = await resp.json().catch(() => ({}));
          if (resp.ok) return mountWithPayload(body.data || body);
        } catch (_retry) {
          /* fall through */
        }
      }
      renderError(err.message || 'Could not load SOAP comparison.');
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
    SUBFIELD_ORDER,
    mount,
    mountWithPayload,
    mountFromResult,
    clear: renderEmpty,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumSoapComparison = api;
})(typeof window !== 'undefined' ? window : globalThis);
