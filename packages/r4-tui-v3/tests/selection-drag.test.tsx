/**
 * End-to-end drag-selection probe (OpenTUI offline renderer).
 *
 * Verifies the whole copy pipeline works structurally: drag across text →
 * renderer reports a selection → "selection" event fires on mouse release
 * with non-empty selected text. This is the same hook the auto-copy
 * implementation in src/keymap/index.ts wires to the clipboard.
 */

import { describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { CliRenderEvents } from "@opentui/core";

describe("drag selection lifecycle", () => {
  it("drag over text creates a selection and fires finish event on release", async () => {
    const events: Array<{ dragging: boolean; text: string }> = [];
    const setup = await testRender(() => (
      <box width={40} height={5}>
        <text>hello workspace root /tmp/demo --flag ok 3s</text>
      </box>
    ), { width: 40, height: 5 });
    await setup.renderOnce();

    setup.renderer.on(CliRenderEvents.SELECTION, (selection) => {
      events.push({
        dragging: Boolean(selection?.isDragging),
        text: selection?.getSelectedText?.() ?? "",
      });
    });

    await setup.mockMouse.drag(1, 0, 20, 0);
    // renderer is live — no explicit destroy needed for assertion path
    expect(setup.renderer.hasSelection).toBe(true);
    const sel = setup.renderer.getSelection();
    expect(sel).not.toBeNull();
    const emitted = events.some((e) => e.text.trim().length > 0);
    expect(emitted).toBe(true);
    setup.renderer.destroy();
  });
});
