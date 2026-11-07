/**
 * TUI audit 2026-10-17 — local v3 rows and verbs that shadow a companion
 * command of the same name keep their bare form, and the args form reaches
 * the Python handler through dispatch_slash_command (the handleKnowledgeMgmt
 * pattern). Before the fix every line below was swallowed locally.
 */
import { describe, test, expect, beforeEach } from "bun:test";
import { dispatchSlashCommand } from "../src/commands/slash";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { actions, workspace, resetWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

type Call = { method: string; params: Record<string, unknown> };

function fakeBridge() {
  const calls: Call[] = [];
  return {
    calls,
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      if (method === "dispatch_slash_command") {
        return { handled: true, output: [{ kind: "info", text: "companion answered" }], state: {} };
      }
      if (method.startsWith("loops.")) return { status: "stopping" };
      return { answer: "rag answer", loops: [] };
    },
  };
}

async function run(line: string) {
  const bridge = fakeBridge();
  setCompanionBridgeForTests(bridge as never);
  try {
    return { result: await dispatchSlashCommand(line), calls: bridge.calls };
  } finally {
    setCompanionBridgeForTests(null);
  }
}

function forwarded(command: string): Call[] {
  return [{ method: "dispatch_slash_command", params: { command } }];
}

beforeEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("bare-only local rows forward their args form", () => {
  const lines = [
    "/models default kimi-k3@rikyu",
    "/model kimi-k3@rikyu",
    "/research stop L1",
    "/research start Optimize MOF-5",
    "/overview myproj",
    "/skills on literature",
    "/skill off code",
    "/mcp connect all",
    "/governance quotas list",
    "/gov shares",
  ];
  for (const line of lines) {
    test(line, async () => {
      const { result, calls } = await run(line);
      expect(result.kind).toBe("handled");
      expect(calls).toEqual(forwarded(line));
      expect(workspace.listOverlay).toBeNull();
    });
  }

  test("bare /research, /overview, /skills, /mcp and /governance stay local", async () => {
    expect((await run("/research")).calls).toEqual([]);
    expect(workspace.paneView).toBe("research");
    expect((await run("/overview")).calls).toEqual([]);
    expect(workspace.paneView).toBe("dashboard");
    for (const [line, overlay] of [
      ["/skills", "skills"],
      ["/mcp", "mcp"],
      ["/governance", "governance"],
    ] as const) {
      const { calls } = await run(line);
      expect(calls).toEqual([]);
      expect(workspace.listOverlay).toBe(overlay);
      actions.closeAllOverlays();
    }
  });
});

describe("/code", () => {
  test("a workbench verb runs the companion /code, not the coding toggle", async () => {
    for (const line of ["/code status", "/code run a.py", "/coding diff"]) {
      const { calls } = await run(line);
      const args = line.slice(line.indexOf(" ") + 1);
      expect(calls).toEqual(forwarded(`/code ${args}`));
      expect(workspace.codingMode).toBe(false);
    }
  });

  // r2 followup: the companion no longer runs the turn inside
  // dispatch_slash_command; it hands the prompt back (follow_up_messages)
  // and v3 sends it as a streamed chat turn (tui-r2-glue-slash.test.ts).
  test("edit/review/fix reach the companion /code, which hands back a chat turn", async () => {
    for (const line of ["/code review a.py", "/code edit a.py --details x", "/code fix"]) {
      const { result, calls } = await run(line);
      expect(result.kind).toBe("handled");
      expect(calls).toEqual(forwarded(`/code ${line.slice("/code ".length)}`));
      expect(workspace.codingMode).toBe(false);
    }
  });

  test("bare, on and off keep the toggle", async () => {
    expect((await run("/code")).calls).toEqual(forwarded("/skills on code"));
    expect((await run("/code on")).calls).toEqual(forwarded("/skills on code"));
    expect((await run("/code off")).calls).toEqual(forwarded("/skills off code"));
  });
});

