/**
 * Collision-aware tooltips. Content stays in the trigger; the visible copy
 * is portaled to document.body so overflow:hidden ancestors cannot clip it.
 */
(function (root) {
  const GAP = 8;
  const PAD = 8;
  const TOOLTIP_Z_INDEX = 10000;
  const PREFERRED = 'top';
  const TRIGGER = '[data-tooltip-trigger], .score-pill-wrapper, .stat-card-tip';

  function placeTooltip(anchor, size, viewport, prefer) {
    const tw = Number((size && size.width) || 0);
    const th = Number((size && size.height) || 0);
    const vw = Math.max(1, Number((viewport && viewport.width) || 1));
    const vh = Math.max(1, Number((viewport && viewport.height) || 1));
    const at = Number((anchor && anchor.top) || 0);
    const al = Number((anchor && anchor.left) || 0);
    const aw = Number((anchor && anchor.width) || 0);
    const ah = Number((anchor && anchor.height) || 0);
    const ab = anchor && anchor.bottom != null ? Number(anchor.bottom) : at + ah;

    const spaceAbove = at - PAD;
    const spaceBelow = vh - PAD - ab;
    const wantTop = (prefer || PREFERRED) === 'top';
    let placement;
    if (wantTop) {
      placement = (spaceAbove >= th + GAP || spaceAbove >= spaceBelow) ? 'top' : 'bottom';
    } else {
      placement = (spaceBelow >= th + GAP || spaceBelow >= spaceAbove) ? 'bottom' : 'top';
    }

    let top = placement === 'top' ? at - th - GAP : ab + GAP;
    if (th + 2 * PAD <= vh) {
      top = Math.max(PAD, Math.min(top, vh - PAD - th));
    } else {
      top = PAD;
    }
    if (top < 0) top = 0;

    let left = al + aw / 2 - tw / 2;
    if (tw + 2 * PAD <= vw) {
      left = Math.max(PAD, Math.min(left, vw - PAD - tw));
    } else {
      left = PAD;
    }
    if (left < 0) left = 0;

    return { top, left, placement, zIndex: TOOLTIP_Z_INDEX };
  }

  function ensureLayer() {
    let layer = document.getElementById('medsum-tooltip-layer');
    if (!layer) {
      layer = document.createElement('div');
      layer.id = 'medsum-tooltip-layer';
      layer.className = 'medsum-tooltip-layer';
      layer.hidden = true;
      const popup = document.createElement('div');
      popup.id = 'medsum-tooltip-popup';
      popup.className = 'reason-popup medsum-tooltip-popup';
      popup.setAttribute('role', 'tooltip');
      layer.appendChild(popup);
      document.body.appendChild(layer);
    }
    return layer;
  }

  function sourcePopup(trigger) {
    return trigger && trigger.querySelector
      ? trigger.querySelector('.reason-popup, [role="tooltip"]')
      : null;
  }

  let activeTrigger = null;
  let bound = false;

  function hideTooltip() {
    activeTrigger = null;
    const layer = document.getElementById('medsum-tooltip-layer');
    if (layer) layer.hidden = true;
  }

  function showFor(trigger) {
    const source = sourcePopup(trigger);
    if (!source || !source.innerHTML.trim()) return;
    const layer = ensureLayer();
    const popup = layer.querySelector('.medsum-tooltip-popup') || layer.firstElementChild;
    popup.innerHTML = source.innerHTML;
    layer.hidden = false;
    activeTrigger = trigger;
    popup.style.maxWidth = Math.min(360, Math.max(160, window.innerWidth - PAD * 2)) + 'px';
    popup.style.visibility = 'hidden';
    popup.style.display = 'block';
    positionActive();
    popup.style.visibility = '';
  }

  function positionActive() {
    if (!activeTrigger || !document.body.contains(activeTrigger)) {
      hideTooltip();
      return;
    }
    const layer = document.getElementById('medsum-tooltip-layer');
    const popup = layer && (layer.querySelector('.medsum-tooltip-popup') || layer.firstElementChild);
    if (!layer || layer.hidden || !popup) return;
    const rect = activeTrigger.getBoundingClientRect();
    if (!rect.width && !rect.height) {
      hideTooltip();
      return;
    }
    const pos = placeTooltip(
      rect,
      { width: popup.offsetWidth, height: popup.offsetHeight },
      { width: window.innerWidth, height: window.innerHeight },
      PREFERRED
    );
    popup.style.top = pos.top + 'px';
    popup.style.left = pos.left + 'px';
    popup.style.zIndex = String(pos.zIndex);
    popup.setAttribute('data-placement', pos.placement);
  }

  function onPointerOver(event) {
    const trigger = event.target.closest && event.target.closest(TRIGGER);
    if (!trigger) return;
    if (trigger === activeTrigger) return;
    showFor(trigger);
  }

  function onPointerOut(event) {
    if (!activeTrigger) return;
    const related = event.relatedTarget;
    if (related && activeTrigger.contains(related)) return;
    const leaving = event.target.closest && event.target.closest(TRIGGER);
    if (leaving !== activeTrigger) return;
    hideTooltip();
  }

  function onFocusIn(event) {
    const trigger = event.target.closest && event.target.closest(TRIGGER);
    if (trigger) showFor(trigger);
  }

  function onFocusOut(event) {
    if (!activeTrigger) return;
    const next = event.relatedTarget;
    if (next && activeTrigger.contains(next)) return;
    hideTooltip();
  }

  function bindTooltipPositioning() {
    if (bound) return;
    bound = true;
    ensureLayer();
    document.addEventListener('mouseover', onPointerOver);
    document.addEventListener('mouseout', onPointerOut);
    document.addEventListener('focusin', onFocusIn);
    document.addEventListener('focusout', onFocusOut);
    window.addEventListener('scroll', positionActive, true);
    window.addEventListener('resize', positionActive);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') hideTooltip();
    });
  }

  const api = {
    GAP,
    PAD,
    TOOLTIP_Z_INDEX,
    PREFERRED,
    placeTooltip,
    bindTooltipPositioning,
    hideTooltip,
    showFor,
    positionActive,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumTooltipPosition = api;
})(typeof window !== 'undefined' ? window : globalThis);
