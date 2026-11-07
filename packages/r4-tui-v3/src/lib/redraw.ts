/**
 * Ctrl+L redraw (Claude-Code parity). The chat panel registers the renderer's
 * repaint; the global keymap calls it. A same-size resize makes OpenTUI drop
 * its diff buffers and paint the whole frame (recovers a garbled terminal).
 */

type Redraw = () => void;

let registered: Redraw | null = null;

export function registerRedraw(fn: Redraw | null): void {
  registered = fn;
}

export function redrawScreen(): boolean {
  if (!registered) return false;
  registered();
  return true;
}
