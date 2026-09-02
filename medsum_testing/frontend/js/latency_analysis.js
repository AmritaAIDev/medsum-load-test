/**
 * Latency Analysis cells from Flask /transcribe timings.
 * SOAP column = llm-time (no separate SOAP timer). Missing → unavailable, not 0.
 */
(function (root) {
  const LATENCY_ANALYSIS_HEADERS = [
    'Audio File',
    'Audio Length',
    'Transcription',
    'Translation',
    'SOAP',
    'Total Time',
  ];
  const UNAVAILABLE = 'unavailable';
  const FIELD_MAP = {
    transcription: 'transcription-time',
    translation: 'translation-time',
    soap: 'llm-time',
    total_time: 'total-time',
    audio_length: 'audio_length',
  };
  const NESTED = {
    'transcription-time': ['ASR', 'transcription'],
    'translation-time': ['Translation', 'translation'],
    'llm-time': ['llm', 'LLM'],
    'total-time': ['total'],
    audio_length: ['audio_length'],
  };

  function trOf(result) {
    const tr = result && result.transcription_result;
    return tr && typeof tr === 'object' ? tr : {};
  }

  function present(obj, key) {
    if (!obj || !Object.prototype.hasOwnProperty.call(obj, key)) return null;
    const val = obj[key];
    if (val == null || val === '') return null;
    return val;
  }

  function pickTranscribeTime(result, column) {
    const tr = trOf(result);
    const nested = tr.time && typeof tr.time === 'object' ? tr.time : {};
    const key = FIELD_MAP[column];
    if (!key) return null;
    let val = present(tr, key);
    if (val != null) return val;
    const aliases = NESTED[key] || [];
    for (let i = 0; i < aliases.length; i++) {
      val = present(nested, aliases[i]);
      if (val != null) return val;
    }
    if (column === 'audio_length') {
      const dur = result && result.audio_duration_seconds;
      if (dur != null && dur !== '' && Number(dur) !== 0) return dur;
    }
    return null;
  }

  function formatSeconds(value, audio) {
    if (value == null || value === '') return UNAVAILABLE;
    const n = Number(value);
    if (!Number.isFinite(n)) return UNAVAILABLE;
    if (audio) {
      if (n >= 60) return Math.floor(n / 60) + 'm ' + Math.round(n % 60) + 's';
      return Math.round(n) + 's';
    }
    return n.toFixed(2) + 's';
  }

  function latencyAnalysisRow(result) {
    const data = result || {};
    const audio = String(data.audio_filename || data.filename || '').trim() || UNAVAILABLE;
    return {
      'Audio File': audio,
      'Audio Length': formatSeconds(pickTranscribeTime(data, 'audio_length'), true),
      Transcription: formatSeconds(pickTranscribeTime(data, 'transcription')),
      Translation: formatSeconds(pickTranscribeTime(data, 'translation')),
      SOAP: formatSeconds(pickTranscribeTime(data, 'soap')),
      'Total Time': formatSeconds(pickTranscribeTime(data, 'total_time')),
    };
  }

  function latencyAnalysisValues(result) {
    const row = latencyAnalysisRow(result);
    return LATENCY_ANALYSIS_HEADERS.map(h => row[h]);
  }

  const api = {
    LATENCY_ANALYSIS_HEADERS,
    UNAVAILABLE,
    pickTranscribeTime,
    formatSeconds,
    latencyAnalysisRow,
    latencyAnalysisValues,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumLatencyAnalysis = api;
})(typeof window !== 'undefined' ? window : globalThis);
