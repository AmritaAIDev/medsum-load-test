"""Results-table row actions. Remove never deletes source audio or saved JSON.

The only row action is Download. Click a results row to open the case.
Remove (if used via API) excludes the case from the current results view
and from that view's total report. It does not delete Google Drive files,
local uploads, or results/{id}.json.
"""

from __future__ import annotations

ROW_ACTIONS = ("Download",)

REMOVE_DOES_NOT_DELETE_SOURCE = True


def visible_results(rows: list[dict] | None, excluded_ids: set[str] | None) -> list[dict]:
    """Drop excluded ids from the view. Source files stay on disk / Drive."""
    drop = {str(i) for i in (excluded_ids or set()) if i}
    if not drop:
        return list(rows or [])
    out = []
    for row in rows or []:
        tid = str(row.get("test_id") or row.get("id") or "")
        if tid and tid in drop:
            continue
        out.append(row)
    return out
