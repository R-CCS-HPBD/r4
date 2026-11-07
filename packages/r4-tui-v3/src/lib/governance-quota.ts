/**
 * Quota gauge line for the Knowledge pane strip (D3 default: quotas ride
 * with the project build view). Pure — bridge overview in, line out.
 */

import type { GovernanceOverview } from "../types";

interface QuotaRow {
  project_id?: unknown;
  resource_type?: unknown;
  current_count?: unknown;
  max_count?: unknown;
}

/** `'quota proj1/endpoints 2/4 · proj1/models 6/8'` — null when no quotas. */
export function formatQuotaLine(
  overview: GovernanceOverview | null,
  maxItems = 3,
): string | null {
  const quotas = (overview?.quotas as { quotas?: QuotaRow[] } | undefined)?.quotas;
  if (!Array.isArray(quotas) || quotas.length === 0) return null;
  const parts = quotas.slice(0, maxItems).map((q) => {
    const proj = String(q.project_id ?? "?");
    const type = String(q.resource_type ?? "?");
    const cur = Number(q.current_count ?? 0);
    const max = Number(q.max_count ?? 0);
    return `${proj}/${type} ${cur}/${max}`;
  });
  const more = quotas.length > maxItems ? ` · +${quotas.length - maxItems} more` : "";
  return `quota ${parts.join(" · ")}${more}`;
}
