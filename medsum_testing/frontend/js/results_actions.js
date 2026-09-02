/**
 * Results-table row actions: Download only.
 * Click a row to open the case. Remove (API) excludes the row from this
 * view; it never deletes Drive, uploads, or results/{id}.json.
 */
(function (root) {
  const ROW_ACTIONS = ['Download'];
  const excludedIds = new Set();

  function stableId(row) {
    if (!row) return '';
    if (root.MedsumTestCaseView && root.MedsumTestCaseView.stableTestId) {
      return String(root.MedsumTestCaseView.stableTestId(row) || '');
    }
    return String(row.test_id || row.id || '');
  }

  function excludeFromView(testId) {
    const id = String(testId || '').trim();
    if (id) excludedIds.add(id);
    return id;
  }

  function isExcluded(testId) {
    return excludedIds.has(String(testId || ''));
  }

  function visibleResults(rows) {
    return (rows || []).filter(row => !isExcluded(stableId(row)));
  }

  function rowActionsHtml(testId, esc) {
    const escape = esc || (s => String(s == null ? '' : s));
    const id = escape(testId || '');
    const disabled = testId ? '' : ' disabled';
    return (
      '<span class="row-actions">'
      + `<button type="button" class="view-btn" data-row-action="download" data-test-id="${id}" title="Download this case report"${disabled}>Download</button>`
      + '</span>'
    );
  }

  const api = {
    ROW_ACTIONS,
    REMOVE_DOES_NOT_DELETE_SOURCE: true,
    excludeFromView,
    isExcluded,
    visibleResults,
    rowActionsHtml,
    stableId,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumResultsActions = api;
})(typeof window !== 'undefined' ? window : globalThis);
