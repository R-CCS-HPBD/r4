/**
 * Agent Runtime Store tests.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { agentRuntime, agentActions, loadDemoWorkflowGraph } from "../src/stores/agent-runtime";

beforeEach(() => {
  agentActions.resetAgentRuntime();
});

describe("Agent Runtime Store", () => {
  describe("Agent Lifecycle", () => {
    it("creates an agent with idle state", () => {
      const id = agentActions.createAgent({
        role: "literature",
        goal: "Find missing papers",
        budget: { maxTokens: 50000, maxIterations: 10 },
        permissions: { egress: "internet", filesystem: "read_only", compute: "none" },
      });

      expect(id).toBeDefined();
      const agent = agentActions.getAgentById(id);
      expect(agent).toBeDefined();
      expect(agent!.role).toBe("literature");
      expect(agent!.state).toBe("idle");
    });

    it("transitions agent state correctly", () => {
      const id = agentActions.createAgent({
        role: "experiment",
        goal: "Execute experiment E17",
        budget: { maxTokens: 30000, maxIterations: 5 },
        permissions: { egress: "none", filesystem: "read_write_scratch", compute: "slurm" },
      });

      // Agent starts as idle
      expect(agentActions.getAgentById(id)!.state).toBe("idle");

      // idle -> planning is valid
      expect(agentActions.transitionAgent(id, "planning")).toBe(true);
      expect(agentActions.getAgentById(id)!.state).toBe("planning");

      // planning -> running is valid
      expect(agentActions.transitionAgent(id, "running")).toBe(true);
      expect(agentActions.getAgentById(id)!.state).toBe("running");

      // running -> completed is valid
      expect(agentActions.transitionAgent(id, "completed")).toBe(true);
      expect(agentActions.getAgentById(id)!.state).toBe("completed");
    });

    it("rejects invalid state transitions", () => {
      const id = agentActions.createAgent({
        role: "reviewer",
        goal: "Critique hypothesis H7",
        budget: { maxTokens: 30000, maxIterations: 5 },
        permissions: { egress: "none", filesystem: "read_only", compute: "none" },
      });

      // Agent is idle, can't go directly to completed (idle only allows planning or cancelled)
      expect(agentActions.transitionAgent(id, "completed")).toBe(false);
      expect(agentActions.getAgentById(id)!.state).toBe("idle");
    });

    it("updates agent progress and reasoning", () => {
      const id = agentActions.createAgent({
        role: "analysis",
        goal: "Analyze experiment results",
        budget: { maxTokens: 30000, maxIterations: 5 },
        permissions: { egress: "none", filesystem: "read_only", compute: "none" },
      });

      agentActions.updateAgentProgress(id, 75, "Found significant correlation");
      const agent = agentActions.getAgentById(id);
      expect(agent!.progress).toBe(75);
      expect(agent!.reasoning).toContain("Found significant correlation");
    });
  });

  describe("Research Queue", () => {
    it("enqueues research items", () => {
      const id = agentActions.enqueueResearch("read_paper", {
        hypothesisId: "H7",
        priority: 2,
      });

      expect(id).toBeDefined();
      const items = agentActions.getPendingQueueItems();
      expect(items.length).toBe(1);
      expect(items[0].intent).toBe("read_paper");
      expect(items[0].priority).toBe(2);
    });

    it("cancels queue items", () => {
      const id = agentActions.enqueueResearch("submit_hpc_job", {
        experimentId: "E17",
        priority: 3,
      });

      agentActions.cancelQueueItem(id);
      const item = agentRuntime.researchQueue.find((q) => q.id === id);
      expect(item!.status).toBe("cancelled");
    });
  });

  describe("Timeline", () => {
    it("adds timeline entries", () => {
      agentActions.addTimelineEntry("artifact_upload", "Uploaded 15 papers");

      const timeline = agentActions.getTimeline();
      expect(timeline.length).toBe(1);
      expect(timeline[0].type).toBe("artifact_upload");
      expect(timeline[0].title).toBe("Uploaded 15 papers");
    });
  });

  describe("Approval Gates", () => {
    it("requests approval", () => {
      const id = agentActions.requestApproval(
        "experiment_execution",
        "Approve experiment E19",
        "4 GPUs, 8h runtime",
      );

      expect(id).toBeDefined();
      const pending = agentActions.getPendingApprovals();
      expect(pending.length).toBe(1);
      expect(pending[0].title).toBe("Approve experiment E19");
    });

    it("resolves approval", () => {
      const id = agentActions.requestApproval(
        "hypothesis_generation",
        "Approve hypothesis H7",
      );

      agentActions.resolveApproval(id, "approved", "Looks good");

      const gate = agentRuntime.approvalGates.find((g) => g.id === id);
      expect(gate!.status).toBe("approved");
      expect(gate!.comment).toBe("Looks good");
      expect(gate!.resolvedAt).toBeDefined();
    });
  });

  describe("Workflow Graph", () => {
    it("starts with an empty graph in live mode", () => {
      expect(agentRuntime.workflowGraph.nodes).toEqual([]);
      expect(agentRuntime.workflowGraph.edges).toEqual([]);
    });

    it("updates workflow node status", () => {
      loadDemoWorkflowGraph();
      agentActions.updateWorkflowNode("start", "completed");

      const node = agentRuntime.workflowGraph.nodes.find((n) => n.id === "start");
      expect(node!.status).toBe("completed");
    });

    it("adds new workflow nodes and edges", () => {
      loadDemoWorkflowGraph();
      const nodeId = agentActions.addWorkflowNode({
        type: "decision",
        label: "Branch point",
        status: "pending",
      });

      agentActions.addWorkflowEdge({ from: "experiment", to: nodeId });

      const node = agentRuntime.workflowGraph.nodes.find((n) => n.id === nodeId);
      expect(node).toBeDefined();
      expect(node!.label).toBe("Branch point");

      const edge = agentRuntime.workflowGraph.edges.find((e) => e.from === "experiment" && e.to === nodeId);
      expect(edge).toBeDefined();
    });
  });

  describe("Mission Control", () => {
    it("updates mission control state", () => {
      agentActions.updateMissionControl({
        researchGoal: "Test hypothesis H7",
        successCriteriaProgress: 45,
      });

      expect(agentRuntime.missionControl.researchGoal).toBe("Test hypothesis H7");
      expect(agentRuntime.missionControl.successCriteriaProgress).toBe(45);
    });

    it("refreshes mission control from current state", () => {
      const id = agentActions.createAgent({
        role: "literature",
        goal: "Find papers",
        budget: { maxTokens: 1000, maxIterations: 1 },
        permissions: { egress: "none", filesystem: "read_only", compute: "none" },
      });
      // Transition to running so it shows up as active
      agentActions.transitionAgent(id, "planning");
      agentActions.transitionAgent(id, "running");
      agentActions.requestApproval("experiment_execution", "Approve E19");

      agentActions.refreshMissionControl();

      expect(agentRuntime.missionControl.activeAgents.length).toBeGreaterThan(0);
      expect(agentRuntime.missionControl.pendingApprovals.length).toBeGreaterThan(0);
    });
  });

  describe("Explainability", () => {
    it("adds decision explanations", () => {
      agentActions.addExplanation({
        decisionId: "D1",
        decisionType: "experiment_recommendation",
        target: "E17",
        reasoningChain: ["Strategy Engine ranked E17 highest"],
        graphPaths: [["H7", "depends_on", "E12"]],
        supportingEvidence: [{ id: "P23", description: "Supports H7", confidence: 0.85 }],
        confidenceCalculation: { prior: 0.45, likelihood: 0.62, posterior: 0.51 },
        alternatives: [{ id: "E18", description: "Replicate E12", score: 0.71 }],
        rejected: [{ id: "E20", description: "Too expensive", reason: "Budget exceeded" }],
        timestamp: new Date().toISOString(),
        agentId: "planner-001",
      });

      const explanations = agentActions.getExplanations();
      expect(explanations.length).toBe(1);
      expect(explanations[0].decisionId).toBe("D1");
      expect(explanations[0].target).toBe("E17");
    });
  });

  describe("Scientific Events", () => {
    it("emits and retrieves events", () => {
      agentActions.emitEvent("agent_started", { agentId: "test" });

      const events = agentActions.getEvents();
      expect(events.length).toBe(1);
      expect(events[0].eventType).toBe("agent_started");
    });

    it("filters events by type", () => {
      agentActions.emitEvent("agent_started", { agentId: "1" });
      agentActions.emitEvent("agent_completed", { agentId: "1" });
      agentActions.emitEvent("agent_started", { agentId: "2" });

      const startedEvents = agentActions.getEventsByType("agent_started");
      expect(startedEvents.length).toBe(2);
    });
  });

  describe("Verification Query", () => {
    it("builds support answer from live explanation state", () => {
      process.env["R4_TUI_BACKEND"] = "offline";
      try {
        agentActions.updateMissionControl({
          researchGoal: "Validate hypothesis H7",
          hypotheses: [
            { id: "H7", statement: "H7 statement", confidence: 0.72 },
          ],
        });

        agentActions.addExplanation({
          decisionId: "D-verify-1",
          decisionType: "hypothesis_check",
          target: "H7",
          reasoningChain: ["Collected relevant evidence", "Ranked evidence by confidence"],
          graphPaths: [["H7", "supported_by", "P23"]],
          supportingEvidence: [
            { id: "P23", description: "Paper support", confidence: 0.82 },
            { id: "E12", description: "Experiment support", confidence: 0.64 },
          ],
          confidenceCalculation: { prior: 0.5, likelihood: 0.7, posterior: 0.74 },
          alternatives: [],
          rejected: [],
          timestamp: new Date().toISOString(),
          agentId: "planner-1",
        });

        const result = agentActions.queryVerification("what evidence supports this?");

        expect(result.answer.toLowerCase()).toContain("evidence");
        expect(result.evidenceIds).toContain("P23");
        expect(result.relatedHypotheses[0]).toContain("H7");
        expect(result.confidence).toBeGreaterThan(0.5);
      } finally {
        delete process.env["R4_TUI_BACKEND"];
      }
    });

    it("emits reasoning event when verification query runs", () => {
      process.env["R4_TUI_BACKEND"] = "offline";
      try {
        agentActions.queryVerification("why this decision?");

        const reasoningEvents = agentActions.getEventsByType("agent_reasoning");
        expect(reasoningEvents.length).toBe(1);
        expect(reasoningEvents[0].payload.source).toBe("verification_query");
      } finally {
        delete process.env["R4_TUI_BACKEND"];
      }
    });

    it("gates verification in CLI mode", () => {
      process.env["R4_TUI_BACKEND"] = "cli";
      try {
        const result = agentActions.queryVerification("what supports H7?");
        expect(result.confidence).toBe(0);
        expect(result.evidenceIds).toEqual([]);
      } finally {
        delete process.env["R4_TUI_BACKEND"];
      }
    });
  });
});
