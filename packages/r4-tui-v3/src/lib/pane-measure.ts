/**
 * Measured detail-pane bounds — the ONLY honest source for the pane's on
 * screen columns. The CSS/percentage prediction and the flexbox engine's
 * resolved width disagree (auto-basis content widths compress the pane),
 * which was the root cause of the terminal's wrapped-output overlap: the
 * PTY was sized by formula while the paint used a different width.
 *
 * DetailPane publishes its laid-out {x, width} every frame (cheap compare,
 * set only on change); drags and the terminal's own sizing read from here.
 */

import { createSignal } from "solid-js";

export interface PaneBounds {
  x: number;
  width: number;
}

const [bounds, setBounds] = createSignal<PaneBounds>({ x: 0, width: 0 });

export function paneBounds(): PaneBounds {
  return bounds();
}

/** Publish the laid-out pane bounds (idempotent on unchanged values). */
export function publishPaneBounds(x: number, width: number): void {
  const current = bounds();
  if (current.x !== x || current.width !== width) setBounds({ x, width });
}
