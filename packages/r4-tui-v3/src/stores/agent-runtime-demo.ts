/**
 * Demo seed for the Research Workspace.
 *
 * Populates the agent runtime store with realistic demo data so the
 * Research Workspace is immediately explorable without a live backend.
 *
 * This is for demonstration purposes only. In production, the data
 * comes from UMRS via the Gateway.
 */

import { agentActions, loadDemoWorkflowGraph } from "./agent-runtime";
import { demoProject } from "../adapters/fixtures";
import type { AgentRole } from "../types";

const DEMO_GOAL = demoProject.description;

export function seedAgentRuntimeDemo(): void {
  // ── Create Agents ──────────────────────────────────────────────────

  const literatureAgentId = agentActions.createAgent({
    role: "literature" as AgentRole,
    goal: "Index cataloged videos for hypothesis H7",
    currentTask: "Searching catalog for Tuesday sessions",
    currentObjective: "Map pose-coverage gaps",
    confidence: 0.72,
    progress: 45,
    reasoning: ["Knowledge graph links videos to the YOLO run", "Inventory + plan make the inputs explicit"],
    budget: { maxTokens: 50000, maxIterations: 10 },
    permissions: { egress: "internet", filesystem: "read_only", compute: "none" },
  });
  agentActions.transitionAgent(literatureAgentId, "planning");
  agentActions.transitionAgent(literatureAgentId, "running");

  const experimentAgentId = agentActions.createAgent({
    role: "experiment" as AgentRole,
    goal: "Run YOLO experiment E17 on cataloged videos",
    currentTask: "Running YOLO on staged videos",
    currentObjective: "Test H7 stability under condition A",
    confidence: 0.58,
    progress: 68,
    reasoning: ["Hypothesis H7 predicts condition A is most stable", "Night sessions (H8) are not compared yet"],
    budget: { maxTokens: 30000, maxIterations: 5, maxGpus: 2 },
    permissions: { egress: "none", filesystem: "read_write_scratch", compute: "slurm" },
  });
  agentActions.transitionAgent(experimentAgentId, "planning");
  agentActions.transitionAgent(experimentAgentId, "running");

  const reviewerAgentId = agentActions.createAgent({
    role: "reviewer" as AgentRole,
    goal: "Review video workflow provenance",
    currentTask: "Checking catalog links to the run",
    currentObjective: "Confirm detections point back to video keys",
    confidence: 0.65,
    progress: 30,
    reasoning: ["E17 used catalog inventory and analysis plan", "Finding F8 says night sessions look less stable"],
    budget: { maxTokens: 30000, maxIterations: 5 },
    permissions: { egress: "none", filesystem: "read_only", compute: "none" },
  });
  agentActions.transitionAgent(reviewerAgentId, "planning");
  agentActions.transitionAgent(reviewerAgentId, "running");

  // ── Enqueue Research Items ─────────────────────────────────────────

  agentActions.enqueueResearch("read_paper", {
    hypothesisId: "H7",
    priority: 2,
    agentId: literatureAgentId,
  });

  agentActions.enqueueResearch("generate_hypothesis", {
    priority: 1,
    dependencies: ["read_paper"],
  });

  agentActions.enqueueResearch("submit_hpc_job", {
    experimentId: "E17",
    priority: 3,
    agentId: experimentAgentId,
  });

  agentActions.enqueueResearch("analyze_results", {
    experimentId: "E17",
    priority: 2,
    dependencies: ["submit_hpc_job"],
  });

  // ── Add Timeline Entries ───────────────────────────────────────────

  agentActions.addTimelineEntry(
    "artifact_upload",
    "Uploaded videos, inventory.csv, analysis_plan.csv",
    "Catalog ingest: pending → ready",
  );

  agentActions.addTimelineEntry(
    "knowledge_extraction",
    "Extracted knowledge from cataloged videos",
    "Knowledge graph built with 42 nodes and 128 edges",
  );

  agentActions.addTimelineEntry(
    "hypothesis_created",
    "Generated hypothesis H7",
    "H7: Condition A yields the most stable movement pattern",
    undefined,
    "H7",
  );

  agentActions.addTimelineEntry(
    "experiment_started",
    "Started experiment E17",
    "Running YOLO on cataloged videos (mock profile)",
    experimentAgentId,
    "E17",
  );

  agentActions.addTimelineEntry(
    "finding_registered",
    "Registered finding F8",
    "F8: night sessions look less stable than condition A",
    reviewerAgentId,
    "F8",
  );

  agentActions.addTimelineEntry(
    "knowledge_correction",
    "Corrected relationship: Paper P3 → IMPLEMENTS → Algorithm A",
    "User identified missing implementation relationship",
  );

  // ── Request Approval ───────────────────────────────────────────────

  agentActions.requestApproval(
    "experiment_execution",
    "Approve experiment E19: 1 GPU, night-session compare",
    "E19 will test H8 on cataloged night videos. Human approval required.",
    experimentAgentId,
    "E19",
  );

  // ── Update Workflow Graph ──────────────────────────────────────────

  loadDemoWorkflowGraph();
  agentActions.updateWorkflowNode("start", "completed");
  agentActions.updateWorkflowNode("ingest", "completed");
  agentActions.updateWorkflowNode("extract", "completed");
  agentActions.updateWorkflowNode("hypothesis", "completed");
  agentActions.updateWorkflowNode("experiment", "running");
  agentActions.updateWorkflowNode("analyze", "waiting");
  agentActions.updateWorkflowNode("review", "pending");
  agentActions.updateWorkflowNode("publish", "pending");

  // ── Mission Control (sync aggregates from agents / gates) ───────────

  agentActions.updateMissionControl({
    researchGoal: DEMO_GOAL,
    successCriteriaProgress: 67,
    computeBudget: {
      remaining: 340,
      burnRate: 12,
      estimatedRunway: 28,
    },
    hypotheses: [
      { id: "H7", statement: "Condition A yields the most stable movement pattern", confidence: 0.72 },
      { id: "H8", statement: "Condition B is more stable at night", confidence: 0.45 },
      { id: "H9", statement: "Inventory + plan are enough to reproduce the run", confidence: 0.89 },
    ],
    blockers: [
      { id: "B1", description: "Two videos still pending catalog ingest", severity: "warning" },
    ],
  });
  agentActions.refreshMissionControl();

  // ── Decision Explanations ──────────────────────────────────────────

  agentActions.addExplanation({
    decisionId: "D1",
    decisionType: "experiment_recommendation",
    target: "E17",
    reasoningChain: [
      "Strategy Engine ranked E17 highest (score: 0.87)",
      "Strategy Engine evaluated 5 candidate experiments",
      "Strategy Engine used knowledge graph state at T=14:32",
    ],
    graphPaths: [
      ["H7", "depends_on", "E12"],
      ["E12", "inconclusive", "p=0.08"],
      ["H7", "supported_by", "P23"],
    ],
    supportingEvidence: [
      { id: "VID-12", description: "Tuesday session video in the catalog", confidence: 0.85 },
      { id: "YOLO-17", description: "Detections from the mock YOLO run", confidence: 0.42 },
      { id: "INV-1", description: "Inventory + analysis plan for those keys", confidence: 0.78 },
    ],
    confidenceCalculation: {
      prior: 0.45,
      likelihood: 0.62,
      posterior: 0.51,
    },
    alternatives: [
      { id: "E18", description: "Replicate E12 with larger sample", score: 0.71 },
      { id: "E19", description: "Test contradicting hypothesis H8", score: 0.65 },
    ],
    rejected: [
      { id: "E20", description: "Insufficient compute budget remaining", reason: "Budget exceeded" },
      { id: "E21", description: "Requires data not yet ingested", reason: "Missing data" },
    ],
    timestamp: new Date().toISOString(),
    agentId: "planner-001",
  });
}
