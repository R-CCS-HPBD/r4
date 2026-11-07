/**
 * TUI audit 2026-10-17 (ts-lib lane) — slash routing: bare-valid verbs run on
 * one palette Enter, /teams and /guide keep their args, /teamup hardware
 * shows the profiles, /venue tells a failed call from "nothing there", and
 * /clear also resets the companion conversation.
 */
import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { dispatchSlashCommand, localSlashCommands, needsArgPrompt } from "../src/commands/slash";
import { buildSlashPaletteCommands } from "../src/lib/slash-palette";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

type Reply = unknown | ((params: Record<string, unknown>) => unknown);

function fakeBridge(replies: Record<string, Reply>): Array<{ method: string; params: Record<string, unknown> }> {
  const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    request: async (method: string, params: Record<string, unknown> = {}) => {
      calls.push({ method, params });
      const reply = replies[method];
      if (typeof reply === "function") return (reply as (p: Record<string, unknown>) => unknown)(params);
      return reply ?? {};
    },
  } as never);
  return calls;
}

function texts(): string {
  return workspace.chatMessages.map((m) => m.content).join("\n");
}

beforeEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

afterEach(() => setCompanionBridgeForTests(null));

describe("bare-valid verbs run on one palette Enter (#44)", () => {
  const names = ["loops", "checkpoint", "circles", "sweep", "evidence", "statusline"];

  test.each(names)("/%s dispatches bare instead of filling the draft", (name) => {
    const entry = localSlashCommands().find((e) => e.name === name)!;
    expect(needsArgPrompt(entry, "")).toBe(false);
    const seen: string[] = [];
    const drafts: string[] = [];
    const cmd = buildSlashPaletteCommands([entry], {
      dispatch: (line) => void seen.push(line),
      insertDraft: (text) => void drafts.push(text),
    })[0]!;
    cmd.run();
    expect(drafts).toEqual([]);
    expect(seen).toEqual([`/${name}`]);
  });

  test("/btw still asks for its question", () => {
    const entry = localSlashCommands().find((e) => e.name === "btw")!;
    expect(needsArgPrompt(entry, "")).toBe(true);
  });
});

describe("aliases keep their args (#45)", () => {
  test("/teams status runs the status verb", async () => {
    const calls = fakeBridge({ "teamup.status": { session_id: "s1", status: "running" } });
    const result = await dispatchSlashCommand("/teams status s1");
    expect(result.kind).toBe("handled");
    expect(calls.map((c) => c.method)).toContain("teamup.status");
    expect(calls.find((c) => c.method === "teamup.status")?.params).toEqual({ session_id: "s1" });
  });

  test.each(["guide", "welcome"])("/%s map prints the function map, not the wizard", async (alias) => {
    fakeBridge({});
    await dispatchSlashCommand(`/${alias} map`);
    for (let i = 0; i < 50 && !texts().includes("core flows"); i++) await Bun.sleep(10);
    expect(texts()).toContain("— core flows");
    expect(workspace.tourOpen).toBe(false);
  });
});

describe("/teamup hardware (#62)", () => {
  test("shows the hardware profiles, not the session list", async () => {
    const calls = fakeBridge({
      "teamup.hardware": {
        profiles: [{ id: "gb10", name: "DGX Spark GB10", description: "128 GB unified memory" }],
        count: 1,
      },
    });
    await dispatchSlashCommand("/teamup hardware");
    const methods = calls.map((c) => c.method);
    expect(methods).toContain("teamup.hardware");
    expect(methods).not.toContain("teamup.list");
    expect(texts()).toContain("gb10");
    expect(texts()).toContain("DGX Spark GB10");
  });
});

describe("/venue reports failed calls (#64)", () => {
  test("/venue status names the error instead of 'none recorded'", async () => {
    fakeBridge({
      "session.venue_status": () => {
        throw new Error("venue.unavailable: journal unreachable");
      },
    });
    setWorkspace("sessionVenue", "frontend");
    const result = await dispatchSlashCommand("/venue status");
    expect(result).toEqual({ kind: "error", message: "venue status failed: venue.unavailable: journal unreachable" });
    expect(texts()).not.toContain("none recorded");
    expect(texts()).not.toContain("runs locally");
  });

  test("a local session still reads as local when the lookup fails", async () => {
    fakeBridge({
      "session.venue_status": () => {
        throw new Error("venue.bad_request: session id required");
      },
    });
    const result = await dispatchSlashCommand("/venue");
    expect(result.kind).toBe("handled");
    expect(texts()).toContain("runs locally");
    expect(texts()).not.toContain("venue status failed");
  });

  test("not_found still reads as nothing recorded", async () => {
    fakeBridge({
      "session.venue_status": () => {
        throw new Error("venue.not_found: no runtime");
      },
    });
    setWorkspace("sessionVenue", "frontend");
    await dispatchSlashCommand("/venue");
    expect(texts()).toContain("none recorded");
  });

  test("/venue approvals names the error instead of 'no pending'", async () => {
    fakeBridge({
      "session.venue_approvals": () => {
        throw new Error("venue.error: 401 unauthorized");
      },
    });
    const result = await dispatchSlashCommand("/venue approvals");
    expect(result).toEqual({ kind: "error", message: "venue approvals failed: venue.error: 401 unauthorized" });
    expect(texts()).not.toContain("no pending remote approvals");
  });

  test("/venue approvals with a card already open says so", async () => {
    const calls = fakeBridge({ "session.venue_approvals": { approvals: [] } });
    actions.openToolApproval({ id: "t1", tool: "bash", description: "ls", sessionApprovalAllowed: true });
    await dispatchSlashCommand("/venue approvals");
    expect(texts()).not.toContain("no pending remote approvals");
    expect(texts()).toContain("already open");
    expect(calls.map((c) => c.method)).not.toContain("session.venue_approvals");
  });
});

describe("/clear resets the companion conversation (#8, #46)", () => {
  test("/clear asks the companion to clear the session", async () => {
    const calls = fakeBridge({
      dispatch_slash_command: { handled: true, cleared: true, output: ["session cleared"] },
    });
    actions.sendSystemNotice("old turn");
    const result = await dispatchSlashCommand("/clear");
    expect(result.kind).toBe("handled");
    for (let i = 0; i < 50 && calls.length === 0; i++) await Bun.sleep(10);
    expect(calls.find((c) => c.method === "dispatch_slash_command")?.params).toEqual({ command: "/clear" });
    expect(texts()).not.toContain("old turn");
  });

  test("/clear during a running turn leaves both halves alone", async () => {
    const calls = fakeBridge({
      dispatch_slash_command: { handled: true, cleared: true, output: ["session cleared"] },
    });
    actions.sendSystemNotice("visible turn");
    setWorkspace("chatStreaming", true);
    await dispatchSlashCommand("/clear");
    await Bun.sleep(20);
    expect(calls.map((c) => c.method)).not.toContain("dispatch_slash_command");
    expect(texts()).toContain("visible turn");
    expect(texts()).toContain("turn in progress");
    expect(texts()).not.toContain("kept the conversation");
  });

  test("offline /clear only blanks the transcript", async () => {
    setDemoMode(true);
    const calls = fakeBridge({});
    actions.sendSystemNotice("old turn");
    await dispatchSlashCommand("/clear");
    await Bun.sleep(20);
    expect(calls.map((c) => c.method)).not.toContain("dispatch_slash_command");
    expect(texts()).not.toContain("old turn");
  });
});
