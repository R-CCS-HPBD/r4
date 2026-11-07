/**
 * Status-bar workflow badge (2026-10-24, wave 72): `▶ 1 workflow · ◐ 3/7`
 * while UMRS workflow executions run — the count of live executions, and
 * for the most recent running one its done/total steps from the record the
 * store already polls. Pure read of the store: no poll of its own, hidden
 * at zero, dropped below WORKFLOW_BADGE_MIN_COLS so a narrow bar keeps its
 * identity and model. Alt+W opens that execution's live DAG overlay.
 */

import { umrsWorkflowStore, type UmrsWorkflowExecution } from "../stores/umrs-workflow";
import { statusForStep } from "./workflow-graph-projection";
import { latestExecution, liveExecutions } from "./workflow-graph-open";

/** Below this terminal width the badge is not shown at all. */
export const WORKFLOW_BADGE_MIN_COLS = 100;

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : undefined;
}

/** Raw step statuses of an execution record (`results.steps`, else
 * `steps`; map or list form) — uncapped, the badge counts every step. */
export function stepStatuses(exec: UmrsWorkflowExecution): string[] {
  const record = exec as unknown as Record<string, unknown>;
  const raw = asRecord(record.results)?.steps ?? record.steps;
  const rows = Array.isArray(raw) ? raw : Object.values(asRecord(raw) ?? {});
  return rows.map((row) => String(asRecord(row)?.status ?? ""));
}

/** A step that will not run again: finished (UMRS "succeeded") or skipped
 * after an earlier wave failed — the badge's "done". */
function stepSettled(status: string): boolean {
  return statusForStep(status) === "completed" || status.toLowerCase() === "skipped";
}

export interface StepProgress {
  done: number;
  total: number;
  /** False while the definition is not cached: UMRS adds a step row only
   * when the step starts, so `total` is then a lower bound (shown `n+`). */
  totalKnown: boolean;
}

/** done/total for one execution; total is the larger of the reported rows
 * and the cached definition's steps (unreported steps are pending). */
export function stepProgress(exec: UmrsWorkflowExecution): StepProgress {
  const statuses = stepStatuses(exec);
  const defined = exec.workflow_name ? umrsWorkflowStore.definitions[exec.workflow_name]?.steps?.length : undefined;
  const done = statuses.filter(stepSettled).length;
  return { done, total: Math.max(statuses.length, defined ?? 0), totalKnown: defined !== undefined };
}

function progressText(progress: StepProgress): string {
  return ` · ◐ ${progress.done}/${progress.total}${progress.totalKnown ? "" : "+"}`;
}

/** The badge text, or null when it should not render (none running in the
 * Work pane's scope, or the terminal is narrower than WORKFLOW_BADGE_MIN_COLS). */
export function workflowBadgeLabel(width: number): string | null {
  if (width < WORKFLOW_BADGE_MIN_COLS) return null;
  const count = liveExecutions().length;
  if (count === 0) return null;
  const head = `▶ ${count} workflow${count === 1 ? "" : "s"}`;
  const latest = latestExecution(true);
  const progress = latest ? stepProgress(latest) : undefined;
  return progress && progress.total > 0 ? head + progressText(progress) : head;
}

/** Cells the badge may paint: `▶`, `◐` and `·` are East-Asian ambiguous
 * width, two cells on a CJK terminal set that way — the chip budget
 * reserves the wide reading so the bar never overflows there. */
export function workflowBadgeCells(label: string): number {
  return Bun.stringWidth(label, { ambiguousIsNarrow: false });
}
