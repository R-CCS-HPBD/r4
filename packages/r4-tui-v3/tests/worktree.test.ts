/**
 * ADR-108 D-2 (W1b): /worktree in the console — Claude Code-style session
 * worktrees. The tab remembers the worktree it works in, the wt chip shows
 * it, and the console's own file features follow it (resolveWorkspace).
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { actions, activeTab, resetWorkspace, setScopedField, workspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { dispatchSlashCommand } from "../src/commands/slash";
import { handleWorktreeEvent, syncWorktreeFromBridge, WORKTREE_STREAMING } from "../src/commands/worktree-command";
import { launchWorkspace, resolveWorkspace } from "../src/lib/resolve-python";
import { parseWorktreeArgs, worktreeChip, worktreeListText } from "../src/lib/worktree";

type Call = { method: string; params: Record<string, unknown> };

function fakeBridge(answers: Record<string, unknown>): Call[] {
  const calls: Call[] = [];
  setCompanionBridgeForTests({
    on: () => undefined,
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      return answers[method] ?? {};
    },
  } as never);
  return calls;
}

const lastNotice = () => workspace.chatMessages.filter((m) => m.role === "system").at(-1)?.content ?? "";

const row = { id: "feat", branch: "r4/feat", path: "/repo/.r4/worktrees/feat", base: "abc", parent: "", owner: "session:s", level: "session", snapshot_ref: "", created_at: 0, changed: false };

beforeEach(() => {
  delete process.env.R4_TUI_BACKEND;
  resetWorkspace();
});

afterEach(() => {
  setCompanionBridgeForTests(null);
  resetWorkspace();
});

describe("parseWorktreeArgs", () => {
  it("reads list, enter, exit and help", () => {
    expect(parseWorktreeArgs("")).toEqual({ verb: "list" });
    expect(parseWorktreeArgs("list")).toEqual({ verb: "list" });
    expect(parseWorktreeArgs("feat")).toEqual({ verb: "enter", name: "feat", base: "head" });
    expect(parseWorktreeArgs("feat --base=default")).toEqual({ verb: "enter", name: "feat", base: "default" });
    expect(parseWorktreeArgs("exit")).toEqual({ verb: "exit", action: "keep", discard: false });
    expect(parseWorktreeArgs("exit remove --discard")).toEqual({ verb: "exit", action: "remove", discard: true });
    expect(parseWorktreeArgs("help")).toEqual({ verb: "usage" });
  });
});

describe("worktree chip and list text", () => {
  it("shows wt:<id> only while the tab is in one", () => {
    expect(worktreeChip(null)).toBeNull();
    expect(worktreeChip({ id: "feat", branch: "r4/feat", path: "/p", root: "/p" })).toEqual({ label: "wt:feat", state: "info" });
  });

  it("marks the current one and says how to start", () => {
    expect(worktreeListText([], "")).toContain("/worktree <name> creates one");
    const text = worktreeListText([{ ...row, changed: true }], "feat");
    expect(text).toContain("▸ feat  r4/feat  changed");
    expect(worktreeListText([], "", "not a git repository")).toContain("need a git repository");
  });
});

describe("/worktree", () => {
  it("enter: the tab works in the worktree; resolveWorkspace follows it", async () => {
    const calls = fakeBridge({
      "worktree.enter": { ok: true, message: "worktree feat created and entered", workspace_root: row.path, created: true, warning: "", worktree: row },
    });
    const result = await dispatchSlashCommand("/worktree feat");
    expect(result).toEqual({ kind: "handled" });
    expect(calls[0]).toEqual({ method: "worktree.enter", params: { name: "feat", base: "head" } });
    expect(activeTab().worktree).toEqual({ id: "feat", branch: "r4/feat", path: row.path, root: row.path });
    expect(resolveWorkspace()).toBe(row.path);
    expect(lastNotice()).toContain("the agent now works here");
  });

  it("exit: back to the launch workspace", async () => {
    setScopedField("worktree", { id: "feat", branch: "r4/feat", path: row.path, root: row.path });
    fakeBridge({ "worktree.exit": { ok: true, message: "left worktree feat; kept (branch r4/feat)", workspace_root: "/repo", worktree_id: "feat", removed: false, changed: true } });
    await dispatchSlashCommand("/worktree exit");
    expect(activeTab().worktree).toBeNull();
    expect(resolveWorkspace()).toBe(launchWorkspace());
    expect(lastNotice()).toContain("kept");
  });

  it("a refusal is an error with the bridge's words; nothing changes", async () => {
    fakeBridge({ "worktree.enter": { ok: false, message: "already in worktree a: /worktree exit first", workspace_root: "/x", created: false, warning: "", worktree: null } });
    const result = await dispatchSlashCommand("/worktree b");
    expect(result).toEqual({ kind: "error", message: "already in worktree a: /worktree exit first" });
    expect(activeTab().worktree).toBeNull();
  });

  it("entering is refused while an answer streams; listing is not", async () => {
    const calls = fakeBridge({ "worktree.list": { worktrees: [row], current: "", workspace_root: "/repo" } });
    setScopedField("chatStreaming", true);
    expect(await dispatchSlashCommand("/worktree feat")).toEqual({ kind: "error", message: WORKTREE_STREAMING });
    expect(await dispatchSlashCommand("/worktree list")).toEqual({ kind: "handled" });
    expect(calls.map((c) => c.method)).toEqual(["worktree.list"]);
  });

  it("a session launched in a worktree gets its chip from the bridge", async () => {
    fakeBridge({ "worktree.list": { worktrees: [row], current: "feat", workspace_root: row.path } });
    await syncWorktreeFromBridge();
    expect(activeTab().worktree?.id).toBe("feat");
  });
});

describe("W1c: the agent builds the worktree (worktree_event, /worktree mode)", () => {
  const enterEvent = (source: string) => ({
    action: "enter",
    source,
    ok: true,
    message: "worktree auto-s1 created and entered: /repo/.r4/worktrees/auto-s1 (branch r4/auto-s1)",
    workspace_root: "/repo/.r4/worktrees/auto-s1",
    created: true,
    warning: "",
    worktree: { ...row, id: "auto-s1", branch: "r4/auto-s1", path: "/repo/.r4/worktrees/auto-s1" },
  });

  it("an auto enter moves the tab and says so", () => {
    handleWorktreeEvent(enterEvent("auto"));
    expect(activeTab().worktree?.id).toBe("auto-s1");
    expect(resolveWorkspace()).toBe("/repo/.r4/worktrees/auto-s1");
    expect(lastNotice()).toStartWith("auto worktree auto-s1 created and entered");
  });

  it("the agent's own enter is announced; the user's is not repeated", () => {
    handleWorktreeEvent(enterEvent("agent"));
    expect(lastNotice()).toStartWith("agent: worktree auto-s1");
    const before = workspace.chatMessages.length;
    handleWorktreeEvent(enterEvent("user"));
    expect(workspace.chatMessages.length).toBe(before);
  });

  it("an exit clears the tab; a refusal changes nothing", () => {
    handleWorktreeEvent(enterEvent("auto"));
    handleWorktreeEvent({ action: "exit", source: "agent", ok: true, message: "left worktree auto-s1; kept", workspace_root: "/repo" });
    expect(activeTab().worktree).toBeNull();
    handleWorktreeEvent({ action: "enter", source: "agent", ok: false, message: "no" });
    expect(activeTab().worktree).toBeNull();
  });

  it("/worktree mode reads and sets the session's mode", async () => {
    const calls = fakeBridge({ "worktree.mode": { ok: true, message: "", mode: "auto", mode_source: "session" } });
    expect(parseWorktreeArgs("mode auto")).toEqual({ verb: "mode", mode: "auto" });
    await dispatchSlashCommand("/worktree mode auto");
    expect(calls[0]).toEqual({ method: "worktree.mode", params: { mode: "auto" } });
    expect(lastNotice()).toContain("worktree mode: auto (session)");
  });

  it("a bad mode is the bridge's refusal", async () => {
    fakeBridge({ "worktree.mode": { ok: false, message: "worktree mode must be one of off, auto", mode: "off", mode_source: "default" } });
    expect(await dispatchSlashCommand("/worktree mode sometimes")).toEqual({
      kind: "error",
      message: "worktree mode must be one of off, auto",
    });
  });
});
