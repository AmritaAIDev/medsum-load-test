/**
 * Run-scoped audio catalog + selection.
 * Exclude removes a file from this run only — the catalog (Drive ref or File) stays.
 *
 * TAB_SWITCH_KEEPS_SELECTION is false: Upload Manually ↔ Google Drive drops
 * the other source's selected files (MOM). Catalog rows and their manual_gt
 * stay; switching back does not re-select them.
 */
(function (root) {
  const TAB_SWITCH_KEEPS_SELECTION = false;

  const AUDIO_EXTENSIONS = { mp3: 1, wav: 1, m4a: 1, ogg: 1, aac: 1, webm: 1 };
  const JSON_EXTENSIONS = { json: 1 };
  const EXCEL_EXTENSIONS = { xls: 1, xlsx: 1, csv: 1 };
  const PDF_EXTENSIONS = { pdf: 1 };
  const TRANSCRIPT_EXTENSIONS = { txt: 1, doc: 1, docx: 1 };
  const GT_EXTENSIONS = Object.assign(
    {},
    JSON_EXTENSIONS,
    EXCEL_EXTENSIONS,
    PDF_EXTENSIONS,
    TRANSCRIPT_EXTENSIONS
  );

  const STATUS_COMPLETE = 'complete';
  const STATUS_MISSING_LANGUAGE = 'missing_language';
  const STATUS_MISSING_SOAP = 'missing_soap';
  const STATUS_MISSING_TRANSLATION = 'missing_translation';
  const STATUS_MISSING_TRANSCRIPT = 'missing_transcript';
  const STATUS_MISSING_JSON = 'missing_json';
  const STATUS_MISSING_GT_ALL = 'missing_gt_all';

  const STATUS_LABELS = {
    complete: 'Complete',
    missing_language: 'Missing Language',
    missing_soap: 'Missing GT (SOAP)',
    missing_translation: 'Missing Translation',
    missing_transcript: 'Missing Transcript',
    missing_json: 'Missing JSON',
    missing_gt_all: 'Missing GT (All)',
  };

  const STATUS_LEGEND = [
    [STATUS_COMPLETE, 'Complete (All files)'],
    [STATUS_MISSING_LANGUAGE, 'Missing Language'],
    [STATUS_MISSING_SOAP, 'Missing GT (SOAP)'],
    [STATUS_MISSING_TRANSLATION, 'Missing Translation'],
    [STATUS_MISSING_TRANSCRIPT, 'Missing Transcript'],
    [STATUS_MISSING_JSON, 'Missing JSON'],
    [STATUS_MISSING_GT_ALL, 'Missing GT (All)'],
  ];

  const MISSING_LANGUAGE_RUN_MESSAGE =
    'Set a language for each uploaded audio file before running tests.';

  // Same names Drive extract_language feeds into medsum_api.LANGUAGE_CODE_MAP.
  const LANGUAGE_CODE_MAP = {
    hindi: 'hi',
    english: 'en',
    malayalam: 'ml',
    tamil: 'ta',
    telugu: 'te',
    kannada: 'kn',
    bengali: 'bn',
    marathi: 'mr',
    gujarati: 'gu',
    punjabi: 'pa',
    odia: 'or',
    urdu: 'ur',
  };

  const SELECTED_FILES_HEADERS = [
    '#', 'AUDIO FILE', 'DURATION', 'GROUND TRUTH', 'STATUS', 'ACTION',
  ];

  // Same SOAP schema the detail view (Prompt 4) renders — do not invent a second form.
  const SOAP_CONSULT_TEMPLATE = {
    subjective: {
      chief_complaint: '',
      history_of_present_illness: '',
      past_medical_history: '',
      medications: '',
      allergies: '',
      social_history: '',
      family_history: '',
      blood_group: '',
    },
    objective: {
      vitals: {
        blood_pressure: '',
        heart_rate: '',
        respiratory_rate: '',
        temperature: '',
        spo2: '',
      },
      physical_exam: {
        heart: '',
        height: '',
        weight: '',
      },
    },
    assessment: {
      diagnosis: '',
      type: '',
      status: '',
      reasoning: '',
    },
    plan: {
      medications: '',
      activity: '',
      investigations: '',
      education: '',
      follow_up: '',
    },
    summary: '',
  };

  // Flat fact-list JSON for the SOAP manual editor. Sections match the
  // SOAP_CONSULT_TEMPLATE roots the form already edits (summary → Plan).
  const SOAP_JSON_SECTIONS = ['Subjective', 'Objective', 'Assessment', 'Plan'];
  const SOAP_JSON_SECTION_KEYS = {
    subjective: 'Subjective',
    objective: 'Objective',
    assessment: 'Assessment',
    plan: 'Plan',
  };
  const SOAP_DEFAULT_CRITICALITY = 'Normal';
  const SOAP_NESTED_ROOTS = {
    subjective: 'object',
    objective: 'object',
    assessment: 'object',
    plan: 'object',
    summary: 'string',
  };

  const FILE_FLAG_KEYS = [
    'has_transcript_ground_truth',
    'has_transcript',
    'has_translation_ground_truth',
    'has_soap_ground_truth',
    'has_summary_ground_truth',
    'has_json_ground_truth',
    'has_json_applicable',
  ];

  function fileKey(item) {
    const language = String((item && (item.language || item.folder_label)) || '')
      .trim()
      .toLowerCase();
    const audio = String(
      (item && (item.audio || item.audio_filename || item.filename)) || ''
    )
      .trim()
      .toLowerCase();
    return language + '\0' + audio;
  }

  function catalogId(item) {
    // DOM-safe id: no NUL and no raw quotes, so data-exclude-audio round-trips.
    const source = String((item && item.source) || 'drive');
    const language = String((item && (item.language || item.folder_label)) || '')
      .trim()
      .toLowerCase();
    const audio = String(
      (item && (item.audio || item.audio_filename || item.filename)) || ''
    )
      .trim()
      .toLowerCase();
    return [source, language, audio].map(encodeURIComponent).join('::');
  }

  function dropSelectedKey(selectedKeys, targetId) {
    const id = String(targetId || '');
    if (!id) return (selectedKeys || []).slice();
    return (selectedKeys || []).filter(key => key !== id);
  }

  function itemSource(item) {
    return String((item && item.source) || 'drive');
  }

  function supportedLanguageLabels() {
    return Object.keys(LANGUAGE_CODE_MAP).map(function (name) {
      return name.charAt(0).toUpperCase() + name.slice(1);
    });
  }

  function canonicalLanguageLabel(raw) {
    const key = String(raw || '').trim().toLowerCase();
    if (!key) return '';
    if (LANGUAGE_CODE_MAP[key]) {
      return key.charAt(0).toUpperCase() + key.slice(1);
    }
    const names = Object.keys(LANGUAGE_CODE_MAP);
    for (let i = 0; i < names.length; i++) {
      if (LANGUAGE_CODE_MAP[names[i]] === key) {
        return names[i].charAt(0).toUpperCase() + names[i].slice(1);
      }
    }
    return '';
  }

  function uploadNeedsLanguage(item) {
    if (itemSource(item) !== 'upload') return false;
    return !canonicalLanguageLabel((item && (item.language || item.folder_label)) || '');
  }

  function missingLanguageUploads(selected) {
    return (selected || []).filter(uploadNeedsLanguage);
  }

  function setUploadLanguage(catalog, selectedKeys, catalogIdStr, language) {
    const next = Array.isArray(catalog) ? catalog.slice() : [];
    const keys = Array.isArray(selectedKeys) ? selectedKeys.slice() : [];
    const target = String(catalogIdStr || '');
    if (!target) return { catalog: next, selectedKeys: keys };
    const idx = next.findIndex(row => catalogId(row) === target);
    if (idx < 0 || itemSource(next[idx]) !== 'upload') {
      return { catalog: next, selectedKeys: keys };
    }
    const prevId = catalogId(next[idx]);
    const label = canonicalLanguageLabel(language);
    next[idx] = Object.assign({}, next[idx], { language: label, folder_label: label });
    const newId = catalogId(next[idx]);
    return {
      catalog: next,
      selectedKeys: keys.map(key => key === prevId ? newId : key),
    };
  }

  function selectionAfterSourceSwitch(selectedKeys, catalog, nextSource, keepOther) {
    const keys = Array.isArray(selectedKeys) ? selectedKeys.slice() : [];
    const keep = keepOther == null ? TAB_SWITCH_KEEPS_SELECTION : !!keepOther;
    if (keep) return keys;
    const wanted = String(nextSource || '').trim() || 'upload';
    const byId = {};
    (Array.isArray(catalog) ? catalog : []).forEach(item => {
      byId[catalogId(item)] = item;
    });
    return keys.filter(key => {
      const item = byId[key];
      return item && itemSource(item) === wanted;
    });
  }

  function clearAllKeys(selectedKeys) {
    let remaining = (selectedKeys || []).slice();
    (selectedKeys || []).forEach(key => {
      remaining = dropSelectedKey(remaining, key);
    });
    return remaining;
  }

  function filterMultiAudioItems(items, query) {
    const q = String(query || '').trim().toLowerCase();
    const rows = Array.isArray(items) ? items.slice() : [];
    if (!q) return rows;
    return rows.filter(item => {
      const hay = [
        item && item.language,
        item && (item.audio || item.audio_filename),
      ].join(' ').toLowerCase();
      return hay.indexOf(q) !== -1;
    });
  }

  function ingestIntoCatalog(catalog, incoming) {
    const next = Array.isArray(catalog) ? catalog.slice() : [];
    const seen = {};
    next.forEach(item => {
      seen[catalogId(item)] = true;
    });
    (incoming || []).forEach(item => {
      const id = catalogId(item);
      if (seen[id]) {
        const idx = next.findIndex(row => catalogId(row) === id);
        if (idx >= 0) next[idx] = Object.assign({}, next[idx], item);
        return;
      }
      seen[id] = true;
      next.push(item);
    });
    return next;
  }

  function excludeFromSelection(selected, target) {
    const drop = fileKey(target);
    return (selected || []).filter(item => fileKey(item) !== drop);
  }

  function selectedForExecution(catalog, selected) {
    const allow = {};
    (selected || []).forEach(item => {
      allow[fileKey(item)] = true;
    });
    return (catalog || []).filter(item => allow[fileKey(item)]);
  }

  function drivePayload(selected) {
    return (selected || [])
      .filter(item => (item.source || 'drive') === 'drive')
      .map(item => ({
        language: item.language || '',
        audio: item.audio || item.audio_filename || '',
      }))
      .filter(item => item.audio);
  }

  function runPayload(selected) {
    return (selected || [])
      .map(item => {
        const source = item.source || 'drive';
        let language = item.language || item.folder_label || '';
        if (source === 'upload') {
          language = canonicalLanguageLabel(language) || language;
        }
        const row = {
          language: language,
          audio: item.audio || item.audio_filename || item.filename || '',
          source: source,
        };
        if (item.upload_id) row.upload_id = item.upload_id;
        if (hasManualGt(item)) {
          row.manual_gt = normalizeManualGt(item.manual_gt);
        }
        return row;
      })
      .filter(item => item.audio);
  }

  function resultsKeepFailures(results) {
    return Array.isArray(results) ? results.slice() : [];
  }

  function extensionOf(filename) {
    const name = String(filename || '').trim().toLowerCase();
    const dot = name.lastIndexOf('.');
    return dot >= 0 ? name.slice(dot + 1) : '';
  }

  function stripNumberPrefix(name) {
    return String(name || '').toLowerCase().replace(/^\d+_/, '');
  }

  function stripScriptSuffix(name) {
    let n = String(name || '');
    ['_script', '_transcript', '_ground_truth', '_gt'].forEach(suffix => {
      if (n.endsWith(suffix)) n = n.slice(0, -suffix.length);
    });
    return n;
  }

  function isSoapGt(filename) {
    const lower = String(filename || '').toLowerCase();
    if (
      lower.endsWith('_soap')
      || lower.endsWith('_soap.txt')
      || lower.endsWith('_soap.json')
    ) return true;
    const extList = ['.txt', '.json', '.docx'];
    for (let i = 0; i < extList.length; i++) {
      const ext = extList[i];
      if (lower.endsWith(ext) && lower.slice(0, -ext.length).endsWith('_soap')) return true;
    }
    return false;
  }

  function isTranslationGt(filename) {
    const name = String(filename || '').toLowerCase();
    return ['_translation', '_trans', '_english'].some(
      s => name.endsWith(s) || name.endsWith(s + '.txt')
    );
  }

  function getSoapBase(filename) {
    let name = String(filename || '').toLowerCase();
    ['.txt', '.json', '.docx'].forEach(ext => {
      if (name.endsWith(ext)) name = name.slice(0, -ext.length);
    });
    if (name.endsWith('_soap')) name = name.slice(0, -5);
    return stripNumberPrefix(name);
  }

  function getTranslationBase(filename) {
    let name = String(filename || '').toLowerCase();
    ['.txt', '.json', '.docx'].forEach(ext => {
      if (name.endsWith(ext)) name = name.slice(0, -ext.length);
    });
    ['_translation', '_trans', '_english'].forEach(suffix => {
      if (name.endsWith(suffix)) name = name.slice(0, -suffix.length);
    });
    return stripNumberPrefix(name);
  }

  function matchKey(filename) {
    let base = String(filename || '').replace(/\.\w+$/, '');
    base = stripNumberPrefix(base);
    base = stripScriptSuffix(base);
    const durationMatch = /^([a-z]+)(\d+)$/.exec(base);
    if (durationMatch) base = durationMatch[1] + '_' + durationMatch[2];
    return base;
  }

  function gtMatchKey(filename) {
    const name = String(filename || '');
    if (isSoapGt(name)) return getSoapBase(name);
    if (isTranslationGt(name)) return getTranslationBase(name);
    return matchKey(name);
  }

  function classifyUpload(filename) {
    const name = String(filename || '');
    const ext = extensionOf(name);
    if (AUDIO_EXTENSIONS[ext]) return 'audio';
    if (!ext || !GT_EXTENSIONS[ext]) return 'unknown';
    if (isSoapGt(name)) return 'soap';
    if (isTranslationGt(name)) return 'translation';
    if (JSON_EXTENSIONS[ext]) return 'json';
    if (EXCEL_EXTENSIONS[ext]) return 'excel';
    if (PDF_EXTENSIONS[ext]) return 'pdf';
    return 'transcript';
  }

  function isBundleGroundTruth(filename) {
    const kind = classifyUpload(filename);
    if (kind === 'audio' || kind === 'unknown') return false;
    if (isSoapGt(filename) || isTranslationGt(filename)) return false;
    if (kind === 'json' || kind === 'excel') return true;
    const base = String(filename || '').toLowerCase().replace(/\.[^.]+$/, '');
    return base.endsWith('_gt') || base.endsWith('_ground_truth');
  }

  function truthy(value) {
    return !!value && value !== '0' && value !== 'false' && value !== 'False';
  }

  function isEnglishCase(item) {
    const data = item || {};
    if (truthy(data.is_english)) return true;
    const lang = String(data.language || data.folder_label || '').trim().toLowerCase();
    if (lang === 'english' || lang === 'en') return true;
    const audio = String(data.audio || data.audio_filename || '').toLowerCase();
    return audio.indexOf('english') !== -1 || audio.indexOf('en_') === 0 || audio.indexOf('_en_') !== -1;
  }

  function flagsFromGtFile(filename) {
    const name = String(filename || '');
    const kind = classifyUpload(name);
    const ext = extensionOf(name);
    const bundle = isBundleGroundTruth(name);
    const hasJson = !!JSON_EXTENSIONS[ext];
    let hasSoap = isSoapGt(name) || bundle;
    let hasTranslation = isTranslationGt(name) || bundle;
    let hasTranscript = (
      kind === 'transcript' || kind === 'pdf' || kind === 'json' || kind === 'excel' || bundle
    );
    if (kind === 'soap' || kind === 'translation') hasTranscript = false;
    if (bundle) {
      hasTranscript = true;
      hasSoap = true;
      hasTranslation = true;
    }
    return {
      has_transcript_ground_truth: hasTranscript,
      has_transcript: hasTranscript,
      has_translation_ground_truth: hasTranslation,
      has_soap_ground_truth: hasSoap,
      has_summary_ground_truth: hasSoap,
      has_json_ground_truth: hasJson,
      has_json_applicable: hasJson,
    };
  }

  function orFlags(base, extra) {
    const out = Object.assign({}, base);
    Object.keys(extra || {}).forEach(key => {
      out[key] = !!(out[key] || extra[key]);
    });
    return out;
  }

  function primaryGtFilename(names) {
    const unique = [];
    const seen = {};
    (names || []).forEach(name => {
      const key = String(name || '').trim();
      if (!key || seen[key.toLowerCase()]) return;
      seen[key.toLowerCase()] = true;
      unique.push(key);
    });
    unique.sort((a, b) => {
      const rank = name => {
        const ext = extensionOf(name);
        const kind = classifyUpload(name);
        if (isBundleGroundTruth(name)) return 0;
        if (JSON_EXTENSIONS[ext]) return 1;
        if (EXCEL_EXTENSIONS[ext]) return 2;
        if (kind === 'transcript') return 3;
        if (PDF_EXTENSIONS[ext]) return 4;
        if (kind === 'soap') return 5;
        return 6;
      };
      return rank(a) - rank(b);
    });
    return unique[0] || '';
  }

  function incomingFileFlags(item) {
    const data = item || {};
    return {
      has_transcript_ground_truth: truthy(data.has_transcript_ground_truth)
        || truthy(data.has_transcript)
        || truthy(data.has_ground_truth),
      has_transcript: truthy(data.has_transcript)
        || truthy(data.has_transcript_ground_truth)
        || truthy(data.has_ground_truth),
      has_translation_ground_truth: truthy(data.has_translation_ground_truth),
      has_soap_ground_truth: truthy(data.has_soap_ground_truth)
        || truthy(data.has_summary_ground_truth),
      has_summary_ground_truth: truthy(data.has_summary_ground_truth)
        || truthy(data.has_soap_ground_truth),
      has_json_ground_truth: truthy(data.has_json_ground_truth),
      has_json_applicable: truthy(data.has_json_applicable)
        || truthy(data.has_json_ground_truth),
    };
  }

  function snapshotFileFlags(flags) {
    const out = {};
    FILE_FLAG_KEYS.forEach(key => {
      out[key] = !!(flags && flags[key]);
    });
    return out;
  }

  function soapHasContent(soap) {
    if (soap == null) return false;
    if (typeof soap === 'string') return !!soap.trim();
    if (Array.isArray(soap)) return soap.some(soapHasContent);
    if (typeof soap === 'object') {
      return Object.keys(soap).some(key => soapHasContent(soap[key]));
    }
    return !!soap;
  }

  function pruneSoap(soap) {
    if (soap == null) return null;
    if (typeof soap === 'string') {
      const text = soap.trim();
      return text || null;
    }
    if (Array.isArray(soap)) {
      const items = soap.map(pruneSoap).filter(item => item != null && item !== '' && !(typeof item === 'object' && !Object.keys(item).length));
      return items.length ? items : null;
    }
    if (typeof soap === 'object') {
      const out = {};
      Object.keys(soap).forEach(key => {
        const pruned = pruneSoap(soap[key]);
        if (pruned != null && pruned !== '' && !(typeof pruned === 'object' && !Array.isArray(pruned) && !Object.keys(pruned).length)) {
          out[key] = pruned;
        }
      });
      return Object.keys(out).length ? out : null;
    }
    return soap;
  }

  function normalizeManualGt(fields) {
    const data = fields || {};
    return {
      transcription: String(data.transcription || '').trim(),
      translation: String(data.translation || '').trim(),
      soap: pruneSoap(data.soap),
    };
  }

  function hasManualGt(item) {
    const mg = normalizeManualGt((item || {}).manual_gt);
    return !!(mg.transcription || mg.translation || soapHasContent(mg.soap));
  }

  function gtDisplayLabel(item) {
    if (hasManualGt(item)) return 'Manual';
    const name = String((item && item.ground_truth_filename) || '').trim();
    return name || '—';
  }

  function getSoapAtPath(obj, path) {
    return String(path || '').split('.').reduce((node, key) => (
      node && typeof node === 'object' ? node[key] : undefined
    ), obj);
  }

  function setSoapAtPath(obj, path, value) {
    const keys = String(path || '').split('.').filter(Boolean);
    if (!keys.length) return obj;
    let node = obj;
    for (let i = 0; i < keys.length - 1; i++) {
      const key = keys[i];
      if (!node[key] || typeof node[key] !== 'object' || Array.isArray(node[key])) {
        node[key] = {};
      }
      node = node[key];
    }
    node[keys[keys.length - 1]] = value;
    return obj;
  }

  function soapEditorFields(template) {
    const fields = [];
    function walk(node, path, section) {
      if (node && typeof node === 'object' && !Array.isArray(node)) {
        Object.keys(node).forEach(key => {
          walk(node[key], path ? path + '.' + key : key, section || key);
        });
        return;
      }
      fields.push({
        path: path,
        section: section,
        label: String(path.split('.').pop() || '').replace(/_/g, ' '),
      });
    }
    walk(template || SOAP_CONSULT_TEMPLATE, '', '');
    return fields;
  }

  function cloneSoap(soap) {
    if (soap == null) return soap;
    return JSON.parse(JSON.stringify(soap));
  }

  function normName(value) {
    return String(value || '').trim().toLowerCase().replace(/[\s_-]+/g, ' ');
  }

  function displayFieldName(label) {
    const text = String(label || '').replace(/_/g, ' ').trim();
    if (!text) return '';
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function snakeField(name) {
    return String(name || '').trim().replace(/[\s-]+/g, '_').toLowerCase();
  }

  function normalizeSoapSection(section) {
    const raw = String(section || '').trim();
    if (!raw) return null;
    const lower = raw.toLowerCase();
    if (SOAP_JSON_SECTION_KEYS[lower]) return lower;
    const keys = Object.keys(SOAP_JSON_SECTION_KEYS);
    for (let i = 0; i < keys.length; i++) {
      const key = keys[i];
      if (SOAP_JSON_SECTION_KEYS[key].toLowerCase() === lower) return key;
    }
    return null;
  }

  function templateFieldIndex(template) {
    return soapEditorFields(template).map(field => {
      const sectionKey = field.section === 'summary' ? 'plan' : field.section;
      const leaf = field.path.split('.').pop() || '';
      return {
        path: field.path,
        sectionKey: sectionKey,
        label: field.label,
        names: [normName(field.label), normName(leaf)],
      };
    });
  }

  function lookupTemplateField(sectionKey, fieldName, template) {
    const want = normName(fieldName);
    if (!want) return null;
    const rows = templateFieldIndex(template);
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      if (row.sectionKey !== sectionKey) continue;
      if (row.names.indexOf(want) !== -1) return row;
    }
    return null;
  }

  function isTemplatePrefix(path, template) {
    const prefix = path + '.';
    const fields = soapEditorFields(template);
    for (let i = 0; i < fields.length; i++) {
      if (fields[i].path.indexOf(prefix) === 0) return true;
    }
    return false;
  }

  function extraFieldPath(sectionKey, fieldName, template) {
    const leaf = snakeField(fieldName) || 'field';
    const path = sectionKey + '.' + leaf;
    if (lookupTemplateField(sectionKey, fieldName, template)) return path;
    if (isTemplatePrefix(path, template)) return path + '_extra';
    return path;
  }

  function describeJsonSyntaxError(text, err) {
    const msg = String((err && err.message) || 'Unexpected token');
    const lineCol = /line\s+(\d+)\s+column\s+(\d+)/i.exec(msg);
    const posMatch = /position\s+(\d+)/i.exec(msg);
    let line = null;
    let column = null;
    if (lineCol) {
      line = Number(lineCol[1]);
      column = Number(lineCol[2]);
    } else if (posMatch) {
      const pos = Number(posMatch[1]);
      const upto = String(text || '').slice(0, pos);
      line = upto.split('\n').length;
      column = pos - upto.lastIndexOf('\n');
    }
    if (line != null) {
      return 'JSON syntax error at line ' + line + ', column ' + column + ': ' + msg;
    }
    return 'JSON syntax error: ' + msg + '. Check the token, commas, and brackets around that position.';
  }

  function factValueText(value, index) {
    if (value == null) {
      return { error: 'facts[' + index + '].value is required (got null)' };
    }
    if (Array.isArray(value)) {
      return { error: 'facts[' + index + '].value must be a string or number, not array' };
    }
    if (typeof value === 'object') {
      return { error: 'facts[' + index + '].value must be a string or number, not object' };
    }
    if (typeof value === 'boolean') {
      return { text: value ? 'true' : 'false' };
    }
    return { text: String(value) };
  }

  function factsToNestedSoap(facts, template) {
    const errors = [];
    const warnings = [];
    const soap = {};
    const criticalityByPath = {};
    const seenPaths = {};

    if (facts == null) {
      errors.push("'facts' must be an array (got null)");
      return {
        ok: false, soap: null, errors: errors, warnings: warnings, criticalityByPath: {},
      };
    }
    if (!Array.isArray(facts)) {
      errors.push("'facts' must be an array (got " + (typeof facts) + ')');
      return {
        ok: false, soap: null, errors: errors, warnings: warnings, criticalityByPath: {},
      };
    }

    facts.forEach((raw, index) => {
      const prefix = 'facts[' + index + ']';
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        const kind = Array.isArray(raw) ? 'array' : typeof raw;
        errors.push(
          prefix + ' must be an object with section, field, and value (got ' + kind + ')'
        );
        return;
      }
      const missing = ['section', 'field', 'value'].filter(key => !Object.prototype.hasOwnProperty.call(raw, key));
      if (missing.length) {
        errors.push(
          prefix + ' is missing required key' + (missing.length === 1 ? '' : 's')
          + ' ' + missing.map(key => "'" + key + "'").join(', ')
        );
        return;
      }
      if (typeof raw.section !== 'string' || !String(raw.section).trim()) {
        errors.push(prefix + '.section must be a non-empty string');
        return;
      }
      if (typeof raw.field !== 'string' || !String(raw.field).trim()) {
        errors.push(prefix + '.field must be a non-empty string');
        return;
      }
      const sectionKey = normalizeSoapSection(raw.section);
      if (!sectionKey) {
        errors.push(
          prefix + '.section ' + JSON.stringify(raw.section)
          + ' is not a known SOAP section (' + SOAP_JSON_SECTIONS.join(', ') + ')'
        );
        return;
      }
      const valued = factValueText(raw.value, index);
      if (valued.error) {
        errors.push(valued.error);
        return;
      }
      const known = lookupTemplateField(sectionKey, raw.field, template);
      let path;
      if (known) {
        path = known.path;
      } else {
        path = extraFieldPath(sectionKey, raw.field, template);
        warnings.push(
          prefix + '.field ' + JSON.stringify(raw.field)
          + ' is not a SOAP_CONSULT_TEMPLATE field in '
          + SOAP_JSON_SECTION_KEYS[sectionKey]
          + '; it will be stored with the other SOAP ground truth but is not shown in the form'
        );
      }
      if (seenPaths[path] != null) {
        warnings.push(
          prefix + ' overwrites ' + seenPaths[path] + ' for '
          + SOAP_JSON_SECTION_KEYS[sectionKey] + ' / ' + raw.field
        );
      }
      seenPaths[path] = prefix;
      setSoapAtPath(soap, path, valued.text);
      const crit = raw.criticality;
      if (crit == null || (typeof crit === 'string' && !crit.trim())) {
        criticalityByPath[path] = SOAP_DEFAULT_CRITICALITY;
      } else if (typeof crit === 'string') {
        criticalityByPath[path] = crit.trim();
        if (crit.trim() !== SOAP_DEFAULT_CRITICALITY) {
          warnings.push(
            prefix + '.criticality ' + JSON.stringify(crit.trim())
            + ' is not a form field. Switching to Form keeps the SOAP value; '
            + 'explicit criticality is kept only in this editor session and is '
            + 'not stored on the form Ground Truth path (the scorer uses catalog defaults)'
          );
        }
      } else {
        errors.push(prefix + '.criticality must be a string when present');
      }
    });

    if (errors.length) {
      return {
        ok: false, soap: null, errors: errors, warnings: warnings, criticalityByPath: {},
      };
    }
    return {
      ok: true,
      soap: pruneSoap(soap),
      errors: [],
      warnings: warnings,
      criticalityByPath: criticalityByPath,
    };
  }

  function nestedSoapToFacts(soap, criticalityByPath, template) {
    const facts = [];
    const warnings = [];
    const critMap = criticalityByPath || {};
    const seen = {};
    const schema = template || SOAP_CONSULT_TEMPLATE;

    soapEditorFields(schema).forEach(field => {
      const value = getSoapAtPath(soap, field.path);
      if (value == null || (typeof value === 'string' && !String(value).trim())) return;
      if (value && typeof value === 'object') return;
      const sectionKey = field.section === 'summary' ? 'plan' : field.section;
      const sectionLabel = SOAP_JSON_SECTION_KEYS[sectionKey] || displayFieldName(field.section);
      seen[field.path] = true;
      facts.push({
        section: sectionLabel,
        field: displayFieldName(field.label),
        value: String(value),
        criticality: critMap[field.path] || SOAP_DEFAULT_CRITICALITY,
      });
    });

    function walk(node, path, sectionKey) {
      if (node && typeof node === 'object' && !Array.isArray(node)) {
        Object.keys(node).forEach(key => {
          walk(node[key], path ? path + '.' + key : key, sectionKey || key);
        });
        return;
      }
      if (!path || seen[path]) return;
      if (node == null || (typeof node === 'string' && !String(node).trim())) return;
      if (Array.isArray(node)) {
        warnings.push(
          path + ' is a structured list and cannot be shown as a form field; '
          + 'switch stays in nested JSON so the list is not dropped'
        );
        return;
      }
      if (node && typeof node === 'object') return;
      const root = path.split('.')[0];
      const mapped = root === 'summary' ? 'plan' : root;
      const sectionLabel = SOAP_JSON_SECTION_KEYS[mapped] || displayFieldName(mapped);
      const leaf = path.split('.').pop();
      facts.push({
        section: sectionLabel,
        field: displayFieldName(String(leaf).replace(/_/g, ' ')),
        value: String(node),
        criticality: critMap[path] || SOAP_DEFAULT_CRITICALITY,
      });
      warnings.push(
        'Field ' + JSON.stringify(leaf) + ' at ' + path
        + ' is not a SOAP_CONSULT_TEMPLATE form field; it is kept in JSON '
        + 'and will be saved, but is not shown in the form'
      );
    }

    if (soap && typeof soap === 'object' && !Array.isArray(soap)) {
      walk(soap, '', '');
    }
    return { facts: facts, warnings: warnings };
  }

  function normalizeNestedRootKey(key) {
    const raw = String(key || '').trim();
    if (!raw) return null;
    const lower = raw.toLowerCase();
    if (SOAP_NESTED_ROOTS[lower]) return lower;
    return normalizeSoapSection(raw);
  }

  function looksLikeNestedConsult(payload) {
    if (!payload || typeof payload !== 'object') return false;
    return Object.keys(payload).some(key => !!normalizeNestedRootKey(key));
  }

  function soapHasStructuredLeaves(soap) {
    if (Array.isArray(soap)) return true;
    if (soap && typeof soap === 'object') {
      return Object.keys(soap).some(key => soapHasStructuredLeaves(soap[key]));
    }
    return false;
  }

  function nestedConsultWarnings(soap, template) {
    const warnings = [];
    const templatePaths = {};
    soapEditorFields(template).forEach(field => {
      templatePaths[field.path] = true;
    });
    const knownPaths = Object.keys(templatePaths);

    function walk(node, path) {
      if (Array.isArray(node)) {
        if (path) {
          warnings.push(
            path + ' is a structured list and is not shown in the form; '
            + 'it is kept in JSON and will be saved'
          );
        }
        return;
      }
      if (!node || typeof node !== 'object') {
        if (
          path
          && !templatePaths[path]
          && !knownPaths.some(known => path.indexOf(known + '.') === 0)
        ) {
          const leaf = path.split('.').pop();
          warnings.push(
            'Field ' + JSON.stringify(leaf) + ' at ' + path
            + ' is not a SOAP_CONSULT_TEMPLATE form field; it is kept in JSON '
            + 'and will be saved, but is not shown in the form'
          );
        }
        return;
      }
      if (templatePaths[path]) {
        warnings.push(
          path + ' is a structured object and is not shown in the form; '
          + 'it is kept in JSON and will be saved'
        );
        return;
      }
      Object.keys(node).forEach(key => {
        walk(node[key], path ? path + '.' + key : key);
      });
    }

    if (soap && typeof soap === 'object' && !Array.isArray(soap)) {
      walk(soap, '');
    }
    return warnings;
  }

  function parseNestedConsultJson(payload, template) {
    const errors = [];
    const warnings = [];
    const soap = {};
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      return {
        ok: false,
        soap: null,
        errors: ['Nested SOAP JSON must be an object'],
        warnings: [],
      };
    }
    Object.keys(payload).forEach(key => {
      const value = payload[key];
      const mapped = normalizeNestedRootKey(key);
      if (!mapped) {
        warnings.push(
          'Top-level key ' + JSON.stringify(key)
          + ' is not a known SOAP section ('
          + SOAP_JSON_SECTIONS.join(', ')
          + ', or summary); it will be stored but is not shown in the form'
        );
        soap[key] = value;
        return;
      }
      if (SOAP_NESTED_ROOTS[mapped] === 'string') {
        if (value == null || value === '') return;
        if (typeof value === 'object') {
          errors.push(
            "'" + mapped + "' must be a string, not "
            + (Array.isArray(value) ? 'array' : 'object')
          );
          return;
        }
        soap[mapped] = String(value);
        return;
      }
      if (value == null || value === '') return;
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        const kind = Array.isArray(value) ? 'array' : typeof value;
        errors.push("'" + mapped + "' must be an object with field keys (got " + kind + ')');
        return;
      }
      soap[mapped] = value;
    });
    if (errors.length) {
      return { ok: false, soap: null, errors: errors, warnings: warnings };
    }
    const pruned = pruneSoap(soap);
    return {
      ok: true,
      soap: pruned,
      errors: [],
      warnings: warnings.concat(nestedConsultWarnings(pruned, template)),
    };
  }

  function parseSoapFactsJson(text, template) {
    const empty = {
      ok: true,
      soap: null,
      facts: [],
      errors: [],
      warnings: [],
      criticalityByPath: {},
      document: { facts: [] },
      shape: 'facts',
    };
    const raw = text == null ? '' : String(text);
    if (!raw.trim()) return empty;
    let payload;
    try {
      payload = JSON.parse(raw);
    } catch (err) {
      return {
        ok: false,
        soap: null,
        facts: [],
        errors: [describeJsonSyntaxError(raw, err)],
        warnings: [],
        criticalityByPath: {},
        document: null,
      };
    }
    if (Array.isArray(payload)) {
      return {
        ok: false,
        soap: null,
        facts: [],
        errors: [
          'JSON must be an object: either {"facts": [...]} or a nested SOAP '
          + 'object with subjective/objective/assessment/plan. A top-level array '
          + 'is not accepted.',
        ],
        warnings: [],
        criticalityByPath: {},
        document: null,
        shape: null,
      };
    }
    if (!payload || typeof payload !== 'object') {
      return {
        ok: false,
        soap: null,
        facts: [],
        errors: [
          'JSON must be an object with a \'facts\' array or nested SOAP keys (got '
          + (typeof payload) + ')',
        ],
        warnings: [],
        criticalityByPath: {},
        document: null,
        shape: null,
      };
    }
    if (!Object.prototype.hasOwnProperty.call(payload, 'facts')) {
      if (looksLikeNestedConsult(payload)) {
        const nested = parseNestedConsultJson(payload, template);
        if (!nested.ok) {
          return {
            ok: false,
            soap: null,
            facts: [],
            errors: nested.errors,
            warnings: nested.warnings,
            criticalityByPath: {},
            document: null,
            shape: 'nested',
          };
        }
        return {
          ok: true,
          soap: nested.soap,
          facts: [],
          errors: [],
          warnings: nested.warnings,
          criticalityByPath: {},
          document: nested.soap,
          shape: 'nested',
        };
      }
      return {
        ok: false,
        soap: null,
        facts: [],
        errors: [
          'JSON is missing required key \'facts\' and has no nested SOAP sections. '
          + 'Use {"facts": [{"section": "...", "field": "...", "value": "..."}]} '
          + 'or {"subjective": {...}, "objective": {...}, "assessment": {...}, "plan": {...}}.',
        ],
        warnings: [],
        criticalityByPath: {},
        document: null,
        shape: null,
      };
    }
    const converted = factsToNestedSoap(payload.facts, template);
    if (!converted.ok) {
      return {
        ok: false,
        soap: null,
        facts: [],
        errors: converted.errors,
        warnings: converted.warnings,
        criticalityByPath: {},
        document: null,
      };
    }
    const factsDoc = nestedSoapToFacts(
      converted.soap,
      converted.criticalityByPath,
      template
    );
    return {
      ok: true,
      soap: converted.soap,
      facts: Array.isArray(payload.facts) ? payload.facts : [],
      errors: [],
      warnings: converted.warnings,
      criticalityByPath: converted.criticalityByPath,
      document: { facts: factsDoc.facts },
      shape: 'facts',
    };
  }

  function soapToFactsJson(soap, criticalityByPath, template, preferNested) {
    if (preferNested || soapHasStructuredLeaves(soap)) {
      const pruned = pruneSoap(soap);
      const document = pruned && typeof pruned === 'object' && !Array.isArray(pruned)
        ? pruned
        : {};
      return {
        text: JSON.stringify(document, null, 2),
        document: document,
        warnings: nestedConsultWarnings(pruned, template),
        shape: 'nested',
      };
    }
    const converted = nestedSoapToFacts(soap, criticalityByPath, template);
    const document = { facts: converted.facts };
    return {
      text: JSON.stringify(document, null, 2),
      document: document,
      warnings: converted.warnings,
      shape: 'facts',
    };
  }

  function applyManualSoapJson(item, jsonText, transcription, translation) {
    const parsed = parseSoapFactsJson(jsonText);
    if (!parsed.ok) {
      return {
        ok: false,
        errors: parsed.errors,
        warnings: parsed.warnings,
        item: item,
      };
    }
    return {
      ok: true,
      errors: [],
      warnings: parsed.warnings,
      item: applyManualGroundTruth(item, {
        transcription: transcription || '',
        translation: translation || '',
        soap: parsed.soap,
      }),
    };
  }

  function mergeManualGt(item) {
    const data = Object.assign({}, item || {});
    const fileFlags = snapshotFileFlags(data._file_flags || incomingFileFlags(data));
    data._file_flags = fileFlags;
    const mg = normalizeManualGt(data.manual_gt);
    data.manual_gt = mg;
    const flags = Object.assign({}, fileFlags);
    flags.has_transcript_ground_truth = flags.has_transcript_ground_truth || !!mg.transcription;
    flags.has_transcript = flags.has_transcript_ground_truth;
    flags.has_translation_ground_truth = flags.has_translation_ground_truth || !!mg.translation;
    flags.has_soap_ground_truth = flags.has_soap_ground_truth || soapHasContent(mg.soap);
    flags.has_summary_ground_truth = flags.has_soap_ground_truth;
    Object.assign(data, flags);
    if (mg.transcription) {
      data.ground_truth = mg.transcription;
      data.ground_truth_transcription = mg.transcription;
      data._manual_wrote_transcript = true;
    } else if (data._manual_wrote_transcript) {
      data.ground_truth = '';
      data.ground_truth_transcription = '';
      data._manual_wrote_transcript = false;
    }
    if (mg.translation) {
      data.translation_ground_truth = mg.translation;
      data._manual_wrote_translation = true;
    } else if (data._manual_wrote_translation) {
      data.translation_ground_truth = '';
      data._manual_wrote_translation = false;
    }
    if (soapHasContent(mg.soap)) {
      data.soap_ground_truth = mg.soap;
      data._manual_wrote_soap = true;
    } else if (data._manual_wrote_soap) {
      data.soap_ground_truth = null;
      data._manual_wrote_soap = false;
    }
    data.gt_status = completenessStatus(data);
    data.gt_status_label = STATUS_LABELS[data.gt_status];
    return data;
  }

  function applyManualGroundTruth(item, fields) {
    const base = attachGroundTruths(item, []);
    base.manual_gt = normalizeManualGt(fields);
    return mergeManualGt(base);
  }

  function scoringOverlay(item) {
    const mg = normalizeManualGt((item || {}).manual_gt);
    const out = {};
    if (mg.transcription) {
      out.ground_truth = mg.transcription;
      out.ground_truth_transcription = mg.transcription;
      out.has_transcript_ground_truth = true;
      out.has_transcript = true;
      out.has_ground_truth = true;
      out.ground_truth_source = 'upload';
    }
    if (mg.translation) {
      out.translation_ground_truth = mg.translation;
      out.has_translation_ground_truth = true;
    }
    if (soapHasContent(mg.soap)) {
      out.soap_ground_truth = mg.soap;
      out.has_soap_ground_truth = true;
      out.has_summary_ground_truth = true;
    }
    return out;
  }

  function attachGroundTruths(audioItem, gtFiles) {
    const item = Object.assign({}, audioItem || {});
    const audioName = String(item.audio || item.audio_filename || '');
    const audioKey = matchKey(audioName);
    const matched = [];
    (gtFiles || []).forEach(gt => {
      const name = String(gt.filename || gt.name || gt.audio || '');
      const kind = classifyUpload(name);
      if (kind === 'audio' || kind === 'unknown') return;
      if (audioKey && gtMatchKey(name) === audioKey) {
        matched.push(Object.assign({}, gt, { filename: name }));
      }
    });

    let flags = item._file_flags && typeof item._file_flags === 'object'
      ? snapshotFileFlags(item._file_flags)
      : incomingFileFlags(item);
    const names = [];
    [
      'ground_truth_filename',
      'transcript_filename',
      'soap_gt_filename',
      'translation_gt_filename',
      'json_gt_filename',
    ].forEach(key => {
      const existing = String(item[key] || '').trim();
      if (!existing) return;
      names.push(existing);
      flags = orFlags(flags, flagsFromGtFile(existing));
    });
    matched.forEach(gt => {
      names.push(gt.filename);
      flags = orFlags(flags, flagsFromGtFile(gt.filename));
    });
    const display = primaryGtFilename(names);
    item._file_flags = snapshotFileFlags(flags);
    Object.assign(item, item._file_flags);
    item.ground_truth_filename = display;
    item.matched_gt_filenames = names;
    return mergeManualGt(item);
  }

  function completenessStatus(item) {
    const data = item || {};
    if (uploadNeedsLanguage(data)) return STATUS_MISSING_LANGUAGE;
    const mg = normalizeManualGt(data.manual_gt);
    const hasTranscript = truthy(data.has_transcript_ground_truth)
      || truthy(data.has_transcript)
      || truthy(data.has_ground_truth)
      || !!mg.transcription;
    let hasTranslation = truthy(data.has_translation_ground_truth) || !!mg.translation;
    const hasSoap = truthy(data.has_soap_ground_truth)
      || truthy(data.has_summary_ground_truth)
      || soapHasContent(mg.soap);
    const hasJson = truthy(data.has_json_ground_truth);
    const jsonApplicable = truthy(data.has_json_applicable) || hasJson;
    const hasAny = hasTranscript || hasTranslation || hasSoap || hasJson
      || !!String(data.ground_truth_filename || '').trim()
      || hasManualGt(data);
    if (!hasAny) return STATUS_MISSING_GT_ALL;
    if (isEnglishCase(data)) hasTranslation = true;
    if (!hasTranscript) return STATUS_MISSING_TRANSCRIPT;
    if (jsonApplicable && !hasJson) return STATUS_MISSING_JSON;
    if (!hasTranslation) return STATUS_MISSING_TRANSLATION;
    if (!hasSoap) return STATUS_MISSING_SOAP;
    return STATUS_COMPLETE;
  }

  function formatDurationMmSs(seconds) {
    if (seconds == null || seconds === '') return '—';
    const n = Number(seconds);
    if (!Number.isFinite(n) || n <= 0) return '—';
    const total = Math.round(n);
    const minutes = Math.floor(total / 60);
    const secs = total % 60;
    return String(minutes).padStart(2, '0') + ':' + String(secs).padStart(2, '0');
  }

  function durationSecondsForItem(item, results) {
    const data = item || {};
    const direct = data.duration || data.audio_duration_seconds || data.audio_length;
    if (direct != null && direct !== '' && Number(direct) !== 0) return direct;
    const audio = String(data.audio || data.audio_filename || '').trim().toLowerCase();
    if (!audio) return null;
    const latency = root.MedsumLatencyAnalysis;
    const rows = results || [];
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i] || {};
      const name = String(row.audio_filename || row.filename || '').trim().toLowerCase();
      if (name !== audio) continue;
      if (latency && latency.pickTranscribeTime) {
        const picked = latency.pickTranscribeTime(row, 'audio_length');
        if (picked != null) return picked;
      }
      if (row.audio_duration_seconds) return row.audio_duration_seconds;
    }
    return null;
  }

  function selectedFilesTableRow(item, index, results) {
    const enriched = attachGroundTruths(item, []);
    const display = gtDisplayLabel(enriched);
    const status = completenessStatus(enriched);
    return {
      index: index,
      audio_file: String(enriched.audio || enriched.audio_filename || ''),
      duration: formatDurationMmSs(durationSecondsForItem(enriched, results)),
      ground_truth: display,
      enter_manually: display === '—',
      has_manual_gt: hasManualGt(enriched),
      status: status,
      status_label: STATUS_LABELS[status],
      catalog_id: catalogId(enriched),
      source: itemSource(enriched),
      language: String(enriched.language || ''),
      needs_language: uploadNeedsLanguage(enriched),
    };
  }

  const api = {
    TAB_SWITCH_KEEPS_SELECTION,
    STATUS_COMPLETE,
    STATUS_MISSING_LANGUAGE,
    STATUS_MISSING_SOAP,
    STATUS_MISSING_TRANSLATION,
    STATUS_MISSING_TRANSCRIPT,
    STATUS_MISSING_JSON,
    STATUS_MISSING_GT_ALL,
    STATUS_LABELS,
    STATUS_LEGEND,
    MISSING_LANGUAGE_RUN_MESSAGE,
    LANGUAGE_CODE_MAP,
    SELECTED_FILES_HEADERS,
    SOAP_CONSULT_TEMPLATE,
    SOAP_JSON_SECTIONS,
    SOAP_JSON_SECTION_KEYS,
    SOAP_DEFAULT_CRITICALITY,
    SOAP_NESTED_ROOTS,
    fileKey,
    catalogId,
    dropSelectedKey,
    selectionAfterSourceSwitch,
    itemSource,
    supportedLanguageLabels,
    canonicalLanguageLabel,
    uploadNeedsLanguage,
    missingLanguageUploads,
    setUploadLanguage,
    clearAllKeys,
    filterMultiAudioItems,
    ingestIntoCatalog,
    excludeFromSelection,
    selectedForExecution,
    drivePayload,
    runPayload,
    resultsKeepFailures,
    matchKey,
    gtMatchKey,
    classifyUpload,
    isBundleGroundTruth,
    attachGroundTruths,
    completenessStatus,
    formatDurationMmSs,
    durationSecondsForItem,
    selectedFilesTableRow,
    isEnglishCase,
    soapHasContent,
    pruneSoap,
    normalizeManualGt,
    hasManualGt,
    gtDisplayLabel,
    applyManualGroundTruth,
    scoringOverlay,
    soapEditorFields,
    getSoapAtPath,
    setSoapAtPath,
    cloneSoap,
    normalizeSoapSection,
    parseSoapFactsJson,
    parseNestedConsultJson,
    soapToFactsJson,
    soapHasStructuredLeaves,
    factsToNestedSoap,
    nestedSoapToFacts,
    applyManualSoapJson,
    mergeManualGt,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumAudioSelection = api;
})(typeof window !== 'undefined' ? window : globalThis);
