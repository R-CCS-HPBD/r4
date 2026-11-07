/**
 * /circles command pins (ADR-086 amendment 2026-10-19 — console write verbs).
 *
 * The bridge is mocked: verb params pass through byte-shaped, rosters render
 * as member lines, the caller id for `enter` comes from the workspace
 * identity (never invented), bad arity or a bad --role answers the usage
 * line and NEVER calls the bridge, and server refusals (the four-rung
 * ladder's 403) surface verbatim as notices.
 */
import { describe, it, expect, beforeEach, afterEach, mock } from "bun:test";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";

const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
let reply: unknown = {};
let failure: Error | null = null;

mock.module("../src/lib/console-mode", () => ({
  isOfflineBackend: () => false,
}));
mock.module("../src/bridge", () => ({
  getCompanionBridge: () => ({
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      if (failure) throw failure;
      return reply;
    },
  }),
}));

const { handleCirclesCommand } = await import("../src/commands/circles-command");

function lastNotice(): string {
  const systems = workspace.chatMessages.filter((m) => m.role === "system");
  return systems[systems.length - 1]?.content ?? "";
}

const CIRCLE = {
  project_id: "demo",
  circle_id: "alice",
  name: "",
  owner_id: "alice",
  status: "active",
};

describe("/circles write verbs (ADR-086 amendment 2026-10-19)", () => {
  beforeEach(() => {
    resetWorkspace();
    calls.length = 0;
    reply = {};
    failure = null;
  });

  afterEach(() => resetWorkspace());

  it("enter mints the personal cell with the workspace identity — never an invented id", async () => {
    setWorkspace("identityUsername", "alice");
    reply = { circle: CIRCLE, created: true };
    await handleCirclesCommand("enter demo");
    expect(calls).toEqual([
      { method: "circles.create", params: { project_id: "demo", circle_id: "alice" } },
    ]);
    expect(lastNotice()).toContain("demo/alice");
    expect(lastNotice()).toContain("personal cell");
  });

  it("enter without a decoded identity refuses honestly and never calls the bridge", async () => {
    setWorkspace("identityUsername", "");
    await handleCirclesCommand("enter demo");
    expect(calls).toEqual([]);
    expect(lastNotice()).toMatch(/sign in/i);
  });

  it("create passes the joined name through", async () => {
    reply = { circle: { ...CIRCLE, circle_id: "team-x", name: "Team X" }, created: true };
    await handleCirclesCommand("create demo team-x Team X");
    expect(calls).toEqual([
      {
        method: "circles.create",
        params: { project_id: "demo", circle_id: "team-x", name: "Team X" },
      },
    ]);
    expect(lastNotice()).toContain("demo/team-x");
  });

  it("add defaults the role to member; --role admin passes through", async () => {
    reply = {
      circle: CIRCLE,
      members: [{ user_id: "bob", role: "member", added_at: null }],
    };
    await handleCirclesCommand("add demo team-x bob");
    expect(calls[0]).toEqual({
      method: "circles.member_add",
      params: { project_id: "demo", circle_id: "team-x", user_id: "bob", role: "member" },
    });
    expect(lastNotice()).toContain("bob");

    calls.length = 0;
    await handleCirclesCommand("add demo team-x carol --role admin");
    expect(calls[0]!.params.role).toBe("admin");
  });

  it("remove moves one account out and renders the roster left behind", async () => {
    reply = { members: [], project_id: "demo", circle_id: "team-x" };
    await handleCirclesCommand("remove demo team-x bob");
    expect(calls).toEqual([
      {
        method: "circles.member_remove",
        params: { project_id: "demo", circle_id: "team-x", user_id: "bob" },
      },
    ]);
    expect(lastNotice()).toContain("no visible members");
  });

  it("bad arity or role answers usage and never calls the bridge", async () => {
    for (const line of ["enter", "create demo", "add demo c1", "add demo c1 u --role owner", "remove demo", "members demo"]) {
      calls.length = 0;
      const result = await handleCirclesCommand(line);
      expect(calls).toEqual([]);
      expect(result.kind).toBe("error");
      expect(String((result as { message?: string }).message)).toContain("circles");
    }
  });

  it("a server refusal (the ladder's 403) surfaces verbatim as a notice", async () => {
    failure = new Error("API error: 403 — only the circle owner, a circle admin, the project owner, or a platform admin may manage this roster");
    const result = await handleCirclesCommand("add demo team-x bob");
    expect(result.kind).toBe("handled");
    expect(lastNotice()).toContain("403");
    expect(lastNotice()).toContain("project owner");
  });

  it("reads stay positional: bare lists, <p> <c> shows members, members verb is explicit", async () => {
    reply = { circles: [CIRCLE], count: 1 };
    await handleCirclesCommand("");
    expect(calls[0]).toEqual({ method: "circles.list", params: {} });
    expect(lastNotice()).toContain("demo/alice");

    calls.length = 0;
    reply = { members: [{ user_id: "alice", role: "admin", added_at: null }] };
    await handleCirclesCommand("demo alice");
    expect(calls[0]).toEqual({
      method: "circles.members",
      params: { project_id: "demo", circle_id: "alice" },
    });

    calls.length = 0;
    await handleCirclesCommand("members demo alice");
    expect(calls[0]!.method).toBe("circles.members");
  });
});
