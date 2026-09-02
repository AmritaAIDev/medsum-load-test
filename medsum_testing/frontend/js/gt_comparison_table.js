/**
 * Generated vs Ground Truth comparison table (detail panel).
 * Renders SOAP section_details + medication_validation already on the result.
 * Does not recompute accuracy or invent a second severity scale.
 */
(function (root) {
  const SOAP_SECTION_KEYS = ['subjective', 'objective', 'assessment', 'plan'];
  const SOAP_SECTION_LABELS = {
    subjective: 'Subjective',
    objective: 'Objective',
    assessment: 'Assessment',
    plan: 'Plan',
  };
  const MEDICATION_ROW_ID = 'medication';
  const MEDICATION_ROW_LABEL = 'Medication (From Raw LLM)';
  const ROW_IDS = SOAP_SECTION_KEYS.concat([MEDICATION_ROW_ID]);
  const TABLE_COLUMNS = [
    'Section',
    'Ground Truth (GT)',
    'Generated Output (Gen)',
    'Raw LLM (Med Only)',
    'Difference & Notes',
  ];
  const DIFF_SCOPE_ALL = 'all';
  const DIFF_SCOPE_GEN_VS_GT = 'gen_vs_gt';
  const DIFF_SCOPE_RAW_VS_GT = 'raw_vs_gt';
  const DIFF_SCOPE_OPTIONS = [
    [DIFF_SCOPE_ALL, 'All'],
    [DIFF_SCOPE_GEN_VS_GT, 'Generated vs GT'],
    [DIFF_SCOPE_RAW_VS_GT, 'Raw LLM vs GT'],
  ];
  const DISPLAY_CRITICAL = 'Critical';
  const DISPLAY_MAJOR = 'Major';
  const DISPLAY_MINOR = 'Minor';
  const ENGINE_RANK = {
    critical: 4, high: 4, medium: 3, low: 2, none: 1, unknown: 1, '': 1,
  };
  const DASH = '—';
  const BLANK_MARKERS = { '': 1, '—': 1, '-': 1 };
  const NA_MARKERS = {
    NA: 1, na: 1, 'n/a': 1, 'N/A': 1,
    'Not applicable': 1, 'not applicable': 1,
    'Nothing to report': 1, 'not applicable/established': 1,
  };
  const EMPTY_MARKERS = Object.assign({}, BLANK_MARKERS, NA_MARKERS);
  const DOSE_RE = /\d+(?:\.\d+)?\s*mg\b/i;

  function text(value) {
    return value == null ? '' : String(value).trim();
  }

  function asDict(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  }

  function asList(value) {
    if (Array.isArray(value)) return value;
    if (value == null || isEmpty(value)) return [];
    return [value];
  }

  function isEmpty(value) {
    const t = text(value);
    if (!t || BLANK_MARKERS[t]) return true;
    return !!NA_MARKERS[t] || !!NA_MARKERS[t.toLowerCase()];
  }

  function isNaMarker(value) {
    const t = text(value);
    if (!t || BLANK_MARKERS[t]) return true;
    return !!NA_MARKERS[t] || !!NA_MARKERS[t.toLowerCase()];
  }

  function normalizeResultType(raw) {
    const key = text(raw).toLowerCase().replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
    if (key === 'missing' || key === 'missing detail' || key === 'removed in final') return 'Missing';
    if (key === 'hallucination' || key === 'extra' || key === 'added in final') return 'Hallucination';
    if (key === 'incorrect' || key === 'field changed') return 'Incorrect';
    if (key === 'correct') return 'Correct';
    if (key === 'na' || key === 'n/a' || key === 'n a') return 'NA';
    return text(raw);
  }

  function norm(value) {
    return text(value).toLowerCase().replace(/[.,\-–—\s]/g, '').trim();
  }

  function displayCell(value) {
    const t = text(value);
    return t || DASH;
  }

  function displaySeverity(engineSeverity) {
    const key = text(engineSeverity).toLowerCase();
    if (key === 'critical' || key === 'high') return DISPLAY_CRITICAL;
    if (key === 'medium') return DISPLAY_MAJOR;
    return DISPLAY_MINOR;
  }

  function worstEngineSeverity(diffs) {
    let worst = 'none';
    let worstRank = ENGINE_RANK.none;
    (diffs || []).forEach(diff => {
      if (!diff || typeof diff !== 'object') return;
      const sev = text(diff.severity).toLowerCase() || 'low';
      const rank = ENGINE_RANK[sev] != null ? ENGINE_RANK[sev] : 1;
      if (rank > worstRank) {
        worst = sev;
        worstRank = rank;
      }
    });
    return worst;
  }

  function isFormattingOnly(left, right) {
    if (isEmpty(left) && isEmpty(right)) return false;
    if (text(left) === text(right)) return false;
    if (isEmpty(left) || isEmpty(right)) return false;
    return norm(left) === norm(right);
  }

  function textsDiffer(left, right) {
    if (isEmpty(left) && isEmpty(right)) return false;
    return norm(left) !== norm(right);
  }

  function generatedSoap(result) {
    const data = asDict(result);
    if (data.soap_generated) return asDict(data.soap_generated);
    const tr = asDict(data.transcription_result);
    if (tr.subjective || tr.objective || tr.assessment || tr.plan || tr.summary) {
      return {
        subjective: tr.subjective,
        objective: tr.objective,
        assessment: tr.assessment,
        plan: tr.plan,
        summary: tr.summary,
      };
    }
    return asDict(data.soap_raw);
  }

  function rawSoap(result) {
    const data = asDict(result);
    if (data.soap_raw) return asDict(data.soap_raw);
    const tr = asDict(data.transcription_result);
    const debug = asDict(tr.debug);
    return asDict(debug.raw_soap || debug['raw soap']);
  }

  function formatOneMed(med) {
    if (!med || typeof med !== 'object' || Array.isArray(med)) return text(med);
    const parts = [
      text(med.drug_name || med.name),
      text(med.dose),
      text(med.schedule || med.frequency),
      text(med.duration),
    ];
    return parts.filter(p => p && p.toLowerCase() !== 'na' && p.toLowerCase() !== 'n/a').join(' ');
  }

  function formatMedications(meds) {
    const items = asList(meds);
    if (!items.length) return '';
    return items.map(formatOneMed).filter(Boolean).join('; ');
  }

  function flattenSection(value) {
    if (value == null) return '';
    if (typeof value === 'string') {
      const t = value.trim();
      return isNaMarker(t) ? '' : t;
    }
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) {
      return value.map(flattenSection).filter(Boolean).join('; ');
    }
    if (typeof value === 'object') {
      if ('drug_name' in value || 'dose' in value) return formatOneMed(value);
      return Object.keys(value).map(k => flattenSection(value[k])).filter(Boolean).join(' ');
    }
    return text(value);
  }

  function sectionDiffs(comparison, sectionKey) {
    const details = asDict(asDict(comparison).section_details);
    const diffs = asDict(details[sectionKey]).differences || [];
    return diffs.filter(d => {
      if (!d || typeof d !== 'object') return false;
      const label = normalizeResultType(d.type || d.result);
      return label !== 'NA' && label !== 'Correct';
    });
  }

  function soapFactsFromResult(result) {
    const soap = asDict(result && result.soap_comparison);
    const pair = soap.gt_vs_generated && typeof soap.gt_vs_generated === 'object'
      ? soap.gt_vs_generated : soap;
    const facts = pair.facts;
    if (Array.isArray(facts) && facts.length) {
      return facts.filter(f => f && typeof f === 'object');
    }
    if (Array.isArray(soap.facts) && soap.facts.length) {
      return soap.facts.filter(f => f && typeof f === 'object');
    }
    return factsFromNestedSoap(result);
  }

  function classifyNestedPair(gtVal, genVal) {
    const gtEmpty = isEmpty(gtVal);
    const genEmpty = isEmpty(genVal);
    if (gtEmpty && genEmpty) return 'NA';
    if (gtEmpty && !genEmpty) return 'Hallucination';
    if (!gtEmpty && genEmpty) return 'Missing';
    const gtText = text(gtVal).toLowerCase();
    if (gtText === 'not present' || gtText === 'absent' || gtText.indexOf('not present') !== -1) {
      if (text(genVal) && gtText !== text(genVal).toLowerCase()) return 'Hallucination';
    }
    if (norm(gtVal) === norm(genVal)) return 'Correct';
    return 'Incorrect';
  }

  function emitNestedFact(out, section, field, gtVal, genVal, index) {
    const result = classifyNestedPair(gtVal, genVal);
    if (result === 'NA') return;
    const label = text(field).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    out.push({
      section: section,
      field: index != null ? label + ' [' + (index + 1) + ']' : label,
      base_field: field,
      ground_truth: gtVal,
      generated: genVal,
      result: result,
      index: index,
    });
  }

  function walkNestedPair(gtNode, genNode, section, prefix, out) {
    const gtDict = gtNode && typeof gtNode === 'object' && !Array.isArray(gtNode) ? gtNode : {};
    const genDict = genNode && typeof genNode === 'object' && !Array.isArray(genNode) ? genNode : {};
    if ((gtNode && typeof gtNode === 'object' && !Array.isArray(gtNode))
        || (genNode && typeof genNode === 'object' && !Array.isArray(genNode))) {
      const keys = Object.keys(gtDict).concat(Object.keys(genDict).filter(k => !(k in gtDict)));
      keys.forEach(key => {
        const path = prefix ? prefix + '.' + key : key;
        walkNestedPair(gtDict[key], genDict[key], section, path, out);
      });
      return;
    }
    if (Array.isArray(gtNode) || Array.isArray(genNode)) {
      const gtList = Array.isArray(gtNode) ? gtNode : [];
      const genList = Array.isArray(genNode) ? genNode : [];
      const n = Math.max(gtList.length, genList.length);
      for (let i = 0; i < n; i++) {
        const left = i < gtList.length ? gtList[i] : null;
        const right = i < genList.length ? genList[i] : null;
        if ((left && typeof left === 'object') || (right && typeof right === 'object')) {
          const ld = left && typeof left === 'object' ? left : {};
          const rd = right && typeof right === 'object' ? right : {};
          ['drug_name', 'dose', 'schedule', 'duration', 'instructions'].forEach(medKey => {
            if (!(medKey in ld) && !(medKey in rd)) return;
            emitNestedFact(out, section, medKey, ld[medKey], rd[medKey], i);
          });
        } else {
          const leaf = prefix ? prefix.split('.').pop() : section;
          emitNestedFact(out, section, leaf, left, right, i);
        }
      }
      return;
    }
    const leaf = prefix ? prefix.split('.').pop() : section;
    emitNestedFact(out, section, leaf, gtNode, genNode);
  }

  function factsFromNestedSoap(result) {
    const data = asDict(result);
    const gt = asDict(data.soap_ground_truth);
    const gen = generatedSoap(data);
    if (!Object.keys(gt).length && !Object.keys(gen).length) return [];
    const out = [];
    SOAP_SECTION_KEYS.forEach(key => {
      if (gt[key] == null && gen[key] == null) return;
      walkNestedPair(gt[key], gen[key], key, '', out);
    });
    return out;
  }

  function factSectionKey(fact) {
    return text(fact && fact.section).toLowerCase();
  }

  function errorFactsForSection(result, sectionKey) {
    const wanted = text(sectionKey).toLowerCase();
    return soapFactsFromResult(result).filter(f => {
      if (factSectionKey(f) !== wanted) return false;
      const label = normalizeResultType(f.result || f.type);
      return label === 'Incorrect' || label === 'Missing' || label === 'Hallucination';
    });
  }

  function factAsDiff(fact) {
    const label = normalizeResultType(fact.result || fact.type);
    return {
      field: fact.field,
      ground_truth: fact.ground_truth,
      generated: fact.generated,
      type: text(label).toLowerCase(),
      result: label,
      severity: text(fact.criticality || 'Normal').toLowerCase(),
    };
  }

  function badgeForType(diff) {
    if (!diff) return '';
    const kind = normalizeResultType(diff.type || diff.result);
    const field = text(diff.field).toLowerCase();
    const rawKind = text(diff.type).toLowerCase();
    if (kind === 'Missing') return 'Missing';
    if (kind === 'Hallucination') return 'Hallucination';
    if (kind === 'Incorrect') {
      if (field === 'dose' || rawKind.indexOf('dose') !== -1) return 'Dose difference';
      return 'Incorrect';
    }
    if (rawKind === 'extra' || rawKind === 'added_in_final') return 'Hallucination';
    if (rawKind === 'missing' || rawKind === 'removed_in_final') return 'Missing';
    if (field === 'dose' || rawKind.indexOf('dose') !== -1) return 'Dose difference';
    if (rawKind === 'field_changed' && field === 'drug_name') return 'Name difference';
    if (rawKind === 'name_normalized' || rawKind === 'name_difference') return 'Name difference';
    if (rawKind === 'incorrect') return 'Incorrect';
    if (rawKind === 'field_changed') {
      return field ? field.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) : 'Changed';
    }
    if (kind === 'Correct' || kind === 'NA') return '';
    return text(diff.type).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }

  function excerpt(value, limit) {
    const t = text(value);
    const cap = limit == null ? 80 : limit;
    return t.length <= cap ? t : t.slice(0, cap - 1) + '…';
  }

  function notesFromDiff(diff, fallback) {
    if (!diff) return fallback || '';
    const kind = text(diff.type || diff.result).toLowerCase();
    const field = text(diff.field).toLowerCase();
    const gt = text(diff.ground_truth || diff.raw_value);
    const gen = text(diff.generated || diff.final_value);
    const detail = text(diff.detail);
    if (kind === 'missing' || normalizeResultType(kind) === 'Missing') {
      const bit = excerpt(gt);
      return bit ? "Missing: '" + bit + "'" : 'Missing';
    }
    if (normalizeResultType(kind) === 'Hallucination' || kind === 'extra' || kind === 'added_in_final') {
      const bit = excerpt(gen);
      return bit ? "Hallucination: '" + bit + "'" : 'Hallucination';
    }
    if (field === 'dose' || kind.indexOf('dose') !== -1) {
      const left = gt || text(diff.raw_value);
      const right = gen || text(diff.final_value);
      if (left && right) return 'Dose difference (' + left + ' vs ' + right + ')';
      return 'Dose difference';
    }
    if (detail) return detail;
    const badge = badgeForType(diff);
    if (gt && gen) {
      return (badge || fallback || '') + ": '" + excerpt(gt) + "' vs '" + excerpt(gen) + "'";
    }
    return badge || fallback || '';
  }

  function primaryDiff(diffs) {
    const list = diffs || [];
    if (!list.length) return null;
    let best = list[0];
    let bestRank = ENGINE_RANK[text(best.severity).toLowerCase()] || 1;
    for (let i = 1; i < list.length; i++) {
      const rank = ENGINE_RANK[text(list[i].severity).toLowerCase()] || 1;
      if (rank > bestRank) {
        best = list[i];
        bestRank = rank;
      }
    }
    return best;
  }

  function doseToken(value) {
    const match = text(value).match(DOSE_RE);
    return match ? match[0] : '';
  }

  function uniqueToken(focus, other) {
    const dose = doseToken(focus);
    const otherDose = doseToken(other);
    if (dose && otherDose && norm(dose) !== norm(otherDose)) return dose;
    const otherNorm = {};
    text(other).split(/\s+/).forEach(w => {
      const n = norm(w);
      if (n) otherNorm[n] = 1;
    });
    const words = text(focus).split(/\s+/).filter(Boolean);
    for (let i = 0; i < words.length; i++) {
      const n = norm(words[i]);
      if (n && !otherNorm[n]) return words[i];
    }
    return '';
  }

  function highlights(gt, gen, raw, diff) {
    const gtH = text(diff && diff.ground_truth);
    const genH = text(diff && diff.generated);
    const rawH = text(diff && diff.raw_value);
    const finalH = text(diff && diff.final_value);
    const field = text(diff && diff.field).toLowerCase();
    if (field === 'dose' || (gtH && genH && doseToken(gtH) && doseToken(genH))) {
      return {
        gt: doseToken(gt) || gtH || uniqueToken(gt, raw || gen),
        gen: doseToken(gen) || finalH || genH || uniqueToken(gen, raw || gt),
        raw: doseToken(raw) || rawH || uniqueToken(raw, gt),
      };
    }
    return {
      gt: gtH && gt.indexOf(gtH) !== -1 ? gtH : uniqueToken(gt, gen || raw),
      gen: genH && gen.indexOf(genH) !== -1 ? genH : uniqueToken(gen, gt),
      raw: rawH && raw.indexOf(rawH) !== -1 ? rawH : uniqueToken(raw, gt),
    };
  }

  function explanation(opts) {
    const hasDiff = !!opts.hasDiff;
    const formatting = !!opts.formatting;
    const badge = opts.badge || '';
    const notes = opts.notes || '';
    const gt = opts.gt || '';
    const gen = opts.gen || '';
    const raw = opts.raw || '';
    const diff = opts.diff || null;
    if (!hasDiff && !formatting) {
      return 'No difference. Generated output matches Ground Truth.';
    }
    if (formatting && !hasDiff) {
      return 'Minor formatting difference only; medical meaning matches Ground Truth.';
    }
    const gtEqGen = !textsDiffer(gt, gen);
    const rawVsGt = !isEmpty(raw) && textsDiffer(raw, gt);
    if (badge === 'Dose difference' || text(diff && diff.field).toLowerCase() === 'dose') {
      if (rawVsGt && gtEqGen) {
        return 'Dose differs between Raw LLM and Ground Truth. Generated output matches GT.';
      }
      if (!gtEqGen) {
        return 'Dose differs between Generated output and Ground Truth.';
      }
    }
    if (normalizeResultType(text(diff && (diff.type || diff.result))) === 'Missing') {
      const bit = excerpt(text(diff && diff.ground_truth));
      if (bit) {
        return "Generated output is missing detail present in Ground Truth: '" + bit + "'.";
      }
      return 'Generated output is missing detail present in Ground Truth.';
    }
    if (normalizeResultType(text(diff && (diff.type || diff.result))) === 'Hallucination') {
      const bit = excerpt(text(diff && diff.generated));
      if (bit) {
        return "Generated output includes a hallucination not in Ground Truth: '" + bit + "'.";
      }
      return 'Generated output includes a hallucination not in Ground Truth.';
    }
    if (rawVsGt && gtEqGen) {
      return 'Raw LLM differs from Ground Truth. Generated output matches GT.';
    }
    if (notes && notes !== 'No difference') return notes;
    return 'Generated output differs from Ground Truth.';
  }

  function soapRow(result, sectionKey) {
    const soapComp = asDict(result.soap_comparison);
    let genDiffs = sectionDiffs(soapComp.gt_vs_generated, sectionKey);
    const rawDiffs = sectionDiffs(soapComp.gt_vs_raw, sectionKey);
    const allFacts = soapFactsFromResult(result);
    if (allFacts.length) {
      genDiffs = errorFactsForSection(result, sectionKey).map(factAsDiff);
    }
    const gtSoap = asDict(result.soap_ground_truth);
    const genSoap = generatedSoap(result);
    const gtText = flattenSection(gtSoap[sectionKey]);
    const genText = flattenSection(genSoap[sectionKey]);
    const formatting = isFormattingOnly(gtText, genText);
    const primary = primaryDiff(genDiffs) || primaryDiff(rawDiffs);
    let engineSev = worstEngineSeverity(genDiffs) || 'none';
    if (engineSev === 'none' && formatting) engineSev = 'low';
    const hasGen = genDiffs.length > 0 || formatting;
    const hasRaw = rawDiffs.length > 0;
    const hasDiff = genDiffs.length > 0 || rawDiffs.length > 0;
    let badge = '';
    let notes = 'No difference';
    if (formatting && !genDiffs.length) {
      badge = 'Formatting';
      notes = 'Minor formatting difference (space)';
      if (text(gtText).replace(/ /g, '') !== text(genText).replace(/ /g, '')) {
        notes = 'Minor formatting difference';
      }
    }
    if (genDiffs.length) {
      const genPrimary = primaryDiff(genDiffs);
      badge = badgeForType(genPrimary);
      notes = notesFromDiff(genPrimary, notes);
    } else if (rawDiffs.length && !formatting) {
      notes = notesFromDiff(primaryDiff(rawDiffs), notes);
    }
    const hi = highlights(gtText, genText, '', primary);
    return {
      id: sectionKey,
      section_key: sectionKey,
      section_label: SOAP_SECTION_LABELS[sectionKey],
      severity_engine: engineSev,
      severity: displaySeverity(engineSev),
      gt_text: gtText,
      gen_text: genText,
      raw_text: '',
      raw_display: DASH,
      gen_badge: hasGen && badge ? badge : '',
      raw_badge: '',
      notes: notes,
      has_difference: hasDiff || formatting,
      has_gen_gt_diff: hasGen,
      has_raw_gt_diff: hasRaw,
      explanation: explanation({
        hasDiff: hasDiff, formatting: formatting, badge: badge, notes: notes,
        gt: gtText, gen: genText, raw: '', diff: primary,
      }),
      gt_highlight: (hasDiff || formatting) ? hi.gt : '',
      gen_highlight: (hasDiff || formatting) ? hi.gen : '',
      raw_highlight: '',
    };
  }

  function medicationRow(result) {
    const medVal = asDict(result.medication_validation);
    const diffs = (medVal.differences || []).filter(d => d && typeof d === 'object');
    const gtSoap = asDict(result.soap_ground_truth);
    const genSoap = generatedSoap(result);
    const raw = rawSoap(result);
    const gtText = formatMedications(asDict(gtSoap.plan).medications);
    const genText = formatMedications(
      medVal.final_medications || asDict(genSoap.plan).medications
    );
    const rawText = formatMedications(
      medVal.raw_medications || asDict(raw.plan).medications
    );
    const formattingGen = isFormattingOnly(gtText, genText);
    const formattingRaw = isFormattingOnly(gtText, rawText);
    const primary = primaryDiff(diffs);
    let engineSev = worstEngineSeverity(diffs);
    let genVsGt = textsDiffer(gtText, genText) || formattingGen;
    let rawVsGt = !isEmpty(rawText) && (textsDiffer(gtText, rawText) || formattingRaw);
    if (diffs.length && !genVsGt) rawVsGt = true;
    if (engineSev === 'none' && (formattingGen || formattingRaw)) engineSev = 'low';
    let badge = '';
    let rawBadge = '';
    let notes = 'No difference';
    if (formattingGen && !diffs.length) {
      badge = 'Formatting';
      notes = 'Minor formatting difference (space)';
    }
    if (primary) {
      const kindBadge = badgeForType(primary);
      notes = notesFromDiff(primary, notes);
      if (genVsGt && !formattingGen) badge = kindBadge;
      else if (formattingGen) badge = 'Formatting';
      if (rawVsGt) rawBadge = kindBadge || (formattingRaw ? 'Formatting' : '');
      if (kindBadge === 'Dose difference' || text(primary.field).toLowerCase() === 'dose') {
        const left = text(primary.raw_value) || doseToken(rawText);
        const right = text(primary.ground_truth) || doseToken(gtText) || text(primary.final_value);
        if (left && right && left !== right) {
          if (!genVsGt) {
            notes = 'Dose difference (' + (doseToken(gtText) || right) + ' vs '
              + (doseToken(rawText) || left) + ')';
          } else {
            notes = 'Dose difference (' + (doseToken(gtText) || right) + ' vs '
              + (doseToken(genText) || left) + ')';
          }
        }
      }
    } else if (formattingRaw && !genVsGt) {
      rawBadge = 'Formatting';
      notes = 'Minor formatting difference';
    }
    const hasDiff = diffs.length > 0 || genVsGt || rawVsGt;
    const hi = highlights(gtText, genText, rawText, primary);
    return {
      id: MEDICATION_ROW_ID,
      section_key: MEDICATION_ROW_ID,
      section_label: MEDICATION_ROW_LABEL,
      severity_engine: engineSev,
      severity: displaySeverity(engineSev),
      gt_text: gtText,
      gen_text: genText,
      raw_text: rawText,
      raw_display: displayCell(rawText),
      gen_badge: badge,
      raw_badge: rawBadge,
      notes: notes,
      has_difference: hasDiff,
      has_gen_gt_diff: genVsGt,
      has_raw_gt_diff: rawVsGt,
      explanation: explanation({
        hasDiff: diffs.length > 0 || genVsGt || rawVsGt,
        formatting: formattingGen || formattingRaw,
        badge: badge || rawBadge,
        notes: notes,
        gt: gtText, gen: genText, raw: rawText, diff: primary,
      }),
      gt_highlight: hasDiff ? hi.gt : '',
      gen_highlight: hasDiff ? hi.gen : '',
      raw_highlight: hasDiff ? hi.raw : '',
    };
  }

  function buildComparisonRows(result) {
    const data = asDict(result);
    const rows = SOAP_SECTION_KEYS.map(key => soapRow(data, key));
    rows.push(medicationRow(data));
    return rows;
  }

  function filterComparisonRows(rows, filters) {
    const f = filters || {};
    const wantedSection = text(f.section).toLowerCase() || 'all';
    const wantedSev = text(f.severity).toLowerCase() || 'all';
    const scope = text(f.showFor).toLowerCase() || DIFF_SCOPE_ALL;
    const needle = text(f.query).toLowerCase();
    return (rows || []).filter(row => {
      if (wantedSection !== 'all' && wantedSection !== 'all sections') {
        if (text(row.id).toLowerCase() !== wantedSection) return false;
      }
      if (wantedSev !== 'all' && wantedSev !== '') {
        if (text(row.severity).toLowerCase() !== wantedSev) return false;
      }
      if (scope === DIFF_SCOPE_GEN_VS_GT && !row.has_gen_gt_diff) return false;
      if (scope === DIFF_SCOPE_RAW_VS_GT && !row.has_raw_gt_diff) return false;
      if (needle) {
        const hay = [row.section_label, row.gt_text, row.gen_text].join(' ').toLowerCase();
        if (hay.indexOf(needle) === -1) return false;
      }
      return true;
    });
  }

  function detailsForRow(row) {
    const data = asDict(row);
    return {
      id: data.id,
      gt_text: data.gt_text || '',
      gen_text: data.gen_text || '',
      raw_text: data.raw_text || '',
      raw_display: data.raw_display || DASH,
      explanation: data.explanation || '',
      gt_highlight: data.gt_highlight || '',
      gen_highlight: data.gen_highlight || '',
      raw_highlight: data.raw_highlight || '',
    };
  }

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function highlightHtml(value, phrase, cls) {
    const raw = text(value);
    if (!raw) return esc(DASH);
    if (!phrase) return esc(raw);
    const idx = raw.toLowerCase().indexOf(phrase.toLowerCase());
    if (idx < 0) return esc(raw);
    const before = raw.slice(0, idx);
    const match = raw.slice(idx, idx + phrase.length);
    const after = raw.slice(idx + phrase.length);
    return esc(before)
      + '<mark class="' + esc(cls) + '">' + esc(match) + '</mark>'
      + esc(after);
  }

  function badgeClass(label) {
    const key = text(label).toLowerCase();
    if (key.indexOf('missing') !== -1) return 'gt-cmp-inline-missing';
    if (key.indexOf('hallucin') !== -1) return 'gt-cmp-inline-hallucination';
    if (key.indexOf('incorrect') !== -1) return 'gt-cmp-inline-incorrect';
    if (key.indexOf('format') !== -1) return 'gt-cmp-inline-format';
    if (key.indexOf('dose') !== -1) return 'gt-cmp-inline-dose';
    return 'gt-cmp-inline-other';
  }

  function sevClass(label) {
    const key = text(label).toLowerCase();
    if (key === 'critical') return 'gt-cmp-sev-critical';
    if (key === 'major') return 'gt-cmp-sev-major';
    return 'gt-cmp-sev-minor';
  }

  function inlineBadge(label) {
    if (!label) return '';
    return '<span class="gt-cmp-inline-badge ' + badgeClass(label) + '">'
      + esc(label) + '</span>';
  }

  const EYE_SVG = '<svg class="gt-cmp-eye" viewBox="0 0 24 24" aria-hidden="true">'
    + '<path fill="currentColor" d="M12 5c-5 0-9.3 3.1-11 7 1.7 3.9 6 7 11 7s9.3-3.1 11-7c-1.7-3.9-6-7-11-7zm0 12a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-2.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z"/>'
    + '</svg>';

  function chevronSvg(up) {
    const d = up ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6';
    return '<svg class="gt-cmp-chevron" viewBox="0 0 24 24" aria-hidden="true">'
      + '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="' + d + '"/>'
      + '</svg>';
  }

  function detailsPanel(row) {
    const d = detailsForRow(row);
    return (
      '<tr class="gt-cmp-details-row" data-gt-cmp-details="' + esc(row.id) + '">'
      + '<td colspan="5">'
      + '<div class="gt-cmp-details">'
      + '<div class="gt-cmp-details-head">'
      + '<span>Difference Details</span>'
      + '<button type="button" class="gt-cmp-icon-btn" data-gt-cmp-toggle="' + esc(row.id) + '" aria-label="Collapse difference details">'
      + chevronSvg(true)
      + '</button>'
      + '</div>'
      + '<div class="gt-cmp-details-grid">'
      + '<div class="gt-cmp-details-card" data-gt-cmp-detail-col="gt">'
      + '<div class="gt-cmp-details-label">Ground Truth (GT)</div>'
      + '<div class="gt-cmp-details-body">'
      + highlightHtml(d.gt_text, d.gt_highlight, 'gt-cmp-hl-gt')
      + '</div></div>'
      + '<div class="gt-cmp-details-card" data-gt-cmp-detail-col="raw">'
      + '<div class="gt-cmp-details-label">Raw LLM (Med Only)</div>'
      + '<div class="gt-cmp-details-body">'
      + highlightHtml(d.raw_display === DASH ? '' : d.raw_text, d.raw_highlight, 'gt-cmp-hl-raw')
      + '</div></div>'
      + '<div class="gt-cmp-details-card" data-gt-cmp-detail-col="gen">'
      + '<div class="gt-cmp-details-label">Generated Output (Gen)</div>'
      + '<div class="gt-cmp-details-body">'
      + highlightHtml(d.gen_text, d.gen_highlight, 'gt-cmp-hl-gen')
      + '</div></div>'
      + '<div class="gt-cmp-details-card" data-gt-cmp-detail-col="explanation">'
      + '<div class="gt-cmp-details-label">Explanation</div>'
      + '<div class="gt-cmp-details-body">' + esc(d.explanation) + '</div>'
      + '</div></div></div>'
      + '</td></tr>'
    );
  }

  function dataRow(row, expanded) {
    const notesClass = row.notes === 'No difference' ? 'gt-cmp-notes-none' : 'gt-cmp-notes';
    return (
      '<tr class="gt-cmp-row" data-gt-cmp-row="' + esc(row.id) + '" data-severity="' + esc(row.severity) + '">'
      + '<td class="gt-cmp-col-section">'
      + '<div class="gt-cmp-section-name">' + esc(row.section_label) + '</div>'
      + '<span class="gt-cmp-sev ' + sevClass(row.severity) + '" data-gt-cmp-severity="' + esc(row.severity_engine) + '">'
      + esc(row.severity)
      + '</span>'
      + '</td>'
      + '<td class="gt-cmp-col-gt">' + esc(displayCell(row.gt_text)) + '</td>'
      + '<td class="gt-cmp-col-gen">'
      + esc(displayCell(row.gen_text))
      + (row.gen_badge ? ' ' + inlineBadge(row.gen_badge) : '')
      + '</td>'
      + '<td class="gt-cmp-col-raw">'
      + esc(row.raw_display)
      + (row.raw_badge ? ' ' + inlineBadge(row.raw_badge) : '')
      + '</td>'
      + '<td class="gt-cmp-col-notes">'
      + '<div class="' + notesClass + '">' + esc(row.notes) + '</div>'
      + '<div class="gt-cmp-notes-actions">'
      + '<button type="button" class="gt-cmp-view-diff" data-gt-cmp-toggle="' + esc(row.id) + '">'
      + EYE_SVG + ' View Diff'
      + '</button>'
      + '<button type="button" class="gt-cmp-icon-btn" data-gt-cmp-toggle="' + esc(row.id) + '" aria-expanded="'
      + (expanded ? 'true' : 'false') + '" aria-label="Toggle difference details">'
      + chevronSvg(!!expanded)
      + '</button>'
      + '</div>'
      + '</td>'
      + '</tr>'
      + (expanded ? detailsPanel(row) : '')
    );
  }

  function filterShell() {
    const sectionOpts = ['<option value="all">All Sections</option>']
      .concat(SOAP_SECTION_KEYS.map(k =>
        '<option value="' + k + '">' + esc(SOAP_SECTION_LABELS[k]) + '</option>'
      ))
      .concat(['<option value="medication">' + esc(MEDICATION_ROW_LABEL) + '</option>'])
      .join('');
    const sevOpts = [
      '<option value="all">All</option>',
      '<option value="critical">Critical</option>',
      '<option value="major">Major</option>',
      '<option value="minor">Minor</option>',
    ].join('');
    const scopeOpts = DIFF_SCOPE_OPTIONS.map(pair =>
      '<option value="' + pair[0] + '">' + esc(pair[1]) + '</option>'
    ).join('');
    return (
      '<div class="gt-cmp-filters">'
      + '<label class="gt-cmp-filter">Section'
      + '<select data-gt-cmp-filter="section">' + sectionOpts + '</select>'
      + '</label>'
      + '<label class="gt-cmp-filter">Severity'
      + '<select data-gt-cmp-filter="severity">' + sevOpts + '</select>'
      + '</label>'
      + '<label class="gt-cmp-filter">Show differences for'
      + '<select data-gt-cmp-filter="showFor">' + scopeOpts + '</select>'
      + '</label>'
      + '<label class="gt-cmp-filter gt-cmp-filter-search">Search'
      + '<span class="gt-cmp-search-wrap">'
      + '<input type="search" data-gt-cmp-filter="query" placeholder="Search by keyword..." autocomplete="off">'
      + '<span class="gt-cmp-search-icon" aria-hidden="true">'
      + '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M15.5 14h-.8l-.3-.3A6.5 6.5 0 1 0 14 15.5l.3.3v.8l5 5 1.5-1.5-5-5zm-6 0a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9z"/></svg>'
      + '</span></span>'
      + '</label>'
      + '</div>'
    );
  }

  function legend() {
    return (
      '<div class="gt-cmp-legend">'
      + '<span class="gt-cmp-legend-icon" aria-hidden="true">i</span>'
      + 'GT = Ground Truth &nbsp;|&nbsp; Gen = Generated Output &nbsp;|&nbsp; '
      + 'Raw LLM = Raw LLM Output (Medication Only)'
      + '</div>'
    );
  }

  function tableBody(rows, expandedIds) {
    if (!rows.length) {
      return '<tr class="gt-cmp-empty"><td colspan="5">No rows match the current filters.</td></tr>';
    }
    const open = expandedIds || {};
    return rows.map(row => dataRow(row, !!open[row.id])).join('');
  }

  function renderTable(host, state) {
    const tbody = host.querySelector('[data-gt-cmp-body]');
    if (!tbody) return;
    const visible = filterComparisonRows(state.rows, state.filters);
    tbody.innerHTML = tableBody(visible, state.expanded);
  }

  function bind(host, state) {
    host.addEventListener('change', ev => {
      const el = ev.target && ev.target.closest('[data-gt-cmp-filter]');
      if (!el || el.getAttribute('data-gt-cmp-filter') === 'query') return;
      const key = el.getAttribute('data-gt-cmp-filter');
      state.filters[key] = el.value;
      renderTable(host, state);
    });
    host.addEventListener('input', ev => {
      const el = ev.target && ev.target.closest('[data-gt-cmp-filter="query"]');
      if (!el) return;
      state.filters.query = el.value;
      renderTable(host, state);
    });
    host.addEventListener('click', ev => {
      const btn = ev.target && ev.target.closest('[data-gt-cmp-toggle]');
      if (!btn || !host.contains(btn)) return;
      ev.preventDefault();
      const id = btn.getAttribute('data-gt-cmp-toggle');
      if (!id) return;
      if (state.expanded[id]) delete state.expanded[id];
      else state.expanded[id] = true;
      renderTable(host, state);
    });
  }

  function mount(host, result) {
    if (!host) return null;
    const rows = buildComparisonRows(result);
    const state = {
      rows: rows,
      filters: { section: 'all', severity: 'all', showFor: DIFF_SCOPE_ALL, query: '' },
      expanded: {},
    };
    host.innerHTML = (
      '<div class="gt-cmp" data-gt-cmp-root>'
      + filterShell()
      + '<div class="gt-cmp-table-wrap">'
      + '<table class="gt-cmp-table">'
      + '<thead><tr>'
      + TABLE_COLUMNS.map(c => '<th>' + esc(c) + '</th>').join('')
      + '</tr></thead>'
      + '<tbody data-gt-cmp-body></tbody>'
      + '</table></div>'
      + legend()
      + '</div>'
    );
    bind(host, state);
    renderTable(host, state);
    host._gtCmpState = state;
    return state;
  }

  const api = {
    SOAP_SECTION_KEYS,
    ROW_IDS,
    TABLE_COLUMNS,
    DIFF_SCOPE_ALL,
    DIFF_SCOPE_GEN_VS_GT,
    DIFF_SCOPE_RAW_VS_GT,
    DIFF_SCOPE_OPTIONS,
    displaySeverity,
    normalizeResultType,
    soapFactsFromResult,
    factsFromNestedSoap,
    worstEngineSeverity,
    buildComparisonRows,
    filterComparisonRows,
    detailsForRow,
    mount,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumGtComparisonTable = api;
})(typeof window !== 'undefined' ? window : globalThis);
