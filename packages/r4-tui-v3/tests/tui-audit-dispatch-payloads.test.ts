/**
 * TUI audit 2026-10-17 — v3 handlers read the payload shapes the bridge
 * really returns (provenance lineage graph, checkpoint rows, workflow
 * describe, knowledge graph content), and a command whose args are optional
 * runs on a bare Enter.
 */
import { describe, test, expect, beforeEach } from "bun:test";
import { dispatchSlashCommand, needsArgPrompt, resetSlashCatalogCache } from "../src/commands/slash";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { actions, workspace, resetWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { knowledgeGraphContentRows, wfDescribeRows } from "../src/lib/mgmt-card-format";
import type { RpcResult } from "../src/bridge/rpc";

async function run(line: string, replies: Record<string, unknown>) {
  const calls: string[] = [];
  setCompanionBridgeForTests({
    request: async (method: string) => {
      calls.push(method);
      return replies[method] ?? {};
    },
  } as never);
  try {
    return { result: await dispatchSlashCommand(line), calls };
  } finally {
    setCompanionBridgeForTests(null);
  }
}

function lastText(): string {
  return workspace.chatMessages.at(-1)!.content;
}

beforeEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("/provenance <job> lineage", () => {
  // LineageGraph.to_dict (umrs/jobs/lineage_graph.py) — no `lineage` key.
  // Typed by the bridge contract (rpc-types.gen.ts): a drifted fake fails tsc.
  const graph: RpcResult<"provenance.lineage"> = {
    job_id: "j1",
    execution_id: null,
    nodes: [
      { trace_id: "t1", step_name: "fetch", step_type: "tool", status: "succeeded", started_at: null, finished_at: null, duration_seconds: 1.5 },
      { trace_id: "t2", step_name: "fit", step_type: "compute", status: "failed", started_at: null, finished_at: null, duration_seconds: null },
    ],
    edges: [{ source: "t1", target: "t2" }],
    root_ids: ["t1"],
    leaf_ids: ["t2"],
    metrics: { total_nodes: 2, total_edges: 1, depth: 2 },
  };

  test("renders the graph nodes instead of reporting none", async () => {
    await run("/provenance j1 lineage", { "provenance.lineage": graph });
    const text = lastText();
    expect(text).not.toContain("none for job");
    expect(text).toContain("2 steps");
    expect(text).toContain("1 edges");
    expect(text).toContain("fetch");
    expect(text).toContain("fit");
    expect(text).toContain("failed");
  });

  test("an empty graph still says none", async () => {
    await run("/provenance j1 lineage", { "provenance.lineage": { ...graph, nodes: [], edges: [] } });
    expect(lastText()).toContain("provenance lineage — none for job j1");
  });
});

describe("/graph <id>", () => {
  // GraphRegistry.graph_content (umrs/knowledge/graph_registry.py).
  const content = {
    graph_id: "g-1",
    name: "mof-lit",
    kind: "literature",
    project_id: "p1",
    owner_id: "alice",
    nodes: [
      { id: "n1", label: "MOF-5", type: "material" },
      { id: "n2", label: "BET surface", type: "property" },
    ],
    edges: [{ from: "n1", to: "n2" }],
    truncated: true,
  };

  test("shows the graph content on its own card, not only '<graph_id> [kind]'", async () => {
    const { calls } = await run("/graph g-1", { knowledge_graph_content: content });
    expect(calls).toEqual(["knowledge_graph_content"]);
    // One content card, no interim summary notice beside it.
    const messages = workspace.chatMessages.filter((m) => m.role === "system");
    expect(messages.length).toBe(1);
    const card = messages[0]!.mgmtCard!;
    expect(card.type).toBe("knowledge-graph-content");
    const { header, nodes } = knowledgeGraphContentRows(card.payload);
    const map = Object.fromEntries(header.map((r) => [r.key, r.value]));
    expect(map["graph"]).toBe("mof-lit");
    expect(map["owner"]).toBe("alice");
    expect(map["content"]).toBe("2 nodes, 1 edges (truncated)");
    expect(nodes).toEqual([["MOF-5", "material"], ["BET surface", "property"]]);
  });
});

describe("/checkpoint list", () => {
  test("reads the id and timestamp the checkpoint store returns", async () => {
    // CheckpointManager.list_checkpoints: SELECT id, agent_id, timestamp, checkpoint_type
    const rows = [
      { id: "cp-1", agent_id: "agent", timestamp: "2026-10-17T09:00:00", checkpoint_type: "manual" },
    ];
    await run("/checkpoint list", { list_checkpoints: { checkpoints: rows } });
    const text = lastText();
    expect(text).toContain("cp-1");
    expect(text).toContain("2026-10-17T09:00:00");
    expect(text).not.toContain("?");
  });
});

describe("/workflow show", () => {
  test("the card carries the flat describe payload", async () => {
    const describePayload = {
      workflow_name: "relax",
      version: "1.2.0",
      description: "Relax a structure",
      steps: [{ name: "a" }, { name: "b" }],
    };
    await run("/workflow show relax", { workflow_describe: describePayload });
    const card = workspace.chatMessages.at(-1)!.mgmtCard!;
    expect(card.type).toBe("wf-show");
    const rows = wfDescribeRows(card.payload);
    expect(rows).toContainEqual({ key: "version", value: "1.2.0" });
    expect(rows).toContainEqual({ key: "steps", value: "2 item(s)" });
  });
});

describe("needsArgPrompt with optional args", () => {
  test("help that only shows an optional [arg] dispatches bare", () => {
    // bridge list_slash_commands marks /export takesArgs from its "[path.md]".
    const exportRow = { takesArgs: true, help: "Preview markdown or save: /export [path.md]" };
    expect(needsArgPrompt(exportRow, "")).toBe(false);
  });

  test("bare /export reaches the companion instead of re-inserting '/export '", async () => {
    resetSlashCatalogCache();
    const exportRow = {
      name: "export",
      help: "Preview markdown or save: /export [path.md]",
      category: "session",
      takesArgs: true,
    };
    const { result, calls } = await run("/export", {
      list_slash_commands: { commands: [exportRow] },
      dispatch_slash_command: { handled: true, output: ["# preview"] },
    });
    resetSlashCatalogCache();
    expect(result.kind).toBe("handled");
    expect(calls).toEqual(["list_slash_commands", "dispatch_slash_command"]);
  });

  test("a required <arg> still prompts", () => {
    expect(needsArgPrompt({ takesArgs: true, help: "Ask: /btw <question> [--x]" }, "")).toBe(true);
    expect(needsArgPrompt({ takesArgs: true, argsHint: "<question>", help: "Side question" }, "")).toBe(true);
  });
});
