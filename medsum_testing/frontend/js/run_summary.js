/**
 * Headline run metrics for Dashboard / Test Run Summary.
 * Total = current results rows (excluded files never ran, so they are absent).
 * Done = status complete. Avg accuracy omits NOT_SCORED.
 * Avg latency = mean of Flask total-time (Latency Analysis), not wall-clock.
 */
(function (root) {
  const UNAVAILABLE = (root.MedsumLatencyAnalysis && root.MedsumLatencyAnalysis.UNAVAILABLE)
    || 'unavailable';

  function isDoneStatus(value) {
    return String(value || '').trim().toLowerCase() === 'complete';
  }

  function isPassedFinalResult(value) {
    const v = String(value || '').trim().toLowerCase();
    return v === 'pass' || v === 'complete_no_accuracy';
  }

  function transcriptionScore(row) {
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

  function rowModel(row) {
    const data = row || {};
    return String(data.ai_model_used || data.ai_model || data.llm_model || '').trim();
  }

  function uniqueNonempty(values) {
    const seen = new Set();
    const out = [];
    (values || []).forEach(raw => {
      const text = String(raw || '').trim();
      if (!text || seen.has(text)) return;
      seen.add(text);
      out.push(text);
    });
    return out;
  }

  function pickTotalTime(row) {
    const api = root.MedsumLatencyAnalysis;
    if (api && api.pickTranscribeTime) {
      return api.pickTranscribeTime(row, 'total_time');
    }
    const tr = row && row.transcription_result;
    return tr && tr['total-time'] != null && tr['total-time'] !== ''
      ? tr['total-time']
      : null;
  }

  function computeRunSummary(rows, selectedModel) {
    const items = rows || [];
    const scores = [];
    const times = [];
    items.forEach(row => {
      const score = transcriptionScore(row);
      if (score != null) scores.push(score);
      const raw = pickTotalTime(row);
      if (raw == null || raw === '') return;
      const n = Number(raw);
      if (Number.isFinite(n)) times.push(n);
    });
    const models = uniqueNonempty(items.map(rowModel));
    const fallback = String(selectedModel || '').trim();
    if (fallback && !models.length) models.push(fallback);

    return {
      total_test_cases: items.length,
      done_tests: items.filter(r => isDoneStatus(r.status)).length,
      passed_tests: items.filter(r => isPassedFinalResult(r.final_result)).length,
      average_accuracy: scores.length
        ? Math.round((scores.reduce((a, b) => a + b, 0) / scores.length) * 10) / 10
        : null,
      average_latency: times.length
        ? Math.round((times.reduce((a, b) => a + b, 0) / times.length) * 100) / 100
        : null,
      selected_model: models.join(', '),
      doctor_names: uniqueNonempty(items.map(r => r.doctor_name || r.phone || '')),
      patient_ids: uniqueNonempty(items.map(r => r.patient_id || r.patientId || '')),
      scored_count: scores.length,
      latency_count: times.length,
    };
  }

  function formatAccuracy(value) {
    return value == null ? UNAVAILABLE : value + '%';
  }

  function formatLatency(value) {
    return value == null ? UNAVAILABLE : Number(value).toFixed(2) + 's';
  }

  function formatModel(value) {
    const text = String(value || '').trim();
    return text || UNAVAILABLE;
  }

  function formatMeta(summary) {
    const data = summary || {};
    const parts = [];
    if ((data.doctor_names || []).length) {
      parts.push('Doctor: ' + data.doctor_names.join(', '));
    }
    if ((data.patient_ids || []).length) {
      parts.push('Patient: ' + data.patient_ids.join(', '));
    }
    return parts.join(' · ');
  }

  function summaryDisplay(rows, selectedModel) {
    const raw = computeRunSummary(rows, selectedModel);
    return {
      total_test_cases: String(raw.total_test_cases),
      done_tests: String(raw.done_tests),
      passed_tests: String(raw.passed_tests),
      average_accuracy: formatAccuracy(raw.average_accuracy),
      average_latency: formatLatency(raw.average_latency),
      selected_model: formatModel(raw.selected_model),
      meta: formatMeta(raw),
    };
  }

  const api = {
    HEADLINE_METRIC_KEYS: [
      'total_test_cases',
      'done_tests',
      'average_accuracy',
      'average_latency',
      'selected_model',
    ],
    computeRunSummary,
    summaryDisplay,
    transcriptionScore,
    formatAccuracy,
    formatLatency,
    formatModel,
    formatMeta,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumRunSummary = api;
})(typeof window !== 'undefined' ? window : globalThis);
