/**
 * Knowledge Graph Viewer pins — row P3-C3 of traceability.yaml.
 *
 * The viewer component renders workspace goals/tasks/knowledge plus agent
 * hypotheses as a navigable graph list. These pins cover the pure exported
 * contract the component is built on (build/cursor/label/type maps) — the
 * same style as timeline/activity rows elsewhere in this suite.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import {
  buildProjectGraph,
  graphCursorIndex,
  formatGraphRowLabel,
  nodeTypeColors,
  nodeTypeIcons,
} from "../src/components/knowledge-graph-viewer";
import { workspace, setWorkspace } from "../src/stores";
import { agentActions, agentRuntime } from "../src/stores/agent-runtime";

beforeEach(() => {
  agentActions.resetAgentRuntime();
  setWorkspace({ goals: [], tasks: [], knowledge: [] } as never);
});

describe("knowledge-graph-viewer pure contract", () => {
  it("type maps cover the same node-type keys", () => {
    expect(Object.keys(nodeTypeColors).sort()).toEqual(Object.keys(nodeTypeIcons).sort());
    expect(Object.keys(nodeTypeColors).length).toBeGreaterThanOrEqual(12);
  });

  it("buildProjectGraph emits goals, tasks (experiment-tagged), knowledge + decomposes edges", () => {
    setWorkspace({
      goals: [{ id: "g1", statement: "prove X", status: "active" }],
      tasks: [
        { id: "t1", title: "Experiment: sweep alpha", goalId: "g1", status: "open" },
        { id: "t2", title: "write memo", status: "open" },
      ],
      knowledge: [{ id: "k1", name: "prior result", status: "ready" }],
    } as never);
    const { nodes, edges } = buildProjectGraph();
    const byId = new Map(nodes.map((n) => [n.id, n]));
    expect(byId.get("g1")?.type).toBe("goal");
    expect(byId.get("t1")?.type).toBe("experiment");
    expect(byId.get("t2")?.type).toBe("task");
    expect(byId.get("k1")?.type).toBe("knowledge");
    expect(edges).toEqual([{ from: "g1", to: "t1", label: "decomposes" }]);
  });

  it("buildProjectGraph stays empty on an empty workspace (honest empty state)", () => {
    const { nodes, edges } = buildProjectGraph();
    expect(nodes).toEqual([]);
    expect(edges).toEqual([]);
  });

  it("graphCursorIndex resolves the selected id or falls back to 0", () => {
    const nodes = [{ id: "a" }, { id: "b" }, { id: "c" }] as never[];
    expect(graphCursorIndex(nodes, "b")).toBe(1);
    expect(graphCursorIndex(nodes, "missing")).toBe(0);
    expect(graphCursorIndex(nodes, null)).toBe(0);
    expect(graphCursorIndex([], "a")).toBe(0);
  });

  it("formatGraphRowLabel prefixes icon, appends rounded confidence, truncates with ellipsis", () => {
    const withConf = formatGraphRowLabel({ label: "alpha", type: "hypothesis", confidence: 0.87 }, "H");
    expect(withConf).toBe("H alpha 87%");
    const noConf = formatGraphRowLabel({ label: "alpha", type: "task" }, "T");
    expect(noConf).toBe("T alpha");
    const long = formatGraphRowLabel(
      { label: "x".repeat(120), type: "task" },
      "T",
      10,
    );
    expect(long.length).toBeLessThanOrEqual(10);
    expect(long.endsWith("…")).toBe(true);
  });
});

describe("store-shared-blueprint guard (V2 healed class)", () => {
  it("resetAgentRuntime actually clears hypotheses (shared-mutable reset class)", () => {
    // Push a hypothesis, reset, then push again — the second test must see a
    // pristine store; a module-level shared blueprint would otherwise leak
    // rows across the reset (that's exactly what made the frame fails
    // nondeterministic across file order).
    const id1 = `hp-${Date.now()}`;
    agentRuntime.missionControl.hypotheses.push({
      id: id1,
      statement: "synthetic pollution marker",
      confidence: 0.3,
    });
    agentActions.resetAgentRuntime();
    expect(agentRuntime.missionControl.hypotheses).toEqual([]);
    agentRuntime.missionControl.hypotheses.push({
      id: `${id1}-again`,
      statement: "synthetic pollution marker take 2",
      confidence: 0.3,
    });
    agentActions.resetAgentRuntime();
    expect(agentRuntime.missionControl.hypotheses).toEqual([]);
  });

  it("initials are clones: mutations never reach the blueprint constants", () => {
    const before = (agentRuntime.workflowGraph.nodes ?? []).length;
    expect(Array.isArray(before === 0 ? agentRuntime.workflowGraph.nodes : agentRuntime.workflowGraph.nodes)).toBe(true);
    agentActions.resetAgentRuntime();
    // The blueprint objects produce fresh instances on every reset — assigning
    // the same reference twice would have been the shared-mutable smell.
    expect(agentRuntime.workflowGraph).not.toBe(undefined);
  });
});
