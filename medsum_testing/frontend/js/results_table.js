/**
 * One results table. Results / Latency tabs change columns only.
 * Test Case ID stays the leading column. Sort, filter, and page survive tab switch.
 */
(function (root) {
  const TAB_RESULTS = 'results';
  const TAB_LATENCY = 'latency';
  const RESULTS_TAB_HEADERS = [
    'Test Case ID',
    'Audio File',
    'Language',
    'SOAP accuracy',
    'Clinical Quality',
    'Execution Status',
  ];
  const LATENCY_TIMING_HEADERS = [
    'Audio File',
    'Audio Length',
    'Transcription',
    'Translation',
    'SOAP',
    'Total Time',
  ];
  const LATENCY_TAB_HEADERS = ['Test Case ID'].concat(LATENCY_TIMING_HEADERS);
  const DEFAULT_PAGE_SIZE = 50;

  const SORT_FIELDS = {
    'Test Case ID': ['tc_ref', 'test_case_id', 'test_id'],
    test_id: ['test_id', 'tc_ref'],
    tc_ref: ['tc_ref', 'test_id'],
    'Audio File': ['audio_filename', 'filename'],
    audio_filename: ['audio_filename', 'filename'],
    Language: ['language'],
    language: ['language'],
  };

  const states = {};

  function defaultState(overrides) {
    const state = {
      tab: TAB_RESULTS,
      page: 1,
      pageSize: DEFAULT_PAGE_SIZE,
      sortKey: '',
      sortDir: 'asc',
      filter: '',
    };
    Object.assign(state, overrides || {});
    return state;
  }

  function getState(source) {
    const key = source || 'dashboard';
    if (!states[key]) states[key] = defaultState();
    return states[key];
  }

  function setTab(source, tab) {
    const state = getState(source);
    state.tab = tab === TAB_LATENCY ? TAB_LATENCY : TAB_RESULTS;
    return state;
  }

  function setPage(source, page) {
    const state = getState(source);
    state.page = Math.max(1, Number(page) || 1);
    return state;
  }

  function setSort(source, sortKey, sortDir) {
    const state = getState(source);
    if (sortKey && state.sortKey === sortKey && !sortDir) {
      state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
    } else if (sortKey) {
      state.sortKey = sortKey;
      if (sortDir) state.sortDir = sortDir;
    }
    return state;
  }

  function setFilter(source, filter) {
    const state = getState(source);
    state.filter = String(filter || '');
    return state;
  }

  function visibleHeaders(tab) {
    return tab === TAB_LATENCY ? LATENCY_TAB_HEADERS.slice() : RESULTS_TAB_HEADERS.slice();
  }

  function stableId(row) {
    if (root.MedsumTestCaseView && root.MedsumTestCaseView.stableTestId) {
      return String(root.MedsumTestCaseView.stableTestId(row) || '');
    }
    const tid = String((row && row.test_id) || '');
    if (tid && !/^\d{1,12}$/.test(tid)) return tid;
    return '';
  }

  function displayTestCaseId(row) {
    const data = row || {};
    const ref = String(data.tc_ref || data.test_case_id || '').trim();
    if (ref) return ref;
    return stableId(data) || '—';
  }

  function rowIdentities(rows) {
    return (rows || []).map(stableId);
  }

  function haystack(row) {
    const data = row || {};
    return [
      displayTestCaseId(data),
      stableId(data),
      data.audio_filename || data.filename || '',
      data.language || '',
    ].join(' ').toLowerCase();
  }

  function sortValue(row, sortKey) {
    const fields = SORT_FIELDS[sortKey] || [sortKey];
    for (let i = 0; i < fields.length; i++) {
      const val = String((row || {})[fields[i]] || '').trim();
      if (val) return val.toLowerCase();
    }
    if (sortKey === 'Test Case ID' || sortKey === 'test_id' || sortKey === 'tc_ref') {
      return displayTestCaseId(row).toLowerCase();
    }
    return '';
  }

  function applyTableView(rows, state) {
    const cfg = defaultState(state);
    const tab = cfg.tab === TAB_LATENCY ? TAB_LATENCY : TAB_RESULTS;
    let items = (rows || []).slice();
    const query = String(cfg.filter || '').trim().toLowerCase();
    if (query) {
      items = items.filter(row => haystack(row).indexOf(query) !== -1);
    }
    const sortKey = String(cfg.sortKey || '').trim();
    if (sortKey) {
      const reverse = String(cfg.sortDir || 'asc').toLowerCase() === 'desc';
      items = items.slice().sort((a, b) => {
        const av = sortValue(a, sortKey);
        const bv = sortValue(b, sortKey);
        if (av < bv) return reverse ? 1 : -1;
        if (av > bv) return reverse ? -1 : 1;
        return 0;
      });
    }
    const pageSize = Math.max(1, Number(cfg.pageSize) || DEFAULT_PAGE_SIZE);
    const total = items.length;
    const totalPages = Math.max(1, total ? Math.ceil(total / pageSize) : 1);
    let page = Math.max(1, Number(cfg.page) || 1);
    if (page > totalPages) page = totalPages;
    const start = (page - 1) * pageSize;
    const pageRows = items.slice(start, start + pageSize);
    return {
      tab,
      headers: visibleHeaders(tab),
      rows: pageRows,
      identities: rowIdentities(pageRows),
      displayIds: pageRows.map(displayTestCaseId),
      page,
      pageSize,
      total,
      totalPages,
      sortKey,
      sortDir: cfg.sortDir || 'asc',
      filter: query,
    };
  }

  const api = {
    TAB_RESULTS,
    TAB_LATENCY,
    RESULTS_TAB_HEADERS,
    LATENCY_TAB_HEADERS,
    LATENCY_TIMING_HEADERS,
    DEFAULT_PAGE_SIZE,
    defaultState,
    getState,
    setTab,
    setPage,
    setSort,
    setFilter,
    visibleHeaders,
    displayTestCaseId,
    rowIdentities,
    stableId,
    applyTableView,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumResultsTable = api;
})(typeof window !== 'undefined' ? window : globalThis);
