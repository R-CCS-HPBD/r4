/**
 * c3-tui 2026-10-21 (item 4): below FULL_WIDTH_OVERLAY_COLS a centered
 * (absolute) overlay spans the full terminal width.
 *
 * On the base, at 40 columns the 50% command palette was 20 columns wide:
 * its rows were clipped mid-word and the chat transcript showed at both of
 * its edges (PTY repro $C3/tui/o4-narrow-overlays.ts — palette, F12 help and
 * the Esc Esc rewind overlay all leaked chat text).
 */

import { describe, expect, it } from "bun:test";
import { For } from "solid-js";
import { testRender } from "@opentui/solid";
import { Floating, FULL_WIDTH_OVERLAY_COLS, overlaySpansWidth } from "../src/ui/Floating";

function Scene() {
  return (
    <box width="100%" height="100%" flexDirection="column">
      <For each={Array.from({ length: 12 })}>{() => <text>{"X".repeat(200)}</text>}</For>
      <Floating position="absolute" width="50%" left="25%" top={2} height={5} flexDirection="column">
        <text>overlay-body</text>
      </Floating>
    </box>
  );
}

async function overlayRows(width: number): Promise<string[]> {
  const setup = await testRender(() => <Scene />, { width, height: 12 });
  await setup.renderOnce();
  const rows = setup.captureCharFrame().split("\n").slice(2, 7);
  setup.renderer.destroy();
  return rows;
}

describe("Floating at narrow widths", () => {
  it("spans the full width below the threshold: no underlying text at its edges", async () => {
    const rows = await overlayRows(40);
    expect(rows[0]).toContain("overlay-body");
    for (const row of rows) expect(row).not.toContain("X");
  });

  it("keeps its centered width on a roomy terminal", async () => {
    const rows = await overlayRows(100);
    expect(rows[0]).toContain("overlay-body");
    expect(rows[0]!.startsWith("X")).toBe(true);
    expect(rows[0]!.trimEnd().endsWith("X")).toBe(true);
  });

  it("only absolute overlays change (inline popups already follow their parent)", () => {
    expect(overlaySpansWidth("absolute", FULL_WIDTH_OVERLAY_COLS - 1)).toBe(true);
    expect(overlaySpansWidth("absolute", FULL_WIDTH_OVERLAY_COLS)).toBe(false);
    expect(overlaySpansWidth(undefined, 30)).toBe(false);
  });
});
