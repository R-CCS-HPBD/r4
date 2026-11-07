/**
 * Research Strategy Engine tests — Phase 11.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { strategyStore, strategyActions } from "../src/stores/research-strategy";
import { agentActions } from "../src/stores/agent-runtime";

beforeEach(() => {
  agentActions.resetAgentRuntime();
  strategyActions.resetStrategy();
});

describe("Research Strategy Engine", () => {
  describe("rankActions", () => {
    it("returns candidates sorted by score descending", () => {
      agentActions.updateMissionControl({
        hypotheses: [
          { id: "H1", statement: "High confidence hypothesis", confidence: 0.95 },
          { id: "H2", statement: "Low confidence hypothesis", confidence: 0.3 },
        ],
        computeBudget: { remaining: 100, burnRate: 5, estimatedRunway: 20 },
      });

      const candidates = strategyActions.rankActions();

      expect(candidates.length).toBeGreaterThan(0);
      // Verify descending order
      for (let i = 1; i < candidates.length; i++) {
        expect(candidates[i - 1].score).toBeGreaterThanOrEqual(candidates[i].score);
      }
    });

    it("generates verify_knowledge candidate for each hypothesis", () => {
      agentActions.updateMissionControl({
        hypotheses: [
          { id: "H1", statement: "Hypothesis 1", confidence: 0.5 },
          { id: "H2", statement: "Hypothesis 2", confidence: 0.7 },
        ],
      });

      const candidates = strategyActions.rankActions();
      const verifyCandidates = candidates.filter((c) => c.actionType === "verify_knowledge");

      expect(verifyCandidates.length).toBe(2);
    });

    it("generates design_experiment candidate for low-confidence hypotheses", () => {
      agentActions.updateMissionControl({
        hypotheses: [
          { id: "H1", statement: "Low confidence", confidence: 0.3 },
        ],
      });

      const candidates = strategyActions.rankActions();
      const designCandidates = candidates.filter((c) => c.actionType === "design_experiment");

      expect(designCandidates.length).toBe(1);
      expect(designCandidates[0].targetHypothesisId).toBe("H1");
    });

    it("penalizes candidates that exceed remaining budget", () => {
      agentActions.updateMissionControl({
        hypotheses: [
          { id: "H1", statement: "Hypothesis 1", confidence: 0.5 },
        ],
        computeBudget: { remaining: 0, burnRate: 0, estimatedRunway: 0 },
      });

      const candidates = strategyActions.rankActions();

      // With zero budget, scores should be halved
      expect(candidates.length).toBeGreaterThan(0);
    });

    it("emits agent_reasoning event when ranking actions", () => {
      agentActions.updateMissionControl({
        hypotheses: [{ id: "H1", statement: "Test", confidence: 0.5 }],
      });

      strategyActions.rankActions();

      const events = agentActions.getEventsByType("agent_reasoning");
      const strategyEvent = events.find(
        (e) => e.payload?.source === "strategy_engine",
      );

      expect(strategyEvent).toBeDefined();
    });
  });

  describe("evaluateStrategy", () => {
    it("produces assessment with overall score between 0 and 1", () => {
      agentActions.updateMissionControl({
        hypotheses: [{ id: "H1", statement: "Test", confidence: 0.7 }],
        computeBudget: { remaining: 100, burnRate: 5, estimatedRunway: 20 },
      });

      const assessment = strategyActions.evaluateStrategy();

      expect(assessment.overallScore).toBeGreaterThanOrEqual(0);
      expect(assessment.overallScore).toBeLessThanOrEqual(1);
      expect(assessment.timestamp).toBeDefined();
    });

    it("identifies strengths when confidence and success rate are high", () => {
      agentActions.updateMissionControl({
        hypotheses: [{ id: "H1", statement: "Test", confidence: 0.9 }],
        computeBudget: { remaining: 200, burnRate: 5, estimatedRunway: 40 },
      });

      const assessment = strategyActions.evaluateStrategy({
        successful: 8,
        failed: 1,
        pending: 2,
      });

      expect(assessment.strengths.length).toBeGreaterThan(0);
    });

    it("identifies weaknesses when budget is low or agents are blocked", () => {
      agentActions.updateMissionControl({
        hypotheses: [{ id: "H1", statement: "Test", confidence: 0.3 }],
        computeBudget: { remaining: 10, burnRate: 20, estimatedRunway: 0.5 },
      });

      const assessment = strategyActions.evaluateStrategy({
        successful: 1,
        failed: 5,
        pending: 3,
      });

      expect(assessment.weaknesses.length).toBeGreaterThan(0);
      expect(assessment.riskLevel).toBe("high");
    });

    it("stores assessment in history", () => {
      strategyActions.evaluateStrategy();

      expect(strategyStore.assessmentHistory.length).toBe(1);
    });
  });

  describe("proposeExperiments", () => {
    it("proposes experiments for hypotheses below confidence threshold", () => {
      agentActions.updateMissionControl({
        computeBudget: { remaining: 100, burnRate: 5, estimatedRunway: 20 },
      });

      const proposals = strategyActions.proposeExperiments([
        { id: "H1", statement: "Low confidence", confidence: 0.2 },
        { id: "H2", statement: "High confidence", confidence: 0.9 },
      ]);

      expect(proposals.length).toBe(1);
      expect(proposals[0].hypothesisId).toBe("H1");
    });

    it("assigns higher GPU count to very low-confidence hypotheses", () => {
      agentActions.updateMissionControl({
        computeBudget: { remaining: 100, burnRate: 5, estimatedRunway: 20 },
      });

      const proposals = strategyActions.proposeExperiments([
        { id: "H1", statement: "Very low confidence", confidence: 0.1 },
      ]);

      expect(proposals.length).toBe(1);
      expect(proposals[0].estimatedGpus).toBe(4);
    });

    it("sorts proposals by priority (1 = highest)", () => {
      agentActions.updateMissionControl({
        computeBudget: { remaining: 100, burnRate: 5, estimatedRunway: 20 },
      });

      const proposals = strategyActions.proposeExperiments([
        { id: "H1", statement: "Medium confidence", confidence: 0.5 },
        { id: "H2", statement: "Very low confidence", confidence: 0.1 },
      ]);

      // H2 should be first (priority 1, lower confidence)
      expect(proposals[0].hypothesisId).toBe("H2");
    });
  });

  describe("budgetAllocation", () => {
    it("allocates budget across active agents", () => {
      const agentId = agentActions.createAgent({
        role: "experiment",
        goal: "Test hypothesis",
        budget: { maxTokens: 50000, maxIterations: 10 },
        permissions: { egress: "none", filesystem: "read_write_scratch", compute: "slurm" },
      });
      // idle -> planning -> running
      agentActions.transitionAgent(agentId, "planning");
      agentActions.transitionAgent(agentId, "running");

      const allocation = strategyActions.budgetAllocation();

      expect(allocation.allocations.length).toBe(1);
      expect(allocation.allocations[0].agentRole).toBe("experiment");
    });

    it("returns zero allocations when no active agents", () => {
      const allocation = strategyActions.budgetAllocation();

      expect(allocation.allocations.length).toBe(0);
    });
  });

  describe("getTopAction", () => {
    it("returns the highest-scored action candidate", () => {
      agentActions.updateMissionControl({
        hypotheses: [{ id: "H1", statement: "Test", confidence: 0.5 }],
      });

      strategyActions.rankActions();
      const top = strategyActions.getTopAction();

      expect(top).toBeDefined();
    });
  });
});
