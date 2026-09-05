/**
 * Summary-tab SOAP field navigator.
 * Default: nothing selected → empty "Select a field to compare" pane.
 * Facts come from the result (Prompt 1). Does not rescore.
 */
(function (root) {
  const FILTER_ALL = 'all';
  const FILTER_MATCH = 'match';
  const FILTER_INCORRECT = 'incorrect';
  const FILTER_MISSING = 'missing';
  const FILTER_HALLUCINATED = 'hallucinated';
  const FILTER_ORDER = [FILTER_ALL, FILTER_MATCH, FILTER_INCORRECT, FILTER_MISSING, FILTER_HALLUCINATED];
  const FILTER_LABELS = {
    all: 'All',
    match: 'Match',
    incorrect: 'Incorrect',
    missing: 'Missing',
    hallucinated: 'Hallucinated',
  };
  const SECTION_KEYS = ['subjective', 'objective', 'assessment', 'plan'];
  const SECTION_LABELS = {
    subjective: 'SUBJECTIVE',
    objective: 'OBJECTIVE',
    assessment: 'ASSESSMENT',
    plan: 'PLAN',
  };
  const PLAN_GROUP_ORDER = ['medications', 'investigations', 'procedures', 'follow_up', 'other'];
  const PLAN_GROUP_LABELS = {
    medications: 'Medications',
    investigations: 'Investigations',
    procedures: 'Procedures',
    follow_up: 'Follow up',
    other: 'Other',
  };
  const ALWAYS_PLAN_GROUPS = {
    medications: 1, investigations: 1, procedures: 1, follow_up: 1,
  };
  const OBJECTIVE_GROUP_ORDER = ['vitals', 'physical_exam', 'other'];
  const OBJECTIVE_GROUP_LABELS = {
    vitals: 'Vitals',
    physical_exam: 'Physical Exam',
    other: 'Other',
  };
  const ALWAYS_OBJECTIVE_GROUPS = { vitals: 1, physical_exam: 1 };
  const TABLE_GROUP_KEYS = { vitals: 1, physical_exam: 1 };
  const SOAP_NAV_CATALOG = [
    { field: 'Chief complaint', section: 'subjective', base: 'chief_complaint' },
    { field: 'History of present illness', section: 'subjective', base: 'history_of_present_illness' },
    { field: 'Past medical history', section: 'subjective', base: 'past_medical_history' },
    { field: 'Current medications', section: 'subjective', base: 'subjective_medications' },
    { field: 'Allergy', section: 'subjective', base: 'allergy' },
    { field: 'Social history', section: 'subjective', base: 'social_history' },
    { field: 'Family history', section: 'subjective', base: 'family_history' },
    { field: 'Blood pressure', section: 'objective', base: 'blood_pressure', group: 'vitals' },
    { field: 'Pulse', section: 'objective', base: 'heart_rate', group: 'vitals' },
    { field: 'Respiratory rate', section: 'objective', base: 'respiratory_rate', group: 'vitals' },
    { field: 'Temperature', section: 'objective', base: 'temperature', group: 'vitals' },
    { field: 'Heart exam', section: 'objective', base: 'heart_exam', group: 'physical_exam' },
    { field: 'Other findings', section: 'objective', base: 'other_findings', group: 'physical_exam' },
    { field: 'Diagnosis', section: 'assessment', base: 'diagnosis' },
    { field: 'Diagnosis type', section: 'assessment', base: 'diagnosis_type' },
    { field: 'Diagnosis status', section: 'assessment', base: 'diagnosis_status' },
    { field: 'Assessment reasoning', section: 'assessment', base: 'assessment_reasoning' },
    { field: 'Medicine', section: 'plan', base: 'medicine', group: 'medications' },
    { field: 'Activity', section: 'plan', base: 'activity', group: 'other' },
    { field: 'Investigations', section: 'plan', base: 'investigations', group: 'investigations' },
    { field: 'Education', section: 'plan', base: 'education', group: 'other' },
    { field: 'Follow-up', section: 'plan', base: 'follow_up', group: 'follow_up' },
    { field: 'Summary', section: 'plan', base: 'soap_summary', group: 'other' },
  ];
  const EMPTY_HEADING = 'Select a field to compare';
  const EMPTY_BODY = 'Choose any field from the SOAP sections to view Ground Truth vs AI Output comparison and field level details';
  const SEARCH_PLACEHOLDER = 'Search fields, medication, diagnosis...';
  const DEFAULT_EXPANDED = {
    subjective: false,
    objective: false,
    assessment: false,
    plan: false,
  };
  const MED_FIELDS = {
    'drug name': 1, dose: 1, schedule: 1, duration: 1, instructions: 1,
  };
  const MED_ATTRS = [
    { keys: ['drug name', 'drug_name'], label: 'Medicine name' },
    { keys: ['dose'], label: 'Dose' },
    { keys: ['schedule'], label: 'Schedule' },
    { keys: ['duration'], label: 'Duration' },
    { keys: ['instructions'], label: 'Instructions' },
  ];
  const INVEST_FIELDS = { investigations: 1, investigation: 1 };
  const PROC_FIELDS = { procedures: 1, procedure: 1 };
  const FOLLOW_FIELDS = {
    'follow-up': 1, 'follow up': 1, follow_up: 1, followup: 1,
  };
  const VITALS_FIELDS = {
    'blood pressure': 1, bp: 1,
    pulse: 1, 'heart rate': 1, hr: 1,
    'respiratory rate': 1, rr: 1, 'resp rate': 1,
    temperature: 1, temp: 1,
  };
  const PHYSICAL_EXAM_FIELDS = {
    'heart exam': 1, heart: 1,
    'other findings': 1, 'other finding': 1,
  };

  function text(value) {
    return value == null ? '' : String(value).trim();
  }

  function asDict(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  }

  function normName(value) {
    return text(value).toLowerCase().replace(/[_-]/g, ' ').replace(/\s+/g, ' ').trim();
  }

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function normalizeResultType(raw) {
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

  function displayFilterResult(raw) {
    const label = normalizeResultType(raw);
    if (label === 'NA') return 'Missing';
    if (label === 'Missing') return 'Incorrect';
    if (label === 'Correct' || label === 'Incorrect' || label === 'Hallucination') return label;
    return label ? label : 'Correct';
  }

  function soapFactsFromResult(result) {
    const api = root.MedsumGtComparisonTable || {};
    if (typeof api.soapFactsFromResult === 'function') {
      return api.soapFactsFromResult(result);
    }
    const soap = asDict(result && result.soap_comparison);
    const pair = soap.gt_vs_generated && typeof soap.gt_vs_generated === 'object'
      ? soap.gt_vs_generated : soap;
    if (Array.isArray(pair.facts) && pair.facts.length) {
      return pair.facts.filter(f => f && typeof f === 'object');
    }
    if (Array.isArray(soap.facts) && soap.facts.length) {
      return soap.facts.filter(f => f && typeof f === 'object');
    }
    if (typeof api.factsFromNestedSoap === 'function') {
      return api.factsFromNestedSoap(result);
    }
    return [];
  }

  function encodedCells(resultLabel, gtVal, genVal) {
    const key = text(resultLabel);
    const gtText = text(gtVal) || '—';
    const genText = text(genVal) || '—';
    if (key === 'Missing') {
      return { ground_truth: text(gtVal) ? gtText : '—', generated: '—', gt_empty: false, gen_empty: true };
    }
    if (key === 'Hallucination') {
      return { ground_truth: '—', generated: text(genVal) ? genText : '—', gt_empty: true, gen_empty: false };
    }
    return { ground_truth: gtText, generated: genText, gt_empty: false, gen_empty: false };
  }

  function errorTypeLabel(result) {
    if (result === 'Correct') return 'Match';
    if (result === 'Incorrect') return 'Value Mismatch';
    if (result === 'Missing') return 'Missing';
    if (result === 'Hallucination') return 'Hallucination';
    return result || '—';
  }

  function subtypeLabel(fact) {
    const cats = asDict(fact).categories || [];
    const map = { temporal: 'Duration', medication: 'Medication', numerical: 'Numeric', diagnosis: 'Diagnosis' };
    for (let i = 0; i < cats.length; i++) {
      const mapped = map[String(cats[i]).toLowerCase()];
      if (mapped) return mapped;
    }
    return displayFieldLabel(fact);
  }

  function differenceLine(fact) {
    const result = resultOf(fact);
    const cells = encodedCells(result, asDict(fact).ground_truth, asDict(fact).generated);
    if (result === 'Correct') return 'No difference. Generated output matches Ground Truth.';
    if (result === 'NA') return 'Field is empty in both Ground Truth and Generated output.';
    if (result === 'Missing') {
      const bit = cells.ground_truth !== '—' ? cells.ground_truth : displayFieldLabel(fact);
      return "Generated output is missing: '" + bit + "'.";
    }
    if (result === 'Hallucination') {
      const bit = cells.generated !== '—' ? cells.generated : displayFieldLabel(fact);
      return "Generated output includes content not in Ground Truth: '" + bit + "'.";
    }
    if (cells.ground_truth !== '—' && cells.generated !== '—') {
      return 'Difference: ' + displayFieldLabel(fact) + ' changed from ' + cells.ground_truth
        + ' to ' + cells.generated + '.';
    }
    return 'Generated output differs from Ground Truth.';
  }

  function highlightPhrase(left, right) {
    const a = text(left);
    const b = text(right);
    if (!a || !b || a === b) return '';
    const aw = a.split(/\s+/);
    const bw = b.split(/\s+/);
    const bset = {};
    bw.forEach(w => { bset[w.toLowerCase()] = 1; });
    for (let i = 0; i < aw.length; i++) {
      if (!bset[aw[i].toLowerCase()]) return aw[i];
    }
    return '';
  }

  function resultOf(fact) {
    return normalizeResultType(asDict(fact).result || asDict(fact).type) || 'Correct';
  }

  function sectionKey(fact) {
    const data = asDict(fact);
    let raw = text(data.section);
    if (!raw) {
      const name = text(data.base_field || data.field);
      const lower = normName(name);
      if (/chief|history|allergy|cough|fever|social|family|blood group|past medical|current med/.test(lower)) {
        raw = 'subjective';
      } else if (/blood pressure|pulse|heart rate|temperature|spo2|respiratory|height|weight|heart exam|other findings/.test(lower)) {
        raw = 'objective';
      } else if (/diagnosis|assessment reasoning/.test(lower)) {
        raw = 'assessment';
      } else if (/drug|dose|schedule|duration|instruction|follow|investigation|procedure|activity|education/.test(lower)) {
        raw = 'plan';
      }
    }
    const label = normName(raw);
    return SECTION_KEYS.indexOf(label) !== -1 ? label : (label || 'other');
  }

  function planGroupKey(fact) {
    const data = asDict(fact);
    if (sectionKey(data) !== 'plan') return null;
    const cats = (data.categories || []).map(c => String(c).toLowerCase());
    if (cats.indexOf('medication') !== -1) return 'medications';
    const name = normName(data.base_field || data.field);
    if (MED_FIELDS[name] || name.indexOf('drug') !== -1) return 'medications';
    if (INVEST_FIELDS[name] || name.indexOf('investigation') !== -1) return 'investigations';
    if (PROC_FIELDS[name] || name.indexOf('procedure') !== -1) return 'procedures';
    if (FOLLOW_FIELDS[name] || name.indexOf('follow') === 0) return 'follow_up';
    return 'other';
  }

  function objectiveGroupKey(fact) {
    const data = asDict(fact);
    if (sectionKey(data) !== 'objective') return null;
    const name = normName(data.base_field || data.field || data.label);
    if (VITALS_FIELDS[name]) return 'vitals';
    if (PHYSICAL_EXAM_FIELDS[name]) return 'physical_exam';
    return 'other';
  }

  function fieldId(fact, index) {
    const data = asDict(fact);
    const section = sectionKey(data);
    const slug = (normName(data.base_field || data.field) || 'field').replace(/ /g, '_');
    if (data.index != null && data.index !== '') return section + '.' + slug + '.' + data.index;
    return section + '.' + slug + '.' + index;
  }

  function displayFieldLabel(fact) {
    const name = text(asDict(fact).field || asDict(fact).base_field);
    if (!name) return 'Unknown';
    if (name.indexOf('_') !== -1 && name.indexOf(' ') === -1) {
      return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    }
    return name;
  }

  function navField(fact, index) {
    const data = asDict(fact);
    const prompt1 = resultOf(data);
    const result = displayFilterResult(prompt1);
    const section = sectionKey(data);
    let group = null;
    if (section === 'plan') group = planGroupKey(data);
    else if (section === 'objective') group = objectiveGroupKey(data);
    let gt = data.ground_truth;
    const gen = data.generated;
    if (!('ground_truth' in data) && 'value' in data) gt = data.value;
    const cells = encodedCells(result, gt, gen);
    return {
      id: fieldId(data, index),
      section: section,
      group: group,
      label: displayFieldLabel(data),
      base_field: text(data.base_field || data.field),
      result: result,
      ground_truth: cells.ground_truth,
      generated: cells.generated,
      gt_empty: cells.gt_empty,
      gen_empty: cells.gen_empty,
      raw_ground_truth: text(gt),
      raw_generated: text(gen),
      gt_highlight: cells.gt_empty ? '' : highlightPhrase(gt, gen),
      gen_highlight: cells.gen_empty ? '' : highlightPhrase(gen, gt),
      categories: data.categories || [],
      criticality: text(data.criticality),
      confidence: data.confidence,
      error_type: errorTypeLabel(result),
      subtype: subtypeLabel(data),
      difference: differenceLine(data),
      index: data.index,
      is_na: false,
      is_match: result === 'Correct',
      is_incorrect: result === 'Incorrect',
      is_missing: result === 'Missing',
      is_hallucinated: result === 'Hallucination',
    };
  }

  function fieldsFromFacts(facts) {
    const out = [];
    (facts || []).forEach((fact, i) => {
      if (!fact || typeof fact !== 'object') return;
      out.push(navField(fact, i));
    });
    return out;
  }

  function mergeKey(row) {
    return normName(row.base_field || row.label || row.field) + '|' + normName(row.section);
  }

  function orderByCatalog(rows) {
    const index = {};
    SOAP_NAV_CATALOG.forEach((spec, i) => {
      index[normName(spec.base) + '|' + spec.section] = i;
      index[normName(spec.field) + '|' + spec.section] = i;
    });
    const sectionRank = {};
    SECTION_KEYS.forEach((key, i) => { sectionRank[key] = i; });
    return (rows || []).slice().sort((a, b) => {
      const sa = sectionRank[a.section] != null ? sectionRank[a.section] : 99;
      const sb = sectionRank[b.section] != null ? sectionRank[b.section] : 99;
      if (sa !== sb) return sa - sb;
      const ia = index[mergeKey(a)];
      const ib = index[mergeKey(b)];
      const ca = ia == null ? index[normName(a.label) + '|' + a.section] : ia;
      const cb = ib == null ? index[normName(b.label) + '|' + b.section] : ib;
      const na = ca == null ? 999 : ca;
      const nb = cb == null ? 999 : cb;
      if (na !== nb) return na - nb;
      const ma = Number(a.index);
      const mb = Number(b.index);
      const xa = Number.isFinite(ma) ? ma : 0;
      const xb = Number.isFinite(mb) ? mb : 0;
      if (xa !== xb) return xa - xb;
      return String(a.label || '').localeCompare(String(b.label || ''));
    });
  }

  function mergeCatalogFields(scored) {
    const rows = (scored || []).slice();
    const seen = {};
    rows.forEach(row => {
      seen[mergeKey(row)] = 1;
      seen[normName(row.label) + '|' + normName(row.section)] = 1;
    });
    SOAP_NAV_CATALOG.forEach((spec, i) => {
      const keys = [
        normName(spec.base) + '|' + spec.section,
        normName(spec.field) + '|' + spec.section,
      ];
      if (keys.some(k => seen[k])) return;
      rows.push(navField({
        field: spec.field,
        base_field: spec.base,
        section: spec.section,
        categories: spec.group === 'medications' ? ['medication'] : [],
        result: 'NA',
        ground_truth: '',
        generated: '',
      }, 'cat-' + i));
    });
    return orderByCatalog(rows);
  }

  function fieldsFromResult(result) {
    return collapseMedicineFields(mergeCatalogFields(fieldsFromFacts(soapFactsFromResult(result))));
  }

  function isMedLeaf(row) {
    if (!row || row.is_medicine || normName(row.base_field) === 'medicine') return false;
    if (row.group === 'medications') return true;
    const name = normName(row.base_field || row.label || row.field);
    return !!MED_FIELDS[name] && row.section === 'plan';
  }

  function worstResult(results) {
    const rank = { Hallucination: 4, Incorrect: 3, Missing: 2, Correct: 1, NA: 0 };
    let worst = 'NA';
    let best = -1;
    (results || []).forEach(raw => {
      const label = normalizeResultType(raw) || 'NA';
      const score = rank[label] || 0;
      if (score > best) {
        best = score;
        worst = label;
      }
    });
    return worst;
  }

  function emptyMedicineRows() {
    return MED_ATTRS.map(attr => ({
      label: attr.label,
      ground_truth: '—',
      generated: '—',
      gt_empty: true,
      gen_empty: true,
      result: 'Missing',
    }));
  }

  function medicineTableForLeaves(leaves, index) {
    const byAttr = {};
    (leaves || []).forEach(leaf => {
      byAttr[normName(leaf.base_field || leaf.label)] = leaf;
    });
    const rows = MED_ATTRS.map(attr => {
      let leaf = null;
      attr.keys.forEach(key => { if (!leaf) leaf = byAttr[key]; });
      if (leaf) {
        return {
          label: attr.label,
          ground_truth: leaf.ground_truth || '—',
          generated: leaf.generated || '—',
          gt_empty: !!leaf.gt_empty,
          gen_empty: !!leaf.gen_empty,
          result: leaf.result || 'Missing',
        };
      }
      return {
        label: attr.label,
        ground_truth: '—',
        generated: '—',
        gt_empty: true,
        gen_empty: true,
        result: 'Missing',
      };
    });
    const nameLeaf = byAttr['drug name'] || byAttr.drug_name;
    let name = '';
    if (nameLeaf) {
      name = text(nameLeaf.raw_ground_truth) || text(nameLeaf.raw_generated);
      if (name === '—') name = '';
    }
    const n = Number(index);
    const title = name || ('Medicine ' + ((Number.isFinite(n) ? n : 0) + 1));
    return { title: title, rows: rows, result: worstResult(rows.map(r => r.result)) };
  }

  function buildMedicineField(leaves) {
    const buckets = {};
    (leaves || []).forEach(leaf => {
      const idx = leaf.index == null || leaf.index === '' ? 0 : leaf.index;
      if (!buckets[idx]) buckets[idx] = [];
      buckets[idx].push(leaf);
    });
    const keys = Object.keys(buckets).sort((a, b) => Number(a) - Number(b));
    let tables;
    let count;
    let worst;
    if (!keys.length) {
      tables = [{ title: 'Medicine', rows: emptyMedicineRows(), result: 'Missing' }];
      count = 0;
      worst = 'Missing';
    } else {
      tables = keys.map(k => medicineTableForLeaves(buckets[k], k));
      count = keys.length;
      worst = worstResult(tables.map(t => t.result));
    }
    const result = worst || 'Missing';
    return {
      id: 'plan.medicine.0',
      section: 'plan',
      group: 'medications',
      label: 'Medicine',
      base_field: 'medicine',
      result: result,
      ground_truth: '—',
      generated: '—',
      gt_empty: true,
      gen_empty: true,
      raw_ground_truth: '',
      raw_generated: '',
      categories: ['medication'],
      criticality: '',
      confidence: null,
      error_type: errorTypeLabel(result),
      subtype: 'Medication',
      difference: result === 'Correct'
        ? 'No difference. Generated output matches Ground Truth.'
        : result === 'Missing'
          ? 'No medication facts to compare.'
          : 'See medicine table for name, dose, schedule, and instruction differences.',
      index: 0,
      is_na: false,
      is_match: result === 'Correct',
      is_incorrect: result === 'Incorrect',
      is_missing: result === 'Missing',
      is_hallucinated: result === 'Hallucination',
      is_medicine: true,
      medicine_tables: tables,
      medication_count: count,
    };
  }

  function collapseMedicineFields(rows) {
    const leaves = [];
    const rest = [];
    (rows || []).forEach(row => {
      if (row.is_medicine || normName(row.base_field) === 'medicine') return;
      if (isMedLeaf(row)) leaves.push(row);
      else rest.push(row);
    });
    rest.push(buildMedicineField(leaves));
    return orderByCatalog(rest);
  }

  function resultCounts(fields) {
    const rows = fields || [];
    return {
      all: rows.length,
      match: rows.filter(f => f.is_match).length,
      incorrect: rows.filter(f => f.is_incorrect).length,
      missing: rows.filter(f => f.is_missing).length,
      hallucinated: rows.filter(f => f.is_hallucinated).length,
    };
  }

  function sectionMatchPct(fields) {
    const rows = fields || [];
    if (!rows.length) return null;
    const correct = rows.filter(f => f.is_match).length;
    return Math.round((100 * correct) / rows.length);
  }

  function pctTone(pct) {
    if (pct == null) return 'empty';
    if (pct >= 90) return 'high';
    if (pct >= 80) return 'good';
    return 'mid';
  }

  function medicationCount(fields) {
    const rows = fields || [];
    if (!rows.length) return 0;
    const indexes = rows.map(f => f.index);
    if (indexes.some(i => i != null && i !== '')) {
      const set = {};
      indexes.forEach(i => { set[i == null || i === '' ? 0 : i] = 1; });
      return Object.keys(set).length;
    }
    const drugs = rows.filter(f => normName(f.label).indexOf('drug') !== -1);
    return drugs.length || 1;
  }

  function groupCountLabel(groupKey, fields) {
    const rows = fields || [];
    if (groupKey === 'medications') {
      let n = 0;
      rows.forEach(f => {
        if (f.medication_count) n += Number(f.medication_count) || 0;
        else if (f.is_medicine) n += 1;
      });
      if (!n) n = medicationCount(rows);
      return n + ' medication' + (n !== 1 ? 's' : '');
    }
    const n = rows.length;
    return n + ' field' + (n !== 1 ? 's' : '');
  }

  function fieldCountLabel(n) {
    return n + ' field' + (n !== 1 ? 's' : '');
  }

  function matchesStatus(field, status) {
    const key = text(status).toLowerCase() || FILTER_ALL;
    if (key === FILTER_ALL) return true;
    if (key === FILTER_MATCH) return !!field.is_match;
    if (key === FILTER_INCORRECT) return !!field.is_incorrect;
    if (key === FILTER_MISSING) return !!field.is_missing;
    if (key === FILTER_HALLUCINATED) return !!field.is_hallucinated;
    return true;
  }

  function matchesQuery(field, query) {
    const needle = text(query).toLowerCase();
    if (!needle) return true;
    const extra = [];
    (field.medicine_tables || []).forEach(table => {
      extra.push(table.title);
      (table.rows || []).forEach(r => extra.push(r.label, r.ground_truth, r.generated));
    });
    const hay = [field.label, field.ground_truth, field.generated,
      field.raw_ground_truth, field.raw_generated, field.section, field.group]
      .concat(extra)
      .join(' ').toLowerCase();
    return hay.indexOf(needle) !== -1;
  }

  function orderedFieldIds(fields) {
    const rows = fields || [];
    const ids = [];
    SECTION_KEYS.forEach(key => {
      rows.forEach(f => { if (f.section === key) ids.push(f.id); });
    });
    rows.forEach(f => {
      if (SECTION_KEYS.indexOf(f.section) === -1) ids.push(f.id);
    });
    return ids;
  }

  function adjacentFieldId(fields, currentId, step) {
    const ids = orderedFieldIds(fields);
    const idx = ids.indexOf(currentId);
    if (idx < 0) return null;
    const next = idx + step;
    if (next < 0 || next >= ids.length) return null;
    return ids[next];
  }

  function filterFields(fields, opts) {
    const status = (opts && opts.status) || FILTER_ALL;
    const query = (opts && opts.query) || '';
    return (fields || []).filter(f => matchesStatus(f, status) && matchesQuery(f, query));
  }

  function buildPlanGroups(fields) {
    const rows = (fields || []).filter(f => f.section === 'plan');
    const buckets = {};
    PLAN_GROUP_ORDER.forEach(k => { buckets[k] = []; });
    rows.forEach(field => {
      if (field.group && buckets[field.group]) buckets[field.group].push(field);
      else buckets.other.push(field);
    });
    const groups = [];
    PLAN_GROUP_ORDER.forEach(key => {
      const items = buckets[key];
      if (!items.length && !ALWAYS_PLAN_GROUPS[key]) return;
      groups.push({
        id: 'plan.' + key,
        key: key,
        label: PLAN_GROUP_LABELS[key],
        count_label: groupCountLabel(key, items),
        field_ids: items.map(f => f.id),
        fields: items,
      });
    });
    return groups;
  }

  function buildObjectiveGroups(fields) {
    const rows = (fields || []).filter(f => f.section === 'objective');
    const buckets = {};
    OBJECTIVE_GROUP_ORDER.forEach(k => { buckets[k] = []; });
    rows.forEach(field => {
      if (field.group && buckets[field.group]) buckets[field.group].push(field);
      else buckets.other.push(field);
    });
    const groups = [];
    OBJECTIVE_GROUP_ORDER.forEach(key => {
      const items = buckets[key];
      if (!items.length && !ALWAYS_OBJECTIVE_GROUPS[key]) return;
      groups.push({
        id: 'objective.' + key,
        key: key,
        label: OBJECTIVE_GROUP_LABELS[key],
        count_label: groupCountLabel(key, items),
        field_ids: items.map(f => f.id),
        fields: items,
      });
    });
    return groups;
  }

  function buildSections(fields) {
    return SECTION_KEYS.map(key => {
      const owned = (fields || []).filter(f => f.section === key);
      const pct = sectionMatchPct(owned);
      return {
        key: key,
        label: SECTION_LABELS[key],
        field_count: owned.length,
        count_label: fieldCountLabel(owned.length),
        match_pct: pct,
        pct_tone: pctTone(pct),
        pct_label: pct == null ? '' : pct + '%',
        field_ids: owned.map(f => f.id),
        fields: owned,
        groups: key === 'plan' ? buildPlanGroups(owned)
          : key === 'objective' ? buildObjectiveGroups(owned)
          : [],
      };
    });
  }

  function navModel(result, opts) {
    const o = opts || {};
    const allFields = o.facts ? fieldsFromFacts(o.facts) : fieldsFromResult(result);
    const counts = resultCounts(allFields);
    const status = o.status || FILTER_ALL;
    const query = o.query || '';
    const visible = filterFields(allFields, { status: status, query: query });
    const selected = (o.selectedIds || []).filter(id => allFields.some(f => f.id === id));
    return {
      fields: allFields,
      visible: visible,
      sections: buildSections(visible),
      counts: counts,
      status: status,
      query: query,
      selected_ids: selected,
      selected_count: selected.length,
      total_count: counts.all,
      nothing_selected: selected.length === 0,
      empty_heading: EMPTY_HEADING,
      empty_body: EMPTY_BODY,
      search_placeholder: SEARCH_PLACEHOLDER,
    };
  }

  const SEARCH_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M15.5 14h-.8l-.3-.3A6.5 6.5 0 1 0 14 15.5l.3.3v.8l5 5 1.5-1.5-5-5zm-6 0a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9z"/></svg>';

  function chevronSvg(dir) {
    const d = dir === 'up' ? 'M6 15l6-6 6 6'
      : dir === 'down' ? 'M6 9l6 6 6-6'
      : 'M9 6l6 6-6 6';
    return '<svg class="soap-nav-chevron" viewBox="0 0 24 24" aria-hidden="true">'
      + '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="' + d + '"/>'
      + '</svg>';
  }

  function sectionIcon(key) {
    if (key === 'subjective') {
      return '<span class="soap-nav-icon soap-nav-icon-subjective" aria-hidden="true">'
        + '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4zm0 2c-3.3 0-10 1.7-10 5v1h20v-1c0-3.3-6.7-5-10-5z"/></svg>'
        + '</span>';
    }
    if (key === 'objective') {
      return '<span class="soap-nav-icon soap-nav-icon-objective" aria-hidden="true">'
        + '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M7 3h10a2 2 0 0 1 2 2v15l-7-3-7 3V5a2 2 0 0 1 2-2zm2 4v2h6V7H9zm0 4v2h6v-2H9z"/></svg>'
        + '</span>';
    }
    if (key === 'assessment') {
      return '<span class="soap-nav-icon soap-nav-icon-assessment" aria-hidden="true">'
        + '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M6 2h9l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm8 1.5V8h4.5zM8 12h8v2H8zm0 4h8v2H8z"/></svg>'
        + '</span>';
    }
    return '<span class="soap-nav-icon soap-nav-icon-plan" aria-hidden="true">'
      + '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M7 3h10a2 2 0 0 1 2 2v16H5V5a2 2 0 0 1 2-2zm1 5v2h8V8H8zm0 4v2h8v-2H8zm0 4v2h5v-2H8z"/></svg>'
      + '</span>';
  }

  function emptyGraphic() {
    return (
      '<div class="soap-nav-empty-art" aria-hidden="true">'
      + '<span class="soap-nav-empty-ring">'
      + '<svg viewBox="0 0 64 64">'
      + '<rect x="16" y="12" width="28" height="36" rx="3" fill="none" stroke="currentColor" stroke-width="2"/>'
      + '<path d="M22 22h16M22 28h16M22 34h10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
      + '<circle cx="40" cy="40" r="10" fill="#EEF4FF" stroke="currentColor" stroke-width="2"/>'
      + '<path d="M47 47l7 7" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>'
      + '</svg>'
      + '</span></div>'
    );
  }

  function emptyPane() {
    return (
      '<div class="soap-nav-empty" data-soap-nav-empty>'
      + emptyGraphic()
      + '<h3 class="soap-nav-empty-title">' + esc(EMPTY_HEADING) + '</h3>'
      + '<p class="soap-nav-empty-body">' + esc(EMPTY_BODY) + '</p>'
      + '</div>'
    );
  }

  function tableStatusLabel(result) {
    const key = text(result).toLowerCase();
    if (key === 'correct') return 'Match';
    if (key === 'incorrect') return 'Modified';
    if (key === 'hallucination') return 'Hallucinated';
    return result || '';
  }

  function resultBadge(result, opts) {
    const key = text(result).toLowerCase();
    let cls = 'soap-nav-badge-incorrect';
    let label = result || '';
    if (key === 'correct') { cls = 'soap-nav-badge-match'; label = 'Match'; }
    else if (key === 'missing') { cls = 'soap-nav-badge-missing'; }
    else if (key === 'hallucination') { cls = 'soap-nav-badge-hallucinated'; label = 'Hallucinated'; }
    else if (key === 'incorrect') {
      if (opts && opts.modified) {
        cls = 'soap-nav-badge-modified';
        label = 'Modified';
      } else {
        cls = 'soap-nav-badge-incorrect';
        label = 'Incorrect';
      }
    }
    return '<span class="soap-nav-result-badge ' + cls + '">' + esc(label) + '</span>';
  }

  function groupTableRows(fields) {
    return (fields || []).map(field => {
      const result = text(field.result);
      return {
        id: field.id,
        field: field.label || field.field || 'Unknown',
        ground_truth: field.gt_empty ? '—' : (field.ground_truth || '—'),
        generated: field.gen_empty ? '—' : (field.generated || '—'),
        result: result,
        status: tableStatusLabel(result),
        is_mismatch: result !== 'Correct' && result !== 'NA',
      };
    });
  }

  function findGroup(state, groupId) {
    const sections = (state && state.sections) || [];
    for (let i = 0; i < sections.length; i++) {
      const group = (sections[i].groups || []).find(g => g.id === groupId);
      if (group) return group;
    }
    return null;
  }

  function isTableGroup(group) {
    return !!(group && TABLE_GROUP_KEYS[group.key]);
  }

  function highlightHtml(value, phrase) {
    const raw = text(value);
    if (!raw || raw === '—') return esc(raw || '—');
    if (!phrase) return esc(raw);
    const idx = raw.toLowerCase().indexOf(text(phrase).toLowerCase());
    if (idx < 0) return esc(raw);
    return esc(raw.slice(0, idx))
      + '<mark class="soap-nav-hl">' + esc(raw.slice(idx, idx + phrase.length)) + '</mark>'
      + esc(raw.slice(idx + phrase.length));
  }

  function compareCell(label, value, empty, phrase, side) {
    const cls = empty ? ' is-empty' : '';
    return (
      '<div class="soap-nav-compare-col' + cls + '" data-soap-cell="' + side + '">'
      + '<div class="soap-nav-compare-label">' + esc(label) + '</div>'
      + '<div class="soap-nav-compare-value">' + highlightHtml(empty ? '—' : value, empty ? '' : phrase) + '</div>'
      + '</div>'
    );
  }

  function medicineTableHtml(field, collapsedMeds) {
    const tables = field.medicine_tables || [];
    if (!tables.length) return '';
    const closed = collapsedMeds || {};
    return tables.map((table, idx) => {
      const key = String(idx);
      const collapsed = !!closed[key];
      const title = table.title || 'Medicine';
      const rows = (table.rows || []).map(row => {
        const result = row.result || 'NA';
        const gt = row.gt_empty ? '—' : (row.ground_truth || '—');
        const gen = row.gen_empty ? '—' : (row.generated || '—');
        return '<tr data-soap-result="' + esc(result) + '">'
          + '<td class="med-field">' + esc(row.label) + '</td>'
          + '<td data-soap-cell="gt">' + esc(gt) + '</td>'
          + '<td data-soap-cell="gen">' + esc(gen) + '</td>'
          + '<td class="soap-result-cell">'
          + (result === 'NA' || !result ? '—' : resultBadge(result))
          + '</td>'
          + '</tr>';
      }).join('');
      const badge = table.result && table.result !== 'NA' ? resultBadge(table.result) : '';
      return (
        '<div class="soap-nav-med-block' + (collapsed ? ' is-collapsed' : '')
        + '" data-soap-nav-med="' + key + '">'
        + '<div class="soap-nav-med-head">'
        + '<button type="button" class="soap-nav-med-toggle" data-soap-nav-med-toggle="'
        + key + '" aria-expanded="' + (collapsed ? 'false' : 'true')
        + '" aria-controls="soap-nav-med-body-' + key + '">'
        + '<span class="soap-nav-med-title">' + esc(title) + '</span>'
        + badge
        + chevronSvg(collapsed ? 'right' : 'down')
        + '</button>'
        + '<button type="button" class="soap-nav-close soap-nav-med-close" data-soap-nav-med-close="'
        + key + '" aria-label="Collapse ' + esc(title) + '">×</button>'
        + '</div>'
        + '<div class="soap-nav-med-body" id="soap-nav-med-body-' + key
        + '" data-soap-nav-med-body="' + key + '"'
        + (collapsed ? ' hidden' : '') + '>'
        + '<div class="detail-table-scroll soap-nav-med-scroll">'
        + '<table class="soap-compare-table soap-nav-med-table">'
        + '<thead><tr><th>Field</th><th>Ground Truth</th><th>AI Output</th><th>Result</th></tr></thead>'
        + '<tbody>' + rows + '</tbody></table></div></div></div>'
      );
    }).join('');
  }

  function compareBody(field, state) {
    if (field.is_medicine) {
      return medicineTableHtml(field, (state && state.collapsedMeds) || {});
    }
    return (
      '<div class="soap-nav-compare-grid">'
      + compareCell('Ground Truth', field.ground_truth, field.gt_empty, field.gt_highlight, 'gt')
      + compareCell('AI Output', field.generated, field.gen_empty, field.gen_highlight, 'gen')
      + '</div>'
      + '<p class="soap-nav-difference" data-soap-nav-difference>' + esc(field.difference) + '</p>'
    );
  }

  function groupTableHtml(group) {
    const rows = groupTableRows(group && group.fields);
    const body = rows.map(row => {
      const mismatch = row.is_mismatch ? ' is-mismatch' : '';
      const badge = !row.result || row.result === 'NA'
        ? '—'
        : resultBadge(row.result, { modified: true });
      return '<tr class="' + mismatch.trim() + '" data-soap-nav-pick="' + esc(row.id || '')
        + '" data-soap-result="' + esc(row.result) + '">'
        + '<td class="soap-nav-group-field">' + esc(row.field) + '</td>'
        + '<td data-soap-cell="gt">' + esc(row.ground_truth) + '</td>'
        + '<td data-soap-cell="gen">' + esc(row.generated) + '</td>'
        + '<td class="soap-result-cell">' + badge + '</td>'
        + '</tr>';
    }).join('');
    return (
      '<div class="detail-table-scroll soap-nav-group-scroll">'
      + '<table class="soap-compare-table soap-nav-group-table">'
      + '<thead><tr><th>Field</th><th>Ground Truth</th><th>AI Output</th><th>Status</th></tr></thead>'
      + '<tbody>' + (body || '<tr><td colspan="4">No SOAP values for this group.</td></tr>')
      + '</tbody></table></div>'
    );
  }

  function groupPane(group, state) {
    return (
      '<div class="soap-nav-selected" data-soap-nav-selected data-soap-nav-panel tabindex="0">'
      + '<article class="soap-nav-compare-card" data-soap-nav-group-pane="' + esc(group.id) + '">'
      + '<header class="soap-nav-compare-head">'
      + '<h4>' + esc(group.label) + '</h4>'
      + '<div class="soap-nav-compare-actions">'
      + '<button type="button" class="soap-nav-close" data-soap-nav-close aria-label="Close group comparison">×</button>'
      + '</div></header>'
      + '<div class="soap-nav-compare-scroll">'
      + groupTableHtml(group)
      + '</div></article></div>'
    );
  }

  function selectedPane(state) {
    if (state.selectedGroupId) {
      const group = findGroup(state, state.selectedGroupId);
      if (group && isTableGroup(group)) return groupPane(group, state);
    }
    if (!state.selected_ids.length) return emptyPane();
    const byId = {};
    state.fields.forEach(f => { byId[f.id] = f; });
    const currentId = state.selected_ids[0];
    const field = byId[currentId];
    if (!field) return emptyPane();
    const prevId = adjacentFieldId(state.fields, currentId, -1);
    const nextId = adjacentFieldId(state.fields, currentId, 1);
    const conf = field.confidence == null || field.confidence === ''
      ? '—'
      : String(field.confidence);
    const sig = field.criticality || '—';
    const badge = field.is_na ? '' : resultBadge(field.result);
    const extraDiff = field.is_medicine
      ? '<p class="soap-nav-difference" data-soap-nav-difference>' + esc(field.difference) + '</p>'
      : '';
    return (
      '<div class="soap-nav-selected" data-soap-nav-selected data-soap-nav-panel tabindex="0">'
      + '<article class="soap-nav-compare-card" data-soap-nav-field="' + esc(field.id) + '">'
      + '<header class="soap-nav-compare-head">'
      + '<h4>' + esc(field.label) + '</h4>'
      + '<div class="soap-nav-compare-actions">'
      + badge
      + '<button type="button" class="soap-nav-close" data-soap-nav-close aria-label="Close field comparison">×</button>'
      + '</div></header>'
      + '<div class="soap-nav-compare-scroll">'
      + compareBody(field, state)
      + extraDiff
      + '<dl class="soap-nav-meta">'
      + '<div><dt>Error Type</dt><dd data-soap-meta="error-type">' + esc(field.error_type) + '</dd></div>'
      + '<div><dt>Sub Type</dt><dd data-soap-meta="subtype">' + esc(field.subtype) + '</dd></div>'
      + '<div><dt>Clinical Significance</dt><dd data-soap-meta="significance">'
      + '<span class="soap-nav-sig soap-nav-sig-' + esc((field.criticality || 'normal').toLowerCase()) + '">'
      + esc(sig) + '</span></dd></div>'
      + '<div><dt>Confidence</dt><dd data-soap-meta="confidence">' + esc(conf) + '</dd></div>'
      + '</dl></div>'
      + '<div class="soap-nav-seq">'
      + '<button type="button" class="soap-nav-ghost" data-soap-nav-step="-1"'
      + (prevId ? '' : ' disabled') + '>← Previous field</button>'
      + '<span class="soap-nav-seq-hint">Next field ↓</span>'
      + '<button type="button" class="soap-nav-ghost" data-soap-nav-step="1"'
      + (nextId ? '' : ' disabled') + '>Next field →</button>'
      + '</div></article></div>'
    );
  }

  function filterPills(state) {
    return FILTER_ORDER.map(key => {
      const active = state.status === key ? ' is-active' : '';
      const n = state.counts[key] || 0;
      return '<button type="button" class="soap-nav-pill' + active + '" data-soap-nav-filter="'
        + key + '" aria-pressed="' + (state.status === key ? 'true' : 'false') + '">'
        + esc(FILTER_LABELS[key]) + ' (' + n + ')</button>';
    }).join('');
  }

  function toolbar(state) {
    return (
      '<div class="soap-nav-toolbar">'
      + '<div class="soap-nav-pills" role="tablist" aria-label="SOAP result filters">'
      + filterPills(state)
      + '</div>'
      + '<label class="soap-nav-search">'
      + '<span class="soap-nav-search-icon">' + SEARCH_SVG + '</span>'
      + '<input type="search" data-soap-nav-query placeholder="' + esc(SEARCH_PLACEHOLDER)
      + '" value="' + esc(state.query) + '" autocomplete="off">'
      + '</label>'
      + '<div class="soap-nav-expand-btns">'
      + '<button type="button" class="soap-nav-ghost" data-soap-nav-expand="all">Expand All</button>'
      + '<button type="button" class="soap-nav-ghost" data-soap-nav-expand="none">Collapse All</button>'
      + '</div></div>'
    );
  }

  function fieldRow(field, selected) {
    const on = selected[field.id] ? ' is-selected' : '';
    return (
      '<button type="button" class="soap-nav-leaf' + on + '" data-soap-nav-pick="'
      + esc(field.id) + '" data-soap-result="' + esc(field.result) + '">'
      + '<span class="soap-nav-leaf-label">' + esc(field.label) + '</span>'
      + chevronSvg('right')
      + '</button>'
    );
  }

  function groupRow(group, selected, open, state) {
    const any = group.field_ids.some(id => selected[id]);
    const groupOn = !!(state && state.selectedGroupId === group.id);
    const on = (any || groupOn) ? ' is-selected' : '';
    const body = open
      ? '<div class="soap-nav-subleaves">'
        + group.fields.map(f => fieldRow(f, selected)).join('')
        + '</div>'
      : '';
    return (
      '<div class="soap-nav-group">'
      + '<button type="button" class="soap-nav-leaf' + on + '" data-soap-nav-group="'
      + esc(group.id) + '" aria-expanded="' + (open ? 'true' : 'false') + '">'
      + '<span class="soap-nav-leaf-label">' + esc(group.label) + '</span>'
      + '<span class="soap-nav-leaf-count">' + esc(group.count_label) + '</span>'
      + chevronSvg(open ? 'down' : 'right')
      + '</button>'
      + body
      + '</div>'
    );
  }

  function sectionBlock(section, state) {
    const open = !!state.expanded[section.key];
    const selected = {};
    state.selected_ids.forEach(id => { selected[id] = true; });
    let body = '';
    if (open) {
      if (section.groups && section.groups.length) {
        body = '<div class="soap-nav-leaves">'
          + section.groups.map(g => {
            if (g.key === 'medications' && g.fields.length === 1 && g.fields[0].is_medicine) {
              return fieldRow(g.fields[0], selected);
            }
            return groupRow(g, selected, !!(state.expandedGroups && state.expandedGroups[g.id]), state);
          }).join('')
          + '</div>';
      } else {
        body = '<div class="soap-nav-leaves">'
          + section.fields.map(f => fieldRow(f, selected)).join('')
          + '</div>';
      }
    }
    const pct = section.pct_label
      ? '<span class="soap-nav-pct soap-nav-pct-' + esc(section.pct_tone) + '">'
        + esc(section.pct_label) + '</span>'
      : '';
    return (
      '<div class="soap-nav-section' + (open ? ' is-open' : '') + '" data-soap-nav-section="'
      + esc(section.key) + '">'
      + '<button type="button" class="soap-nav-section-btn" data-soap-nav-toggle="'
      + esc(section.key) + '" aria-expanded="' + (open ? 'true' : 'false') + '">'
      + sectionIcon(section.key)
      + '<span class="soap-nav-section-copy">'
      + '<span class="soap-nav-section-name">' + esc(section.label) + '</span>'
      + '</span>'
      + pct
      + '<span class="soap-nav-section-count">' + esc(section.count_label) + '</span>'
      + chevronSvg(open ? 'up' : 'down')
      + '</button>'
      + body
      + '</div>'
    );
  }

  function sidebar(state) {
    const n = state.selected_count;
    const total = state.total_count;
    return (
      '<aside class="soap-nav-sidebar">'
      + '<div class="soap-nav-sidebar-head">'
      + '<h3>SOAP Sections</h3>'
      + '<p data-soap-nav-selected-count>' + n + ' of ' + total + ' fields selected</p>'
      + '</div>'
      + '<div class="soap-nav-section-list">'
      + state.sections.map(s => sectionBlock(s, state)).join('')
      + '</div></aside>'
    );
  }

  function frame(state) {
    return (
      '<div class="soap-nav" data-soap-nav-root>'
      + toolbar(state)
      + '<div class="soap-nav-body">'
      + sidebar(state)
      + '<div class="soap-nav-main">' + selectedPane(state) + '</div>'
      + '</div></div>'
    );
  }

  function idsForGroup(state, groupId) {
    for (let i = 0; i < (state.sections || []).length; i++) {
      const group = (state.sections[i].groups || []).find(g => g.id === groupId);
      if (group) return group.field_ids.slice();
    }
    return [];
  }

  function toggleIds(current, ids) {
    const set = {};
    current.forEach(id => { set[id] = true; });
    const allOn = ids.length && ids.every(id => set[id]);
    if (allOn) ids.forEach(id => { delete set[id]; });
    else ids.forEach(id => { set[id] = true; });
    return Object.keys(set);
  }

  function paint(host, state) {
    const visible = filterFields(state.fields, { status: state.status, query: state.query });
    state.sections = buildSections(visible);
    if (state.selectedGroupId) {
      const group = findGroup(state, state.selectedGroupId);
      state.selected_count = group ? (group.field_ids || []).length : 0;
    } else {
      state.selected_count = state.selected_ids.length;
    }
    state.total_count = state.fields.length;
    const queryEl = host.querySelector('[data-soap-nav-query]');
    const caret = queryEl ? queryEl.selectionStart : null;
    const focused = document.activeElement === queryEl;
    host.innerHTML = frame(state);
    if (focused) {
      const next = host.querySelector('[data-soap-nav-query]');
      if (next) {
        next.focus();
        try { next.setSelectionRange(caret, caret); } catch (e) { /* ignore */ }
      }
    }
  }

  function bind(host) {
    host.addEventListener('click', ev => {
      const state = host._soapNavState;
      if (!state) return;
      const pill = ev.target && ev.target.closest('[data-soap-nav-filter]');
      if (pill && host.contains(pill)) {
        state.status = pill.getAttribute('data-soap-nav-filter') || FILTER_ALL;
        paint(host, state);
        return;
      }
      const expand = ev.target && ev.target.closest('[data-soap-nav-expand]');
      if (expand && host.contains(expand)) {
        const all = expand.getAttribute('data-soap-nav-expand') === 'all';
        SECTION_KEYS.forEach(k => { state.expanded[k] = all; });
        paint(host, state);
        return;
      }
      const toggle = ev.target && ev.target.closest('[data-soap-nav-toggle]');
      if (toggle && host.contains(toggle)) {
        const key = toggle.getAttribute('data-soap-nav-toggle');
        state.expanded[key] = !state.expanded[key];
        paint(host, state);
        return;
      }
      const groupBtn = ev.target && ev.target.closest('[data-soap-nav-group]');
      if (groupBtn && host.contains(groupBtn) && !ev.target.closest('[data-soap-nav-pick]')) {
        const gid = groupBtn.getAttribute('data-soap-nav-group');
        const group = findGroup(state, gid);
        state.expandedGroups = state.expandedGroups || {};
        if (isTableGroup(group)) {
          state.selectedGroupId = gid;
          state.selected_ids = [];
          state.expanded.objective = true;
          state.expandedGroups[gid] = true;
          paint(host, state);
          const panel = host.querySelector('[data-soap-nav-panel]');
          if (panel && panel.focus) panel.focus();
          return;
        }
        state.expandedGroups[gid] = !state.expandedGroups[gid];
        paint(host, state);
        return;
      }
      const medClose = ev.target && ev.target.closest('[data-soap-nav-med-close]');
      if (medClose && host.contains(medClose)) {
        const key = medClose.getAttribute('data-soap-nav-med-close');
        state.collapsedMeds = state.collapsedMeds || {};
        state.collapsedMeds[key] = true;
        paint(host, state);
        return;
      }
      const medToggle = ev.target && ev.target.closest('[data-soap-nav-med-toggle]');
      if (medToggle && host.contains(medToggle)) {
        const key = medToggle.getAttribute('data-soap-nav-med-toggle');
        state.collapsedMeds = state.collapsedMeds || {};
        state.collapsedMeds[key] = !state.collapsedMeds[key];
        paint(host, state);
        return;
      }
      const pick = ev.target && ev.target.closest('[data-soap-nav-pick]');
      if (pick && host.contains(pick)) {
        const id = pick.getAttribute('data-soap-nav-pick');
        state.selected_ids = id ? [id] : [];
        state.selectedGroupId = '';
        paint(host, state);
        const panel = host.querySelector('[data-soap-nav-panel]');
        if (panel && panel.focus) panel.focus();
        return;
      }
      const close = ev.target && ev.target.closest('[data-soap-nav-close]');
      if (close && host.contains(close)) {
        state.selected_ids = [];
        state.selectedGroupId = '';
        paint(host, state);
        return;
      }
      const stepBtn = ev.target && ev.target.closest('[data-soap-nav-step]');
      if (stepBtn && host.contains(stepBtn) && !stepBtn.disabled) {
        const step = Number(stepBtn.getAttribute('data-soap-nav-step') || 0);
        const current = state.selected_ids[0];
        const next = adjacentFieldId(state.fields, current, step);
        if (next) {
          const field = state.fields.find(f => f.id === next);
          if (field) state.expanded[field.section] = true;
          if (field && field.group) {
            state.expandedGroups = state.expandedGroups || {};
            state.expandedGroups[field.section + '.' + field.group] = true;
          }
          state.selected_ids = [next];
          paint(host, state);
          const panel = host.querySelector('[data-soap-nav-panel]');
          if (panel && panel.focus) panel.focus();
        }
      }
    });
    host.addEventListener('keydown', ev => {
      const state = host._soapNavState;
      if (!state) return;
      const panel = host.querySelector('[data-soap-nav-panel]');
      if (!panel || document.activeElement !== panel) return;
      if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
      ev.preventDefault();
      const step = ev.key === 'ArrowDown' ? 1 : -1;
      const next = adjacentFieldId(state.fields, state.selected_ids[0], step);
      if (!next) return;
      const field = state.fields.find(f => f.id === next);
      if (field) state.expanded[field.section] = true;
      state.selected_ids = [next];
      paint(host, state);
      const again = host.querySelector('[data-soap-nav-panel]');
      if (again && again.focus) again.focus();
    });
    host.addEventListener('input', ev => {
      const state = host._soapNavState;
      if (!state) return;
      const el = ev.target && ev.target.closest('[data-soap-nav-query]');
      if (!el || !host.contains(el)) return;
      state.query = el.value;
      paint(host, state);
    });
  }

  function mount(host, result) {
    if (!host) return null;
    const model = navModel(result);
    const state = {
      fields: model.fields,
      sections: model.sections,
      counts: model.counts,
      status: FILTER_ALL,
      query: '',
      selected_ids: [],
      selectedGroupId: '',
      selected_count: 0,
      total_count: model.total_count,
      expandedGroups: {},
      collapsedMeds: {},
      expanded: {
        subjective: false,
        objective: false,
        assessment: false,
        plan: false,
      },
    };
    host._soapNavState = state;
    paint(host, state);
    if (!host._soapNavBound) {
      bind(host);
      host._soapNavBound = true;
    }
    return state;
  }

  const api = {
    FILTER_ALL,
    FILTER_MATCH,
    FILTER_INCORRECT,
    FILTER_MISSING,
    FILTER_HALLUCINATED,
    EMPTY_HEADING,
    EMPTY_BODY,
    SEARCH_PLACEHOLDER,
    DEFAULT_EXPANDED,
    fieldsFromFacts,
    fieldsFromResult,
    mergeCatalogFields,
    collapseMedicineFields,
    resultCounts,
    filterFields,
    buildSections,
    navModel,
    orderedFieldIds,
    adjacentFieldId,
    encodedCells,
    displayFilterResult,
    tableStatusLabel,
    groupTableRows,
    groupTableHtml,
    findGroup,
    mount,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumSoapSummaryNav = api;
})(typeof window !== 'undefined' ? window : globalThis);
