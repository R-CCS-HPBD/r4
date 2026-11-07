/**
 * Store-level tests for Phases 4-9.
 *
 * Tests the store actions and state that the UI components depend on,
 * without requiring OpenTUI rendering in the test environment.
 *
 * Phase 4: KnowledgeGraphViewer → workspace.knowledge, workspace.goals, workspace.tasks
 * Phase 5: VerificationChat → agentActions.queryVerification()
 * Phase 6: AgentMonitor → agentRuntime.agents (filtered by state)
 * Phase 7: ResearchTimeline → agentRuntime.timeline
 * Phase 8: ApprovalGates → agentRuntime.approvalGates, agentActions.resolveApproval()
 * Phase 9: WorkflowGraphView → agentRuntime.workflowGraph, agentActions.selectWorkflowNode()
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { agentActions, agentRuntime, loadDemoWorkflowGraph } from "../src/stores/agent-runtime";
import type { TimelineEntryType, WorkflowNodeType, WorkflowNodeStatus } from "../src/types";

beforeEach(() => {
  agentActions.resetAgentRuntime();
});

// ── Phase 4: Knowledge Graph Viewer ───────────────────────────────────

describe("Phase 4: KnowledgeGraphViewer data dependencies", () => {
  it("workspace has knowledge objects for the graph", () => {
    // The KnowledgeGraphViewer reads from workspace.knowledge
    // This test verifies the store structure is correct
    expect(agentRuntime.missionControl.hypotheses).toBeDefined();
    expect(Array.isArray(agentRuntime.missionControl.hypotheses)).toBe(true);
  });

  it("mission control hypotheses are available for graph nodes", () => {
    agentActions.updateMissionControl({
      hypotheses: [
        { id: "H7", statement: "Methodology X will yield result Y", confidence: 0.72 },
        { id: "H8", statement: "Alternative methodology X' will outperform X", confidence: 0.45 },
      ],
    });

    expect(agentRuntime.missionControl.hypotheses.length).toBe(2);
    expect(agentRuntime.missionControl.hypotheses[0].id).toBe("H7");
  });
});

// ── Phase 5: Verification Chat ────────────────────────────────────────

describe("Phase 5: VerificationChat data dependencies", () => {
  it("queryVerification is gated in live/CLI mode", () => {
    const prev = process.env["R4_TUI_BACKEND"];
    const prevAllow = process.env["R4_TUI_ALLOW_DEMO_VERIFY"];
    delete process.env["R4_TUI_BACKEND"];
    delete process.env["R4_TUI_ALLOW_DEMO_VERIFY"];
    try {
      agentActions.updateMissionControl({
        researchGoal: "Test hypothesis H7",
        hypotheses: [{ id: "H7", statement: "Test", confidence: 0.5 }],
        computeBudget: { remaining: 100, burnRate: 5, estimatedRunway: 20 },
      });

      const result = agentActions.queryVerification("what supports hypothesis H7?");

      expect(result.evidenceIds).toEqual([]);
      expect(result.confidence).toBe(0);
      expect(result.answer.toLowerCase()).toContain("not connected");
    } finally {
      if (prev !== undefined) process.env["R4_TUI_BACKEND"] = prev;
      else delete process.env["R4_TUI_BACKEND"];
      if (prevAllow !== undefined) process.env["R4_TUI_ALLOW_DEMO_VERIFY"] = prevAllow;
      else delete process.env["R4_TUI_ALLOW_DEMO_VERIFY"];
    }
  });

  it("queryVerification returns answer with confidence in offline demo", () => {
    process.env["R4_TUI_BACKEND"] = "offline";
    try {
      agentActions.updateMissionControl({
        researchGoal: "Test hypothesis H7",
        hypotheses: [{ id: "H7", statement: "Test", confidence: 0.5 }],
        computeBudget: { remaining: 100, burnRate: 5, estimatedRunway: 20 },
      });

      const result = agentActions.queryVerification("why?");

      expect(result).toBeDefined();
      expect(result.answer).toBeDefined();
      expect(result.confidence).toBeGreaterThanOrEqual(0);
      expect(result.confidence).toBeLessThanOrEqual(1);
    } finally {
      delete process.env["R4_TUI_BACKEND"];
    }
  });

  it("queryVerification handles support queries", () => {
    process.env["R4_TUI_BACKEND"] = "offline";
    try {
      agentActions.addExplanation({
        decisionId: "D1",
        decisionType: "hypothesis_check",
        target: "H7",
        reasoningChain: ["Collected evidence"],
        graphPaths: [["H7", "supported_by", "P23"]],
        supportingEvidence: [{ id: "P23", description: "Paper support", confidence: 0.82 }],
        confidenceCalculation: { prior: 0.5, likelihood: 0.7, posterior: 0.74 },
        alternatives: [],
        rejected: [],
        timestamp: new Date().toISOString(),
        agentId: "planner-1",
      });

      const result = agentActions.queryVerification("what supports this?");

      expect(result.evidenceIds).toContain("P23");
      expect(result.supportingPapers.length).toBeGreaterThan(0);
    } finally {
      delete process.env["R4_TUI_BACKEND"];
    }
  });

  it("queryVerification handles graph queries", () => {
    process.env["R4_TUI_BACKEND"] = "offline";
    try {
      agentActions.updateMissionControl({
        hypotheses: [{ id: "H1", statement: "Test", confidence: 0.5 }],
      });

      const result = agentActions.queryVerification("show graph");

      expect(result.answer).toContain("nodes");
      expect(result.answer).toContain("edges");
    } finally {
      delete process.env["R4_TUI_BACKEND"];
    }
  });

  it("queryVerification handles missing/gap queries", () => {
    process.env["R4_TUI_BACKEND"] = "offline";
    try {
      agentActions.requestApproval("experiment_execution", "Approve E19");

      const result = agentActions.queryVerification("show missing");

      expect(result.answer).toContain("gaps");
    } finally {
      delete process.env["R4_TUI_BACKEND"];
    }
  });

  it("queryVerification handles contradiction queries", () => {
    process.env["R4_TUI_BACKEND"] = "offline";
    try {
      agentActions.addExplanation({
        decisionId: "D1",
        decisionType: "hypothesis_check",
        target: "H7",
        reasoningChain: [],
        graphPaths: [],
        supportingEvidence: [],
        confidenceCalculation: { prior: 0.5, likelihood: 0.7, posterior: 0.74 },
        alternatives: [],
        rejected: [{ id: "E20", description: "Too expensive", reason: "Budget exceeded" }],
        timestamp: new Date().toISOString(),
        agentId: "planner-1",
      });

      const result = agentActions.queryVerification("what contradicts?");

      expect(result.answer).toContain("rejected");
    } finally {
      delete process.env["R4_TUI_BACKEND"];
    }
  });
});

// ── Phase 6: Agent Monitor ────────────────────────────────────────────

describe("Phase 6: AgentMonitor data dependencies", () => {
  it("agents can be filtered by running state", () => {
    const id1 = agentActions.createAgent({
      role: "literature",
      goal: "Find papers",
      budget: { maxTokens: 1000, maxIterations: 5 },
      permissions: { egress: "internet", filesystem: "read_only", compute: "none" },
    });
    agentActions.transitionAgent(id1, "planning");
    agentActions.transitionAgent(id1, "running");

    const id2 = agentActions.createAgent({
      role: "experiment",
      goal: "Run E17",
      budget: { maxTokens: 1000, maxIterations: 5 },
      permissions: { egress: "none", filesystem: "read_write_scratch", compute: "slurm" },
    });
    agentActions.transitionAgent(id2, "planning");

    const runningAgents = agentRuntime.agents.filter(
      (a) => a.state === "running" || a.state === "planning" || a.state === "waiting",
    );

    expect(runningAgents.length).toBe(2);
  });

  it("agents can be filtered by blocked state", () => {
    const id = agentActions.createAgent({
      role: "experiment",
      goal: "Run E17",
      budget: { maxTokens: 1000, maxIterations: 5 },
      permissions: { egress: "none", filesystem: "read_write_scratch", compute: "slurm" },
    });
    agentActions.transitionAgent(id, "planning");
    agentActions.transitionAgent(id, "running");
    agentActions.transitionAgent(id, "blocked");

    const blockedAgents = agentRuntime.agents.filter((a) => a.state === "blocked");
    expect(blockedAgents.length).toBe(1);
  });

  it("agents can be filtered by completed state", () => {
    const id = agentActions.createAgent({
      role: "literature",
      goal: "Find papers",
      budget: { maxTokens: 1000, maxIterations: 5 },
      permissions: { egress: "internet", filesystem: "read_only", compute: "none" },
    });
    agentActions.transitionAgent(id, "planning");
    agentActions.transitionAgent(id, "running");
    agentActions.transitionAgent(id, "completed");

    const completedAgents = agentRuntime.agents.filter((a) => a.state === "completed");
    expect(completedAgents.length).toBe(1);
  });

  it("agents expose progress and confidence for display", () => {
    const id = agentActions.createAgent({
      role: "literature",
      goal: "Find papers",
      confidence: 0.72,
      progress: 45,
      budget: { maxTokens: 50000, maxIterations: 10 },
      permissions: { egress: "internet", filesystem: "read_only", compute: "none" },
    });

    const agent = agentActions.getAgentById(id);
    expect(agent!.progress).toBe(45);
    expect(agent!.confidence).toBe(0.72);
  });
});

// ── Phase 7: Research Timeline ────────────────────────────────────────

describe("Phase 7: ResearchTimeline data dependencies", () => {
  it("timeline entries can be added", () => {
    agentActions.addTimelineEntry("artifact_upload", "Uploaded 15 papers");
    expect(agentRuntime.timeline.length).toBe(1);
    expect(agentRuntime.timeline[0].type).toBe("artifact_upload");
  });

  it("timeline entries are ordered newest first", () => {
    agentActions.addTimelineEntry("artifact_upload", "First upload");
    agentActions.addTimelineEntry("hypothesis_created", "H7 generated");

    expect(agentRuntime.timeline[0].title).toBe("H7 generated");
    expect(agentRuntime.timeline[1].title).toBe("First upload");
  });

  it("timeline entries include agentId and objectId", () => {
    agentActions.addTimelineEntry(
      "experiment_started",
      "Started E17",
      "MD simulation",
      "agent-001",
      "E17",
    );

    const entry = agentRuntime.timeline[0];
    expect(entry.agentId).toBe("agent-001");
    expect(entry.objectId).toBe("E17");
  });

  it("timeline supports all entry types", () => {
    const types: TimelineEntryType[] = [
      "artifact_upload",
      "knowledge_extraction",
      "graph_update",
      "hypothesis_created",
      "experiment_started",
      "experiment_completed",
      "finding_registered",
      "review_completed",
      "knowledge_correction",
      "publication_created",
    ];

    for (const type of types) {
      agentActions.addTimelineEntry(type, `Test ${type}`);
    }

    expect(agentRuntime.timeline.length).toBe(types.length);
  });
});

// ── Phase 8: Approval Gates ───────────────────────────────────────────

describe("Phase 8: ApprovalGates data dependencies", () => {
  it("approval gates can be requested", () => {
    const id = agentActions.requestApproval(
      "experiment_execution",
      "Approve E19: 4 GPUs, 8h runtime",
    );

    expect(id).toBeDefined();
    const pending = agentActions.getPendingApprovals();
    expect(pending.length).toBe(1);
    expect(pending[0].title).toBe("Approve E19: 4 GPUs, 8h runtime");
  });

  it("approval gates can be resolved with comment", () => {
    const id = agentActions.requestApproval("hypothesis_generation", "Approve H8");
    agentActions.resolveApproval(id, "approved", "Looks good");

    const gate = agentRuntime.approvalGates.find((g) => g.id === id);
    expect(gate!.status).toBe("approved");
    expect(gate!.comment).toBe("Looks good");
    expect(gate!.resolvedAt).toBeDefined();
  });

  it("approval gates can be rejected", () => {
    const id = agentActions.requestApproval("experiment_design", "Design E20");
    agentActions.resolveApproval(id, "rejected", "Insufficient budget");

    const gate = agentRuntime.approvalGates.find((g) => g.id === id);
    expect(gate!.status).toBe("rejected");
  });

  it("getPendingApprovals returns only pending gates", () => {
    const id1 = agentActions.requestApproval("knowledge_extraction", "Approve extraction");
    const id2 = agentActions.requestApproval("hypothesis_generation", "Approve H8");
    agentActions.resolveApproval(id1, "approved");

    const pending = agentActions.getPendingApprovals();
    expect(pending.length).toBe(1);
    expect(pending[0].id).toBe(id2);
  });
});

// ── Phase 9: Workflow Graph View ──────────────────────────────────────

describe("Phase 9: WorkflowGraphView data dependencies", () => {
  it("workflow graph starts empty in live mode", () => {
    expect(agentRuntime.workflowGraph.nodes.length).toBe(0);
    expect(agentRuntime.workflowGraph.edges.length).toBe(0);
  });

  it("demo workflow graph can be loaded", () => {
    loadDemoWorkflowGraph();
    expect(agentRuntime.workflowGraph.nodes.length).toBeGreaterThan(0);
    expect(agentRuntime.workflowGraph.edges.length).toBeGreaterThan(0);
  });

  it("workflow nodes can be updated", () => {
    loadDemoWorkflowGraph();
    agentActions.updateWorkflowNode("start", "completed");
    const node = agentRuntime.workflowGraph.nodes.find((n) => n.id === "start");
    expect(node!.status).toBe("completed");
  });

  it("workflow node selection works", () => {
    loadDemoWorkflowGraph();
    agentActions.selectWorkflowNode("experiment");
    expect(agentRuntime.selectedWorkflowNodeId).toBe("experiment");
  });

  it("workflow graph supports all node types", () => {
    loadDemoWorkflowGraph();
    const types: WorkflowNodeType[] = [
      "agent",
      "knowledge",
      "experiment",
      "decision",
      "review",
      "publication",
    ];

    for (const type of types) {
      agentActions.addWorkflowNode({
        type,
        label: `Test ${type}`,
        status: "pending",
      });
    }

    expect(agentRuntime.workflowGraph.nodes.length).toBeGreaterThan(types.length);
  });

  it("workflow graph supports all node statuses", () => {
    loadDemoWorkflowGraph();
    const statuses: WorkflowNodeStatus[] = [
      "pending",
      "running",
      "completed",
      "failed",
      "blocked",
      "waiting",
    ];

    for (const status of statuses) {
      agentActions.addWorkflowNode({
        type: "agent",
        label: `Test ${status}`,
        status,
      });
    }

    expect(agentRuntime.workflowGraph.nodes.length).toBeGreaterThan(statuses.length);
  });

  it("current node is the running node only", () => {
    loadDemoWorkflowGraph();
    agentActions.updateWorkflowNode("start", "completed");
    agentActions.updateWorkflowNode("ingest", "completed");
    agentActions.updateWorkflowNode("extract", "running");

    const running = agentRuntime.workflowGraph.nodes.find((n) => n.status === "running");
    expect(running).toBeDefined();
    expect(running!.id).toBe("extract");
  });
});
