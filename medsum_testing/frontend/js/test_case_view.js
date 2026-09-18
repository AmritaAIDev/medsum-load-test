/**
 * Stable test-case identity for View / detail.
 * Lookup is by test_id only — never table index or Django integer PK.
 */
(function (root) {
  const SOURCE_DRIVE = 'google_drive';
  const SOURCE_UPLOAD = 'upload';

  function text(value) {
    return value == null ? '' : String(value).trim();
  }

  function looksLikeIntegerPk(value) {
    const raw = text(value);
    return raw !== '' && /^\d{1,12}$/.test(raw);
  }

  function looksLikeStableId(value) {
    const raw = text(value);
    if (!raw || looksLikeIntegerPk(raw)) return false;
    return (
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(raw)
      || /^[0-9a-f]{32}$/i.test(raw)
      || raw.indexOf('-') !== -1
      || raw.length >= 8
    );
  }

  function stableTestId(row) {
    const data = row || {};
    const fromField = text(data.test_id);
    if (fromField && !looksLikeIntegerPk(fromField)) return fromField;
    const fallback = text(data.id);
    if (looksLikeStableId(fallback) && !looksLikeIntegerPk(fallback)) return fallback;
    return '';
  }

  function findResultByTestId(rows, testId) {
    const wanted = text(testId);
    if (!wanted) return null;
    const list = rows || [];
    for (let i = 0; i < list.length; i++) {
      if (stableTestId(list[i]) === wanted) return list[i];
    }
    return null;
  }

  function isStaleOpen(openedId, payload) {
    const wanted = text(openedId);
    const got = stableTestId(payload);
    return !!wanted && !!got && wanted !== got;
  }

  function lastClickedId(clickIds) {
    const list = clickIds || [];
    for (let i = list.length - 1; i >= 0; i--) {
      const val = text(list[i]);
      if (val) return val;
    }
    return '';
  }

  const DETAIL_HOST_IDS = {
    'detail-view': 1, 'back-btn': 1, 'pdf-btn': 1, 'excel-btn': 1,
    'export-btn': 1, 'export-menu': 1,
  };

  function clickShouldOpenDetail(closestHostId, testId) {
    const tid = text(testId);
    if (!tid) return false;
    const host = text(closestHostId);
    return !DETAIL_HOST_IDS[host];
  }

  function inferGroundTruthSource(result) {
    const data = result || {};
    const explicit = text(data.ground_truth_source).toLowerCase();
    if (explicit === SOURCE_DRIVE || explicit === SOURCE_UPLOAD) return explicit;
    if (data.drive_transcript_file_id || data.drive_soap_gt_file_id) return SOURCE_DRIVE;
    if (data.uploaded_ground_truth_filename) return SOURCE_UPLOAD;
    const hasGt = !!(
      text(data.ground_truth)
      || text(data.ground_truth_transcription)
      || data.soap_ground_truth
    );
    if (hasGt && !data.drive_audio_file_id) return SOURCE_UPLOAD;
    if (data.drive_audio_file_id || data.drive_transcript_file_id) return SOURCE_DRIVE;
    return '';
  }

  function inferAudioSource(result) {
    const data = result || {};
    const explicit = text(data.audio_source).toLowerCase();
    if (explicit === SOURCE_DRIVE || explicit === SOURCE_UPLOAD) return explicit;
    if (data.drive_audio_file_id) return SOURCE_DRIVE;
    if (text(data.audio_filename) && !data.drive_audio_file_id) return SOURCE_UPLOAD;
    return '';
  }

  function soapOutput(result) {
    const data = result || {};
    if (data.soap_generated) return data.soap_generated;
    const tr = data.transcription_result || {};
    if (tr.subjective || tr.objective || tr.assessment || tr.plan || tr.summary) {
      return {
        subjective: tr.subjective,
        objective: tr.objective,
        assessment: tr.assessment,
        plan: tr.plan,
        summary: tr.summary,
      };
    }
    return data.soap_raw || {};
  }

  const SOAP_FACT_TILES = [
    { key: 'Correct', label: 'Match (Correct)', legend: 'Generated matches ground truth.' },
    { key: 'Incorrect', label: 'Incorrect', legend: 'Generated contradicts ground truth.' },
    { key: 'Missing', label: 'Missing', legend: 'Ground-truth fact not captured in generated output.' },
    { key: 'Hallucination', label: 'Hallucinated', legend: 'Generated content not supported by ground truth.' },
  ];

  const COUNTED_RESULTS = { Correct: 1, Incorrect: 1, Missing: 1, Hallucination: 1 };

  function normalizeFactType(raw) {
    const api = root.MedsumGtComparisonTable || {};
    if (typeof api.normalizeResultType === 'function') {
      return api.normalizeResultType(raw);
    }
    const key = text(raw).toLowerCase().replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
    if (key === 'missing' || key === 'missing detail' || key === 'removed in final') return 'Missing';
    if (key === 'hallucination' || key === 'extra' || key === 'added in final') return 'Hallucination';
    if (key === 'incorrect' || key === 'field changed') return 'Incorrect';
    if (key === 'correct') return 'Correct';
    if (key === 'na' || key === 'n/a' || key === 'n a') return 'NA';
    return text(raw);
  }

  function factClassification(row) {
    const label = normalizeFactType(row && (row.result || row.type));
    if (label === 'NA' || label === 'N/A') return 'NA';
    if (COUNTED_RESULTS[label]) return label;
    return label ? label : 'Correct';
  }

  function soapFactsFromResult(result) {
    const api = root.MedsumGtComparisonTable || {};
    if (typeof api.soapFactsFromResult === 'function') {
      return api.soapFactsFromResult(result);
    }
    const soap = (result && result.soap_comparison) || {};
    const pair = soap.gt_vs_generated && typeof soap.gt_vs_generated === 'object'
      ? soap.gt_vs_generated : soap;
    if (Array.isArray(pair.facts) && pair.facts.length) {
      return pair.facts.filter(f => f && typeof f === 'object');
    }
    if (Array.isArray(soap.facts) && soap.facts.length) {
      return soap.facts.filter(f => f && typeof f === 'object');
    }
    return [];
  }

  function formatPct(value) {
    if (value == null || value === '') return '—';
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    return `${Math.round(n)}%`;
  }

  function formatAudioLength(seconds) {
    if (seconds == null || seconds === '') return '—';
    const n = Number(seconds);
    if (!Number.isFinite(n) || n <= 0) return '—';
    if (n >= 60) return `${Math.floor(n / 60)}m ${Math.round(n % 60)}s`;
    return `${Math.round(n)}s`;
  }

  function formatLatencySeconds(value) {
    if (value == null || value === '') return '—';
    const n = Number(value);
    if (!Number.isFinite(n) || n < 0) return '—';
    return `${n.toFixed(2)}s`;
  }

  function formatEndToEnd(seconds) {
    if (seconds == null || seconds === '') return '—';
    const n = Number(seconds);
    if (!Number.isFinite(n) || n < 0) return '—';
    if (n < 60) return `${Math.round(n)}s`;
    return `${Math.floor(n / 60)}m ${Math.round(n % 60)}s`;
  }

  function sourceLabel(source) {
    if (source === SOURCE_DRIVE) return 'Google Drive';
    if (source === SOURCE_UPLOAD) return 'Frontend upload';
    return source || 'Unknown source';
  }

  function audioLengthValue(data) {
    const tr = (data && data.transcription_result) || {};
    return (data && data.audio_duration_seconds)
      || tr.audio_length
      || (data && data.audio_length)
      || null;
  }

  function soapScoreValue(data) {
    const soap = (data && data.soap_comparison) || {};
    const pair = soap.gt_vs_generated && typeof soap.gt_vs_generated === 'object'
      ? soap.gt_vs_generated
      : soap;
    const metrics = pair.metrics && typeof pair.metrics === 'object' ? pair.metrics : {};
    const scores = soap.scores && typeof soap.scores === 'object' ? soap.scores : {};
    const pct = pair.overall_weighted_clinical_score ?? pair.similarity_score
      ?? scores.gt_vs_generated ?? metrics.overall_weighted_clinical_score;
    const n = pct == null || pct === '' ? NaN : Number(pct);
    return Number.isFinite(n) ? n : null;
  }

  function accuracyDetails(data) {
    const trans = data.comparison || data.transcription_comparison || {};
    const translationComp = data.translation_comparison || {};
    const soap = data.soap_comparison || {};
    return {
      accuracy_score: data.accuracy_score,
      transcription_score: trans.similarity_score,
      translation_score: translationComp.similarity_score,
      soap_score: soapScoreValue(data),
      summary: trans.summary || '',
      skipped: !!data.accuracy_skipped,
      skip_reason: data.accuracy_skip_reason || '',
      metrics: (soap.gt_vs_generated && soap.gt_vs_generated.metrics) || {},
    };
  }

  function soapFactCounts(result) {
    const data = result || {};
    const facts = soapFactsFromResult(data);
    const counts = { Correct: 0, Incorrect: 0, Missing: 0, Hallucination: 0 };
    if (facts && facts.length) {
      facts.forEach(row => {
        const kind = factClassification(row);
        if (kind === 'NA') return;
        if (kind in counts) counts[kind] += 1;
      });
      return counts;
    }
    const soap = data.soap_comparison || {};
    const pair = soap.gt_vs_generated && typeof soap.gt_vs_generated === 'object'
      ? soap.gt_vs_generated
      : soap;
    const metrics = pair.metrics && typeof pair.metrics === 'object' ? pair.metrics : {};
    if (metrics.correct_count != null || metrics.missing_count != null
        || metrics.hallucination_count != null || metrics.captured_count != null
        || metrics.incorrect_count != null) {
      counts.Correct = Number(metrics.correct_count) || 0;
      counts.Missing = Number(metrics.missing_count) || 0;
      counts.Hallucination = Number(metrics.hallucination_count) || 0;
      counts.Incorrect = metrics.incorrect_count != null
        ? Number(metrics.incorrect_count) || 0
        : Math.max(0, (Number(metrics.captured_count) || 0) - counts.Correct
          - counts.Hallucination);
      return counts;
    }
    return null;
  }

  function displayOutcomeCounts(counts) {
    const data = counts || {};
    return {
      Correct: Number(data.Correct) || 0,
      Incorrect: Number(data.Incorrect) || 0,
      Missing: Number(data.Missing) || 0,
      Hallucination: Number(data.Hallucination) || 0,
    };
  }

  function soapFactTiles(result) {
    const raw = soapFactCounts(result);
    const counts = displayOutcomeCounts(raw);
    const total = SOAP_FACT_TILES.reduce((sum, tile) => sum + (Number(counts[tile.key]) || 0), 0);
    const tiles = SOAP_FACT_TILES.map(tile => {
      const count = Number(counts[tile.key]) || 0;
      const percent = total ? Math.round(100 * count / total) : 0;
      return {
        key: tile.key,
        label: tile.label,
        legend: tile.legend,
        count,
        percent,
        percent_display: total ? formatPct(percent) : '—',
      };
    });
    return { tiles, total, has_counts: !!raw };
  }

  function latencyFigures(result) {
    const data = result || {};
    const tr = data.transcription_result || {};
    const timeMap = tr.time && typeof tr.time === 'object' ? tr.time : {};
    const items = [
      {
        key: 'translation_time',
        label: 'Translation time',
        value: tr['translation-time'] ?? timeMap.Translation,
        display: '',
      },
      {
        key: 'transcription_time',
        label: 'Transcription time',
        value: tr['transcription-time'] ?? timeMap.ASR,
        display: '',
      },
      {
        key: 'llm_time',
        label: 'LLM pre-processing time',
        value: tr['llm-time'] ?? timeMap.llm,
        display: '',
      },
      {
        key: 'total_time',
        label: 'Total processing time',
        value: tr['total-time'],
        display: '',
      },
      {
        key: 'end_to_end',
        label: 'End-to-end',
        value: data.total_test_time_seconds,
        display: '',
      },
    ];
    items.forEach(item => {
      item.display = item.key === 'end_to_end'
        ? formatEndToEnd(item.value)
        : formatLatencySeconds(item.value);
    });
    const visible = items.filter(item => item.value != null && item.display !== '—');
    return { items, visible, has_any: visible.length > 0 };
  }

  function medDiffSummary(result) {
    const medVal = (result && result.medication_validation) || {};
    const count = Number(medVal.difference_count) || 0;
    const hasCritical = !!medVal.has_critical_differences;
    const label = count === 0
      ? '✓ Meds'
      : `${count} Med Diff${count !== 1 ? 's' : ''}`;
    const tone = count === 0 ? 'high' : (hasCritical ? 'low' : 'warn');
    return { count, has_critical: hasCritical, label, tone };
  }

  function infoFieldList(opts) {
    const fields = [
      { key: 'tc-ref', label: 'Test Case ID', value: opts.tc_ref || '—', id: 'detail-tc-ref' },
      { key: 'batch', label: 'Batch', value: opts.batch_label || '—', id: 'detail-batch-ref' },
      { key: 'audio-length', label: 'Audio length', value: opts.audio_length_display },
    ];
    if (opts.model_name) {
      fields.push({ key: 'model', label: 'Model', value: opts.model_name });
    }
    return fields;
  }

  function headerMetricSites(model) {
    const data = model || {};
    const acc = data.accuracy || {};
    const overall = acc.accuracy_score;
    const trans = acc.transcription_score;
    const transl = acc.translation_score;
    const soap = acc.soap_score;
    const length = data.audio_length;
    const lengthDisplay = data.audio_length_display || formatAudioLength(length);
    const sites = {
      overall_accuracy: [overall, formatPct(overall)],
      transcription_score: [trans, formatPct(trans)],
      translation_score: [transl, formatPct(transl)],
      soap_score: [soap, formatPct(soap)],
      audio_length: [length, lengthDisplay],
    };
    (data.info_fields || []).forEach(field => {
      if (field.key === 'audio-length') sites.audio_length.push(field.value);
    });
    return sites;
  }

  function detailViewModel(result) {
    const data = result || {};
    const testId = stableTestId(data);
    const audioSource = inferAudioSource(data);
    const canPlay = audioSource === SOURCE_DRIVE && !!data.drive_audio_file_id;
    const tr = data.transcription_result || {};
    const trans = data.comparison || data.transcription_comparison || {};
    const length = audioLengthValue(data);
    const lengthDisplay = formatAudioLength(length);
    const modelName = text(data.ai_model_used || data.ai_model || data.llm_model);
    const runRef = text(data.run_ref);
    const audioName = text(data.audio_filename || data.drive_audio_filename);
    const language = text(data.language);
    const batchId = text(data.batch_id);
    const batchRef = text(data.batch_ref);
    const batchLabel = /^\d{2}-[A-Za-z]{3}-\d{4} \| \d+$/.test(batchId)
      ? batchId
      : (batchRef || (batchId
        ? (/^[0-9a-f-]{36}$/i.test(batchId) ? batchId.slice(0, 8) : batchId)
        : '—'));
    return {
      test_id: testId,
      tc_ref: text(data.tc_ref),
      audio_filename: audioName,
      audio_source: audioSource,
      audio_url: canPlay && testId ? `/api/medsum-test/results/${testId}/audio` : '',
      audio_player: canPlay,
      audio_length: length,
      audio_length_display: lengthDisplay,
      language,
      run_ref: runRef,
      model_name: modelName,
      transcription: text(data.transcription || data.generated_transcription),
      translation: text(
        data.generated_translation
        || data.translation
        || data.text_translation
        || (tr.debug && tr.debug.translation)
      ),
      soap_output: soapOutput(data),
      ground_truth: text(data.ground_truth || data.ground_truth_transcription),
      ground_truth_source: inferGroundTruthSource(data),
      comparison: trans,
      accuracy: accuracyDetails(data),
      soap_facts: soapFactTiles(data),
      latency: latencyFigures(data),
      med_diffs: medDiffSummary(data),
      info_fields: infoFieldList({
        tc_ref: text(data.tc_ref),
        batch_label: batchLabel,
        audio_length_display: lengthDisplay,
        model_name: modelName,
      }),
    };
  }

  const api = {
    SOURCE_DRIVE,
    SOURCE_UPLOAD,
    SOAP_FACT_TILES,
    displayOutcomeCounts,
    looksLikeIntegerPk,
    stableTestId,
    findResultByTestId,
    isStaleOpen,
    lastClickedId,
    clickShouldOpenDetail,
    inferGroundTruthSource,
    inferAudioSource,
    detailViewModel,
    formatPct,
    formatAudioLength,
    formatLatencySeconds,
    formatEndToEnd,
    sourceLabel,
    soapFactCounts,
    soapFactTiles,
    latencyFigures,
    medDiffSummary,
    headerMetricSites,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumTestCaseView = api;
})(typeof window !== 'undefined' ? window : globalThis);
