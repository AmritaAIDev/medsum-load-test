/**
 * Ground Truth vs Generated SOAP comparison report (frontend display).
 * Renders the backend payload and downloads CSV / Excel / HTML / PDF / JSON.
 */
(function (root) {
  const FORMATS = [
    { id: 'pdf', label: 'PDF' },
    { id: 'excel', label: 'Excel' },
    { id: 'csv', label: 'CSV' },
    { id: 'html', label: 'HTML' },
    { id: 'json', label: 'JSON' },
  ];

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function apiBase() {
    return (root.MEDSUM_TEST_API || '/api/medsum-test').replace(/\/$/, '');
  }

  function downloadUrl(testId, format) {
    return apiBase()
      + '/report/'
      + encodeURIComponent(testId)
      + '/soap-comparison?format='
      + encodeURIComponent(format);
  }

  function statusClass(status) {
    const key = String(status || '').toUpperCase();
    if (key === 'PASS') return 'pass';
    if (key === 'FAIL') return 'fail';
    return 'partial';
  }

  function sectionTable(section) {
    const rows = (section.rows || []).map(row => {
      const css = row.passed ? 'pass' : 'fail';
      return `<tr class="soap-gt-row soap-gt-row-${css}">
        <td class="soap-field-name">${esc(row.field)}</td>
        <td>${esc(row.ground_truth_full || row.ground_truth)}</td>
        <td>${esc(row.generated_full || row.generated)}</td>
        <td>${esc(row.in_gt)}</td>
        <td class="soap-gt-error-mark">${esc(row.error_found)}</td>
        <td>${esc(row.error_description)}</td>
        <td class="soap-gt-status">${esc(row.status_display || row.status)}</td>
      </tr>`;
    }).join('');
    return `<section class="soap-gt-section" data-soap-gt-section="${esc(section.id)}">
      <h4 class="soap-section-heading">${esc(section.label)}</h4>
      <div class="detail-table-scroll">
        <table class="soap-compare-table soap-gt-report-table">
          <thead><tr>
            <th>Field Name</th>
            <th>Ground Truth Value</th>
            <th>Generated Value</th>
            <th>In GT</th>
            <th>Error Found</th>
            <th>Error Description</th>
            <th>Status</th>
          </tr></thead>
          <tbody>${rows || '<tr><td colspan="7"><em>No fields in this section</em></td></tr>'}</tbody>
        </table>
      </div>
    </section>`;
  }

  function summaryHtml(report) {
    const summary = report.summary || {};
    const br = summary.error_breakdown || {};
    const actions = (summary.action_items || [])
      .map(item => `<li>✗ ${esc(item)}</li>`)
      .join('');
    return `<section class="soap-gt-summary" data-soap-gt-summary>
      <h4 class="soap-section-heading">Comparison Summary</h4>
      <p>
        Total Fields: <strong>${esc(summary.total_fields)}</strong> ·
        PASSED: <strong>${esc(summary.passed)}</strong> (${esc(summary.passed_percent)}%) ·
        FAILED: <strong>${esc(summary.failed)}</strong> (${esc(summary.failed_percent)}%)
      </p>
      <ul class="soap-gt-breakdown">
        <li>Exact Matches: ${esc(br.exact_matches)}</li>
        <li>Type Mismatches: ${esc(br.type_mismatches)}${br.type_mismatch_fields && br.type_mismatch_fields.length ? ' (' + esc(br.type_mismatch_fields.join(', ')) + ')' : ''}</li>
        <li>Format Mismatches: ${esc(br.format_mismatches)}${br.format_mismatch_fields && br.format_mismatch_fields.length ? ' (' + esc(br.format_mismatch_fields.join(', ')) + ')' : ''}</li>
        <li>Value Mismatches: ${esc(br.value_mismatches)}</li>
        <li>Missing from Generated: ${esc(br.missing_from_generated)}</li>
        <li>Extra in Generated: ${esc(br.extra_in_generated)}</li>
        <li>Missing from Both (Correct): ${esc(br.missing_from_both)}</li>
      </ul>
      ${actions ? `<p>Action Items:</p><ul class="soap-gt-actions">${actions}</ul>` : ''}
    </section>`;
  }

  function toolbarHtml(report, testId) {
    const buttons = FORMATS.map(fmt => (
      `<button type="button" class="btn-outline soap-gt-dl-btn"
               data-soap-gt-format="${esc(fmt.id)}"
               data-test-id="${esc(testId)}"
               title="Download ${esc(fmt.label)} comparison report">${esc(fmt.label)}</button>`
    )).join('');
    const metrics = report.metrics || {};
    const metricLine = (metrics.schema_accuracy != null)
      ? `Schema: ${esc(metrics.schema_accuracy)}% · GT match: ${esc(metrics.ground_truth_match)}% · Type errors: ${esc(metrics.type_error_rate)}% · Missing: ${esc(metrics.missing_field_rate)}% · Extra: ${esc(metrics.extra_field_rate)}%`
      : '';
    const badge = statusClass(report.threshold_status || report.status);
    return `<div class="soap-gt-toolbar">
      <div class="soap-gt-toolbar-meta">
        <strong>${esc(report.title || 'GROUND TRUTH vs GENERATED SOAP COMPARISON REPORT')}</strong>
        <p>
          Test Case ID: ${esc(report.test_case_id)} |
          Test Name: ${esc(report.test_name)} |
          <span class="soap-gt-badge soap-gt-badge-${badge}">${esc(report.threshold_status || report.status)}</span>
          · Compliance: <strong>${esc(report.compliance_percent)}%</strong>
          · Generated: ${esc(report.generated_at)}
        </p>
        ${metricLine ? `<p>${metricLine}</p>` : ''}
      </div>
      <div class="soap-gt-downloads" data-soap-gt-downloads>${buttons}</div>
    </div>`;
  }

  function render(report, testId) {
    if (!report || !Array.isArray(report.sections)) {
      return '<p class="empty-sub">No SOAP ground-truth comparison is available for this case.</p>';
    }
    const sections = report.sections.map(sectionTable).join('');
    return `<div class="soap-gt-report" data-soap-gt-report>
      ${toolbarHtml(report, testId || report.test_id || '')}
      ${sections}
      ${summaryHtml(report)}
    </div>`;
  }

  function mount(host, result) {
    if (!host) return;
    const report = result && result.soap_gt_comparison_report;
    const testId = (result && (result.test_id || result.tc_ref)) || '';
    host.innerHTML = render(report, testId);
    bindDownloads(host);
  }

  function bindDownloads(rootEl) {
    const node = rootEl || (typeof document !== 'undefined' ? document : null);
    if (!node || !node.querySelectorAll) return;
    node.querySelectorAll('[data-soap-gt-format]').forEach(btn => {
      if (btn.dataset.soapGtBound === '1') return;
      btn.dataset.soapGtBound = '1';
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-test-id') || '';
        const format = btn.getAttribute('data-soap-gt-format') || 'json';
        if (!id) return;
        root.location.href = downloadUrl(id, format);
      });
    });
  }

  const api = {
    FORMATS,
    render,
    mount,
    downloadUrl,
    bindDownloads,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumSoapGtComparisonReport = api;
})(typeof window !== 'undefined' ? window : globalThis);
