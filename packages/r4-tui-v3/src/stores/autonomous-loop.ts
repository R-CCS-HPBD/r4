/**
 * Autonomous Research Loop — Phase 13.
 *
 * Wires the Research Strategy Engine + Scientific Blackboard into an
 * autonomous loop with planner adaptation.
 *
 * Loop cycle:
 * 1. Assess — call Strategy Engine for action ranking + assessment
 * 2. Select — pick highest-ranked action candidate
 * 3. Dispatch — assign action to matching agent (or spawn one)
 * 4. Execute — agent runs, writes findings to Blackboard
 * 5. Adapt — planner adjusts plan based on new evidence
 * 6. Repeat until goal achieved or budget exhausted
 *
 * Architectural Invariants:
 * - Loop is orchestrator-only; it never calls Reasoning Engine directly
 * - All scientific output goes through Blackboard (no direct agent-to-agent)
 * - Budget gate stops loop when remaining < threshold
 * - Approval gate pauses loop for human-in-the-loop
 * - Loop state is ephemeral (R4-owned); scientific state persists in UMRS
 */

import { createStore, produce } from "solid-js/store";
import { ulid } from "ulid";
import { isDemoVisuals } from "../lib/console-mode";
import { agentRuntime, agentActions } from "./agent-runtime";
import { strategyStore, strategyActions } from "./research-strategy";
import { blackboardActions } from "./scientific-blackboard";
import type {
  AgentRole,
  AgentRuntime,
  ActionCandidate,
  ActionType,
  BlackboardEntryType,
} from "../types";

// ── Loop State ───────────────────────────────────────────────────────────

export type LoopPhase =
  | "idle"
  | "assessing"
  | "selecting"
  | "dispatching"
  | "executing"
  | "adapting"
  | "paused_approval"
  | "paused_budget"
  | "completed"
  | "failed";

export interface LoopIteration {
  iteration: number;
  phase: LoopPhase;
  timestamp: string;
  selectedAction?: ActionCandidate;
  dispatchedAgentId?: string;
  blackboardEntryId?: string;
  plannerAdaptation?: string;
  error?: string;
}

export interface AutonomousLoopStore {
  // Current phase
  phase: LoopPhase;
  // Iteration count
  iteration: number;
  // Maximum iterations (safety)
  maxIterations: number;
  // Budget threshold (GPU-hours) — stop if remaining < threshold
  budgetThreshold: number;
  // Whether loop is auto-running
  autoRun: boolean;
  // History of iterations
  history: LoopIteration[];
  // Last error
  lastError: string | null;
  // Planner adaptation log
  plannerAdaptations: { timestamp: string; adaptation: string; trigger: string }[];
}

const initialState: AutonomousLoopStore = {
  phase: "idle",
  iteration: 0,
  maxIterations: 100,
  budgetThreshold: 10,
  autoRun: false,
  history: [],
  lastError: null,
  plannerAdaptations: [],
};

const [loopStore, setLoopStore] = createStore<AutonomousLoopStore>(initialState);

export { loopStore };

// ── Agent Role Mapping ──────────────────────────────────────────────────

/**
 * Map action type to the agent role that should handle it.
 * This ensures the right agent picks up the right action.
 */
const actionToRole: Record<ActionType, AgentRole> = {
  read_paper: "literature",
  extract_knowledge: "literature",
  verify_knowledge: "reviewer",
  generate_hypothesis: "planner",
  design_experiment: "planner",
  submit_hpc_job: "experiment",
  analyze_results: "analysis",
  review_findings: "reviewer",
  prepare_publication: "planner",
};

/**
 * Map action type to the Blackboard entry type it should produce.
 */
const actionToBlackboardType: Record<ActionType, BlackboardEntryType> = {
  read_paper: "observation",
  extract_knowledge: "observation",
  verify_knowledge: "review",
  generate_hypothesis: "hypothesis",
  design_experiment: "experiment",
  submit_hpc_job: "experiment",
  analyze_results: "finding",
  review_findings: "review",
  prepare_publication: "publication",
};

// ── Autonomous Loop Actions ──────────────────────────────────────────────

