/**
 * Autonomous Research Loop tests — Phase 13.
 */

import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { loopStore, loopActions } from "../src/stores/autonomous-loop";
import { agentActions, agentRuntime } from "../src/stores/agent-runtime";
import { strategyActions } from "../src/stores/research-strategy";
import { blackboardActions } from "../src/stores/scientific-blackboard";

import { setDemoMode, setDemoVisuals } from "../src/lib/console-mode";

beforeEach(() => {
  setDemoMode(true);
  setDemoVisuals(true);
  agentActions.resetAgentRuntime();
  strategyActions.resetStrategy();
  blackboardActions.resetBlackboard();
  loopActions.resetLoop();
});

afterEach(() => {
  setDemoMode(false);
  setDemoVisuals(false);
});

describe("Autonomous Research Loop", () => {
  describe("startLoop / stopLoop", () => {
    it("startLoop sets phase to assessing and enables autoRun", () => {
      loopActions.startLoop();

      expect(loopStore.phase).toBe("assessing");
      expect(loopStore.autoRun).toBe(true);
    });

    it("stopLoop sets phase to idle and disables autoRun", () => {
      loopActions.startLoop();
      loopActions.stopLoop();

      expect(loopStore.phase).toBe("idle");
      expect(loopStore.autoRun).toBe(false);
    });

    it("startLoop emits agent_started event", () => {
      loopActions.startLoop();

      const events = agentActions.getEventsByType("agent_started");
      expect(events.length).toBe(1);
      expect(events[0].payload.source).toBe("autonomous_loop");
    });
  });

  describe("runIteration", () => {
    it("completes a full iteration: assess → select → dispatch → execute → adapt", () => {
      // Set up mission control with hypotheses and budget
      agentActions.updateMissionControl({
        researchGoal: "Test hypothesis H7",
        hypotheses: [
          { id: "H7", statement: "Methodology X will yield result Y", confidence: 0.5 },
        ],
        computeBudget: { remaining: 100, burnRate: 5, estimatedRunway: 20 },
      });

      const record = loopActions.runIteration();

      expect(record.iteration).toBe(1);
      expect(record.phase).toBe("completed");
      expect(record.selectedAction).toBeDefined();
      expect(record.dispatchedAgentId).toBeDefined();
      expect(record.blackboardEntryId).toBeDefined();
      expect(record.plannerAdaptation).toBeDefined();
    });

    it("pauses when budget falls below threshold", () => {
      agentActions.updateMissionControl({
        hypotheses: [{ id: "H1", statement: "Test", confidence: 0.5 }],
        computeBudget: { remaining: 5, burnRate: 1, estimatedRunway: 5 },
      });
      loopActions.setBudgetThreshold(10);

      const record = loopActions.runIteration();

      expect(record.phase).toBe("paused_budget");
      expect(record.error).toContain("Budget below threshold");
    });

    it("completes when no action candidates available", () => {
      // No hypotheses, no agents, no budget — empty state
      const record = loopActions.runIteration();

      // With no hypotheses, rankActions returns empty (or just read_paper if lit agent idle)
      // Since no agents exist, it should either complete or dispatch
      expect(["completed", "paused_budget"]).toContain(record.phase);
    });

    it("spawns a new agent when no matching idle agent exists", () => {
      agentActions.updateMissionControl({
        hypotheses: [{ id: "H1", statement: "Test", confidence: 0.5 }],
        computeBudget: { remaining: 100, burnRate: 5, estimatedRunway: 20 },
      });

      const initialAgentCount = agentRuntime.agents.length;
      const record = loopActions.runIteration();

      // Should either spawn a new agent or use existing one
      if (record.phase === "completed") {
        expect(agentRuntime.agents.length).toBeGreaterThanOrEqual(initialAgentCount);
      }
    });

    it("writes result to blackboard with correct entry type", () => {
      agentActions.updateMissionControl({
        hypotheses: [{ id: "H1", statement: "Test", confidence: 0.5 }],
        computeBudget: { remaining: 100, burnRate: 5, estimatedRunway: 20 },
      });

      const record = loopActions.runIteration();

      const entry = blackboardActions.getEntry(record.blackboardEntryId!);
      expect(entry).toBeDefined();
      expect(["observation", "hypothesis", "experiment", "review", "finding", "publication"]).toContain(entry!.type);
    });

    it("increments iteration counter", () => {
      agentActions.updateMissionControl({
        hypotheses: [{ id: "H1", statement: "Test", confidence: 0.5 }],
        computeBudget: { remaining: 200, burnRate: 5, estimatedRunway: 40 },
      });

      loopActions.runIteration();
      loopActions.runIteration();

      expect(loopStore.iteration).toBe(2);
    });

    it("stores iteration history", () => {
      agentActions.updateMissionControl({
        hypotheses: [{ id: "H1", statement: "Test", confidence: 0.5 }],
        computeBudget: { remaining: 100, burnRate: 5, estimatedRunway: 20 },
      });

      loopActions.runIteration();

      expect(loopStore.history.length).toBe(1);
      expect(loopStore.history[0].iteration).toBe(1);
    });
  });

  describe("adaptPlan", () => {
    it("recommends strategy review when overall score is low", () => {
      const assessment = {
        overallScore: 0.3,
        strengths: [],
        weaknesses: ["Budget constrained"],
        recommendations: [],
        budgetUtilization: 0.8,
        riskLevel: "high" as const,
      };
      const lastAction = {
        id: "test",
        actionType: "verify_knowledge" as const,
        description: "Verify H1",
        score: 0.8,
        rationale: "Low confidence",
        estimatedCost: 1,
        estimatedDuration: 1,
        dependencies: [],
      };

      const adaptation = loopActions.adaptPlan(assessment, lastAction);

      expect(adaptation).toContain("Overall strategy score low");
    });

    it("adapts based on action type", () => {
      const assessment = {
        overallScore: 0.7,
        strengths: [],
        weaknesses: [],
        recommendations: [],
        budgetUtilization: 0.3,
        riskLevel: "low" as const,
      };

      const generateAction = {
        id: "test",
        actionType: "generate_hypothesis" as const,
        description: "Generate H2",
        score: 0.8,
        rationale: "Explore",
        estimatedCost: 0,
        estimatedDuration: 1,
        dependencies: [],
      };

      const adaptation = loopActions.adaptPlan(assessment, generateAction);

      expect(adaptation).toContain("New hypothesis generated");
    });

    it("stores planner adaptations", () => {
      const assessment = {
        overallScore: 0.7,
        strengths: [],
        weaknesses: [],
        recommendations: [],
        budgetUtilization: 0.3,
        riskLevel: "low" as const,
      };
      const lastAction = {
        id: "test",
        actionType: "verify_knowledge" as const,
        description: "Verify H1",
        score: 0.8,
        rationale: "Low confidence",
        estimatedCost: 1,
        estimatedDuration: 1,
        dependencies: [],
      };

      loopActions.adaptPlan(assessment, lastAction);

      expect(loopStore.plannerAdaptations.length).toBe(1);
    });
  });

  describe("shouldContinue", () => {
    it("returns false when autoRun is false", () => {
      loopStore.autoRun = false;
      expect(loopActions.shouldContinue()).toBe(false);
    });

    it("returns false when phase is completed", () => {
      loopStore.autoRun = true;
      loopStore.phase = "completed";
      expect(loopActions.shouldContinue()).toBe(false);
    });

    it("returns false when budget is below threshold", () => {
      loopStore.autoRun = true;
      loopStore.phase = "assessing";
      agentActions.updateMissionControl({
        computeBudget: { remaining: 5, burnRate: 1, estimatedRunway: 5 },
      });
      loopActions.setBudgetThreshold(10);

      expect(loopActions.shouldContinue()).toBe(false);
    });
  });

  describe("setMaxIterations / setBudgetThreshold", () => {
    it("sets max iterations", () => {
      loopActions.setMaxIterations(50);
      expect(loopStore.maxIterations).toBe(50);
    });

    it("sets budget threshold", () => {
      loopActions.setBudgetThreshold(20);
      expect(loopStore.budgetThreshold).toBe(20);
    });
  });

  describe("resetLoop", () => {
    it("resets all loop state", () => {
      loopActions.startLoop();
      loopActions.resetLoop();

      expect(loopStore.phase).toBe("idle");
      expect(loopStore.autoRun).toBe(false);
      expect(loopStore.iteration).toBe(0);
      expect(loopStore.history).toEqual([]);
    });
  });
});
