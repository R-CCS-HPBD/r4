/**
 * /acquisition — session knowledge write mode (user-switchable mid-session).
 * Parser contract + dispatch → bridge verb → store slice + honest summary.
 */
import { describe, test, expect, beforeEach } from "bun:test";
import { parseAcquisitionArgs } from "../src/lib/acquisition";
import { dispatchSlashCommand } from "../src/commands/slash";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { actions, workspace, resetWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

describe("parseAcquisitionArgs", () => {
  test("empty args read the current mode", () => {
    expect(parseAcquisitionArgs("")).toEqual({ ok: true, params: {} });
    expect(parseAcquisitionArgs("   ")).toEqual({ ok: true, params: {} });
  });

  test("open / off map to modes", () => {
    expect(parseAcquisitionArgs("open")).toEqual({ ok: true, params: { mode: "open" } });
    expect(parseAcquisitionArgs("off")).toEqual({ ok: true, params: { mode: "disabled" } });
    expect(parseAcquisitionArgs("disable")).toEqual({ ok: true, params: { mode: "disabled" } });
  });

  test("extend pins one graph", () => {
    expect(parseAcquisitionArgs("extend experiment-history")).toEqual({
      ok: true,
      params: { mode: "pinned", graphs: ["experiment-history"] },
    });
  });

  test("extend accepts comma-separated multi-graph pins", () => {
    expect(parseAcquisitionArgs("extend g1, g2")).toEqual({
      ok: true,
      params: { mode: "pinned", graphs: ["g1", "g2"] },
    });
  });

  test("extend without a graph is a usage error", () => {
    const res = parseAcquisitionArgs("extend");
    expect(res.ok).toBe(false);
  });

  test("unknown verb is a usage error", () => {
    expect(parseAcquisitionArgs("frobnicate").ok).toBe(false);
  });
});

describe("/acquisition dispatch", () => {
  beforeEach(() => {
    stopBackendPoller();
    stopKnowledgePoller();
    setDemoMode(false);
    agentActions.resetAgentRuntime();
    resetWorkspace();
    actions.closeAllOverlays();
  });

  function fakeBridge(reply: Record<string, unknown>) {
    const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
    return {
      calls,
      request: async (method: string, params: Record<string, unknown>) => {
        calls.push({ method, params });
        return reply;
      },
    };
  }

  test("extend sends pinned mode, updates the store slice, prints summary", async () => {
    const bridge = fakeBridge({
      summary: "pinned — this chat may only extend: experiment-history",
      acquisition: { mode: "pinned", graphs: ["experiment-history"] },
      state: {
        session: {
          knowledge_acquisition: { mode: "pinned", graphs: ["experiment-history"] },
        },
      },
    });
    setCompanionBridgeForTests(bridge as never);
    try {
      const result = await dispatchSlashCommand("/acquisition extend experiment-history");
      expect(result.kind).toBe("handled");
      expect(bridge.calls).toEqual([
        { method: "set_knowledge_acquisition", params: { mode: "pinned", graphs: ["experiment-history"] } },
      ]);
      expect(workspace.knowledgeAcquisition).toEqual({
        mode: "pinned",
        graphs: ["experiment-history"],
      });
      const notice = workspace.chatMessages.at(-1)!.content;
      expect(notice).toContain("experiment-history");
    } finally {
      setCompanionBridgeForTests(null);
    }
  });

  test("off sends disabled mode", async () => {
    const bridge = fakeBridge({
      summary: "disabled — this chat must not create or extend any knowledge",
      acquisition: { mode: "disabled", graphs: [] },
      state: { session: { knowledge_acquisition: { mode: "disabled", graphs: [] } } },
    });
    setCompanionBridgeForTests(bridge as never);
    try {
      const result = await dispatchSlashCommand("/acquisition off");
      expect(result.kind).toBe("handled");
      expect(bridge.calls[0]!.params).toEqual({ mode: "disabled" });
      expect(workspace.knowledgeAcquisition.mode).toBe("disabled");
    } finally {
      setCompanionBridgeForTests(null);
    }
  });

  test("bare /acquisition only reads (no mode param sent)", async () => {
    const bridge = fakeBridge({
      summary: "open — agent may propose/extend knowledge",
      acquisition: { mode: "open", graphs: [] },
      state: { session: { knowledge_acquisition: { mode: "open", graphs: [] } } },
    });
    setCompanionBridgeForTests(bridge as never);
    try {
      const result = await dispatchSlashCommand("/acquisition");
      expect(result.kind).toBe("handled");
      expect(bridge.calls[0]!.params).toEqual({});
    } finally {
      setCompanionBridgeForTests(null);
    }
  });

  test("bad usage never reaches the bridge", async () => {
    const bridge = fakeBridge({});
    setCompanionBridgeForTests(bridge as never);
    try {
      const result = await dispatchSlashCommand("/acquisition extend");
      expect(result.kind).toBe("error");
      expect(bridge.calls).toEqual([]);
    } finally {
      setCompanionBridgeForTests(null);
    }
  });
});
