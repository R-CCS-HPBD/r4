/**
 * Model-catalog rows for the P10-C3 console badge (WP-06 r2): the /models
 * picker's visibility chips. Rows are taken verbatim — the server decides
 * which rows exist and which chip they carry; the view never invents
 * visibility.
 *
 * G8 (onboarding plan 2026-10-15 §5 B): the catalog (`/gw/v1/models` → UMRS
 * /umrs/models) is an authenticated fact, and the console holds no source key
 * and sends no credentials. Asking it keylessly can only answer 401 (one
 * `auth_missing` audit row per source per picker open) or hang on an
 * unreachable gateway, so the console makes no request: the rows will come
 * from the r4 CLI (`r4 source list --health --json`, a later wave) through
 * authenticatedCatalogRows — the one place that wave plugs in. Until then
 * there are none, and the picker shows no chips (health only).
 */

export interface ModelCatalogRow {
  model_id?: string;
  name?: string;
  scope?: string;
  status?: string;
  visibility_source?: string | null;
}

/** Rows from a catalog body (`{models: [...]}` or `{data: [...]}`); [] for anything else. */
export function catalogRows(body: unknown): ModelCatalogRow[] {
  const record = (body ?? {}) as Record<string, unknown>;
  const rows = record.models ?? record.data ?? [];
  return Array.isArray(rows) ? (rows as ModelCatalogRow[]) : [];
}

/**
 * Catalog rows the r4 CLI has fetched with the source key. No request is
 * made from the console; until the CLI reports them (later wave) → [].
 */
export async function authenticatedCatalogRows(): Promise<ModelCatalogRow[]> {
  return [];
}

/** Map model name → visibility chip. Keys are exact model names; no fuzzy merge. */
export function visibilityByName(rows: ModelCatalogRow[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const row of rows) {
    const name = String(row.name ?? "");
    const chip = String(row.visibility_source ?? "").trim();
    if (name && chip && !out.has(name)) out.set(name, chip);
  }
  return out;
}
