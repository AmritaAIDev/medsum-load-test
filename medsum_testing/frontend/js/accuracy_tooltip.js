/**
 * Per-case accuracy tooltip. Describes the stored score; does not recompute it.
 * Overall % = transcription LLM similarity only.
 */
(function (root) {
  const NOT_SCORED = 'NOT_SCORED';
  const TRANSCRIPTION_CRITERIA = [
    'drug names',
    'dosages',
    'diagnoses',
    'procedures',
    'symptoms',
    'frequencies',
    'durations',
  ];
  const METHOD_BLURB =
    'LLM medical-meaning similarity (0–100). Not exact text match — '
    + 'punctuation, number format, and spacing are ignored.';
  const OVERALL_IS = 'Overall % is the transcription score only.';
  const DETAIL_HINT = 'Open the test case for the full breakdown.';

  function text(value) {
    return value == null ? '' : String(value).trim();
  }

  function asScore(value) {
    if (value == null || value === '') return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function comp(result, keys) {
    const data = result || {};
    for (let i = 0; i < keys.length; i++) {
      const val = data[keys[i]];
      if (val && typeof val === 'object' && !Array.isArray(val)) return val;
    }
    return {};
  }

  function hasGtText() {
    for (let i = 0; i < arguments.length; i++) {
      if (text(arguments[i])) return true;
    }
    return false;
  }

  function diffTypes(c) {
    const details = (c && c.medical_difference_details) || [];
    const types = [];
    const seen = {};
    details.forEach(item => {
      const kind = text(item && item.type).replace(/_/g, ' ');
      if (kind && !seen[kind]) {
        seen[kind] = true;
        types.push(kind);
      }
    });
    if (types.length) return types;
    const diffs = (c && c.medical_differences) || [];
    if (diffs.length) return [diffs.length + ' listed'];
    return [];
  }

  function piece(name, compared, score, notScored, reason, criteria) {
    if (notScored || score == null) {
      return {
        name,
        compared,
        status: NOT_SCORED,
        score: null,
        reason: reason || 'No ground truth for this field',
        criteria: criteria.slice(),
      };
    }
    return {
      name,
      compared,
      status: 'scored',
      score,
      reason: '',
      criteria: criteria.slice(),
    };
  }

  function transcriptionPiece(result) {
    const c = comp(result, ['transcription_comparison', 'comparison']);
    const score = asScore(
      result.accuracy_score != null ? result.accuracy_score : (c.similarity_score != null ? c.similarity_score : result.similarity_score)
    );
    const skipped = !!(
      result.accuracy_skipped
      || c.skipped
      || result.final_result === 'complete_no_accuracy'
    );
    const skipReason = text(
      result.accuracy_skip_reason
      || c.skip_reason
      || (skipped ? c.summary : '')
    );
    let gtMissing = skipped || !(
      result.has_ground_truth
      || hasGtText(result.ground_truth, result.ground_truth_transcription)
      || score != null
    );
    if (score != null && !skipped) gtMissing = false;
    return piece(
      'Transcription',
      'generated transcript vs ground-truth transcript',
      gtMissing ? null : score,
      gtMissing,
      skipReason || 'No ground truth transcript found for this audio',
      TRANSCRIPTION_CRITERIA
    );
  }

  function translationPiece(result) {
    const c = comp(result, ['translation_comparison']);
    const score = asScore(
      c.similarity_score != null ? c.similarity_score : result.translation_score
    );
    const lang = text(result.language).toLowerCase();
    const english = lang === 'english' || lang === 'en';
    let gt = hasGtText(result.translation_ground_truth, result.ground_truth_translation);
    if (english) {
      gt = gt || hasGtText(result.ground_truth, result.ground_truth_transcription);
    }
    let gtMissing = result.has_translation_ground_truth === false
      || (score == null && !gt);
    if (score != null) gtMissing = false;
    const error = text(c.error || c.skip_reason);
    if (error.toLowerCase().indexOf('missing ground truth') !== -1) {
      gtMissing = true;
    }
    const compared = english
      ? 'generated translation vs ground-truth transcript (English)'
      : 'generated translation vs ground-truth translation';
    const reason = error || (
      english
        ? 'No transcription ground truth to use as English translation GT'
        : 'No translation ground truth found for this audio'
    );
    return piece(
      'Translation',
      compared,
      gtMissing ? null : score,
      gtMissing || score == null,
      reason,
      ['medical terms', 'drug names', 'dosages', 'diagnoses']
    );
  }

  function soapPiece(result) {
    const soap = comp(result, ['soap_comparison']);
    const scores = soap.scores && typeof soap.scores === 'object' ? soap.scores : {};
    const gtVsGen = soap.gt_vs_generated && typeof soap.gt_vs_generated === 'object'
      ? soap.gt_vs_generated
      : {};
    const score = asScore(
      scores.gt_vs_generated != null
        ? scores.gt_vs_generated
        : (gtVsGen.similarity_score != null ? gtVsGen.similarity_score : result.soap_score)
    );
    let gtMissing = result.has_soap_ground_truth === false
      || (score == null && !result.soap_ground_truth);
    if (score != null) gtMissing = false;
    const soapSkip = text(soap.skip_reason);
    return piece(
      'SOAP',
      'generated SOAP vs ground-truth SOAP (subjective, objective, assessment, plan)',
      score,
      gtMissing || score == null,
      soapSkip || 'No SOAP ground truth found for this audio',
      ['Correct', 'Incorrect', 'Missing', 'Hallucination']
    );
  }

  function whyThisCase(result, transcription) {
    if (transcription.status === NOT_SCORED) {
      return transcription.reason || 'Transcription was not scored.';
    }
    const c = comp(result, ['transcription_comparison', 'comparison']);
    const types = diffTypes(c);
    if (types.length) {
      const n = (c.medical_difference_details || c.medical_differences || types).length;
      return n + ' medical difference(s): ' + types.join(', ') + '.';
    }
    const summary = text(c.summary);
    if (summary) {
      const first = summary.split('.')[0].trim();
      return first.length > 160 ? first.slice(0, 160) + '…' : first;
    }
    if (transcription.score != null) {
      return 'Transcription similarity ' + Math.round(transcription.score) + '%.';
    }
    return '';
  }

  function isExecutionError(result) {
    const status = text(result && result.status).toLowerCase();
    const verdict = text(result && result.final_result).toLowerCase();
    return status === 'failed' || verdict === 'failed';
  }

  function buildAccuracyTooltip(result, options) {
    const focus = text(options && options.focus).toLowerCase() || 'overall';
    const data = result || {};
    if (isExecutionError(data)) {
      const errors = data.errors || [];
      let firstError = '';
      if (typeof errors === 'string') firstError = text(errors);
      else {
        for (let i = 0; i < errors.length; i++) {
          const line = text(errors[i]);
          if (line && line.toLowerCase().indexOf('traceback') === -1) {
            firstError = line.split('\n')[0];
            break;
          }
        }
      }
      const reason = text(data.accuracy_skip_reason)
        || firstError
        || 'This case did not produce output — no SOAP evaluation.';
      const empty = piece(
        'SOAP',
        'no generated output to evaluate',
        null,
        true,
        reason,
        ['Correct', 'Incorrect', 'Missing', 'Hallucination']
      );
      const transcription = piece(
        'Transcription',
        'no generated output to evaluate',
        null,
        true,
        reason,
        TRANSCRIPTION_CRITERIA.slice()
      );
      const translation = piece(
        'Translation',
        'no generated output to evaluate',
        null,
        true,
        reason,
        ['medical terms', 'drug names', 'dosages', 'diagnoses']
      );
      return {
        focus,
        overall_score: null,
        overall_status: NOT_SCORED,
        compared: 'no generated output to evaluate',
        criteria: TRANSCRIPTION_CRITERIA.slice(),
        method: METHOD_BLURB,
        overall_note: OVERALL_IS,
        pieces: [transcription, translation, empty],
        why: reason,
        hint: DETAIL_HINT,
        not_scored_present: true,
      };
    }
    const transcription = transcriptionPiece(data);
    const translation = translationPiece(result || {});
    const soap = soapPiece(result || {});
    const pieces = [transcription, translation, soap];
    const focused = focus === 'translation'
      ? translation
      : focus === 'soap' ? soap : transcription;
    return {
      focus,
      overall_score: transcription.score,
      overall_status: transcription.status,
      compared: focus === 'overall' ? transcription.compared : focused.compared,
      criteria: focus === 'overall' ? TRANSCRIPTION_CRITERIA.slice() : focused.criteria,
      method: METHOD_BLURB,
      overall_note: OVERALL_IS,
      pieces,
      why: whyThisCase(result || {}, transcription),
      hint: DETAIL_HINT,
      not_scored_present: pieces.some(p => p.status === NOT_SCORED),
    };
  }

  function formatPieceLine(p) {
    if (p.status === NOT_SCORED) {
      return p.name + ': ' + NOT_SCORED + ' — ' + (p.reason || 'no ground truth');
    }
    const shown = p.score != null ? Math.round(p.score) + '%' : '—';
    return p.name + ': ' + shown;
  }

  function tooltipPlainText(model) {
    const lines = [
      'Compared: ' + (model.compared || ''),
      'Evaluated: ' + ((model.criteria || []).join(', ')),
      'Method: ' + (model.method || ''),
      model.overall_note || '',
    ].concat((model.pieces || []).map(formatPieceLine));
    if (model.why) lines.push('This case: ' + model.why);
    lines.push(model.hint || DETAIL_HINT);
    return lines.filter(Boolean).join('\n');
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function tooltipHtml(model, header) {
    const m = model || {};
    const pieces = (m.pieces || []).map(p => {
      const cls = p.status === NOT_SCORED ? 'tip-not-scored' : 'tip-scored';
      return '<li class="' + cls + '">' + esc(formatPieceLine(p)) + '</li>';
    }).join('');
    return (
      '<div class="reason-header">' + esc(header || 'Accuracy') + '</div>'
      + '<div class="reason-text accuracy-tip">'
      + '<p><strong>Compared</strong> ' + esc(m.compared || '') + '</p>'
      + '<p><strong>Evaluated</strong> ' + esc((m.criteria || []).join(', ')) + '</p>'
      + '<p><strong>Method</strong> ' + esc(m.method || '') + '</p>'
      + (m.overall_note ? '<p class="tip-note">' + esc(m.overall_note) + '</p>' : '')
      + (pieces ? '<ul class="tip-pieces">' + pieces + '</ul>' : '')
      + (m.why ? '<p class="tip-why"><strong>This case</strong> ' + esc(m.why) + '</p>' : '')
      + '<p class="tip-hint">' + esc(m.hint || DETAIL_HINT) + '</p>'
      + '</div>'
    );
  }

  function overallAverageTooltip() {
    return {
      focus: 'average',
      overall_score: null,
      overall_status: 'scored',
      compared: "each case's generated transcript vs that case's ground-truth transcript",
      criteria: TRANSCRIPTION_CRITERIA.slice(),
      method: METHOD_BLURB,
      overall_note:
        'Avg Accuracy is the mean of scored transcription similarities. '
        + 'NOT_SCORED cases (no transcription ground truth) are omitted — not counted as 0%.',
      pieces: [],
      why: '',
      hint: "Open a test case for that case's field-level breakdown.",
      not_scored_present: true,
    };
  }

  const api = {
    NOT_SCORED,
    TRANSCRIPTION_CRITERIA,
    METHOD_BLURB,
    buildAccuracyTooltip,
    formatPieceLine,
    tooltipPlainText,
    tooltipHtml,
    overallAverageTooltip,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumAccuracyTooltip = api;
})(typeof window !== 'undefined' ? window : globalThis);
