/**
 * B2-selection pin (2026-09-27): expanded detail text must stay selectable
 * (drag reaches the renderer selection), and the details box must NOT
 * collapse when the user drags across it. The onMouseDown toggler fired at
 * the START of a drag and swallowed the copy — the user's exact complaint;
 * the onMouseUp + drag-guard fixes it.
 */
import { describe, expect, it, beforeEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { CliRenderEvents } from "@opentui/core";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { ChatPanel } from "../src/components/chat";

describe("details copy semantics", () => {
  beforeEach(() => resetWorkspace());

  it("drag over an expanded details row: selection works, block stays open", async () => {
    actions.upsertSystemNotice("team:s1", "team x — iter 1/2 · pm thinking");
    actions.setNoticeDetails("team:s1", ["i1 pm: plan drafted", "i1 pg: sbatch submitted"]);
    actions.toggleNoticeExpanded("team:s1");
    const setup = await testRender(() => <ChatPanel />, { width: 80, height: 24 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("i1 pm: plan drafted");

    const events: string[] = [];
    setup.renderer.on(CliRenderEvents.SELECTION, (sel) => {
      if (sel && !sel.isDragging) events.push(sel.getSelectedText());
    });
    const lines = frame.split("\n");
    const rowIndex = lines.findIndex((l) => l.includes("i1 pm: plan drafted"));
    expect(rowIndex).toBeGreaterThanOrEqual(0);
    // Drag across the TEXT (columns are content-aware since the message
    // cell spine wave 2026-10-19 — chrome columns vary with the chrome).
    const x0 = lines[rowIndex].indexOf("i1 pm");
    await setup.mockMouse.drag(x0, rowIndex, x0 + 25, rowIndex);

    expect(setup.renderer.hasSelection).toBe(true);
    expect(workspace.noticeExpanded["team:s1"]).toBeTruthy();
    const after = setup.captureCharFrame();
    expect(after).toContain("i1 pg: sbatch submitted");
    setup.renderer.destroy();
  });

  it("a plain click still toggles the block", async () => {
    actions.upsertSystemNotice("team:s1", "team x — iter 1/2 · pm thinking");
    actions.setNoticeDetails("team:s1", ["i1 pm: plan drafted"]);
    actions.toggleNoticeExpanded("team:s1");
    const setup = await testRender(() => <ChatPanel />, { width: 80, height: 24 });
    await setup.renderOnce();
    const lines = setup.captureCharFrame().split("\n");
    const rowIndex = lines.findIndex((l) => l.includes("collapse details"));
    expect(rowIndex).toBeGreaterThanOrEqual(0);
    // Click INSIDE the row's text (the cell spine column itself is not the
    // toggle target since the visibility wave 2026-10-19).
    await setup.mockMouse.pressDown(lines[rowIndex].indexOf("collapse details"), rowIndex);
    await setup.mockMouse.release(lines[rowIndex].indexOf("collapse details"), rowIndex);
    await setup.renderOnce();
    expect(workspace.noticeExpanded["team:s1"]).toBeFalsy();
    setup.renderer.destroy();
  });
});
