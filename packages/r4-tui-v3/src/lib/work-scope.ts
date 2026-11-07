/**
 * The Work pane's project scope as one rule (2026-10-24 review): the pane's
 * rows, the status-bar workflow badge and its Alt+W jump all show the same
 * executions. STRICT, as the pane always was (2026-10-01 (11)+(12)): a row
 * without a matching project stamp hides until `A` (all projects) is on;
 * with no active project there is nothing to scope by and everything shows.
 */

import { activeTab } from "../stores";

/** The project the Work pane scopes by, or null when none is active. */
export function activeWorkProjectId(): string | null {
  return activeTab().project?.id ?? (activeTab().identityProjectId || null);
}

/** True when a row stamped `projectId` is inside the Work pane's scope. */
export function inWorkScope(projectId?: string | null): boolean {
  if (activeTab().workScopeAll) return true;
  const active = activeWorkProjectId();
  return !active || projectId === active;
}
