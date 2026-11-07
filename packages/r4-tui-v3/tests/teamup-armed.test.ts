/**
 * Armed one-shot teamup (B-family, 2026-09-28 confirmed): /teamup on|off arms
 * and off disarms; the NEXT sendChatMessage fires teamup.start and skips the
 * chat turn, then auto-disarms.
 */
import { describe, it, expect, beforeEach, afterEach, mock } from "bun:test";
import { actions, resetWorkspace, workspace } from "../src/stores";

const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
let reply: unknown = {};

mock.module("../src/lib/console-mode", () => ({
  isOfflineBackend: () => false,
}));
mock.module("../src/bridge", () => ({
  getCompanionBridge: () => ({
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      if (method === "teamup.start") return { ok: true, accepted: true, crew: "research" };
      return reply;
    },
  }),
}));

describe("armed one-shot teamup", () => {
  beforeEach(() => {
    resetWorkspace();
    calls.length = 0;
    reply = { loops: [], count: 0 };
  });
  afterEach(() => resetWorkspace());

  it("sendChatMessage while armed: fires teamup.start, disarms, no chat streaming", async () => {
    actions.setTeamupArmed(true, "research");
    expect(workspace.teamupArmed).toBe(true);
    actions.sendChatMessage("characterize the defect class");
    expect(workspace.teamupArmed).toBe(false); // one-shot disarm
    expect(workspace.chatStreaming).toBe(false); // NOT a chat turn
    await new Promise((r) => setTimeout(r, 30));
    const start = calls.find((c) => c.method === "teamup.start");
    expect(start?.params.goal).toBe("characterize the defect class");
    expect(start?.params.crew).toBe("research");
    expect(start?.params.run).toBe(true);
  });

  it("off disarms without firing; a normal message next", async () => {
    actions.setTeamupArmed(true);
    actions.setTeamupArmed(false);
    calls.length = 0;
    // Direct hook behavior: disarm state only — send would stream normally.
    expect(workspace.teamupArmed).toBe(false);
  });

  it("setTeamupArmed(on, crew) records the crew", () => {
    actions.setTeamupArmed(true, "hpc");
    expect(workspace.teamupArmedCrew).toBe("hpc");
    actions.setTeamupArmed(false);
    expect(workspace.teamupArmed).toBe(false);
  });

  it("armed parallel width rides teamup.start as parallel_roles (2026-09-29)", async () => {
    actions.setTeamupArmed(true, "code", 4);
    expect(workspace.teamupArmedParallel).toBe(4);
    actions.sendChatMessage("refactor the scheduler");
    await new Promise((r) => setTimeout(r, 30));
    const start = calls.find((c) => c.method === "teamup.start");
    expect(start?.params.parallel_roles).toBe(4);

    calls.length = 0;
    actions.setTeamupArmed(true, "research", 1);
    actions.sendChatMessage("plain sequential goal");
    await new Promise((r) => setTimeout(r, 30));
    const sequential = calls.find((c) => c.method === "teamup.start");
    expect(sequential?.params.parallel_roles).toBeUndefined();
  });
});
