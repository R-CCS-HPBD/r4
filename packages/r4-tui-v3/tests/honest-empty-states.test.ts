import { describe, expect, it, beforeEach, afterEach } from "bun:test";
import { agentActions, agentRuntime, loadDemoWorkflowGraph } from "../src/stores/agent-runtime";
import { loopActions, loopStore } from "../src/stores/autonomous-loop";
import { resolveCurrentWorkflowNodeId } from "../src/components/workflow-graph-view";
import { setDemoMode, setDemoVisuals } from "../src/lib/console-mode";
import type { WorkflowNode } from "../src/types";

beforeEach(() => {
  agentActions.resetAgentRuntime();
  setDemoMode(false);
  setDemoVisuals(false);
});

afterEach(() => {
  setDemoMode(false);
  setDemoVisuals(false);
});

describe("honest empty states", () => {
  it("workflow graph is empty after reset in live mode", () => {
    expect(agentRuntime.workflowGraph.nodes).toEqual([]);
  });

  it("CURRENT marker only applies to running nodes", () => {
    const nodes: WorkflowNode[] = [
      { id: "start", type: "knowledge", label: "Start", status: "completed" },
      { id: "ingest", type: "knowledge", label: "Ingest", status: "pending" },
    ];
    expect(resolveCurrentWorkflowNodeId(nodes)).toBeNull();

    nodes[1] = { ...nodes[1], status: "running" };
    expect(resolveCurrentWorkflowNodeId(nodes)).toBe("ingest");
  });

  it("loadDemoWorkflowGraph restores offline demo DAG", () => {
    loadDemoWorkflowGraph();
    expect(agentRuntime.workflowGraph.nodes.length).toBe(8);
  });

  it("autonomous loop does not run iterations in live mode", () => {
    const before = loopStore.iteration;
    const result = loopActions.runIteration();
    expect(loopStore.iteration).toBe(before);
    expect(result.phase).toBe("idle");
  });

  it("autonomous loop runs iterations in demo mode", () => {
    setDemoMode(true);
    setDemoVisuals(true);
    agentActions.updateMissionControl({
      hypotheses: [{ id: "H1", statement: "Test", confidence: 0.5 }],
      computeBudget: { remaining: 100, burnRate: 5, estimatedRunway: 20 },
    });
    loopActions.runIteration();
    expect(loopStore.iteration).toBeGreaterThan(0);
  });
});
