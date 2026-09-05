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

  function rowAsrModel(row) {
    const data = row || {};
    return String(data.stt_model || data.asr_model || '').trim();
  }

  function isFailedFinalResult(value) {
    const v = String(value || '').trim().toLowerCase();
    return v === 'fail' || v === 'review';
  }

  function audioKey(row) {
    const data = row || {};
    return String(
      data.audio_filename || data.filename || data.test_id || data.id || ''
    ).trim().toLowerCase();
  }

  function rowAudioSeconds(row) {
    const data = row || {};
    const tr = data.transcription_result || {};
    const raw = data.audio_duration_seconds || tr.audio_length || data.audio_length;
    if (raw == null || raw === '') return null;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  function rowCriticalErrorCount(row) {
    const data = row || {};
    const soap = data.soap_comparison || {};
    const pair = soap.gt_vs_generated && typeof soap.gt_vs_generated === 'object'
      ? soap.gt_vs_generated
      : soap;
    const metrics = (pair && pair.metrics) || soap.metrics || {};
    const raw = metrics.critical_error_count;
    if (raw == null || raw === '') return 0;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  function rowHasSafetyFlag(row) {
    if (rowCriticalErrorCount(row) > 0) return true;
    const data = row || {};
    const soap = data.soap_comparison || {};
    const pair = soap.gt_vs_generated && typeof soap.gt_vs_generated === 'object'
      ? soap.gt_vs_generated
      : soap;
    const severity = String(
      (pair && pair.overall_severity) || soap.overall_severity || ''
    ).trim().toLowerCase();
    return severity === 'high' || severity === 'critical';
  }

  function formatTotalAudio(seconds) {
    if (seconds == null || seconds === '') return '—';
    const n = Number(seconds);
    if (!Number.isFinite(n) || n <= 0) return '—';
    const total = Math.round(n);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hours > 0) {
      return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
    }
    if (minutes > 0) {
      return secs ? `${minutes}m ${secs}s` : `${minutes}m`;
    }
    return `${secs}s`;
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

  function computeBatchOverview(rows, selectedModel) {
    const items = rows || [];
    const byAudio = new Map();
    items.forEach((row, index) => {
      const key = audioKey(row) || `row-${index}`;
      if (!byAudio.has(key)) byAudio.set(key, row);
    });
    const uniqueAudios = Array.from(byAudio.values());
    const audioSeconds = uniqueAudios
      .map(rowAudioSeconds)
      .filter(n => n != null)
      .reduce((sum, n) => sum + n, 0);
    const models = uniqueNonempty(items.map(rowModel));
    const fallback = String(selectedModel || '').trim();
    if (fallback && !models.length) models.push(fallback);
    const asrModels = uniqueNonempty(items.map(rowAsrModel));
    const total = items.length;
    const passed = items.filter(r => isPassedFinalResult(r.final_result)).length;
    const failed = items.filter(r => isFailedFinalResult(r.final_result)).length;
    const safety = items.filter(rowHasSafetyFlag).length;
    return {
      recordings: uniqueAudios.length,
      audio_seconds: audioSeconds || null,
      evaluation_model: models.join(', '),
      asr_model: asrModels.join(', '),
      passed,
      failed,
      safety_flags: safety,
      total,
    };
  }

  function batchOverviewDisplay(rows, selectedModel) {
    const raw = computeBatchOverview(rows, selectedModel);
    const total = raw.total;
    return {
      recordings: String(raw.recordings),
      recordings_label: raw.recordings === 1 ? 'recording' : 'recordings',
      audio: formatTotalAudio(raw.audio_seconds),
      model: formatModel(raw.evaluation_model),
      asr: formatModel(raw.asr_model),
      passed: total ? `${raw.passed} / ${total}` : '0 / 0',
      failed: total ? `${raw.failed} / ${total}` : '0 / 0',
      safety: String(raw.safety_flags),
      safety_note: raw.safety_flags
        ? `${raw.safety_flags} of the ${raw.failed} failed cases carry a safety concern`
        : 'No safety concerns in this batch',
      has_asr: Boolean(String(raw.asr_model || '').trim()),
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
    computeBatchOverview,
    batchOverviewDisplay,
    transcriptionScore,
    formatAccuracy,
    formatLatency,
    formatModel,
    formatMeta,
    formatTotalAudio,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumRunSummary = api;
})(typeof window !== 'undefined' ? window : globalThis);