describe("/job", () => {
  test("a Python verb is forwarded instead of read as a job id", async () => {
    for (const line of ["/job list", "/job cancel j1", "/job retry j1", "/job describe j1"]) {
      const { calls } = await run(line);
      expect(calls).toEqual(forwarded(line));
      expect(workspace.listOverlay).toBeNull();
    }
  });

  test("a bare id still opens the attempt timeline", async () => {
    const { calls } = await run("/job j-123");
    expect(calls).toEqual([]);
    expect(workspace.listOverlay).toBe("jobgraph");
    expect(workspace.overlayTargetId).toBe("j-123");
  });
});

describe("/rag", () => {
  test("docs/list/documents/upload reach the companion /rag", async () => {
    for (const line of ["/rag docs", "/rag list", "/rag documents", "/rag upload paper.pdf"]) {
      expect((await run(line)).calls).toEqual(forwarded(line));
    }
  });

  test("a leading 'query' is not part of the question", async () => {
    const { calls } = await run("/rag query what is MOF-5 top_k=3");
    expect(calls).toEqual([{ method: "rag.query", params: { query: "what is MOF-5", top_k: 3 } }]);
  });

  test("a plain question keeps the typed rag.query lane", async () => {
    const { calls } = await run("/rag what is MOF-5");
    expect(calls).toEqual([{ method: "rag.query", params: { query: "what is MOF-5" } }]);
  });
});

describe("/loop control verbs", () => {
  test("stop runs the runtime-aware companion /research stop", async () => {
    const { result, calls } = await run("/loop stop L1");
    expect(result.kind).toBe("handled");
    expect(calls).toEqual(forwarded("/research stop L1"));
  });

  test("pause/resume ride the loops.<verb> RPCs", async () => {
    for (const verb of ["pause", "resume"]) {
      const { result, calls } = await run(`/loop ${verb} L1`);
      expect(result.kind).toBe("handled");
      expect(calls).toEqual([{ method: `loops.${verb}`, params: { loop_id: "L1" } }]);
      expect(workspace.paneView).not.toBe("loop");
    }
  });

  test("a control verb without an id is a usage error", async () => {
    const { result, calls } = await run("/loop stop");
    expect(result.kind).toBe("error");
    expect(calls).toEqual([]);
  });

  test("start launches through the companion /research start", async () => {
    const { calls } = await run("/loop start Optimize MOF-5");
    expect(calls).toEqual(forwarded("/research start Optimize MOF-5"));
  });

  // 2026-11-03 (maintainer: "loop command should inform more kindly"): an
  // unknown verb used to answer one terse usage line; it now shows the help.
  const lastNotice = () => workspace.chatMessages.filter((m) => m.role === "system").at(-1)?.content ?? "";

  test("an unknown verb shows the whole help, never the builder pane", async () => {
    const { result, calls } = await run("/loop frobnicate");
    expect(result.kind).toBe("handled");
    expect(calls).toEqual([]);
    expect(workspace.paneView).not.toBe("loop");
    expect(lastNotice()).toContain('"frobnicate" is not a /loop verb');
    expect(lastNotice()).toContain("/loop start <goal>");
  });

  test("/loop help explains the verbs, that it is not a timer, and which models it uses", async () => {
    for (const line of ["/loop help", "/loop ?"]) {
      const { result, calls } = await run(line);
      expect(result.kind).toBe("handled");
      expect(calls).toEqual([]);
      const text = lastNotice();
      expect(text).toContain("/loop status <id>");
      expect(text).toContain("Not a timer");
      expect(text).toContain("/project register");
      expect(text).toContain("svc-* service models");
    }
  });

  test("start without a goal gives an example, never the companion's /research usage", async () => {
    const { result, calls } = await run("/loop start");
    expect(result).toEqual({ kind: "error", message: expect.stringContaining('/loop start "Screen MOF-5') });
    expect(calls).toEqual([]);
  });

  test("a control verb without an id names how to find one", async () => {
    const { result } = await run("/loop pause");
    expect(result).toEqual({ kind: "error", message: expect.stringContaining("(ids: /loop list)") });
  });
});

describe("/traces", () => {
  test("the companion /trace verbs are forwarded, not read as a job id", async () => {
    const { calls } = await run("/traces list --job j1");
    expect(calls).toEqual(forwarded("/trace list --job j1"));
  });

  test("a job id keeps the typed traces.job lane", async () => {
    const { calls } = await run("/traces j1");
    expect(calls.map((c) => c.method)).toEqual(["traces.job"]);
  });
});
