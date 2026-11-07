/**
 * Wave-intercept mistakes get exactly one honest answer
 * (stabilization 2026-10-21).
 *
 * slash-c#5            '/term help' printed two error lines (a notice AND
 *                      the returned error, which the dispatcher prints).
 * slash-b#7 / tabs-sessions#7  '/tab 9', '/tab 0', '/tab 1.5' did nothing.
 * slash-a#5            '/teamup help' and '/teamup zzbogus' silently listed.
 */

import { beforeEach, describe, expect, it, spyOn } from "bun:test";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import { handleWaveIntercepts } from "../src/commands/wave-intercepts";
import { setDemoMode } from "../src/lib/console-mode";
import { teamsActions } from "../src/stores/teams";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  setDemoMode(false);
  resetWorkspace();
  resetTabsRegistryForTests();
});

const notices = () => workspace.chatMessages.filter((m) => m.role === "system").map((m) => m.content);

describe("slash-c#5 — one error line per /term mistake", () => {
  for (const args of ["help", "zzbogus", "share zz"]) {
    it(`/term ${args} returns the error and posts no notice of its own`, async () => {
      const result = await handleWaveIntercepts({ name: "term", args });
      expect(result?.kind).toBe("error");
      expect(notices()).toEqual([]);
    });
  }

  it("the error names the verbs", async () => {
    const result = await handleWaveIntercepts({ name: "term", args: "help" });
    expect(result?.kind === "error" && result.message).toContain("bare, new, kill, or share [lines]");
  });
});

describe("slash-b#7 / tabs-sessions#7 — /tab <bad n> is an error", () => {
  for (const arg of ["9", "0", "-1", "1.5"]) {
    it(`/tab ${arg} reports the open range and leaves the active tab`, async () => {
      actions.tabCreate();
      const active = workspace.activeChatTabId;
      const result = await handleWaveIntercepts({ name: "tab", args: arg });
      expect(result).toEqual({ kind: "error", message: `no tab ${arg} (open: 1..2) — usage: /tab <n>` });
      expect(workspace.activeChatTabId).toBe(active);
    });
  }

  it("/tab 1 still jumps", async () => {
    const first = workspace.activeChatTabId;
    actions.tabCreate();
    expect(await handleWaveIntercepts({ name: "tab", args: "1" })).toEqual({ kind: "handled" });
    expect(workspace.activeChatTabId).toBe(first);
  });
});

describe("slash-a#5 — /teamup help and unknown subs say so", () => {
  it("help prints the verbs and lists nothing", async () => {
    const list = spyOn(teamsActions, "list").mockImplementation(async () => undefined);
    try {
      const result = await handleWaveIntercepts({ name: "teamup", args: "help" });
      expect(result).toEqual({ kind: "handled" });
      expect(notices().at(-1)).toContain("/teamup verbs: on [crew|plan:<template>]");
      expect(list).not.toHaveBeenCalled();
    } finally {
      list.mockRestore();
    }
  });

  it("an unknown sub is an error naming the verbs", async () => {
    const list = spyOn(teamsActions, "list").mockImplementation(async () => undefined);
    try {
      const result = await handleWaveIntercepts({ name: "teamup", args: "zzbogus" });
      expect(result?.kind === "error" && result.message).toContain('teamup: unknown sub "zzbogus"');
      expect(list).not.toHaveBeenCalled();
    } finally {
      list.mockRestore();
    }
  });

  it("bare /teamup still lists today's sessions", async () => {
    const list = spyOn(teamsActions, "list").mockImplementation(async () => undefined);
    try {
      await handleWaveIntercepts({ name: "teamup", args: "" });
      expect(list).toHaveBeenCalledTimes(1);
    } finally {
      list.mockRestore();
    }
  });
});
