/**
 * Ownership label of a UMRS workflow row (2026-10-23).
 *
 * UMRS marks a catalog row another user shared with the caller `shared: true`
 * and keeps the owner's fields on it (`scope: "user"`, `user_id` = owner), so
 * a label built from the scope alone read "[user]" on a workflow the caller
 * does not own. One wording on every surface (TUI and `r4 workflow list`):
 *   own row      "user"              the server's scope, unchanged
 *   system row   "system"
 *   shared row   "shared · <owner>"
 * The access entry that admitted the caller is decided server-side (circle
 * membership, the verified project header), so no label names it.
 */

export type WorkflowOwnership = {
  owner?: string | null;
  shared?: boolean | null;
  tags?: string[] | null;
};

export type CatalogOwnershipRow = {
  workflow_name?: string;
  name?: string | null;
  owner?: string | null;
  shared?: boolean | null;
};

export function sharedLabel(owner?: string | null): string {
  return owner ? `shared · ${owner}` : "shared";
}

/** The bracketed label of a `/workflow list` row: shared rows by the flag, else the tags. */
export function workflowScopeLabel(w: WorkflowOwnership): string {
  if (w.shared === true) return sharedLabel(w.owner);
  return (w.tags ?? []).slice(0, 3).join(",");
}

/** True when the loaded catalog lists `name` from `owner` as shared with the
 * caller. Both must be known: a describe body with an empty `user_id` (legacy
 * entry) never borrows another owner's shared row of the same name. */
export function isSharedInCatalog(
  catalog: readonly CatalogOwnershipRow[],
  name: string | null | undefined,
  owner?: string | null,
): boolean {
  if (!name || !owner) return false;
  return catalog.some(
    (row) => row.shared === true && (row.workflow_name ?? row.name) === name && row.owner === owner,
  );
}

/**
 * The describe payload (`/workflow show`) with `scope` read as
 * "shared · <owner>" when the catalog marks that workflow shared. The describe
 * body carries no shared flag of its own, only the owner's scope and user_id.
 */
export function describeWithSharing(payload: unknown, catalog: readonly CatalogOwnershipRow[]): unknown {
  const p = (payload ?? {}) as Record<string, unknown>;
  const name = String(p.name ?? p.workflow_name ?? "");
  const owner = typeof p.user_id === "string" ? p.user_id : undefined;
  if (!isSharedInCatalog(catalog, name, owner)) return payload;
  return { ...p, scope: sharedLabel(owner) };
}
