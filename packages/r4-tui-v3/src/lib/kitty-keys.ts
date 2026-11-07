/**
 * Runtime truth about Shift+Enter delivery (debug wave 2026-10-19).
 *
 * Shift+Enter is byte-identical to plain Enter ("\r") unless the terminal
 * speaks the kitty keyboard protocol; the two only diverge after kitty
 * negotiation succeeds. Verified end-to-end 2026-10-19: a legacy byte feed
 * (\r with the shift modifier unrepresentable) SUBMITS the draft, while the
 * kitty sequence (CSI 13;2u) appends a draft line through the full stack —
 * so a composer hint promising Shift+Enter is only honest when the renderer
 * negotiated kitty. Pessimistic default: null capabilities (legacy terminal,
 * tmux swallowing the push-flags, no answer to the query) read as NO kitty,
 * i.e. the hint degrades to the always-true Alt+Enter/Ctrl+J fallback.
 */

import { CliRenderEvents, type CliRenderer } from "@opentui/core";
import { createSignal, onCleanup } from "solid-js";

/** Reactive accessor: true while kitty keyboard is negotiated. */
export function useKittyKeys(renderer: CliRenderer): () => boolean {
  const read = () => renderer.capabilities?.kitty_keyboard === true;
  const [kitty, setKitty] = createSignal(read());
  const update = () => setKitty(read());
  renderer.on(CliRenderEvents.CAPABILITIES, update);
  onCleanup(() => renderer.off(CliRenderEvents.CAPABILITIES, update));
  return kitty;
}

/** Composer footer wording for the newline key cluster. */
export function newlineHint(kitty: boolean): string {
  return kitty ? "Shift/Alt+Enter" : "Alt+Enter/Ctrl+J";
}
