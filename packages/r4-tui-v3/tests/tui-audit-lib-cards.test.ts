/**
 * TUI audit 2026-10-17 (ts-lib lane) — cards and lists read the payload
 * shapes the bridge really sends: research_session.view (dict loops and
 * sessions), get_knowledge (KnowledgeBase.get_stats), workflow.results
 * (dict outputs), evidence.list (evidence_id, content), workflow.detail
 * (`{execution: …}` wrapper), and the catalog skill toggle reads the
 * skill's real state.
 */
import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import {
  knowledgeInventoryRows,
  sessionViewRows,
  wfResultsRows,
} from "../src/lib/mgmt-card-format";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { umrsWorkflowActions } from "../src/stores/umrs-workflow";
import { waitingApprovalStepName } from "../src/lib/workflow-graph-projection";
import { toggleSkill } from "../src/bridge/catalog-bridge";
import { handleEvidenceCommand } from "../src/commands/evidence-command";

type Calls = Array<{ method: string; params: Record<string, unknown> }>;

function fakeBridge(replies: Record<string, unknown>): Calls {
  const calls: Calls = [];
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    request: async (method: string, params: Record<string, unknown> = {}) => {
      calls.push({ method, params });
      return replies[method] ?? {};
    },
  } as never);
  return calls;
}

function value(rows: Array<{ key: string; value: string }>, key: string): string | undefined {
  return rows.find((r) => r.key === key)?.value;
}

beforeEach(() => {
  setDemoMode(false);
  resetWorkspace();
  actions.closeAllOverlays();
});

afterEach(() => setCompanionBridgeForTests(null));

describe("/session view card (#51)", () => {
  // r4/runtime/session_view.py research_session_view.
  const view = {
    project_id: "p1",
    runtime: "umrs",
    authoritative_runtime: "umrs",
    loops: { count: 3, active_count: 1, items: [] },
    sessions: { available: true, count: 2, items: [] },
    knowledge: { nodes: 5, edges: 7 },
  };

  test("loops and sessions rows come from the dict counts", () => {
    const rows = sessionViewRows(view);
    expect(value(rows, "loops")).toBe("3 (1 active)");
    expect(value(rows, "sessions")).toBe("2");
  });

  test("an unavailable durable store says why", () => {
    const rows = sessionViewRows({
      ...view,
      sessions: { available: false, reason: "no project", count: 0, items: [] },
    });
    expect(value(rows, "sessions")).toBe("unavailable (no project)");
  });

  test("array payloads still render", () => {
    const rows = sessionViewRows({ loops: [{ status: "running" }, { status: "done" }], sessions: [{}] });
    expect(value(rows, "loops")).toBe("2 (1 active)");
    expect(value(rows, "sessions")).toBe("1");
  });
});

describe("/knowledge detail card (#53)", () => {
  // bridge get_knowledge → KnowledgeBase.get_stats (no nodes/edges/backend).
  const payload = {
    stats: { patterns: 31, anti_patterns: 4, lessons: 25, avg_confidence: 0.734 },
    patterns: new Array(20).fill({}),
    lessons: new Array(20).fill({}),
    session_id: "s1",
  };

  test("shows the stats the server returns, never invented zeros", () => {
    const rows = knowledgeInventoryRows(payload);
    expect(value(rows, "nodes")).toBeUndefined();
    expect(value(rows, "edges")).toBeUndefined();
    expect(value(rows, "patterns")).toBe("31");
    expect(value(rows, "anti-patterns")).toBe("4");
    expect(value(rows, "lessons")).toBe("25");
    expect(value(rows, "avg confidence")).toBe("0.73");
  });

  test("list lengths stand in when stats lack a count", () => {
    const rows = knowledgeInventoryRows({ patterns: ["a", "b"], lessons: ["c"] });
    expect(value(rows, "patterns")).toBe("2");
    expect(value(rows, "lessons")).toBe("1");
  });
});

describe("/workflow results card (#55)", () => {
  test("dict outputs show the step count and the final result", () => {
    const rows = wfResultsRows({
      execution_id: "e1",
      status: "succeeded",
      artifacts: ["a.txt"],
      outputs: { steps: { fetch: { ok: 1 }, fit: { ok: 1 } }, final: { score: 0.91 } },
    });
    expect(value(rows, "artifacts")).toBe("1 item(s)");
    expect(value(rows, "steps")).toBe("2 step result(s)");
    expect(value(rows, "final")).toContain("0.91");
  });

  test("array outputs still render", () => {
    expect(value(wfResultsRows({ outputs: ["stdout.log"] }), "outputs")).toBe("1 item(s)");
  });
});

describe("/evidence rows (#65)", () => {
  test("rows show the evidence id and its content", async () => {
    fakeBridge({
      "evidence.list": {
        evidence: [
          {
            evidence_id: "evd-0123456789ab",
            claim_id: "clm-1",
            content: "Fit converged at 0.91",
            citation: "doi:10/x",
            status: "active",
            source_type: "experiment",
          },
        ],
        count: 1,
      },
    });
    await handleEvidenceCommand("");
    const text = workspace.chatMessages.at(-1)!.content;
    expect(text).toContain("evd-0123456789ab");
    expect(text).toContain("Fit converged at 0.91");
    expect(text).not.toContain("  ? ·");
  });
});

describe("workflow overlay [a] reads the wrapped detail (#77)", () => {
  test("fetchDetail unwraps {execution} so the waiting gate is found", async () => {
    fakeBridge({
      "workflow.detail": {
        execution: {
          execution_id: "exec-1",
          status: "running",
          results: { steps: { review_gate: { status: "waiting_approval" } } },
        },
      },
    });
    const detail = await umrsWorkflowActions.fetchDetail("exec-1");
    expect(detail.execution_id).toBe("exec-1");
    expect(waitingApprovalStepName(detail)).toBe("review_gate");
  });
});

describe("catalog skill toggle (#63)", () => {
  const row = { kind: "skill" as const, id: "umrs", meta: "builtin", verb: "toggle" as const };

  test("an already-active skill is turned off on the first click", async () => {
    setWorkspace("skills", [{ name: "umrs", source: "builtin", hasMcp: false, description: "", active: true }]);
    const calls = fakeBridge({ activate_skill: {} });
    await toggleSkill(row);
    expect(calls[0]?.params).toEqual({ name: "umrs", active: false });
  });

  test("an inactive skill is turned on", async () => {
    setWorkspace("skills", [{ name: "umrs", source: "builtin", hasMcp: false, description: "", active: false }]);
    const calls = fakeBridge({ activate_skill: {} });
    await toggleSkill(row);
    expect(calls[0]?.params).toEqual({ name: "umrs", active: true });
  });
});
