/**
 * `/workflow graph [id|latest]` (2026-10-24, wave 72): opens the live
 * workflow-execution DAG overlay directly — the same overlay Enter on an
 * execution row in the Work pane opens. Without an id (or with `latest`)
 * it picks the most recent running execution, else the most recent one.
 * An id the session has not tracked is read once from UMRS (an execution
 * started elsewhere joins like `/workflow trigger` adopts one); an id UMRS
 * does not know is named back, never a blank overlay.
 *
 * Kept out of slash.ts (gate-frozen): the verb table there maps `graph`
 * to `workflowGraphCommand` and nothing more.
 */

import type { SlashDispatchResult } from "./slash";
import { isExecutionNotFound, umrsWorkflowActions, type UmrsWorkflowExecution } from "../stores/umrs-workflow";
import { defaultGraphTarget, matchTrackedExecution, openExecutionGraph } from "../lib/workflow-graph-open";

const HANDLED: SlashDispatchResult = { kind: "handled" };

export const NO_EXECUTIONS_MESSAGE =
  "no workflow executions in this session yet — /work lists workflows (t triggers one); /workflow trigger <name> starts one";

function openLatest(): SlashDispatchResult {
  const target = defaultGraphTarget();
  if (!target) return { kind: "error", message: NO_EXECUTIONS_MESSAGE };
  openExecutionGraph(target.execution_id);
  return HANDLED;
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** A failed read named honestly: "unknown" only when UMRS said not found;
 * a timeout, a restarting bridge or offline mode is "could not read". */
function readFailure(id: string, err: unknown): SlashDispatchResult {
  const head = isExecutionNotFound(err) ? "unknown workflow execution" : "could not read workflow execution";
  return {
    kind: "error",
    message: `${head}: ${id} (${errorText(err)}) — /workflow graph opens the latest; /work lists the tracked runs`,
  };
}

/** An id this session never tracked: one UMRS read decides, and that read
 * is the overlay's first record (no second read on open). */
async function adoptAndOpen(id: string): Promise<SlashDispatchResult> {
  let record: UmrsWorkflowExecution;
  try {
    record = await umrsWorkflowActions.refreshStatus(id);
  } catch (err) {
    return readFailure(id, err);
  }
  openExecutionGraph(record.execution_id, record);
  return HANDLED;
}

/** Parse `graph [id|latest]` (tokens[0] is the verb) and open the overlay. */
export async function openWorkflowGraph(tokens: string[]): Promise<SlashDispatchResult> {
  const typed = tokens[1] ?? "";
  if (typed === "" || typed.toLowerCase() === "latest") return openLatest();
  const match = matchTrackedExecution(typed);
  if (match && "id" in match) {
    openExecutionGraph(match.id);
    return HANDLED;
  }
  if (match) {
    return { kind: "error", message: `ambiguous execution id ${typed}: ${match.ambiguous.map((id) => id.slice(0, 16)).join(", ")}` };
  }
  return adoptAndOpen(typed);
}

/** The slash.ts verb-table signature (bridge unused: the store owns reads). */
export function workflowGraphCommand(_bridge: unknown, tokens: string[]): Promise<SlashDispatchResult> {
  return openWorkflowGraph(tokens);
}
