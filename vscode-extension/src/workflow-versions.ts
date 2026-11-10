/**
 * Workflow versions in the VS Code sidebar (ADR-113 WV4, 2026-11-09). Pure
 * (no `vscode` import), so the tree, the trigger command and the unit tests
 * share it; the wording comes from the console's own helpers
 * (packages/r4-tui-v3/src/lib/workflow-versions.ts, ADR-103 D-3).
 *
 * Older servers send no `versions`: a workflow is then one version, its
 * tree row has no children and the trigger asks for no version.
 */

import {
  defaultVersionOf,
  parseWorkflowRef,
  rowVersions,
  runLabelWithDigest,
  versionCountLabel,
  workflowRefLabel,
  type VersionedRow,
  type VersionedRun,
} from "../../packages/r4-tui-v3/src/lib/workflow-versions";

export { parseWorkflowRef, workflowRefLabel };

/** The fields of a catalog row this module reads. */
export type VersionedWorkflow = VersionedRow & {
  name: string;
  description?: string;
  code?: string;
};

/** The tree row of one workflow: `name` with `v1.2.0 (+2) · description`. */
export interface WorkflowRowSpec {
  label: string;
  description: string;
  /** Expandable when it has more than one version (a child per version). */
  expandable: boolean;
}

export function workflowRowSpec(w: VersionedWorkflow): WorkflowRowSpec {
  const count = versionCountLabel(w);
  const parts = [count, w.description ?? ""].filter(Boolean);
  return { label: w.name, description: parts.join(" · "), expandable: rowVersions(w).length > 1 };
}

/** The workflow's default version: the server's `version`, else the D-3 rule over `versions`. */
export function defaultVersion(w: VersionedRow): string | undefined {
  return w.version || defaultVersionOf(rowVersions(w));
}

/** One child row per version, as the server lists them (SemVer-descending). */
export interface VersionRowSpec {
  label: string;
  description: string;
  version: string;
}

export function versionRowSpecs(w: VersionedWorkflow): VersionRowSpec[] {
  const fallback = defaultVersion(w);
  return rowVersions(w).map((version) => ({
    label: `v${version}`,
    description: version === fallback ? "default" : "",
    version,
  }));
}

/** Quick-pick choices when triggering: the default first, then the rest in server order. */
export interface VersionPickItem {
  label: string;
  description?: string;
  version: string;
}

export function versionPickItems(w: VersionedRow): VersionPickItem[] {
  const fallback = defaultVersion(w);
  const rest = rowVersions(w).filter((v) => v !== fallback);
  const first: VersionPickItem[] = fallback ? [{ label: fallback, description: "default", version: fallback }] : [];
  return [...first, ...rest.map((version) => ({ label: version, version }))];
}

/** True when the trigger should ask which version: more than one is registered. */
export function needsVersionPick(w: VersionedRow | undefined): boolean {
  return Boolean(w) && rowVersions(w!).length > 1;
}

/** The trigger request body: parameters, plus `version` when one was chosen. */
export function triggerBody(
  parameters: Record<string, unknown> | undefined,
  version?: string,
): { parameters: Record<string, unknown>; version?: string } {
  return version ? { parameters: parameters ?? {}, version } : { parameters: parameters ?? {} };
}

/** `name@version · digest` of a run (bare name from an older server). */
export function runLabel(run: VersionedRun): string {
  return runLabelWithDigest(run);
}
