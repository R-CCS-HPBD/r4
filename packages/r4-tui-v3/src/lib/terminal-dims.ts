/**
 * One terminal-dimensions signal per renderer (stabilization 2026-10-21).
 *
 * OpenTUI's useTerminalDimensions() registers a `renderer.on("resize")`
 * listener per CALLING COMPONENT INSTANCE. Per-row components (every chat
 * message cell, tool row, diff block) called it, so the listener count grew
 * with the transcript: Node printed "MaxListenersExceededWarning: 11 resize
 * listeners added to CliRenderer" after a dozen messages, and every resize
 * fanned out to all of them. Here the first caller for a renderer subscribes
 * ONCE and every later caller shares the same accessor; the subscription
 * lives as long as the renderer (keyed weakly, so a destroyed test renderer
 * takes it along).
 */

import { createSignal, type Accessor } from "solid-js";
import { useRenderer } from "@opentui/solid";

export type TerminalDims = { width: number; height: number };

type ResizeSource = {
  width: number;
  height: number;
  on(event: "resize", listener: (width: number, height: number) => void): unknown;
};

const shared = new WeakMap<object, Accessor<TerminalDims>>();

/** The shared dimensions accessor for `renderer` (subscribes on first use). */
export function terminalDimsFor(renderer: ResizeSource): Accessor<TerminalDims> {
  const known = shared.get(renderer);
  if (known) return known;
  const [dims, setDims] = createSignal<TerminalDims>({ width: renderer.width, height: renderer.height });
  renderer.on("resize", (width, height) => setDims({ width, height }));
  shared.set(renderer, dims);
  return dims;
}

/** Drop-in for useTerminalDimensions() that adds no listener per caller. */
export function useTerminalDims(): Accessor<TerminalDims> {
  return terminalDimsFor(useRenderer() as unknown as ResizeSource);
}
