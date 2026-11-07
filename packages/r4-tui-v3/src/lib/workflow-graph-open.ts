/**
 * Open the live workflow-execution DAG overlay (2026-10-24, wave 72).
 *
 * One opener for every way in: Enter on an execution row in the Work pane,
 * `/workflow graph [id|latest]`, and the Alt+W jump from the status-bar
 * badge. Opening = point the overlay at the execution, re-read its record
 * once (the store's live poll keeps it moving after that) and fetch the
 * definition topology the DAG needs — capability-detected: without the
 * route the overlay draws its honestly labeled linear fallback.
 */

import { actions } from "../stores";
import {
  executionIsLive,
  umrsWorkflowActions,
  umrsWorkflowStore,
  type UmrsWorkflowExecution,
} from "../stores/umrs-workflow";
import { inWorkScope } from "./work-scope";

/**
 * Show the overlay for `id` and fetch its topology. `fresh` is a record the
 * caller has just read (`/workflow graph <id>`): no second read then.
 * Otherwise the record is re-read once (a row tracked without its workflow
 * name learns it from this read).
 */
export function openExecutionGraph(id: string, fresh?: UmrsWorkflowExecution): void {
  actions.setExecDetailId(id);
  umrsWorkflowActions.ensureDefinition(umrsWorkflowStore.activeExecutions[id]?.workflow_name);
  if (fresh) return;
  void umrsWorkflowActions
    .refreshStatus(id)
    .then((exec) => umrsWorkflowActions.ensureDefinition(exec.workflow_name))
    .catch(() => undefined);
}

/** Still running as far as this session knows: a live status, and UMRS has
 * not answered "not found" for it since (the `_stale` mark). */
export function executionIsRunning(exec: UmrsWorkflowExecution): boolean {
  return executionIsLive(exec.status) && !exec._stale;
}

/** The executions the Work pane shows (its project scope, lib/work-scope). */
function scopedExecutions(): UmrsWorkflowExecution[] {
  return Object.values(umrsWorkflowStore.activeExecutions).filter((exec) => inWorkScope(exec._scopeProjectId));
}

/** Sort key: when the run started (else was submitted); NaN when unknown. */
function startedKey(exec: UmrsWorkflowExecution): number {
  const record = exec as unknown as Record<string, unknown>;
  const stamp = record.started_at ?? record.submitted_at;
  return typeof stamp === "number" ? stamp : Number.NaN;
}

/** True when `candidate` is more recent than `best`: a known start time
 * wins over an unknown one; between equals the later-tracked row wins. */
function isNewer(candidate: UmrsWorkflowExecution, best: UmrsWorkflowExecution): boolean {
  const a = startedKey(candidate);
  const b = startedKey(best);
  if (Number.isNaN(b)) return true;
  return !Number.isNaN(a) && a >= b;
}

/** The most recent in-scope execution (running ones only with `liveOnly`). */
export function latestExecution(liveOnly: boolean): UmrsWorkflowExecution | undefined {
  let best: UmrsWorkflowExecution | undefined;
  for (const exec of scopedExecutions()) {
    if (liveOnly && !executionIsRunning(exec)) continue;
    if (!best || isNewer(exec, best)) best = exec;
  }
  return best;
}

/** Every in-scope execution still running (the badge's count). */
export function liveExecutions(): UmrsWorkflowExecution[] {
  return scopedExecutions().filter(executionIsRunning);
}

/** The target `/workflow graph` with no id (and Alt+W) opens: the most
 * recent running execution, else the most recent one of any status. */
export function defaultGraphTarget(): UmrsWorkflowExecution | undefined {
  return latestExecution(true) ?? latestExecution(false);
}

export type TrackedMatch = { id: string } | { ambiguous: string[] } | null;

/** A typed id against the tracked executions: exact, else a unique prefix
 * (the Work pane shows 12-char ids). */
export function matchTrackedExecution(typed: string): TrackedMatch {
  const ids = Object.keys(umrsWorkflowStore.activeExecutions);
  if (ids.includes(typed)) return { id: typed };
  const hits = ids.filter((id) => id.startsWith(typed));
  if (hits.length === 1) return { id: hits[0]! };
  return hits.length > 1 ? { ambiguous: hits } : null;
}

/** Alt+W: the overlay for the most recent RUNNING execution; a notice
 * (never a guess at a finished run) when nothing runs. `blockedBy` names an
 * open dialog (help, palette, a tool approval…): the overlay would draw
 * over it while it still owns Enter / y / n, so a notice instead. */
export function openRunningWorkflowGraph(blockedBy?: string): void {
  if (blockedBy) {
    actions.sendSystemNotice(`Alt+W: close the ${blockedBy} first (Esc) — then Alt+W opens the running workflow's graph`);
    return;
  }
  const running = latestExecution(true);
  if (!running) {
    actions.sendSystemNotice("no workflow execution is running — /workflow graph opens the latest one; /work lists them");
    return;
  }
  openExecutionGraph(running.execution_id);
}
