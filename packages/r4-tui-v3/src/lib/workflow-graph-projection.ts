/**
 * T5 topic-13: live workflow execution → workflow-graph projection.
 *
 * The `agentRuntime.workflowGraph` drive display nodes only from demo seeds
 * today. This mapper builds the same WorkflowGraph from one `UmrsWorkflowExecution`
 * record (raw per-step payload from `workflow.detail`). Runs at every
 * `trackExecution` call so the graph view observes live executions without
 * any new poll thread.
 */

import { agentActions } from "../stores/agent-runtime";
import type { WorkflowNode, WorkflowNodeStatus, WorkflowNodeType, WorkflowGraph } from "../types";
import type { UmrsWorkflowExecution } from "../stores/umrs-workflow";

const NODE_TYPE_KEYWORDS: Array<[WorkflowNodeType, string[]]> = [
  ["publication", ["ingest", "extract", "publish"]],
  ["agent", ["model", "llm", "infer"]],
  ["experiment", ["experiment"]],
  ["review", ["review"]],
  ["decision", ["decide", "decision"]],
];

/** Map a step-kind (backend text) to a node type. Only the existing
 * taxonomy family is allowed (never invented). */
function nodeTypeForStep(text: string): WorkflowNodeType {
  const lower = text.toLowerCase();
  for (const [type, keywords] of NODE_TYPE_KEYWORDS) {
    if (keywords.some((k) => lower.includes(k))) return type;
  }
  return "agent";
}

/** UMRS's own step words (workflow_runner.py set_step): "succeeded" for a
 * finished step, "skipped" for one an earlier wave's failure never ran
 * (2026-10-24 review: only "completed" mapped, so real runs drew every
 * finished step as pending and the status-bar badge stayed at 0 done). */
const STEP_STATUS_MAP: ReadonlyArray<[WorkflowNodeStatus, Set<string>]> = [
  ["completed", new Set(["completed", "succeeded", "success", "ok", "done"])],
  ["running", new Set(["running", "in_progress", "started", "submitted"])],
  ["failed", new Set(["failed", "error"])],
  ["blocked", new Set(["blocked", "denied", "skipped"])],
  ["waiting", new Set(["paused", "waiting", "waiting_approval", "pending"])],
];

/** Step-record status → WorkflowNodeStatus. */
export function statusForStep(rawStatus: string | undefined): WorkflowNodeStatus {
  const s = (rawStatus ?? "").toLowerCase();
  for (const [status, set] of STEP_STATUS_MAP) {
    if (set.has(s)) return status;
  }
  return "pending";
}

interface StepRecordRow {
  name: string;
  status?: string;
  [k: string]: unknown;
}

/** Extract the per-step map from a workflow execution payload:
 * prefer `execution.results.steps`, then `execution.steps`. Returns
 * rows only (jobs-only detail shapes are rejected truthfully). */
function stepRowsFor(execution: UmrsWorkflowExecution): StepRecordRow[] {
  const results = (execution as unknown as Record<string, unknown>).results;
  const stepsMap =
    (results && typeof results === "object" && (results as Record<string, unknown>).steps) ||
    (execution as unknown as Record<string, unknown>).steps;
  if (!stepsMap || typeof stepsMap !== "object") return [];
  if (Array.isArray(stepsMap)) {
    return stepsMap
      .filter((row): row is Record<string, unknown> => !!row && typeof row === "object")
      .map((row) => ({
        name: String(row.name ?? row.step_name ?? row.id ?? "?"),
        status: typeof row.status === "string" ? row.status : undefined,
        ...row,
      }));
  }
  const rows: StepRecordRow[] = [];
  for (const [name, value] of Object.entries(stepsMap as Record<string, unknown>)) {
    if (!value || typeof value !== "object") continue;
    rows.push({ name, status: typeof (value as { status?: string }).status === "string" ? (value as { status: string }).status : undefined, ...value } as StepRecordRow);
  }
  return rows;
}

/** First step name parked at a manual-approval gate (server status
 * `waiting_approval` — umrs workflow_runner sets exactly that string), or
 * null when no step waits. Drives the workflow overlay's [a] approve verb. */
export function waitingApprovalStepName(execution: UmrsWorkflowExecution): string | null {
  const row = stepRowsFor(execution).find(
    (r) => (r.status ?? "").toLowerCase() === "waiting_approval",
  );
  return row ? row.name : null;
}

/** Project one workflow execution onto `agentRuntime.workflowGraph` — replaces
 * the entire graph with this execution's own step topology (upserted per
 * poll tick, replaces-in-place). Node ids carry the execution id so two
 * simultaneous executions never collide. */
export function projectWorkflowExecutionOntoGraph(execution: UmrsWorkflowExecution): void {
  const rows = stepRowsFor(execution);
  if (rows.length === 0) return;
  const execId = String(execution.execution_id ?? "?");

  const nodes: WorkflowNode[] = rows.map((row, idx) => ({
    id: `${execId}:${row.name}`,
    type: nodeTypeForStep(row.name),
    label: `${row.name}`,
    status: statusForStep(row.status),
    metadata: { position: idx, total: rows.length },
  }));

  const edges: WorkflowGraph["edges"] = [];
  for (let i = 0; i < nodes.length - 1; i++) {
    edges.push({ from: nodes[i]!.id, to: nodes[i + 1]!.id });
  }

  agentActions.setWorkflowGraph({ nodes, edges });
}
