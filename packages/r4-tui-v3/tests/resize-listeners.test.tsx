/**
 * Stabilization 2026-10-21 (w3-tui, item 10) — resize listeners stay O(1).
 *
 * Field report: after /connect added a public endpoint the TUI printed
 * "MaxListenersExceededWarning: 11 resize listeners added to CliRenderer".
 * OpenTUI's useTerminalDimensions() adds one renderer "resize" listener per
 * calling component instance, and every chat message cell, tool row and diff
 * block called it, so the count grew with the transcript. lib/terminal-dims
 * shares one subscription per renderer.
 */

import { EventEmitter } from "node:events";
import { beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { terminalDimsFor } from "../src/lib/terminal-dims";
import { Shell } from "../src/layout/shell";
import { resetWorkspace, actions, setWorkspace } from "../src/stores";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  resetWorkspace();
  actions.closeAllOverlays();
});

const DIFF = ["--- a/x.py", "+++ b/x.py", "@@ -1,2 +1,2 @@", "-old = 1", "+new = 2", " keep"].join("\n");
const LONG = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november WRAPEND";

function transcript(count: number) {
  const msgs = [];
  for (let i = 0; i < count; i++) {
    const tools = [{ status: "ok", name: "bash", text: "bash", args: { command: `echo ${i}` } }];
    if (i % 3 === 0) msgs.push({ id: `d${i}`, role: "assistant", content: DIFF, timestamp: i, model: "m" });
    else msgs.push({ id: `a${i}`, role: "assistant", content: `${i} ${LONG}`, timestamp: i, model: "m", toolCalls: tools });
  }
  return msgs;
}

async function listenersFor(count: number): Promise<number> {
  setWorkspace("verboseTranscript", true);
  setWorkspace("chatMessages", transcript(count) as never);
  const setup = await testRender(() => <Shell />, { width: 100, height: 40 });
  await setup.renderOnce();
  await setup.renderOnce();
  const n = setup.renderer.listenerCount("resize");
  setup.renderer.destroy();
  return n;
}

describe("renderer resize listeners", () => {
  it("do not grow with the transcript (MaxListenersExceededWarning)", async () => {
    const few = await listenersFor(1);
    const many = await listenersFor(30);
    expect(many).toBe(few);
    expect(many).toBeLessThan(10);
  });

  it("a resize still re-wraps message cells", async () => {
    setWorkspace("chatMessages", [{ id: "a1", role: "assistant", content: LONG, timestamp: 1, model: "m" }] as never);
    const setup = await testRender(() => <Shell />, { width: 140, height: 30 });
    await setup.renderOnce();
    await setup.renderOnce();
    const wide = setup.captureCharFrame();
    setup.resize(50, 30);
    await setup.renderOnce();
    await setup.renderOnce();
    const narrow = setup.captureCharFrame();
    setup.renderer.destroy();
    const rowOf = (frame: string) => frame.split("\n").find((l) => l.includes("WRAPEND")) ?? "";
    expect(rowOf(wide)).toContain("alpha");
    expect(rowOf(narrow)).not.toContain("alpha");
    expect(narrow).toContain("WRAPEND");
  });

  it("the shared accessor follows resizes with one listener for every caller", () => {
    const renderer = Object.assign(new EventEmitter(), { width: 140, height: 30 });
    const accessors = Array.from({ length: 50 }, () => terminalDimsFor(renderer));
    expect(renderer.listenerCount("resize")).toBe(1);
    expect(new Set(accessors).size).toBe(1);
    renderer.emit("resize", 50, 20);
    expect(accessors[0]!()).toEqual({ width: 50, height: 20 });
  });

  it("the live renderer's resize reaches the shared accessor", async () => {
    const setup = await testRender(() => <Shell />, { width: 140, height: 30 });
    await setup.renderOnce();
    const dims = terminalDimsFor(setup.renderer as never);
    expect(dims().width).toBe(140);
    setup.resize(50, 30);
    await setup.renderOnce();
    expect(dims().width).toBe(50);
    setup.renderer.destroy();
  });
});
