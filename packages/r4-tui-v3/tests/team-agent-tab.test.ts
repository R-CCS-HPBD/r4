/**
 * One role agent → its own chat tab (2026-10-01 wave):
 * teamsActions.openAgentTab RPC's the persona spawn then opens a tab on the
 * returned session — same bridge-barrel mock discipline as workflow-arm pins.
 */
import { describe, test, expect, beforeEach, mock } from "bun:test";
import { teamsActions, setTeamsState } from "../src/stores/teams";
import { resetWorkspace, workspace } from "../src/stores";

type FakeBridge = {
  calls: Array<{ method: string; params: Record<string, unknown> }>;
  request: (method: string, params: Record<string, unknown>) => Promise<unknown>;
};
let currentBridge: FakeBridge | null = null;
mock.module("../src/bridge", () => ({
  getCompanionBridge: () => currentBridge,
}));

function fakeBridge(reply: Record<string, unknown>): FakeBridge {
  const calls: FakeBridge["calls"] = [];
  const bridge: FakeBridge = {
    calls,
    request: async (method, params) => {
      calls.push({ method, params });
      return reply;
    },
  };
  currentBridge = bridge;
  return bridge;
}

describe("agent tab per role (2026-10-01)", () => {
  beforeEach(() => {
    resetWorkspace();
    setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
    currentBridge = null;
  });

  test("openAgentTab posts spawn and opens a tab for the spawned session", async () => {
    const bridge = fakeBridge({
      session_id: "sid-pm-1",
      title: "verify-pr-status-pm",
      role: "pm",
    });
    await teamsActions.openAgentTab("team-1", "pm");

    expect(bridge.calls).toContainEqual({
      method: "teamup.spawn_agent_session",
      params: { session_id: "team-1", role: "pm" },
    });
    const tab = workspace.chatTabs.find((t) => t.sessionId === "sid-pm-1");
    expect(tab?.title).toContain("pm");
  });

  test("bridge error reports honestly and never opens a tab", async () => {
    currentBridge = {
      calls: [],
      request: async () => {
        throw new Error("teamup.spawn_agent_session: unknown role");
      },
    };
    await teamsActions.openAgentTab("team-1", "ghost");
    expect(workspace.chatTabs.some((t) => t.sessionId === "sid-pm-1")).toBe(false);
    const notice = workspace.chatMessages.find((m) =>
      m.content.includes("agent tab failed"),
    );
    expect(notice?.content).toContain("unknown role");
  });
});
