/**
 * Pins for T5 topic-13: live workflow execution → workflow-graph projection.
 */
import { describe, expect, it, beforeEach } from "bun:test";
import { agentActions, agentRuntime } from "../src/stores/agent-runtime";
import { projectWorkflowExecutionOntoGraph } from "../src/lib/workflow-graph-projection";

beforeEach(() => {
  agentActions.resetAgentRuntime();
});

describe("workflow-graph projection", () => {
  it("projects one execution's steps into nodes+edges with statuses", () => {
    projectWorkflowExecutionOntoGraph({
      execution_id: "exec-42",
      status: "running",
      results: {
        steps: {
          ingest: { status: "completed" },
          extract: { status: "running" },
          review: { status: "waiting_approval" },
        },
      },
    } as never);
    const nodes = agentRuntime.workflowGraph.nodes;
    const edges = agentRuntime.workflowGraph.edges;
    expect(nodes.length).toBe(3);
    expect(nodes.map((n) => n.id)).toEqual([
      "exec-42:ingest",
      "exec-42:extract",
      "exec-42:review",
    ]);
    expect(nodes[0].status).toBe("completed");
    expect(nodes[1].status).toBe("running");
    expect(nodes[2].status).toBe("waiting");
    expect(nodes.every((n) => n.type)).toBe(true);
    expect(edges.length).toBe(2);
    expect(edges[0].from).toBe("exec-42:ingest");
    expect(edges[0].to).toBe("exec-42:extract");
  });

  it("no-ops on executions whose payload carries no step topology", () => {
    agentActions.resetAgentRuntime();
    projectWorkflowExecutionOntoGraph({
      execution_id: "exec-quiet",
      status: "queued",
    } as never);
    expect(agentRuntime.workflowGraph.nodes.length).toBe(0);
  });

  it("replaces the graph on subsequent projection calls (replaces-in-place)", () => {
    projectWorkflowExecutionOntoGraph({
      execution_id: "exec-x",
      status: "running",
      results: { steps: { ingest: { status: "running" } } },
    } as never);
    expect(agentRuntime.workflowGraph.nodes.length).toBe(1);
    projectWorkflowExecutionOntoGraph({
      execution_id: "exec-y",
      status: "running",
      results: {
        steps: {
          ingest: { status: "completed" },
          extract: { status: "running" },
        },
      },
    } as never);
    expect(agentRuntime.workflowGraph.nodes.length).toBe(2);
    expect(agentRuntime.workflowGraph.nodes[0].id).toBe("exec-y:ingest");
  });
});
