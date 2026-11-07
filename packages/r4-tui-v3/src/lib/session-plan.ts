/**
 * Session plan — structured plan-of-record for the Plan tab graph.
 *
 * The agent's todo_write calls are mirrored server-side into the session
 * TaskGraph (r4 bridge `get_session_plan`), giving the TUI stable ids,
 * statuses and dependency edges instead of fragile chat-text parsing.
 * Workflow executions launched during the session (workflow_run /
 * workflow_upload tool calls) join the graph as extra nodes so the umrs
 * workflow lane is visible in the same picture.
 *
 * Pure helpers + one tolerant fetcher (bridge-down keeps prior data).
 */

import { actions, workspace } from "../stores";
import { getCompanionBridge } from "../bridge";
import { applyOnTab } from "../tabs/registry";
import { isOfflineBackend } from "./console-mode";
import type { SessionPlanStep } from "../types";
import type { ActivityLine } from "./tool-activity";

/** Node kinds in the session plan graph. */
export type PlanNodeKind = "step" | "workflow";

export interface WorkflowPlanNode {
  kind: "workflow";
  id: string;
  label: string;
  status: string;
}

/** Plan graph row: either a structured step or a workflow execution node. */
export type PlanEntity =
  | { kind: "step"; step: SessionPlanStep }
  | WorkflowPlanNode;

/** Fetch the structured plan snapshot from the bridge (tolerant). */
export async function fetchSessionPlan(): Promise<void> {
  if (isOfflineBackend()) return;
  // The plan belongs to the tab whose bridge answered (w3-tui, 2026-10-21:
  // a fetch resolving after a tab switch drew A's plan into B's Plan tab).
  const tabId = workspace.activeChatTabId;
  try {
    const res = (await getCompanionBridge().request("get_session_plan", {}, 15_000)) as {
      steps?: Array<Record<string, unknown>>;
    };
    const steps = res.steps;
    if (!Array.isArray(steps)) return;
    applyOnTab(tabId, () => actions.setSessionPlan(steps.map(mapPlanStep)));
  } catch {
    /* bridge down or pre-plan backend — the todo mirror stays as fallback */
  }
}

function mapPlanStep(raw: Record<string, unknown>): SessionPlanStep {
  return {
    id: String(raw.id ?? ""),
    title: String(raw.title ?? ""),
    status: String(raw.status ?? "pending"),
    progress: typeof raw.progress === "number" ? raw.progress : 0,
    depends_on: Array.isArray(raw.depends_on) ? raw.depends_on.map(String) : [],
  };
}

/**
 * Depth of one step = longest dependency chain to a root (deps-first DAG
 * layering). Unknown/absent deps → depth 0. Cycles cannot reach a root —
 * the visited set returns their depth as 0 so rendering never loops.
 */
export function planStepDepths(steps: SessionPlanStep[]): Map<string, number> {
  const byId = new Map(steps.map((s) => [s.id, s]));
  const resolved = new Map<string, number>();
  const pending = new Set(steps.map((s) => s.id));
  // Kahn-style layering: a step resolves once every known dep has a depth.
  // Cyclic leftovers never resolve → depth 0 (a cycle has no DAG layer).
  let progress = true;
  while (progress) {
    progress = false;
    for (const id of pending) {
      const depth = resolveDepth(byId.get(id)!, byId, resolved);
      if (depth === null) continue;
      resolved.set(id, depth);
      pending.delete(id);
      progress = true;
    }
  }
  for (const id of pending) resolved.set(id, 0);
  return resolved;
}

/** Depth when every known dep is resolved; null while a known dep pends. */
function resolveDepth(
  step: SessionPlanStep,
  byId: Map<string, SessionPlanStep>,
  resolved: Map<string, number>,
): number | null {
  let depth = 0;
  for (const depId of step.depends_on) {
    if (byId.has(depId) && !resolved.has(depId)) return null;
    const depDepth = resolved.get(depId);
    if (depDepth !== undefined) depth = Math.max(depth, depDepth + 1);
  }
  return depth;
}

/** Status glyph per step (color applied by the caller's theme slot). */
export const PLAN_STATUS_GLYPH: Record<string, string> = {
  pending: "○",
  active: "◐",
  done: "✓",
  failed: "✖",
};

/** Workflow tool names whose results carry a workflow execution record. */
const WORKFLOW_LAUNCH_TOOLS = new Set([
  "workflow_run",
  "workflow_upload",
  "workflow_replay",
  "project_run",
]);

/**
 * Workflow executions visible in the session's tool stream → plan nodes.
 * Reads the tool RESULT JSON (`execution_id`/`workflow_name`/`status`);
 * error envelopes and non-workflow calls drop out. Dedupes by execution id.
 */
export function workflowNodesFromCalls(calls: ActivityLine[]): WorkflowPlanNode[] {
  const nodes = new Map<string, WorkflowPlanNode>();
  for (const call of calls) {
    const name = (call.name ?? "").trim().toLowerCase();
    if (!WORKFLOW_LAUNCH_TOOLS.has(name)) continue;
    const node = parseWorkflowNode(call);
    if (node && !nodes.has(node.id)) nodes.set(node.id, node);
  }
  return [...nodes.values()];
}

function parseWorkflowNode(call: ActivityLine): WorkflowPlanNode | null {
  const result = call.resultText ?? call.text ?? "";
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(result) as Record<string, unknown>;
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const execId = String(parsed.execution_id ?? "").trim();
  if (!execId) return null;
  const wfName = String(parsed.workflow_name ?? call.name ?? "workflow");
  const status = String(parsed.status ?? "launched");
  return { kind: "workflow", id: execId, label: wfName, status };
}

/** Merge steps + workflow nodes into display order (steps first, then workflows). */
export function planEntities(
  steps: SessionPlanStep[],
  calls: ActivityLine[],
): PlanEntity[] {
  const entities: PlanEntity[] = steps.map((step) => ({ kind: "step", step }));
  return entities.concat(workflowNodesFromCalls(calls));
}
