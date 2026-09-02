/**
 * Doctor → at most one patient per accuracy test run.
 * Legacy multi-patient lists are kept as-is; callers must not truncate them.
 *
 * Early GET /api/patient-data/{id}/ on Add Patient is skipped
 * (LOOKUP_PATIENT_ON_ADD). That lookup needs a doctor JWT, so each Add would
 * extra-authenticate and still could not run when Phone/Password are empty.
 * A bad ID stays a Run All concern rather than an extra request per Add.
 */
(function (root) {
  const MAX_PATIENTS_PER_DOCTOR = 1;
  const TOO_MANY_PATIENTS_MSG =
    'Each doctor can be linked to only one patient in a test run';
  const LOOKUP_PATIENT_ON_ADD = false;
  const PATIENT_REQUIRED_MSG = 'Patient ID is required';
  const PATIENT_NUMERIC_MSG = 'Patient ID must be numeric';
  const PATIENT_SAVED_TOAST_PREFIX = 'Patient ';
  const PATIENT_SAVED_TOAST_SUFFIX = ' saved';
  const CREDENTIALS_SAVED_LABEL = 'Saved';

  function normalizePatientIds(raw) {
    const items = Array.isArray(raw)
      ? raw
      : String(raw || '').split(',');
    const seen = {};
    const out = [];
    items.forEach(item => {
      const val = String(item == null ? '' : item).trim();
      if (!val || seen[val]) return;
      seen[val] = true;
      out.push(val);
    });
    return out;
  }

  function addPatientControlsVisible(patientCount, changing) {
    return Number(patientCount || 0) === 0 || !!changing;
  }

  function isNumericPatientId(raw) {
    return /^\d+$/.test(String(raw == null ? '' : raw).trim());
  }

  function patientSavedMessage(patientId) {
    return PATIENT_SAVED_TOAST_PREFIX
      + String(patientId == null ? '' : patientId).trim()
      + PATIENT_SAVED_TOAST_SUFFIX;
  }

  function patientAddValidationError(raw, existing, options) {
    const val = String(raw == null ? '' : raw).trim();
    if (!val) return PATIENT_REQUIRED_MSG;
    if (!isNumericPatientId(val)) return PATIENT_NUMERIC_MSG;
    const result = assignPatientToDoctor(existing, val, options);
    if (!result.ok) return result.error || 'Could not assign patient';
    return '';
  }

  function credentialsLookSaved(phone, password) {
    const p = String(phone == null ? '' : phone).trim();
    const w = String(password == null ? '' : password);
    if (!p || !w) return false;
    return /\d/.test(p);
  }

  function assignPatientToDoctor(existing, newId, options) {
    const replace = !!(options && options.replace);
    const ids = normalizePatientIds(existing);
    const val = String(newId == null ? '' : newId).trim();
    if (!val) {
      return { ok: false, error: PATIENT_REQUIRED_MSG, patients: ids };
    }
    if (replace) {
      return { ok: true, patients: [val] };
    }
    if (ids.indexOf(val) !== -1) {
      return {
        ok: false,
        error: 'Patient ID already assigned to this doctor',
        patients: ids,
      };
    }
    if (ids.length >= MAX_PATIENTS_PER_DOCTOR) {
      return { ok: false, error: TOO_MANY_PATIENTS_MSG, patients: ids };
    }
    return { ok: true, patients: ids.concat([val]) };
  }

  const api = {
    MAX_PATIENTS_PER_DOCTOR,
    TOO_MANY_PATIENTS_MSG,
    LOOKUP_PATIENT_ON_ADD,
    PATIENT_REQUIRED_MSG,
    PATIENT_NUMERIC_MSG,
    CREDENTIALS_SAVED_LABEL,
    normalizePatientIds,
    addPatientControlsVisible,
    isNumericPatientId,
    patientSavedMessage,
    patientAddValidationError,
    credentialsLookSaved,
    assignPatientToDoctor,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumDoctorPatient = api;
})(typeof window !== 'undefined' ? window : globalThis);
