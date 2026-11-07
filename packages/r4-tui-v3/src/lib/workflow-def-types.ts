/**
 * The workflow definition types (`workflow.package`, 2026-10-01). Pure
 * (generated wire types only), so the definition card's row builders
 * (`workflow-card-rows.ts`, `workflow-params.ts`) reach no Solid store and the
 * VS Code host can use them (ADR-103 D-3); `stores/umrs-workflow.ts`
 * re-exports them.
 */

import type { WorkflowPackage, WorkflowPackageFile, WorkflowStepDefinition } from "../bridge/rpc-types.gen";

/** One step row from the workflow DEFINITION (workflow.json verbatim —
 * depends_on drives the pane's graph view; absent on every step = the runner
 * chains them in declaration order). The wire type. */
export type WorkflowDefStep = WorkflowStepDefinition;

/** One registered-code file row (content capped server-side; `truncated`
 * flags it honestly). The wire type. */
export type WorkflowDefFile = WorkflowPackageFile;

/** Definition + registered-code package (`workflow.package` RPC): the wire
 * `WorkflowPackage`, with `workflow_name` filled from the name the store asked
 * for when UMRS sends null. Every other field optional — capability-detected
 * rendering: absent slots stay hidden, never fabricated. */
export type WorkflowDefinitionDetail = Omit<WorkflowPackage, "workflow_name"> & {
  workflow_name: string;
  /** Set from the catalog (the package body has no shared flag): shared with the caller. */
  shared?: boolean;
};
