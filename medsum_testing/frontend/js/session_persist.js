/**
 * Client session persistence. Mirrors session_persist.py.
 *
 * Running batch: store currentBatchId only; GET /results/batch/{id} is truth.
 * Doctor/patient form: persist. File selection: reset on refresh.
 * Dashboard batch filter IDs: persist, drop stale. Results/Latency tab: persist.
 */
(function (root) {
  const STORAGE_KEY_BATCH = 'medsum.currentBatchId';
  const STORAGE_KEY_DOCTORS = 'medsum.doctors';
  const STORAGE_KEY_LT_ROWS = 'medsum.ltRows';
  const STORAGE_KEY_BATCH_FILTER = 'medsum.selectedBatchIds';
  const STORAGE_KEY_TABLE_TAB = 'medsum.resultsTableTab';
  const STORAGE_KEY_SIDEBAR = 'medsum.sidebarCollapsed';
  const FILE_SELECTION_RESETS_ON_REFRESH = true;
  const PHASE_PENDING = 'pending';
  const PHASE_RUNNING = 'running';
  const PHASE_DONE = 'done';

  const memory = {};

  function getStore() {
    if (root.__medsumPersistStore) return root.__medsumPersistStore;
    try {
      if (root.localStorage) return root.localStorage;
    } catch (err) { /* private mode */ }
    return {
      getItem: key => (Object.prototype.hasOwnProperty.call(memory, key) ? memory[key] : null),
      setItem: (key, value) => { memory[key] = String(value); },
      removeItem: key => { delete memory[key]; },
    };
  }

  function readRaw(key) {
    try {
      return getStore().getItem(key);
    } catch (err) {
      return null;
    }
  }

  function writeRaw(key, value) {
    try {
      getStore().setItem(key, value);
    } catch (err) { /* quota / private mode */ }
  }

  function removeRaw(key) {
    try {
      getStore().removeItem(key);
    } catch (err) { /* ignore */ }
  }

  function readJson(key, fallback) {
    const raw = readRaw(key);
    if (raw == null || raw === '') return fallback;
    try {
      return JSON.parse(raw);
    } catch (err) {
      return fallback;
    }
  }

  function writeJson(key, value) {
    writeRaw(key, JSON.stringify(value));
  }

  function batchWatchPhase(payload) {
    const data = payload || {};
    const results = data.results || [];
    const statuses = results.map(row => String((row && row.status) || '').trim().toLowerCase());
    if (statuses.some(status => status === 'running')) return PHASE_RUNNING;
    const pendingN = Number(data.pending);
    const pending = Number.isFinite(pendingN) ? pendingN : 0;
    if (pending > 0 || statuses.some(status => status === 'pending')) return PHASE_PENDING;
    const totalN = data.total == null ? results.length : Number(data.total);
    const total = Number.isFinite(totalN) ? totalN : results.length;
    if (!results.length && total <= 0) return PHASE_PENDING;
    return PHASE_DONE;
  }

  function shouldResumePolling(payload) {
    return batchWatchPhase(payload) !== PHASE_DONE;
  }

  function shouldClearStoredBatch(payload, httpStatus) {
    if (httpStatus != null && httpStatus !== 200) return false;
    return batchWatchPhase(payload) === PHASE_DONE;
  }

  function doctorFormSnapshot(doctors) {
    const out = [];
    (doctors || []).forEach(row => {
      if (!row || typeof row !== 'object') return;
      const phone = String(row.phone || '').trim();
      const password = String(row.password || '');
      const raw = Array.isArray(row.patients) ? row.patients : (row.patients ? [row.patients] : []);
      const patients = raw.map(item => String(item == null ? '' : item).trim()).filter(Boolean);
      if (!phone && !password && !patients.length) return;
      out.push({ phone, password, patients });
    });
    return out;
  }

  function shouldAddBlankDoctorRow(snapshot) {
    return doctorFormSnapshot(snapshot || []).length === 0;
  }

  function ltFormSnapshot(rows) {
    const out = [];
    (rows || []).forEach(row => {
      if (!row || typeof row !== 'object') return;
      const phone = String(row.phone || '').trim();
      const password = String(row.password || '');
      const patientId = String(row.patientId || row.patient_id || '').trim();
      if (!phone && !password && !patientId) return;
      out.push({ phone, password, patientId });
    });
    return out;
  }

  function shouldAddBlankLtRow(snapshot) {
    return ltFormSnapshot(snapshot || []).length === 0;
  }

  function restoreSelectedBatchIds(stored, available) {
    const avail = {};
    (available || []).forEach(item => {
      const key = String(item == null ? '' : item);
      if (key) avail[key] = true;
    });
    const kept = [];
    const seen = {};
    (stored || []).forEach(item => {
      const key = String(item || '').trim();
      if (!key || !avail[key] || seen[key]) return;
      seen[key] = true;
      kept.push(key);
    });
    return kept;
  }

  function normalizeTableTab(tab) {
    return String(tab || '').trim().toLowerCase() === 'latency' ? 'latency' : 'results';
  }

  function loadCurrentBatchId() {
    const value = String(readRaw(STORAGE_KEY_BATCH) || '').trim();
    return value || '';
  }

  function saveCurrentBatchId(batchId) {
    const value = String(batchId || '').trim();
    if (!value) {
      removeRaw(STORAGE_KEY_BATCH);
      return '';
    }
    writeRaw(STORAGE_KEY_BATCH, value);
    return value;
  }

  function clearCurrentBatchId() {
    removeRaw(STORAGE_KEY_BATCH);
  }

  function loadDoctors() {
    const parsed = readJson(STORAGE_KEY_DOCTORS, []);
    return doctorFormSnapshot(Array.isArray(parsed) ? parsed : []);
  }

  function saveDoctors(doctors) {
    writeJson(STORAGE_KEY_DOCTORS, doctorFormSnapshot(doctors));
  }

  function loadLtRows() {
    const parsed = readJson(STORAGE_KEY_LT_ROWS, []);
    return ltFormSnapshot(Array.isArray(parsed) ? parsed : []);
  }

  function saveLtRows(rows) {
    writeJson(STORAGE_KEY_LT_ROWS, ltFormSnapshot(rows));
  }

  function loadSelectedBatchIds() {
    const parsed = readJson(STORAGE_KEY_BATCH_FILTER, []);
    return Array.isArray(parsed) ? parsed.map(item => String(item || '').trim()).filter(Boolean) : [];
  }

  function saveSelectedBatchIds(ids) {
    writeJson(STORAGE_KEY_BATCH_FILTER, (ids || []).map(item => String(item || '').trim()).filter(Boolean));
  }

  function loadTableTabs() {
    const parsed = readJson(STORAGE_KEY_TABLE_TAB, {});
    const data = parsed && typeof parsed === 'object' ? parsed : {};
    return {
      dashboard: normalizeTableTab(data.dashboard),
      history: normalizeTableTab(data.history),
    };
  }

  function saveTableTab(source, tab) {
    const current = loadTableTabs();
    const key = source === 'history' ? 'history' : 'dashboard';
    current[key] = normalizeTableTab(tab);
    writeJson(STORAGE_KEY_TABLE_TAB, current);
  }

  function loadSidebarCollapsed() {
    return readRaw(STORAGE_KEY_SIDEBAR) === '1';
  }

  function saveSidebarCollapsed(collapsed) {
    writeRaw(STORAGE_KEY_SIDEBAR, collapsed ? '1' : '0');
  }

  const api = {
    STORAGE_KEY_BATCH,
    STORAGE_KEY_DOCTORS,
    STORAGE_KEY_LT_ROWS,
    STORAGE_KEY_BATCH_FILTER,
    STORAGE_KEY_TABLE_TAB,
    STORAGE_KEY_SIDEBAR,
    FILE_SELECTION_RESETS_ON_REFRESH,
    PHASE_PENDING,
    PHASE_RUNNING,
    PHASE_DONE,
    batchWatchPhase,
    shouldResumePolling,
    shouldClearStoredBatch,
    doctorFormSnapshot,
    shouldAddBlankDoctorRow,
    ltFormSnapshot,
    shouldAddBlankLtRow,
    restoreSelectedBatchIds,
    normalizeTableTab,
    loadCurrentBatchId,
    saveCurrentBatchId,
    clearCurrentBatchId,
    loadDoctors,
    saveDoctors,
    loadLtRows,
    saveLtRows,
    loadSelectedBatchIds,
    saveSelectedBatchIds,
    loadTableTabs,
    saveTableTab,
    loadSidebarCollapsed,
    saveSidebarCollapsed,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumSessionPersist = api;
})(typeof window !== 'undefined' ? window : globalThis);
