/**
 * `/workflow export NAME` (2026-11-07): save a workflow the caller can see
 * (own, shared, or shared to the active project) as a verified zip in the
 * console's working directory (else ~/Downloads); see lib/workflow-export.ts.
 *
 * Kept out of slash.ts (gate-frozen): the verb table there maps `export` to
 * `workflowExportCommand` and nothing more. A failure throws, so the table's
 * dispatcher names it in its "workflow export failed" error card.
 */

import type { SlashDispatchResult } from "./slash";
import type { RpcRail } from "../bridge/rpc";
import { actions } from "../stores";
import { exportNotice, requestWorkflowExport } from "../lib/workflow-export";

/** The slash.ts verb-table signature: tokens[0] is the verb, tokens[1] the name. */
export async function workflowExportCommand(bridge: RpcRail, tokens: string[]): Promise<SlashDispatchResult> {
  const name = tokens[1];
  if (!name) return { kind: "insert", text: "/workflow export " };
  actions.sendSystemNotice(`exporting workflow ${name}…`);
  actions.sendSystemNotice(exportNotice(await requestWorkflowExport(bridge, name)));
  return { kind: "handled" };
}
