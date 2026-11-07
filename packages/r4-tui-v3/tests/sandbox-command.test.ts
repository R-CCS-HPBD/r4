/**
 * ADR-101: `/sandbox [status|on|off|default on|default off]` over the
 * bridge verbs session.sandbox_get / session.sandbox_set — the request, the
 * state landing on the tab (chip), the keyed notice row updated in place,
 * the MCP lines, and the typed refusals.
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { activeTab, actions, resetWorkspace, setScopedField, workspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { BridgeRequestError } from "../src/bridge/jsonl-client";
import { dispatchSlashCommand } from "../src/commands/slash";
import { SANDBOX_NOTICE_KEY, mapSandbox } from "../src/lib/sandbox";
import type { RpcResult } from "../src/bridge/rpc";
import { getRuntime, resetTabsRegistryForTests } from "../src/tabs/registry";
import { OFF_NOTICE, wireSandbox } from "./helpers/sandbox-wire";

type Call = { method: string; params: Record<string, unknown> };

function fakeBridge(answers: Record<string, unknown>): Call[] {
  const calls: Call[] = [];
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    shutdown: async () => {},
    cancelTurn: () => {},
    abortInFlightSend: () => {},
    respondAskUser: () => {},
    respondApproval: () => {},
    request: async (method: string, params?: Record<string, unknown>) => {
      calls.push({ method, params: params ?? {} });
      const answer = answers[method];
      if (answer instanceof Error) throw answer;
      return answer ?? {};
    },
  } as never);
  return calls;
}

function onReply(over: Partial<RpcResult<"session.sandbox_set">> = {}): RpcResult<"session.sandbox_set"> {
  const sandbox = wireSandbox({ mode: "on", source: "slash", notice: "" });
  return {
    sandbox,
    restarted_mcp: [],
    still_running: [],
    mcp_error: "",
    state: { session: { sandbox, venue: "local" } } as never,
    ...over,
  };
}

function systemRows() {
  return workspace.chatMessages.filter((m) => m.role === "system");
}

function lastNotice(): string {
  return systemRows().at(-1)?.content ?? "";
}

function keyedRows() {
  return systemRows().filter((m) => m.noticeKey === SANDBOX_NOTICE_KEY);
}

beforeEach(() => {
  resetWorkspace();
  setScopedField("sessionVenue", "local");
});

afterEach(() => {
  setCompanionBridgeForTests(null);
  resetWorkspace();
});

describe("/sandbox on|off", () => {
  it("on sends {mode: on}, lands the state (chip) and updates the notice row in place", async () => {
    const calls = fakeBridge({ "session.sandbox_set": onReply({ restarted_mcp: ["fs", "git"] }) });
    actions.upsertSystemNotice(SANDBOX_NOTICE_KEY, `⚠ ${OFF_NOTICE}`);
    const rowId = keyedRows()[0]!.id;

    const result = await dispatchSlashCommand("/sandbox on");

    expect(result.kind).toBe("handled");
    expect(calls).toEqual([{ method: "session.sandbox_set", params: { mode: "on" } }]);
    expect(activeTab().sandbox?.mode).toBe("on");
    const rows = keyedRows();
    expect(rows.length).toBe(1);
    expect(rows[0]!.id).toBe(rowId);
    expect(rows[0]!.content).toMatch(/^✓ Sandbox on \(bwrap\)/);
    expect(lastNotice()).toContain("sandbox on (bwrap)");
    expect(lastNotice()).toContain("MCP servers restarted under the sandbox: fs, git");
  });

  it("names the MCP error and what still runs unconfined", async () => {
    fakeBridge({
      "session.sandbox_set": onReply({ mcp_error: "daemon kept its old setting", still_running: ["bg:1 (pytest)"] }),
    });
    await dispatchSlashCommand("/sandbox on");
    expect(lastNotice()).toContain("⚠ MCP: daemon kept its old setting");
    expect(lastNotice()).toContain("still running without the sandbox: bg:1 (pytest); stop them if needed");
  });

  it("default off sends default: true and the row goes back to the off notice", async () => {
    const off = wireSandbox({ source: "slash" });
    const calls = fakeBridge({ "session.sandbox_set": onReply({ sandbox: off, state: { session: { sandbox: off } } as never }) });
    const result = await dispatchSlashCommand("/sandbox default off");
    expect(result.kind).toBe("handled");
    expect(calls[0]!.params).toEqual({ mode: "off", default: true });
    expect(activeTab().sandbox?.mode).toBe("off");
    expect(keyedRows().at(-1)!.content).toBe(`⚠ ${OFF_NOTICE}`);
    expect(lastNotice()).toContain("default for new sessions: off");
  });

  it("a typed refusal is an error notice with its reason, and nothing changes", async () => {
    setScopedField("sandbox", mapSandbox(wireSandbox()));
    fakeBridge({
      "session.sandbox_set": new BridgeRequestError("sandbox.unavailable: bwrap not found", undefined, "SandboxBridgeError", {
        code: "sandbox.unavailable",
        reason: "bwrap not found — install bubblewrap",
      }),
    });
    const result = await dispatchSlashCommand("/sandbox on");
    expect(result).toEqual({
      kind: "error",
      message: "⚠ sandbox not changed — sandbox.unavailable: bwrap not found — install bubblewrap",
    });
    expect(activeTab().sandbox?.mode).toBe("off");
  });

  it("a frontend session is refused without a bridge call", async () => {
    setScopedField("sessionVenue", "frontend");
    const calls = fakeBridge({});
    const result = await dispatchSlashCommand("/sandbox on");
    expect(result.kind).toBe("error");
    expect((result as { message: string }).message).toContain("server's sandbox");
    expect(calls).toEqual([]);
  });

  it("an unknown verb gives the usage", async () => {
    const calls = fakeBridge({});
    const result = await dispatchSlashCommand("/sandbox sideways");
    expect(result).toEqual({ kind: "error", message: "usage: /sandbox [status|on|off|default on|default off]" });
    expect(calls).toEqual([]);
  });
});

describe("/sandbox on|off while an answer streams (review LC-1)", () => {
  it("is refused without a bridge call: a parked set would miss the turn", async () => {
    setScopedField("chatStreaming", true);
    const calls = fakeBridge({ "session.sandbox_set": onReply(), "session.sandbox_get": { sandbox: wireSandbox() } });
    const result = await dispatchSlashCommand("/sandbox on");
    expect(result.kind).toBe("error");
    expect((result as { message: string }).message).toContain("wait for the answer to finish");
    expect(calls).toEqual([]);
    expect((await dispatchSlashCommand("/sandbox status")).kind).toBe("handled"); // a read still answers
  });

  it("a timed-out set reads the state back instead of saying 'not changed'", async () => {
    fakeBridge({
      "session.sandbox_set": new Error("bridge request timed out: session.sandbox_set"),
      "session.sandbox_get": { sandbox: wireSandbox({ mode: "on", source: "slash", notice: "" }) },
    });
    const result = await dispatchSlashCommand("/sandbox on");
    const message = (result as { message: string }).message;
    expect(message).not.toContain("not changed");
    expect(message).toContain("the sandbox is on now");
    expect(activeTab().sandbox?.mode).toBe("on");
  });
});

describe("/sandbox status", () => {
  it("bare and status read session.sandbox_get and show the details", async () => {
    const reply: RpcResult<"session.sandbox_get"> = { sandbox: wireSandbox({ mode: "on", source: "flag", notice: "" }) };
    for (const line of ["/sandbox", "/sandbox status"]) {
      const calls = fakeBridge({ "session.sandbox_get": reply });
      expect((await dispatchSlashCommand(line)).kind).toBe("handled");
      expect(calls[0]!.method).toBe("session.sandbox_get");
      expect(lastNotice()).toContain("sandbox: on (source: flag)");
      expect(lastNotice()).toContain("writes: /work/proj");
      expect(activeTab().sandbox?.mode).toBe("on");
    }
  });

  it("a failed read is an error", async () => {
    fakeBridge({ "session.sandbox_get": new Error("unknown method: session.sandbox_get") });
    const result = await dispatchSlashCommand("/sandbox status");
    expect(result.kind).toBe("error");
    expect((result as { message: string }).message).toContain("unknown method");
  });
});

describe("offline backend", () => {
  it("needs a live backend", async () => {
    const before = process.env.R4_TUI_BACKEND;
    process.env.R4_TUI_BACKEND = "offline";
    try {
      const result = await dispatchSlashCommand("/sandbox on");
      expect(result).toEqual({ kind: "error", message: "sandbox: requires a live backend" });
    } finally {
      if (before === undefined) delete process.env.R4_TUI_BACKEND;
      else process.env.R4_TUI_BACKEND = before;
    }
  });
});

// tui-lane review 2026-10-30: a set can take up to 60 s (self-test + MCP
// restart). The text once went to whichever tab was on screen when it
// resolved, while the state and the keyed row landed on the tab that asked.
describe("a tab switch while /sandbox runs", () => {
  beforeEach(() => resetTabsRegistryForTests());
  afterEach(() => resetTabsRegistryForTests());

  function switchingBridge(answer: unknown, toTab: string): void {
    setCompanionBridgeForTests({
      ensureStarted: async () => {},
      shutdown: async () => {},
      cancelTurn: () => {},
      abortInFlightSend: () => {},
      respondAskUser: () => {},
      respondApproval: () => {},
      request: async () => {
        actions.tabActivate(toTab);
        if (answer instanceof Error) throw answer;
        return answer;
      },
    } as never);
  }

  function texts(tabId: string): string[] {
    return getRuntime(tabId).tabStore!.state.chatMessages.map((m) => String(m.content ?? ""));
  }

  function twoTabs(): { tabA: string; tabB: string } {
    const tabA = workspace.activeChatTabId;
    const tabB = actions.tabCreate({ sessionId: "sess-B", title: "B" })!;
    actions.tabActivate(tabA);
    return { tabA, tabB };
  }

  it("the reply lands on the tab that asked, not the one on screen", async () => {
    const { tabA, tabB } = twoTabs();
    switchingBridge(onReply({ restarted_mcp: ["fs"] }), tabB);
    expect((await dispatchSlashCommand("/sandbox on")).kind).toBe("handled");
    expect(workspace.activeChatTabId).toBe(tabB);
    expect(workspace.chatMessages.some((m) => /MCP servers restarted/.test(m.content))).toBe(false);
    expect(texts(tabA).some((t) => t.includes("MCP servers restarted under the sandbox: fs"))).toBe(true);
  });

  it("a refusal after the switch is posted on the asking tab too", async () => {
    const { tabA, tabB } = twoTabs();
    switchingBridge(new Error("bridge went away"), tabB);
    expect((await dispatchSlashCommand("/sandbox status")).kind).toBe("handled");
    expect(workspace.chatMessages.some((m) => m.content.includes("bridge went away"))).toBe(false);
    expect(texts(tabA).some((t) => t.includes("sandbox status failed — bridge went away"))).toBe(true);
  });
});