export const loopActions = {
  /**
   * Start the autonomous research loop.
   * Sets phase to "assessing" and enables auto-run.
   */
  startLoop(): void {
    if (!isDemoVisuals()) return;
    setLoopStore("phase", "assessing");
    setLoopStore("autoRun", true);
    setLoopStore("lastError", null);
    agentActions.emitEvent("agent_started", { source: "autonomous_loop", action: "start" });
  },

  /**
   * Stop the autonomous research loop.
   * Sets phase to "idle" and disables auto-run.
   */
  stopLoop(): void {
    setLoopStore("phase", "idle");
    setLoopStore("autoRun", false);
    agentActions.emitEvent("agent_completed", { source: "autonomous_loop", action: "stop" });
  },

  /**
   * Execute one full loop iteration:
   * assess → select → dispatch → execute → adapt
   *
   * Returns the LoopIteration record.
   */
  runIteration(): LoopIteration {
    const noop: LoopIteration = {
      iteration: loopStore.iteration,
      phase: loopStore.phase,
      timestamp: new Date().toISOString(),
    };
    if (!isDemoVisuals()) return noop;

    const iteration = loopStore.iteration + 1;
    const timestamp = new Date().toISOString();
    const record: LoopIteration = { iteration, phase: "assessing", timestamp };

    // Safety: max iterations
    if (iteration > loopStore.maxIterations) {
      setLoopStore("phase", "completed");
      record.phase = "completed";
      record.error = "Max iterations reached";
      setLoopStore("history", (list) => [record, ...list].slice(0, 200));
      return record;
    }

    // Phase 1: Assess — rank actions and evaluate strategy
    setLoopStore("phase", "assessing");
    const candidates = strategyActions.rankActions();
    const assessment = strategyActions.evaluateStrategy();
    record.phase = "selecting";

    // Budget gate: stop if remaining < threshold
    const budget = agentRuntime.missionControl.computeBudget;
    if (budget.remaining < loopStore.budgetThreshold) {
      setLoopStore("phase", "paused_budget");
      record.phase = "paused_budget";
      record.error = `Budget below threshold (${budget.remaining} < ${loopStore.budgetThreshold})`;
      agentActions.emitEvent("agent_failed", { source: "autonomous_loop", reason: "budget_exhausted" });
      setLoopStore("history", (list) => [record, ...list].slice(0, 200));
      return record;
    }

    // Phase 2: Select — pick highest-ranked action
    setLoopStore("phase", "selecting");
    const topAction = candidates[0];
    if (!topAction) {
      setLoopStore("phase", "completed");
      record.phase = "completed";
      record.error = "No action candidates available";
      setLoopStore("history", (list) => [record, ...list].slice(0, 200));
      return record;
    }
    record.selectedAction = topAction;

    // Phase 3: Dispatch — find matching agent or spawn one
    setLoopStore("phase", "dispatching");
    const requiredRole = actionToRole[topAction.actionType];
    const matchingAgent = agentRuntime.agents.find(
      (a) => a.role === requiredRole && (a.state === "idle" || a.state === "waiting"),
    );

    let agentId: string;
    if (matchingAgent) {
      agentId = matchingAgent.id;
      agentActions.transitionAgent(agentId, "running", {
        currentTask: topAction.description,
        currentObjective: topAction.rationale,
      });
    } else {
      // Spawn a new agent for this role
      agentId = agentActions.createAgent({
        role: requiredRole,
        goal: topAction.description,
        currentTask: topAction.description,
        currentObjective: topAction.rationale,
        budget: { maxTokens: 50000, maxIterations: 10 },
        permissions: { egress: "none", filesystem: "read_write_scratch", compute: "slurm" },
        state: "running",
      });
    }
    record.dispatchedAgentId = agentId;
    record.phase = "executing";

    // Phase 4: Execute — local compositional demo only (not RC1).
    // RC1 autonomous research runs via /project run → ResearchProject.run_research().
    // In production this panel stays demo-scoped; do not treat it as live RC1.
    setLoopStore("phase", "executing");
    agentActions.updateAgentProgress(agentId, 50, `Executing ${topAction.actionType}`);

    // Write result to Blackboard
    const blackboardType = actionToBlackboardType[topAction.actionType];
    const entryId = blackboardActions.writeEntry({
      type: blackboardType,
      title: `${topAction.actionType} result`,
      content: topAction.description,
      authorAgentId: agentId,
      authorRole: requiredRole,
      confidence: topAction.score,
      provenance: [],
      tags: [topAction.actionType],
    });
    record.blackboardEntryId = entryId;

    // Mark agent as completed
    agentActions.updateAgentProgress(agentId, 100, `Completed ${topAction.actionType}`);
    agentActions.transitionAgent(agentId, "completed");

    // Phase 5: Adapt — planner adjusts based on new evidence
    setLoopStore("phase", "adapting");
    const adaptation = this.adaptPlan(assessment, topAction);
    record.plannerAdaptation = adaptation;
    record.phase = "completed";

    // Update iteration count
    setLoopStore("iteration", iteration);

    // Store in history
    setLoopStore("history", (list) => [record, ...list].slice(0, 200));

    return record;
  },

  /**
   * Planner adaptation: adjust research plan based on new evidence.
   *
   * Triggers:
   * - Low-confidence hypothesis → verify next
   * - High-confidence hypothesis → design experiment
   * - Budget low → reduce experiment scope
   * - Blocked agents → redirect
   */
  adaptPlan(
    assessment: { overallScore: number; weaknesses: string[]; recommendations: string[] },
    lastAction: ActionCandidate,
  ): string {
    const adaptations: string[] = [];

    // If overall score is low, recommend strategy review
    if (assessment.overallScore < 0.5) {
      adaptations.push("Overall strategy score low — recommend full strategy review");
    }

    // Check weaknesses
    for (const weakness of assessment.weaknesses) {
      if (weakness.includes("budget")) {
        adaptations.push("Budget constrained — reduce experiment scope or defer non-critical actions");
      }
      if (weakness.includes("blocked")) {
        adaptations.push("Blocked agents detected — redirect or cancel");
      }
      if (weakness.includes("approval")) {
        adaptations.push("Pending approvals — prioritize human review");
      }
    }

    // Action-specific adaptations
    if (lastAction.actionType === "generate_hypothesis") {
      adaptations.push("New hypothesis generated — queue verification action");
    } else if (lastAction.actionType === "verify_knowledge") {
      adaptations.push("Knowledge verified — update confidence scores");
    } else if (lastAction.actionType === "analyze_results") {
      adaptations.push("Results analyzed — check if hypothesis supported/refuted");
    }

    // Check recommendations
    for (const rec of assessment.recommendations) {
      adaptations.push(`Recommendation: ${rec}`);
    }

    const adaptation = adaptations.length > 0 ? adaptations.join("; ") : "No adaptation needed";

    setLoopStore("plannerAdaptations", (list) => [
      ...list,
      {
        timestamp: new Date().toISOString(),
        adaptation,
        trigger: lastAction.actionType,
      },
    ].slice(-100));

    agentActions.emitEvent("agent_reasoning", {
      source: "planner_adaptation",
      adaptation,
      trigger: lastAction.actionType,
    });

    return adaptation;
  },

  /**
   * Get the current phase.
   */
  getPhase(): LoopPhase {
    return loopStore.phase;
  },

  /**
   * Get the iteration count.
   */
  getIteration(): number {
    return loopStore.iteration;
  },

  /**
   * Get the loop history.
   */
  getHistory(): LoopIteration[] {
    return loopStore.history;
  },

  /**
   * Get planner adaptations.
   */
  getPlannerAdaptations(): { timestamp: string; adaptation: string; trigger: string }[] {
    return loopStore.plannerAdaptations;
  },

  /**
   * Check if loop should continue.
   */
  shouldContinue(): boolean {
    if (!loopStore.autoRun) return false;
    if (loopStore.phase === "paused_budget" || loopStore.phase === "paused_approval") return false;
    if (loopStore.phase === "completed" || loopStore.phase === "failed") return false;
    if (loopStore.iteration >= loopStore.maxIterations) return false;
    const budget = agentRuntime.missionControl.computeBudget;
    if (budget.remaining < loopStore.budgetThreshold) return false;
    return true;
  },

  /**
   * Set max iterations (safety limit).
   */
  setMaxIterations(max: number): void {
    setLoopStore("maxIterations", max);
  },

  /**
   * Set budget threshold.
   */
  setBudgetThreshold(threshold: number): void {
    setLoopStore("budgetThreshold", threshold);
  },

  /**
   * Reset the loop store.
   */
  resetLoop(): void {
    setLoopStore(produce((state) => {
      state.phase = "idle";
      state.iteration = 0;
      state.autoRun = false;
      state.history = [];
      state.lastError = null;
      state.plannerAdaptations = [];
    }));
  },
};
