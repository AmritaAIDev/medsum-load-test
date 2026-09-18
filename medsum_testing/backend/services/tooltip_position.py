"""Collision-aware tooltip placement. Viewport coordinates (position: fixed).

Prefers opening above the anchor. If that would clip past y=0 (or leave less
space than below), flips below. Horizontal position is clamped into the viewport.
"""

from __future__ import annotations

from typing import Any

GAP = 8
PAD = 8
TOOLTIP_Z_INDEX = 10000
PREFERRED = "top"


def place_tooltip(
    anchor: dict[str, float],
    size: dict[str, float],
    viewport: dict[str, float],
    prefer: str = PREFERRED,
) -> dict[str, Any]:
    """Return {top, left, placement, z_index} in viewport pixels.

    `anchor` uses getBoundingClientRect fields: top, left, width, height, bottom.
    `size` is the tooltip box. `viewport` is {width, height}.
    """
    tw = float(size.get("width") or 0)
    th = float(size.get("height") or 0)
    vw = max(1.0, float(viewport.get("width") or 1))
    vh = max(1.0, float(viewport.get("height") or 1))
    at = float(anchor.get("top") or 0)
    al = float(anchor.get("left") or 0)
    aw = float(anchor.get("width") or 0)
    ah = float(anchor.get("height") or 0)
    ab = float(anchor.get("bottom") if anchor.get("bottom") is not None else at + ah)

    space_above = at - PAD
    space_below = vh - PAD - ab
    want_top = (prefer or PREFERRED) == "top"
    if want_top:
        placement = "top" if space_above >= th + GAP or space_above >= space_below else "bottom"
    else:
        placement = "bottom" if space_below >= th + GAP or space_below >= space_above else "top"

    top = at - th - GAP if placement == "top" else ab + GAP
    top = max(PAD, min(top, vh - PAD - th)) if th + 2 * PAD <= vh else PAD
    if top < 0:
        top = 0

    left = al + aw / 2 - tw / 2
    left = max(PAD, min(left, vw - PAD - tw)) if tw + 2 * PAD <= vw else PAD
    if left < 0:
        left = 0

    return {
        "top": top,
        "left": left,
        "placement": placement,
        "z_index": TOOLTIP_Z_INDEX,
    }
