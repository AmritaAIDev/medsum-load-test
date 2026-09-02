"""Run-scoped audio selection. Exclude never deletes a source file.

Drive files stay on Google Drive. Local uploads stay in the in-memory catalog.
Removing a file from a run only drops it from the selection used for execution.

TAB_SWITCH_KEEPS_SELECTION used to stay True so a run could mix Drive and
uploaded audio across an Upload ↔ Drive tab switch. MOM now requires that
switch to drop the *other* source's selection (display and run state) so
stale rows cannot linger or come back. The catalog — including ``manual_gt``
on a still-selected row — is not wiped; only selected keys of the other
source are dropped.

Manually typed ground truth is stored as ``manual_gt`` on the catalog row and
merged into the same ``has_*_ground_truth`` flags used for uploaded/Drive GT.
Clear All / exclude drop the row from the run; they do not wipe ``manual_gt``
on a catalog entry that is later re-selected.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from medsum_testing.backend.services.drive_service import (
    _match_key,
    get_soap_base,
    get_translation_base,
    is_soap_gt,
    is_translation_gt,
)
from medsum_testing.backend.services.latency_analysis import pick_transcribe_time
from medsum_testing.backend.services.medsum_api import (
    SOAP_CONSULT_TEMPLATE,
    canonical_language_label,
    supported_language_labels,
)

# Same schema the detail view renders for SOAP — the manual editor reuses this.
SOAP_EDITOR_SCHEMA = SOAP_CONSULT_TEMPLATE

# Manual SOAP JSON mode uses this flat fact-list shape. Sections match the
# SOAP_CONSULT_TEMPLATE roots the form already edits (summary maps to Plan).
SOAP_JSON_SECTIONS = ("Subjective", "Objective", "Assessment", "Plan")
SOAP_JSON_SECTION_KEYS = {
    "subjective": "Subjective",
    "objective": "Objective",
    "assessment": "Assessment",
    "plan": "Plan",
}
SOAP_DEFAULT_CRITICALITY = "Normal"
SOAP_NESTED_ROOTS = {
    "subjective": "object",
    "objective": "object",
    "assessment": "object",
    "plan": "object",
    "summary": "string",
}

# MOM: switching Upload Manually ↔ Google Drive clears the other source's
# selected files. Catalog rows (and their manual_gt) stay. Flip to True only
# if mixed-source-on-tab-switch is explicitly required again.
TAB_SWITCH_KEEPS_SELECTION = False

AUDIO_EXTENSIONS = frozenset({"mp3", "wav", "m4a", "ogg", "aac", "webm"})
JSON_EXTENSIONS = frozenset({"json"})
EXCEL_EXTENSIONS = frozenset({"xls", "xlsx", "csv"})
PDF_EXTENSIONS = frozenset({"pdf"})
TRANSCRIPT_EXTENSIONS = frozenset({"txt", "doc", "docx"})
GT_EXTENSIONS = JSON_EXTENSIONS | EXCEL_EXTENSIONS | PDF_EXTENSIONS | TRANSCRIPT_EXTENSIONS

KIND_AUDIO = "audio"
KIND_SOAP = "soap"
KIND_TRANSLATION = "translation"
KIND_JSON = "json"
KIND_EXCEL = "excel"
KIND_PDF = "pdf"
KIND_TRANSCRIPT = "transcript"
KIND_UNKNOWN = "unknown"

STATUS_COMPLETE = "complete"
STATUS_MISSING_LANGUAGE = "missing_language"
STATUS_MISSING_SOAP = "missing_soap"
STATUS_MISSING_TRANSLATION = "missing_translation"
STATUS_MISSING_TRANSCRIPT = "missing_transcript"
STATUS_MISSING_JSON = "missing_json"
STATUS_MISSING_GT_ALL = "missing_gt_all"

STATUS_LABELS = {
    STATUS_COMPLETE: "Complete",
    STATUS_MISSING_LANGUAGE: "Missing Language",
    STATUS_MISSING_SOAP: "Missing GT (SOAP)",
    STATUS_MISSING_TRANSLATION: "Missing Translation",
    STATUS_MISSING_TRANSCRIPT: "Missing Transcript",
    STATUS_MISSING_JSON: "Missing JSON",
    STATUS_MISSING_GT_ALL: "Missing GT (All)",
}

# Legend copy matches every STATUS the table can emit — no extras, no gaps.
STATUS_LEGEND = (
    (STATUS_COMPLETE, "Complete (All files)"),
    (STATUS_MISSING_LANGUAGE, "Missing Language"),
    (STATUS_MISSING_SOAP, "Missing GT (SOAP)"),
    (STATUS_MISSING_TRANSLATION, "Missing Translation"),
    (STATUS_MISSING_TRANSCRIPT, "Missing Transcript"),
    (STATUS_MISSING_JSON, "Missing JSON"),
    (STATUS_MISSING_GT_ALL, "Missing GT (All)"),
)

MISSING_LANGUAGE_RUN_MESSAGE = (
    "Set a language for each uploaded audio file before running tests."
)
SUPPORTED_LANGUAGE_LABELS = supported_language_labels()

SELECTED_FILES_HEADERS = (
    "#",
    "AUDIO FILE",
    "DURATION",
    "GROUND TRUTH",
    "STATUS",
    "ACTION",
)

FILE_FLAG_KEYS = (
    "has_transcript_ground_truth",
    "has_transcript",
    "has_translation_ground_truth",
    "has_soap_ground_truth",
    "has_summary_ground_truth",
    "has_json_ground_truth",
    "has_json_applicable",
)


def audio_file_key(item: dict | None) -> tuple[str, str]:
    data = item or {}
    language = str(data.get("language") or data.get("folder_label") or "").strip().lower()
    audio = str(
        data.get("audio") or data.get("audio_filename") or data.get("filename") or ""
    ).strip().lower()
    return language, audio


def catalog_id(item: dict | None) -> str:
    """DOM-safe row id. Must not contain NUL or raw quotes."""
    data = item or {}
    source = str(data.get("source") or "drive")
    language, audio = audio_file_key(data)
    return "::".join(quote(part, safe="") for part in (source, language, audio))


def drop_selected_key(selected_keys: list[str] | None, target_id: str | None) -> list[str]:
    """Drop by stable catalog id, never by list index."""
    drop = str(target_id or "")
    return [key for key in (selected_keys or []) if key != drop]


def item_source(item: dict | None) -> str:
    return str((item or {}).get("source") or "drive")


def upload_needs_language(item: dict | None) -> bool:
    """Upload rows with no language cannot run. Drive rows are never this."""
    data = item or {}
    if item_source(data) != "upload":
        return False
    return not canonical_language_label(
        data.get("language") or data.get("folder_label") or ""
    )


def missing_language_uploads(selected: list[dict] | None) -> list[dict]:
    return [item for item in (selected or []) if upload_needs_language(item)]


def set_upload_language(
    catalog: list[dict] | None,
    selected_keys: list[str] | None,
    catalog_id_str: str | None,
    language: str | None,
) -> tuple[list[dict], list[str]]:
    """Set language on one upload row. Drive rows and unknown ids are no-ops."""
    next_catalog = list(catalog or [])
    keys = list(selected_keys or [])
    target = str(catalog_id_str or "")
    if not target:
        return next_catalog, keys
    idx = next(
        (i for i, row in enumerate(next_catalog) if catalog_id(row) == target),
        -1,
    )
    if idx < 0 or item_source(next_catalog[idx]) != "upload":
        return next_catalog, keys
    prev_id = catalog_id(next_catalog[idx])
    label = canonical_language_label(language)
    updated = dict(next_catalog[idx])
    updated["language"] = label
    updated["folder_label"] = label
    next_catalog[idx] = updated
    new_id = catalog_id(updated)
    keys = [new_id if key == prev_id else key for key in keys]
    return next_catalog, keys


def selection_after_source_switch(
    selected_keys: list[str] | None,
    catalog: list[dict] | None,
    next_source: str,
    *,
    keep_other: bool | None = None,
) -> list[str]:
    """Keep the destination source's keys. Other-source keys leave the run.

    Catalog is not mutated. ``manual_gt`` on remaining (and dropped-but-still
    catalogued) rows is left as-is. When keep_other is True the mixed-source
    tab-switch behavior is restored.
    """
    keys = list(selected_keys or [])
    if TAB_SWITCH_KEEPS_SELECTION if keep_other is None else keep_other:
        return keys
    wanted = str(next_source or "").strip() or "upload"
    by_id = {catalog_id(item): item for item in (catalog or [])}
    kept: list[str] = []
    for key in keys:
        item = by_id.get(key)
        if item is not None and item_source(item) == wanted:
            kept.append(key)
    return kept


def exclude_from_selection(selected: list[dict], target: dict) -> list[dict]:
    """Drop one file from the run selection. Does not mutate a source catalog."""
    drop = audio_file_key(target)
    return [item for item in selected if audio_file_key(item) != drop]


def filter_multi_audio_items(items: list[dict] | None, query: str | None) -> list[dict]:
    """Hide-only filter for the multi-file picker. Does not change checked state."""
    q = str(query or "").strip().lower()
    rows = list(items or [])
    if not q:
        return rows
    return [
        item
        for item in rows
        if q in f"{item.get('language') or ''} {item.get('audio') or item.get('audio_filename') or ''}".lower()
    ]


def catalog_still_has(catalog: list[dict], target: dict) -> bool:
    key = audio_file_key(target)
    return any(audio_file_key(item) == key for item in catalog)


def filter_cases_for_run(
    discovered: list[dict],
    selected: list[dict] | None,
) -> list[dict]:
    """Execution set: selected files only. None means the full discovery list.

    An empty selected list means run nothing — never fall back to all files.
    """
    if selected is None:
        return list(discovered or [])
    wanted = {audio_file_key(item) for item in selected}
    wanted.discard(("", ""))
    return [case for case in (discovered or []) if audio_file_key(case) in wanted]


def results_include_failures(results: list[dict]) -> list[dict]:
    """Results views must keep FAIL/failed rows — do not filter them out."""
    return list(results or [])


def run_payload(selected: list[dict] | None) -> list[dict]:
    """selected_audios body: every row still in the selected-files table."""
    payload = []
    for item in selected or []:
        audio = str(
            item.get("audio") or item.get("audio_filename") or item.get("filename") or ""
        ).strip()
        if not audio:
            continue
        source = str(item.get("source") or "drive")
        language = str(item.get("language") or item.get("folder_label") or "").strip()
        if source == "upload":
            language = canonical_language_label(language) or language
        row = {
            "language": language,
            "audio": audio,
            "source": source,
        }
        upload_id = str(item.get("upload_id") or "").strip()
        if upload_id:
            row["upload_id"] = upload_id
        if has_manual_gt(item):
            row["manual_gt"] = normalize_manual_gt(item.get("manual_gt"))
        payload.append(row)
    return payload


def clear_all_keys(selected_keys: list[str] | None) -> list[str]:
    """Prompt 13 exclude applied to every selected id. Catalog is not touched."""
    keys = list(selected_keys or [])
    remaining = keys
    for key in keys:
        remaining = drop_selected_key(remaining, key)
    return remaining


def _extension(filename: str) -> str:
    name = str(filename or "").strip().lower()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1]


def match_key(filename: str) -> str:
    """Same stem key Drive ingestion uses (number prefix, _script/_gt, duration)."""
    return _match_key(filename or "")


def gt_match_key(filename: str) -> str:
    """Pair a ground-truth file to audio using the Drive suffix rules."""
    name = str(filename or "")
    if is_soap_gt(name):
        return get_soap_base(name)
    if is_translation_gt(name):
        return get_translation_base(name)
    return match_key(name)


def classify_upload(filename: str) -> str:
    """Sort a dropped/browsed file into audio vs a ground-truth kind."""
    name = str(filename or "")
    ext = _extension(name)
    if ext in AUDIO_EXTENSIONS:
        return KIND_AUDIO
    if not ext or ext not in GT_EXTENSIONS:
        return KIND_UNKNOWN
    if is_soap_gt(name):
        return KIND_SOAP
    if is_translation_gt(name):
        return KIND_TRANSLATION
    if ext in JSON_EXTENSIONS:
        return KIND_JSON
    if ext in EXCEL_EXTENSIONS:
        return KIND_EXCEL
    if ext in PDF_EXTENSIONS:
        return KIND_PDF
    return KIND_TRANSCRIPT


def is_bundle_ground_truth(filename: str) -> bool:
    """JSON/Excel (or *_gt / *_ground_truth) stands in for the full GT package."""
    kind = classify_upload(filename)
    if kind == KIND_AUDIO or kind == KIND_UNKNOWN:
        return False
    if is_soap_gt(filename) or is_translation_gt(filename):
        return False
    if kind in {KIND_JSON, KIND_EXCEL}:
        return True
    base = str(filename or "").lower().rsplit(".", 1)[0]
    return base.endswith("_gt") or base.endswith("_ground_truth")


def _truthy(value: Any) -> bool:
    return bool(value) and value not in ("0", "false", "False")


def is_english_case(item: dict | None) -> bool:
    data = item or {}
    if _truthy(data.get("is_english")):
        return True
    lang = str(data.get("language") or data.get("folder_label") or "").strip().lower()
    if lang in {"english", "en"}:
        return True
    audio = str(data.get("audio") or data.get("audio_filename") or "").lower()
    return "english" in audio or audio.startswith("en_") or "_en_" in audio


def _flags_from_gt_file(filename: str) -> dict[str, bool]:
    name = str(filename or "")
    kind = classify_upload(name)
    ext = _extension(name)
    bundle = is_bundle_ground_truth(name)
    has_json = ext in JSON_EXTENSIONS
    has_soap = is_soap_gt(name) or bundle
    has_translation = is_translation_gt(name) or bundle
    has_transcript = (
        kind in {KIND_TRANSCRIPT, KIND_PDF, KIND_JSON, KIND_EXCEL}
        or bundle
        or (kind not in {KIND_AUDIO, KIND_UNKNOWN, KIND_SOAP, KIND_TRANSLATION})
    )
    if kind == KIND_SOAP:
        has_transcript = False
    if kind == KIND_TRANSLATION:
        has_transcript = False
    if bundle:
        has_transcript = True
        has_soap = True
        has_translation = True
    return {
        "has_transcript_ground_truth": has_transcript,
        "has_transcript": has_transcript,
        "has_translation_ground_truth": has_translation,
        "has_soap_ground_truth": has_soap,
        "has_summary_ground_truth": has_soap,
        "has_json_ground_truth": has_json,
        "has_json_applicable": has_json,
    }


def _or_flags(base: dict[str, bool], extra: dict[str, bool]) -> dict[str, bool]:
    out = dict(base)
    for key, val in extra.items():
        out[key] = bool(out.get(key) or val)
    return out


def soap_has_content(soap: Any) -> bool:
    """True when SOAP GT has any non-empty leaf — empty dicts do not count."""
    if soap is None:
        return False
    if isinstance(soap, str):
        return bool(soap.strip())
    if isinstance(soap, dict):
        return any(soap_has_content(value) for value in soap.values())
    if isinstance(soap, (list, tuple)):
        return any(soap_has_content(value) for value in soap)
    return bool(soap)


def prune_soap(soap: Any) -> Any:
    """Drop blank SOAP leaves so a cleared form does not store fake empty GT."""
    if soap is None:
        return None
    if isinstance(soap, str):
        return soap.strip() or None
    if isinstance(soap, dict):
        out = {}
        for key, value in soap.items():
            pruned = prune_soap(value)
            if pruned not in (None, "", {}, []):
                out[key] = pruned
        return out or None
    if isinstance(soap, (list, tuple)):
        items = [prune_soap(value) for value in soap]
        items = [item for item in items if item not in (None, "", {}, [])]
        return items or None
    return soap


def get_soap_at_path(obj: Any, path: str) -> Any:
    node = obj
    for key in str(path or "").split("."):
        if not key:
            continue
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def set_soap_at_path(obj: dict, path: str, value: Any) -> dict:
    keys = [key for key in str(path or "").split(".") if key]
    if not keys:
        return obj
    node = obj
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[keys[-1]] = value
    return obj


def soap_editor_fields(template: dict | None = None) -> list[dict[str, str]]:
    """Leaf fields of SOAP_CONSULT_TEMPLATE — same list the form renders."""
    fields: list[dict[str, str]] = []

    def walk(node: Any, path: str, section: str) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                walk(val, f"{path}.{key}" if path else key, section or key)
            return
        leaf = path.split(".")[-1] if path else ""
        fields.append({
            "path": path,
            "section": section,
            "label": leaf.replace("_", " "),
        })

    walk(template if template is not None else SOAP_CONSULT_TEMPLATE, "", "")
    return fields


def _norm_name(value: Any) -> str:
    return re.sub(r"[\s_-]+", " ", str(value or "").strip().lower())


def _display_field_name(label: str) -> str:
    text = str(label or "").replace("_", " ").strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def _snake_field(name: str) -> str:
    return re.sub(r"[\s-]+", "_", str(name or "").strip()).lower()


def normalize_soap_section(section: Any) -> str | None:
    raw = str(section or "").strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower in SOAP_JSON_SECTION_KEYS:
        return lower
    for key, label in SOAP_JSON_SECTION_KEYS.items():
        if label.lower() == lower:
            return key
    return None


def _template_field_index(
    template: dict | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for field in soap_editor_fields(template):
        section_key = "plan" if field["section"] == "summary" else field["section"]
        leaf = field["path"].split(".")[-1] if field["path"] else ""
        names = {_norm_name(field["label"]), _norm_name(leaf)}
        rows.append({
            "path": field["path"],
            "section_key": section_key,
            "label": field["label"],
            "names": names,
        })
    return rows


def _lookup_template_field(
    section_key: str,
    field_name: str,
    template: dict | None = None,
) -> dict[str, Any] | None:
    want = _norm_name(field_name)
    if not want:
        return None
    for row in _template_field_index(template):
        if row["section_key"] != section_key:
            continue
        if want in row["names"]:
            return row
    return None


def _is_template_prefix(path: str, template: dict | None = None) -> bool:
    """True when path is an intermediate object in the form schema."""
    prefix = f"{path}."
    for field in soap_editor_fields(template):
        if field["path"].startswith(prefix):
            return True
    return False


def _extra_field_path(section_key: str, field_name: str, template: dict | None = None) -> str:
    leaf = _snake_field(field_name) or "field"
    path = f"{section_key}.{leaf}"
    if _lookup_template_field(section_key, field_name, template):
        return path
    if _is_template_prefix(path, template):
        return f"{path}_extra"
    return path


def _json_syntax_error(exc: json.JSONDecodeError) -> str:
    return (
        f"JSON syntax error at line {exc.lineno}, column {exc.colno}: {exc.msg}"
    )


def _fact_value_text(value: Any, index: int) -> tuple[str | None, str | None]:
    if value is None:
        return None, f"facts[{index}].value is required (got null)"
    if isinstance(value, (dict, list)):
        return None, (
            f"facts[{index}].value must be a string or number, not "
            f"{'object' if isinstance(value, dict) else 'array'}"
        )
    if isinstance(value, bool):
        return ("true" if value else "false"), None
    return str(value), None


def facts_to_nested_soap(
    facts: list | None,
    template: dict | None = None,
) -> dict[str, Any]:
    """Map the flat fact-list onto the same nested SOAP the form writes."""
    errors: list[str] = []
    warnings: list[str] = []
    soap: dict[str, Any] = {}
    criticality_by_path: dict[str, str] = {}
    seen_paths: dict[str, int] = {}

    if facts is None:
        errors.append("'facts' must be an array (got null)")
        return {
            "ok": False,
            "soap": None,
            "errors": errors,
            "warnings": warnings,
            "criticality_by_path": criticality_by_path,
        }
    if not isinstance(facts, list):
        kind = type(facts).__name__
        errors.append(f"'facts' must be an array (got {kind})")
        return {
            "ok": False,
            "soap": None,
            "errors": errors,
            "warnings": warnings,
            "criticality_by_path": criticality_by_path,
        }

    for index, raw in enumerate(facts):
        prefix = f"facts[{index}]"
        if not isinstance(raw, dict):
            kind = type(raw).__name__
            errors.append(f"{prefix} must be an object with section, field, and value (got {kind})")
            continue

        missing = [key for key in ("section", "field", "value") if key not in raw]
        if missing:
            errors.append(
                f"{prefix} is missing required key{'' if len(missing) == 1 else 's'} "
                + ", ".join(repr(key) for key in missing)
            )
            continue

        section_raw = raw.get("section")
        field_raw = raw.get("field")
        if not isinstance(section_raw, str) or not section_raw.strip():
            errors.append(f"{prefix}.section must be a non-empty string")
            continue
        if not isinstance(field_raw, str) or not field_raw.strip():
            errors.append(f"{prefix}.field must be a non-empty string")
            continue

        section_key = normalize_soap_section(section_raw)
        if section_key is None:
            allowed = ", ".join(SOAP_JSON_SECTIONS)
            errors.append(
                f"{prefix}.section {section_raw!r} is not a known SOAP section "
                f"({allowed})"
            )
            continue

        value_text, value_error = _fact_value_text(raw.get("value"), index)
        if value_error:
            errors.append(value_error)
            continue

        known = _lookup_template_field(section_key, field_raw, template)
        if known:
            path = known["path"]
        else:
            path = _extra_field_path(section_key, field_raw, template)
            warnings.append(
                f"{prefix}.field {field_raw!r} is not a SOAP_CONSULT_TEMPLATE "
                f"field in {SOAP_JSON_SECTION_KEYS[section_key]}; it will be "
                "stored with the other SOAP ground truth but is not shown in the form"
            )

        if path in seen_paths:
            warnings.append(
                f"{prefix} overwrites {seen_paths[path]} for "
                f"{SOAP_JSON_SECTION_KEYS[section_key]} / {field_raw}"
            )
        seen_paths[path] = prefix
        set_soap_at_path(soap, path, value_text)

        crit = raw.get("criticality")
        if crit is None or (isinstance(crit, str) and not crit.strip()):
            criticality_by_path[path] = SOAP_DEFAULT_CRITICALITY
        elif isinstance(crit, str):
            criticality_by_path[path] = crit.strip()
            if crit.strip() != SOAP_DEFAULT_CRITICALITY:
                warnings.append(
                    f"{prefix}.criticality {crit.strip()!r} is not a form field. "
                    "Switching to Form keeps the SOAP value; explicit criticality "
                    "is kept only in this editor session and is not stored on the "
                    "form Ground Truth path (the scorer uses catalog defaults)"
                )
        else:
            errors.append(f"{prefix}.criticality must be a string when present")

    if errors:
        return {
            "ok": False,
            "soap": None,
            "errors": errors,
            "warnings": warnings,
            "criticality_by_path": {},
        }
    return {
        "ok": True,
        "soap": prune_soap(soap),
        "errors": [],
        "warnings": warnings,
        "criticality_by_path": criticality_by_path,
    }


def _normalize_nested_root_key(key: Any) -> str | None:
    raw = str(key or "").strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower in SOAP_NESTED_ROOTS:
        return lower
    return normalize_soap_section(raw)


def _looks_like_nested_consult(payload: dict) -> bool:
    return any(_normalize_nested_root_key(key) for key in payload)


def soap_has_structured_leaves(soap: Any) -> bool:
    """True when SOAP has a list (e.g. plan.medications) facts flattening would drop."""
    if isinstance(soap, list):
        return True
    if isinstance(soap, dict):
        return any(soap_has_structured_leaves(value) for value in soap.values())
    return False


def _nested_consult_warnings(
    soap: Any,
    template: dict | None = None,
) -> list[str]:
    warnings: list[str] = []
    template_paths = {field["path"] for field in soap_editor_fields(template)}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, list):
            if path:
                warnings.append(
                    f"{path} is a structured list and is not shown in the form; "
                    "it is kept in JSON and will be saved"
                )
            return
        if not isinstance(node, dict):
            if (
                path
                and path not in template_paths
                and not any(path.startswith(f"{known}.") for known in template_paths)
            ):
                leaf = path.split(".")[-1]
                warnings.append(
                    f"Field {leaf!r} at {path} is not a SOAP_CONSULT_TEMPLATE form "
                    "field; it is kept in JSON and will be saved, but is not shown "
                    "in the form"
                )
            return
        if path in template_paths:
            warnings.append(
                f"{path} is a structured object and is not shown in the form; "
                "it is kept in JSON and will be saved"
            )
            return
        for key, val in node.items():
            walk(val, f"{path}.{key}" if path else key)

    if isinstance(soap, dict):
        walk(soap, "")
    return warnings


def parse_nested_consult_json(
    payload: dict,
    template: dict | None = None,
) -> dict[str, Any]:
    """Accept the nested SOAP_CONSULT_TEMPLATE object the pipeline already emits."""
    errors: list[str] = []
    warnings: list[str] = []
    soap: dict[str, Any] = {}

    if not isinstance(payload, dict):
        return {
            "ok": False,
            "soap": None,
            "errors": ["Nested SOAP JSON must be an object"],
            "warnings": [],
        }

    for key, value in payload.items():
        mapped = _normalize_nested_root_key(key)
        if mapped is None:
            warnings.append(
                f"Top-level key {key!r} is not a known SOAP section "
                f"({', '.join(SOAP_JSON_SECTIONS)}, or summary); it will be stored "
                "but is not shown in the form"
            )
            soap[str(key)] = value
            continue

        expected = SOAP_NESTED_ROOTS[mapped]
        if expected == "string":
            if value is None or value == "":
                continue
            if isinstance(value, (dict, list)):
                kind = "object" if isinstance(value, dict) else "array"
                errors.append(f"'{mapped}' must be a string, not {kind}")
                continue
            soap[mapped] = str(value)
            continue

        if value is None or value == "":
            continue
        if not isinstance(value, dict):
            kind = type(value).__name__
            errors.append(
                f"'{mapped}' must be an object with field keys (got {kind})"
            )
            continue
        soap[mapped] = value

    if errors:
        return {
            "ok": False,
            "soap": None,
            "errors": errors,
            "warnings": warnings,
        }

    pruned = prune_soap(soap)
    warnings.extend(_nested_consult_warnings(pruned, template))
    return {
        "ok": True,
        "soap": pruned,
        "errors": [],
        "warnings": warnings,
    }


def nested_soap_to_facts(
    soap: Any,
    criticality_by_path: dict[str, str] | None = None,
    template: dict | None = None,
) -> dict[str, Any]:
    """Form (or stored) nested SOAP → flat fact-list. Extra keys are kept and flagged."""
    facts: list[dict[str, Any]] = []
    warnings: list[str] = []
    crit_map = criticality_by_path or {}
    seen: set[str] = set()
    schema = template if template is not None else SOAP_CONSULT_TEMPLATE

    for field in soap_editor_fields(schema):
        value = get_soap_at_path(soap, field["path"])
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if isinstance(value, (dict, list)):
            continue
        section_key = "plan" if field["section"] == "summary" else field["section"]
        section_label = SOAP_JSON_SECTION_KEYS.get(section_key) or _display_field_name(
            field["section"]
        )
        path = field["path"]
        seen.add(path)
        facts.append({
            "section": section_label,
            "field": _display_field_name(field["label"]),
            "value": str(value),
            "criticality": crit_map.get(path) or SOAP_DEFAULT_CRITICALITY,
        })

    def walk(node: Any, path: str, section_key: str) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                walk(val, f"{path}.{key}" if path else key, section_key or key)
            return
        if not path or path in seen:
            return
        if node is None or (isinstance(node, str) and not str(node).strip()):
            return
        if isinstance(node, list):
            warnings.append(
                f"{path} is a structured list and cannot be shown as a form field; "
                "switch stays in nested JSON so the list is not dropped"
            )
            return
        if isinstance(node, dict):
            return
        root = path.split(".", 1)[0]
        mapped = "plan" if root == "summary" else root
        section_label = SOAP_JSON_SECTION_KEYS.get(mapped) or _display_field_name(mapped)
        leaf = path.split(".")[-1]
        facts.append({
            "section": section_label,
            "field": _display_field_name(leaf.replace("_", " ")),
            "value": str(node),
            "criticality": crit_map.get(path) or SOAP_DEFAULT_CRITICALITY,
        })
        warnings.append(
            f"Field {leaf!r} at {path} is not a SOAP_CONSULT_TEMPLATE form field; "
            "it is kept in JSON and will be saved, but is not shown in the form"
        )

    if isinstance(soap, dict):
        walk(soap, "", "")
    return {"facts": facts, "warnings": warnings}


def parse_soap_facts_json(text: str, template: dict | None = None) -> dict[str, Any]:
    """Validate SOAP JSON and convert it to the form's nested SOAP object.

    Empty / whitespace-only input is valid and means no SOAP GT (same as a
    blank form). Failures return errors and soap=None — callers must not save.
    """
    empty = {
        "ok": True,
        "soap": None,
        "facts": [],
        "errors": [],
        "warnings": [],
        "criticality_by_path": {},
        "document": {"facts": []},
        "shape": "facts",
    }
    raw = "" if text is None else str(text)
    if not raw.strip():
        return empty
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "soap": None,
            "facts": [],
            "errors": [_json_syntax_error(exc)],
            "warnings": [],
            "criticality_by_path": {},
            "document": None,
        }

    if isinstance(payload, list):
        return {
            "ok": False,
            "soap": None,
            "facts": [],
            "errors": [
                "JSON must be an object: either {\"facts\": [...]} or a nested "
                "SOAP object with subjective/objective/assessment/plan. "
                "A top-level array is not accepted."
            ],
            "warnings": [],
            "criticality_by_path": {},
            "document": None,
            "shape": None,
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "soap": None,
            "facts": [],
            "errors": [
                "JSON must be an object with a 'facts' array or nested SOAP "
                f"keys (got {type(payload).__name__})"
            ],
            "warnings": [],
            "criticality_by_path": {},
            "document": None,
            "shape": None,
        }
    if "facts" not in payload:
        if _looks_like_nested_consult(payload):
            nested = parse_nested_consult_json(payload, template)
            if not nested["ok"]:
                return {
                    "ok": False,
                    "soap": None,
                    "facts": [],
                    "errors": nested["errors"],
                    "warnings": nested["warnings"],
                    "criticality_by_path": {},
                    "document": None,
                    "shape": "nested",
                }
            return {
                "ok": True,
                "soap": nested["soap"],
                "facts": [],
                "errors": [],
                "warnings": nested["warnings"],
                "criticality_by_path": {},
                "document": nested["soap"],
                "shape": "nested",
            }
        return {
            "ok": False,
            "soap": None,
            "facts": [],
            "errors": [
                "JSON is missing required key 'facts' and has no nested SOAP "
                "sections. Use {\"facts\": [{\"section\": \"...\", \"field\": "
                "\"...\", \"value\": \"...\"}]} or "
                "{\"subjective\": {...}, \"objective\": {...}, "
                "\"assessment\": {...}, \"plan\": {...}}."
            ],
            "warnings": [],
            "criticality_by_path": {},
            "document": None,
            "shape": None,
        }

    converted = facts_to_nested_soap(payload.get("facts"), template)
    if not converted["ok"]:
        return {
            "ok": False,
            "soap": None,
            "facts": [],
            "errors": converted["errors"],
            "warnings": converted["warnings"],
            "criticality_by_path": {},
            "document": None,
        }
    facts_doc = nested_soap_to_facts(
        converted["soap"],
        converted["criticality_by_path"],
        template,
    )
    return {
        "ok": True,
        "soap": converted["soap"],
        "facts": payload.get("facts") if isinstance(payload.get("facts"), list) else [],
        "errors": [],
        "warnings": converted["warnings"],
        "criticality_by_path": converted["criticality_by_path"],
        "document": {"facts": facts_doc["facts"]},
        "shape": "facts",
    }


def soap_to_facts_json(
    soap: Any,
    criticality_by_path: dict[str, str] | None = None,
    template: dict | None = None,
    *,
    prefer_nested: bool = False,
) -> dict[str, Any]:
    """Form nested SOAP → pretty JSON text. Extra leaves are kept and flagged.

    Structured values (medication lists) are written back as nested SOAP so
    Form ↔ JSON does not drop them. Simple string-only SOAP stays a fact list.
    """
    if prefer_nested or soap_has_structured_leaves(soap):
        pruned = prune_soap(soap)
        document = pruned if isinstance(pruned, dict) else {}
        return {
            "text": json.dumps(document, indent=2, ensure_ascii=False),
            "document": document,
            "warnings": _nested_consult_warnings(pruned, template),
            "shape": "nested",
        }
    converted = nested_soap_to_facts(soap, criticality_by_path, template)
    document = {"facts": converted["facts"]}
    return {
        "text": json.dumps(document, indent=2, ensure_ascii=False),
        "document": document,
        "warnings": converted["warnings"],
        "shape": "facts",
    }


def apply_manual_soap_json(
    item: dict | None,
    json_text: str,
    transcription: str = "",
    translation: str = "",
) -> dict[str, Any]:
    """Save JSON SOAP through apply_manual_ground_truth. Invalid JSON saves nothing."""
    parsed = parse_soap_facts_json(json_text)
    if not parsed["ok"]:
        return {
            "ok": False,
            "errors": parsed["errors"],
            "warnings": parsed["warnings"],
            "item": item,
        }
    updated = apply_manual_ground_truth(
        item,
        {
            "transcription": transcription,
            "translation": translation,
            "soap": parsed["soap"],
        },
    )
    return {
        "ok": True,
        "errors": [],
        "warnings": parsed["warnings"],
        "item": updated,
    }


def normalize_manual_gt(fields: dict | None) -> dict[str, Any]:
    """Persist only filled fields. Empty string after save means that field is gone.

    Nested SOAP and the flat ``facts`` list are the same GT: a top-level
    ``facts`` array is stored as ``soap.facts`` so the scorer has one shape.
    """
    data = fields or {}
    transcription = str(data.get("transcription") or "").strip()
    translation = str(data.get("translation") or "").strip()
    soap = prune_soap(data.get("soap"))
    raw_facts = data.get("facts")
    if isinstance(raw_facts, list) and raw_facts:
        facts = prune_soap(raw_facts)
        if facts:
            if not isinstance(soap, dict):
                soap = {}
            else:
                soap = dict(soap)
            if not soap.get("facts"):
                soap["facts"] = facts
    return {
        "transcription": transcription,
        "translation": translation,
        "soap": soap,
    }


def has_manual_gt(item: dict | None) -> bool:
    mg = normalize_manual_gt((item or {}).get("manual_gt"))
    return bool(mg["transcription"] or mg["translation"] or soap_has_content(mg.get("soap")))


def gt_display_label(item: dict | None) -> str:
    """Filename, Manual badge, or em dash — never a mix of the three."""
    data = item or {}
    if has_manual_gt(data):
        return "Manual"
    name = str(data.get("ground_truth_filename") or "").strip()
    return name or "—"


def _incoming_file_flags(item: dict) -> dict[str, bool]:
    return {
        "has_transcript_ground_truth": _truthy(item.get("has_transcript_ground_truth"))
        or _truthy(item.get("has_transcript"))
        or _truthy(item.get("has_ground_truth")),
        "has_transcript": _truthy(item.get("has_transcript"))
        or _truthy(item.get("has_transcript_ground_truth"))
        or _truthy(item.get("has_ground_truth")),
        "has_translation_ground_truth": _truthy(item.get("has_translation_ground_truth")),
        "has_soap_ground_truth": _truthy(item.get("has_soap_ground_truth"))
        or _truthy(item.get("has_summary_ground_truth")),
        "has_summary_ground_truth": _truthy(item.get("has_summary_ground_truth"))
        or _truthy(item.get("has_soap_ground_truth")),
        "has_json_ground_truth": _truthy(item.get("has_json_ground_truth")),
        "has_json_applicable": _truthy(item.get("has_json_applicable"))
        or _truthy(item.get("has_json_ground_truth")),
    }


def _snapshot_file_flags(flags: dict[str, bool]) -> dict[str, bool]:
    return {key: bool(flags.get(key)) for key in FILE_FLAG_KEYS}


def merge_manual_gt(item: dict | None) -> dict:
    """OR typed GT onto file/Drive flags. Clearing a field drops that overlay."""
    data = dict(item or {})
    file_flags = _snapshot_file_flags(data.get("_file_flags") or _incoming_file_flags(data))
    data["_file_flags"] = file_flags
    mg = normalize_manual_gt(data.get("manual_gt"))
    data["manual_gt"] = mg

    flags = dict(file_flags)
    flags["has_transcript_ground_truth"] = flags["has_transcript_ground_truth"] or bool(
        mg["transcription"]
    )
    flags["has_transcript"] = flags["has_transcript_ground_truth"]
    flags["has_translation_ground_truth"] = flags["has_translation_ground_truth"] or bool(
        mg["translation"]
    )
    flags["has_soap_ground_truth"] = flags["has_soap_ground_truth"] or soap_has_content(
        mg.get("soap")
    )
    flags["has_summary_ground_truth"] = flags["has_soap_ground_truth"]
    data.update(flags)

    if mg["transcription"]:
        data["ground_truth"] = mg["transcription"]
        data["ground_truth_transcription"] = mg["transcription"]
        data["_manual_wrote_transcript"] = True
    elif data.pop("_manual_wrote_transcript", False):
        data["ground_truth"] = ""
        data["ground_truth_transcription"] = ""

    if mg["translation"]:
        data["translation_ground_truth"] = mg["translation"]
        data["_manual_wrote_translation"] = True
    elif data.pop("_manual_wrote_translation", False):
        data["translation_ground_truth"] = ""

    if soap_has_content(mg.get("soap")):
        data["soap_ground_truth"] = mg["soap"]
        data["_manual_wrote_soap"] = True
    elif data.pop("_manual_wrote_soap", False):
        data["soap_ground_truth"] = None

    status = completeness_status(data)
    data["gt_status"] = status
    data["gt_status_label"] = STATUS_LABELS[status]
    return data


def apply_manual_ground_truth(item: dict | None, fields: dict | None) -> dict:
    """Save typed GT (independently optional fields) into the shared GT model."""
    base = attach_ground_truths(item, [])
    base["manual_gt"] = normalize_manual_gt(fields)
    return merge_manual_gt(base)


def scoring_overlay(item: dict | None) -> dict[str, Any]:
    """Same fields the runner/scorer already reads for uploaded/Drive GT."""
    mg = normalize_manual_gt((item or {}).get("manual_gt"))
    out: dict[str, Any] = {}
    if mg["transcription"]:
        out["ground_truth"] = mg["transcription"]
        out["ground_truth_transcription"] = mg["transcription"]
        out["has_transcript_ground_truth"] = True
        out["has_transcript"] = True
        out["has_ground_truth"] = True
        out["ground_truth_source"] = "upload"
    if mg["translation"]:
        out["translation_ground_truth"] = mg["translation"]
        out["has_translation_ground_truth"] = True
    if soap_has_content(mg.get("soap")):
        out["soap_ground_truth"] = mg["soap"]
        out["has_soap_ground_truth"] = True
        out["has_summary_ground_truth"] = True
    return out


def coerce_manual_gt(override: dict | None) -> dict | None:
    if not override:
        return None
    if any(key in override for key in ("transcription", "translation", "soap", "facts")):
        return override
    inner = override.get("manual_gt")
    return inner if isinstance(inner, dict) else None


def apply_gt_override_to_loaded(
    transcript: str | None,
    translation: str | None,
    soap: Any,
    override: dict | None,
) -> tuple[str, str | None, Any, bool]:
    """Non-empty typed fields replace Drive-loaded GT. Empty overlay does not wipe Drive."""
    overlay = scoring_overlay({"manual_gt": coerce_manual_gt(override)})
    next_transcript = transcript or ""
    next_translation = translation
    next_soap = soap
    if overlay.get("ground_truth_transcription"):
        next_transcript = overlay["ground_truth_transcription"]
    if overlay.get("translation_ground_truth"):
        next_translation = overlay["translation_ground_truth"]
    if overlay.get("soap_ground_truth") is not None:
        next_soap = overlay["soap_ground_truth"]
    return next_transcript, next_translation, next_soap, bool(overlay)


def attach_selection_overrides(
    cases: list[dict] | None,
    selected: list[dict] | None,
) -> list[dict]:
    """Copy manual_gt from the run payload onto Drive-discovered cases."""
    selected_rows = selected or []
    by_full = {audio_file_key(row): row for row in selected_rows}
    by_audio = {
        audio_file_key(row)[1]: row
        for row in selected_rows
        if audio_file_key(row)[1]
    }
    out = []
    for case in cases or []:
        row = dict(case)
        match = by_full.get(audio_file_key(row)) or by_audio.get(audio_file_key(row)[1])
        if match and match.get("manual_gt"):
            row["manual_gt"] = match["manual_gt"]
        out.append(row)
    return out


def attach_ground_truths(audio_item: dict | None, gt_files: list[dict] | None) -> dict:
    """Filename-stem match, same rule as Drive. Unmatched GT → em dash / Missing GT (All)."""
    item = dict(audio_item or {})
    audio_name = str(item.get("audio") or item.get("audio_filename") or "")
    audio_key = match_key(audio_name)
    matched: list[dict] = []
    for gt in gt_files or []:
        name = str(gt.get("filename") or gt.get("name") or gt.get("audio") or "")
        if classify_upload(name) in {KIND_AUDIO, KIND_UNKNOWN}:
            continue
        if gt_match_key(name) == audio_key and audio_key:
            matched.append({**gt, "filename": name})

    if isinstance(item.get("_file_flags"), dict):
        flags = _snapshot_file_flags(item["_file_flags"])
    else:
        flags = _incoming_file_flags(item)

    names: list[str] = []
    for key in (
        "ground_truth_filename",
        "transcript_filename",
        "soap_gt_filename",
        "translation_gt_filename",
        "json_gt_filename",
    ):
        existing = str(item.get(key) or "").strip()
        if existing:
            names.append(existing)
            flags = _or_flags(flags, _flags_from_gt_file(existing))

    for gt in matched:
        name = gt["filename"]
        names.append(name)
        flags = _or_flags(flags, _flags_from_gt_file(name))

    display = _primary_gt_filename(names)
    item["_file_flags"] = _snapshot_file_flags(flags)
    item.update(item["_file_flags"])
    item["ground_truth_filename"] = display
    item["matched_gt_filenames"] = names
    return merge_manual_gt(item)


def _primary_gt_filename(names: list[str]) -> str:
    if not names:
        return ""
    unique: list[str] = []
    seen = set()
    for name in names:
        key = name.strip()
        if not key or key.lower() in seen:
            continue
        seen.add(key.lower())
        unique.append(key)
    if not unique:
        return ""

    def rank(name: str) -> tuple[int, int]:
        ext = _extension(name)
        kind = classify_upload(name)
        if is_bundle_ground_truth(name):
            return (0, 0)
        if ext in JSON_EXTENSIONS:
            return (1, 0)
        if ext in EXCEL_EXTENSIONS:
            return (2, 0)
        if kind == KIND_TRANSCRIPT:
            return (3, 0)
        if ext in PDF_EXTENSIONS:
            return (4, 0)
        if kind == KIND_SOAP:
            return (5, 0)
        return (6, 0)

    unique.sort(key=rank)
    return unique[0]


def completeness_status(item: dict | None) -> str:
    """One STATUS pill from the has_*_ground_truth flags. Never a hardcoded label."""
    data = item or {}
    if upload_needs_language(data):
        return STATUS_MISSING_LANGUAGE
    mg = normalize_manual_gt(data.get("manual_gt"))
    has_transcript = (
        _truthy(data.get("has_transcript_ground_truth"))
        or _truthy(data.get("has_transcript"))
        or _truthy(data.get("has_ground_truth"))
        or bool(mg["transcription"])
    )
    has_translation = _truthy(data.get("has_translation_ground_truth")) or bool(
        mg["translation"]
    )
    has_soap = (
        _truthy(data.get("has_soap_ground_truth"))
        or _truthy(data.get("has_summary_ground_truth"))
        or soap_has_content(mg.get("soap"))
    )
    has_json = _truthy(data.get("has_json_ground_truth"))
    json_applicable = _truthy(data.get("has_json_applicable")) or has_json
    has_any = has_transcript or has_translation or has_soap or has_json or bool(
        str(data.get("ground_truth_filename") or "").strip()
    ) or has_manual_gt(data)
    if not has_any:
        return STATUS_MISSING_GT_ALL
    english = is_english_case(data)
    if english:
        has_translation = True
    if not has_transcript:
        return STATUS_MISSING_TRANSCRIPT
    if json_applicable and not has_json:
        return STATUS_MISSING_JSON
    if not has_translation:
        return STATUS_MISSING_TRANSLATION
    if not has_soap:
        return STATUS_MISSING_SOAP
    return STATUS_COMPLETE


def format_duration_mmss(seconds: Any) -> str:
    """mm:ss for the selected-files table. Same seconds source as Latency Audio Length."""
    if seconds is None or seconds == "":
        return "—"
    try:
        n = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    total = int(round(n))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def duration_seconds_for_item(item: dict | None, results: list[dict] | None = None) -> Any:
    """Reuse Latency's audio_length / audio_duration_seconds. Do not decode audio."""
    data = item or {}
    for key in ("duration", "audio_duration_seconds", "audio_length"):
        val = data.get(key)
        if val not in (None, "", 0, "0"):
            return val
    audio = str(data.get("audio") or data.get("audio_filename") or "").strip().lower()
    if not audio:
        return None
    for row in results or []:
        name = str(row.get("audio_filename") or row.get("filename") or "").strip().lower()
        if name != audio:
            continue
        picked = pick_transcribe_time(row, "audio_length")
        if picked is not None:
            return picked
        dur = row.get("audio_duration_seconds")
        if dur not in (None, "", 0, "0"):
            return dur
    return None


def selected_files_table_row(
    item: dict | None,
    index: int,
    results: list[dict] | None = None,
) -> dict[str, Any]:
    """One selected-files table row. STATUS is computed, never a static string."""
    enriched = attach_ground_truths(item, [])
    display = gt_display_label(enriched)
    status = completeness_status(enriched)
    seconds = duration_seconds_for_item(enriched, results)
    return {
        "index": index,
        "audio_file": str(enriched.get("audio") or enriched.get("audio_filename") or ""),
        "duration": format_duration_mmss(seconds),
        "ground_truth": display,
        "enter_manually": display == "—",
        "has_manual_gt": has_manual_gt(enriched),
        "status": status,
        "status_label": STATUS_LABELS[status],
        "catalog_id": catalog_id(enriched),
        "source": item_source(enriched),
        "language": str(enriched.get("language") or ""),
        "needs_language": upload_needs_language(enriched),
    }
