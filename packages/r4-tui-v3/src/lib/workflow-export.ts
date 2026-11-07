/**
 * Workflow export in the console (2026-11-07): a workflow the caller can see
 * — own, shared to them, or shared to their project — saved as a verified
 * zip. One bridge verb (`workflow.export`) does the work: it picks the folder
 * (the console's working directory when writable, else ~/Downloads), never
 * overwrites (`name-1.zip`, …) and checks EXPORT.json before it answers.
 *
 * Two entry points share it: `e` on the workflow-definition card and
 * `/workflow export NAME` (src/commands/workflow-export.ts). The outcome is
 * a transcript notice (saved path + counts) or an error card, never silent.
 */

import { getCompanionBridge } from "../bridge";
import { rpc, type RpcRail, type RpcResult } from "../bridge/rpc";
import { actions } from "../stores";
import { isOfflineBackend } from "./console-mode";

/** UMRS caps an export at 200 MiB of files: give the stream time. */
export const WORKFLOW_EXPORT_TIMEOUT_MS = 600_000;

export type WorkflowExportResult = RpcResult<"workflow.export">;

function countText(n: number | null | undefined, word: string): string | null {
  return typeof n === "number" ? `${n} ${word}` : null;
}

/** "exported workflow W → /path/W.zip (3 files, 1 excluded) · images not included". */
export function exportNotice(result: WorkflowExportResult): string {
  const counts = [countText(result.files, "files"), countText(result.excluded, "excluded")].filter(Boolean);
  const tail = counts.length ? ` (${counts.join(", ")})` : "";
  return `exported workflow ${result.workflow_name} → ${result.path}${tail} · container images not included`;
}

/** The one bridge call both entry points make. */
export function requestWorkflowExport(rail: RpcRail, workflowName: string): Promise<WorkflowExportResult> {
  return rpc(rail, "workflow.export", { workflow_name: workflowName }, WORKFLOW_EXPORT_TIMEOUT_MS);
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

async function liveExport(workflowName: string): Promise<WorkflowExportResult> {
  if (isOfflineBackend()) throw new Error("workflow export needs a live backend (not offline/demo mode)");
  const bridge = getCompanionBridge();
  await bridge.ensureStarted();
  return requestWorkflowExport(bridge, workflowName);
}

/** The definition card's `e`: export, then a notice or an error card (never throws). */
export async function exportWorkflowWithNotice(workflowName: string): Promise<boolean> {
  actions.sendSystemNotice(`exporting workflow ${workflowName}…`);
  try {
    actions.sendSystemNotice(exportNotice(await liveExport(workflowName)));
    return true;
  } catch (err) {
    actions.sendErrorCard({ what: `workflow export ${workflowName} failed`, action: "needs_you", detail: errorText(err) });
    return false;
  }
}
