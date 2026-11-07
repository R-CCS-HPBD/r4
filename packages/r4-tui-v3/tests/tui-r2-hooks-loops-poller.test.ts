/**
 * TUI audit r2 (2026-10-17), hooks lane — #49: the watched loop card must
 * not flip to "detail failed: bridge request timed out" during a chat turn.
 * A failed background poll keeps the last card and error state. Since plan
 * H-8 (2026-10-18) the poller also polls during a turn: the bridge answers
 * reads mid-turn (it used to step aside while chatStreaming).
 */
import { afterAll, beforeEach, describe, expect, it, mock } from "bun:test";
import * as realBridge from "../src/bridge";

const calls: string[] = [];
let failing = false;
let active = true;
let terminal = false;
const fake = {
  request: async (method: string) => {
    calls.push(method);
    if (failing) throw new Error(`bridge request timed out: ${method}`);
    if (terminal) return method === "loops.list" ? { loops: [] } : { status: "completed" };
    if (method === "loops.list") return { loops: [{ loop_id: "loop-r2", status: "running" }] };
    return { status: "running", phase: "analysis", current_iteration: 1, max_iterations: 3 };
  },
};
const realGet = realBridge.getCompanionBridge;
mock.module("../src/bridge", () => ({
  ...realBridge,
  getCompanionBridge: () => (active ? fake : realGet()),
}));

const { resetWorkspace, setWorkspace, workspace } = await import("../src/stores");
const { loopsActions, loopsStore, setLoopsState, _loopPollTickForTests } = await import("../src/stores/loops");

afterAll(async () => {
  // Leave no live follow-poller behind for sibling files.
  failing = false;
  terminal = true;
  await loopsActions.list();
  await loopsActions.detail("loop-r2");
  _loopPollTickForTests();
  active = false;
});

const card = () =>
  workspace.chatMessages.find((m) => m.role === "system" && m.noticeKey === "loop:loop-r2")?.content ?? "";

beforeEach(async () => {
  resetWorkspace();
  setLoopsState({ rows: [], error: null });
  failing = false;
  await loopsActions.detail("loop-r2");
  calls.length = 0;
});

describe("loop poller during a chat turn", () => {
  it("keeps polling while a chat turn streams (plan H-8: reads answer mid-turn)", () => {
    setWorkspace("chatStreaming", true);
    _loopPollTickForTests();
    expect(calls).toContain("loops.list");
  });

  it("a failed background poll keeps the live card and sets no error", async () => {
    expect(card()).toContain("running");
    failing = true;
    _loopPollTickForTests();
    await new Promise((r) => setTimeout(r, 0));
    expect(calls.length).toBeGreaterThan(0);
    expect(card()).not.toContain("detail failed");
    expect(loopsStore.error).toBeNull();
  });

  it("an interactive detail failure is still reported", async () => {
    failing = true;
    await loopsActions.detail("loop-r2");
    expect(card()).toContain("detail failed");
  });
});
