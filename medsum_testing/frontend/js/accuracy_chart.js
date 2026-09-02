/**
 * Dashboard accuracy chart: last-10 cases, or one figure per selected batch.
 * Batch labels follow Prompt 9: Date | incremental (25-Aug-2026 | 001).
 * Case-level scores match Prompt 1 / run-summary (NOT_SCORED omitted).
 */
(function (root) {
  const CASE_CHART_LIMIT = 10;
  const MAX_COMPARISON_BATCHES = 12;
  const CASE_TITLE = 'Accuracy Over Time';
  const BATCH_TITLE = 'Accuracy by Batch';
  const MONTHS = [
    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  const BATCH_REF_RE = /BATCH-(\d{8})-(\d+)/i;
  const PROMPT9_RE = /^(\d{2}-[A-Za-z]{3}-\d{4}) \| (\d+)$/;
  const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})/;
  const BATCH_COLORS = [
    '#6C5CE7', '#00B894', '#FDCB6E', '#E17055',
    '#0984E3', '#A29BFE', '#55EFC4', '#FAB1A0',
    '#74B9FF', '#81ECEC', '#FD79A8', '#636E72',
  ];

  function transcriptionScore(row) {
    const api = root.MedsumRunSummary;
    if (api && api.transcriptionScore) return api.transcriptionScore(row);
    const data = row || {};
    const verdict = String(data.final_result || '').trim().toLowerCase();
    if (data.accuracy_skipped || verdict === 'complete_no_accuracy') return null;
    const comp = data.comparison || data.transcription_comparison || {};
    const score = (comp && comp.similarity_score != null)
      ? comp.similarity_score
      : (data.accuracy_score != null ? data.accuracy_score : data.similarity_score);
    if (score == null || score === '') return null;
    const n = Number(score);
    return Number.isFinite(n) ? n : null;
  }

  function resultScore(row) {
    const data = row || {};
    const score = data.comparison?.similarity_score
      ?? data.transcription_comparison?.similarity_score
      ?? data.accuracy_score
      ?? data.similarity_score
      ?? null;
    if (score == null || score === '') return null;
    const n = Number(score);
    return Number.isFinite(n) ? n : null;
  }

  function selectedBatchIds(ids) {
    return (ids || []).map(v => String(v || '')).filter(id => id && id !== 'all');
  }

  function filterResultsByBatchIds(rows, selectedIds) {
    const items = rows || [];
    const wanted = selectedBatchIds(selectedIds);
    if (!wanted.length) return items;
    const allowed = new Set(wanted);
    return items.filter(r => allowed.has(String(r.batch_id || '')));
  }

  function isChartComplete(row) {
    const status = String(row?.status || '').trim().toLowerCase();
    const verdict = String(row?.final_result || '').trim().toLowerCase();
    return status === 'complete' || verdict === 'pass' || verdict === 'complete_no_accuracy';
  }

  function caseChartPoints(results, limit) {
    const cap = limit == null ? CASE_CHART_LIMIT : limit;
    return (results || [])
      .filter(isChartComplete)
      .map(r => ({
        name: r.audio_filename || r.filename || r.tc_ref || '',
        timestamp: String(r.timestamp || r.created_at || ''),
        score: resultScore(r),
      }))
      .filter(r => r.score != null)
      .sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)))
      .slice(-cap);
  }

  function batchAccuracy(rows) {
    const scores = [];
    (rows || []).forEach(row => {
      const score = transcriptionScore(row);
      if (score != null) scores.push(score);
    });
    if (!scores.length) return null;
    return Math.round((scores.reduce((a, b) => a + b, 0) / scores.length) * 10) / 10;
  }

  function parseBatchDateParts(raw) {
    const text = String(raw || '').trim();
    if (!text) return null;
    let m = ISO_RE.exec(text);
    if (m) {
      const month = Number(m[2]);
      const day = Number(m[3]);
      if (month >= 1 && month <= 12 && day >= 1 && day <= 31) {
        return [Number(m[1]), month, day];
      }
    }
    m = /^(\d{4})(\d{2})(\d{2})/.exec(text.replace(/-/g, ''));
    if (m) {
      const month = Number(m[2]);
      const day = Number(m[3]);
      if (month >= 1 && month <= 12 && day >= 1 && day <= 31) {
        return [Number(m[1]), month, day];
      }
    }
    const dt = new Date(text);
    if (!Number.isNaN(dt.getTime())) {
      return [dt.getUTCFullYear(), dt.getUTCMonth() + 1, dt.getUTCDate()];
    }
    return null;
  }

  function formatBatchDate(raw) {
    const parts = parseBatchDateParts(raw);
    if (!parts) return '';
    const [year, month, day] = parts;
    return `${String(day).padStart(2, '0')}-${MONTHS[month - 1]}-${year}`;
  }

  function parsedBatchSeq(batch) {
    const data = batch || {};
    if (data.batch_seq != null && String(data.batch_seq).trim() !== '') {
      const n = parseInt(String(data.batch_seq).replace(/\D/g, ''), 10);
      return Number.isFinite(n) ? String(n).padStart(3, '0') : null;
    }
    const ref = String(data.batch_id || data.batch_ref || data.run_ref || '').trim();
    const prompt9 = PROMPT9_RE.exec(ref);
    if (prompt9) return String(parseInt(prompt9[2], 10)).padStart(3, '0');
    const m = BATCH_REF_RE.exec(ref);
    if (m) return String(parseInt(m[2], 10)).padStart(3, '0');
    return null;
  }

  function collectBatches(rows) {
    const seen = new Map();
    (rows || []).forEach(row => {
      const batchId = String(row.batch_id || '').trim();
      if (!batchId || seen.has(batchId)) return;
      seen.set(batchId, {
        batch_id: batchId,
        batch_ref: row.batch_ref || row.run_ref || '',
        batch_display_label: row.batch_display_label || '',
        batch_seq: row.batch_seq,
        timestamp: String(row.created_at || row.timestamp || ''),
      });
    });
    return [...seen.values()];
  }

  function looksLikeUuidPrefix(label, batchId) {
    const text = String(label || '');
    const ident = String(batchId || '');
    return Boolean(ident) && text === ident.slice(0, 8);
  }

  function assignBatchLabels(batches) {
    const items = (batches || [])
      .filter(b => b && b.batch_id)
      .map(b => Object.assign({}, b))
      .sort((a, b) => String(a.timestamp || '').localeCompare(String(b.timestamp || '')));
    const perDay = new Map();
    const labels = {};
    items.forEach(batch => {
      const bid = String(batch.batch_id);
      const ref = String(batch.batch_ref || '').trim();
      const prompt9 = PROMPT9_RE.exec(bid) || PROMPT9_RE.exec(ref);
      if (prompt9) {
        labels[bid] = `${prompt9[1]} | ${String(parseInt(prompt9[2], 10)).padStart(3, '0')}`;
        return;
      }
      if (ref && !looksLikeUuidPrefix(ref, bid)) {
        labels[bid] = ref;
        return;
      }
      const parts = parseBatchDateParts(batch.timestamp);
      const dateText = formatBatchDate(batch.timestamp);
      let seq = parsedBatchSeq(batch);
      if (seq == null && parts) {
        const key = parts.join('-');
        const next = (perDay.get(key) || 0) + 1;
        perDay.set(key, next);
        seq = String(next).padStart(3, '0');
      } else if (seq == null) {
        seq = '001';
      }
      labels[bid] = dateText && seq ? `${dateText} | ${seq}` : (seq || bid.slice(0, 8));
    });
    return labels;
  }

  function batchDisplayLabel(batch, labels) {
    const data = batch || {};
    const batchId = String(data.batch_id || '');
    if (labels && labels[batchId]) return labels[batchId];
    return assignBatchLabels([data])[batchId] || batchId.slice(0, 8) || 'Batch';
  }

  function batchSortKey(batch) {
    const parts = parseBatchDateParts(batch.timestamp) || [0, 0, 0];
    const seq = parsedBatchSeq(batch) || '000';
    return `${parts.map(n => String(n).padStart(4, '0')).join('-')}|${seq}|${batch.timestamp || ''}`;
  }

  function buildAccuracyChart(results, selectedIds) {
    const rows = results || [];
    const wanted = selectedBatchIds(selectedIds);
    if (wanted.length < 2) {
      const points = caseChartPoints(rows);
      return {
        mode: 'cases',
        title: CASE_TITLE,
        labels: points.map(p => (p.name || '').slice(0, 12) || 'run'),
        values: points.map(p => p.score),
        truncated: false,
        shown: points.length,
        selected: wanted.length,
        note: '',
        legend: [],
      };
    }

    const grouped = {};
    wanted.forEach(id => { grouped[id] = []; });
    rows.forEach(row => {
      const bid = String(row.batch_id || '');
      if (grouped[bid]) grouped[bid].push(row);
    });

    const meta = {};
    collectBatches(rows).forEach(b => { meta[b.batch_id] = b; });
    wanted.forEach(bid => {
      if (!meta[bid]) meta[bid] = { batch_id: bid, timestamp: '', batch_ref: '' };
      if ((grouped[bid] || []).length && !meta[bid].timestamp) {
        const first = grouped[bid][0];
        meta[bid].timestamp = String(first.created_at || first.timestamp || '');
      }
    });

    let ordered = wanted.map(bid => meta[bid]).sort((a, b) => (
      batchSortKey(a).localeCompare(batchSortKey(b))
    ));
    const truncated = ordered.length > MAX_COMPARISON_BATCHES;
    if (truncated) ordered = ordered.slice(-MAX_COMPARISON_BATCHES);
    const labelsMap = assignBatchLabels(ordered);

    const legend = [];
    const labels = [];
    const values = [];
    ordered.forEach(batch => {
      const bid = batch.batch_id;
      const label = labelsMap[bid] || batchDisplayLabel(batch);
      const accuracy = batchAccuracy(grouped[bid] || []);
      labels.push(label);
      values.push(accuracy);
      legend.push({ batch_id: bid, label, value: accuracy });
    });

    return {
      mode: 'batches',
      title: BATCH_TITLE,
      labels,
      values,
      truncated,
      shown: ordered.length,
      selected: wanted.length,
      note: truncated
        ? `Showing ${ordered.length} of ${wanted.length} selected batches (most recent ${MAX_COMPARISON_BATCHES})`
        : '',
      legend,
    };
  }

  function barColor(index) {
    return BATCH_COLORS[index % BATCH_COLORS.length];
  }

  const api = {
    CASE_CHART_LIMIT,
    MAX_COMPARISON_BATCHES,
    CASE_TITLE,
    BATCH_TITLE,
    BATCH_COLORS,
    selectedBatchIds,
    filterResultsByBatchIds,
    resultScore,
    transcriptionScore,
    caseChartPoints,
    batchAccuracy,
    formatBatchDate,
    assignBatchLabels,
    batchDisplayLabel,
    collectBatches,
    buildAccuracyChart,
    barColor,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumAccuracyChart = api;
})(typeof window !== 'undefined' ? window : globalThis);
