/**
 * Slash-command ownership, read from catalog.manifest.json (plan H-5,
 * 2026-10-18). slash.ts and wave-intercepts.ts take every ownership decision
 * from here: which rows hand their args form to the companion (bareOnly),
 * the rows' aliases, which aliases are rewritten to their canonical command,
 * which names the wave-intercept stage sees, which companion rows v3 drops,
 * and the verb sets that go to the companion. The Python bridge reads the
 * same file (r4/src/r4/companion/ui/slash_manifest.py).
 */

import manifest from "./catalog.manifest.json";

export type SlashOwner = "local" | "forward" | "bare-local-args-forward";
export type InterceptStage = "wave" | "builtin" | "owned" | "pane" | "managed" | "catalog";

export interface ManifestRow {
  name: string;
  owner: SlashOwner;
  aliases: string[];
  intercept: InterceptStage;
  canon?: Record<string, string>;
  forwardVerbs?: string[];
  localVerbs?: string[];
  forwardAs?: string;
  interceptAliases?: string[];
  companionRow?: "omit" | "ignore";
  slashRow?: boolean;
  ignoresArgs?: boolean;
}

export const MANIFEST_ROWS: readonly ManifestRow[] = manifest.rows as ManifestRow[];

// Maps, not object literals: the keys are user input, and `/constructor` or
// `/__proto__` must not resolve to an inherited Object.prototype member.
const BY_NAME = new Map<string, ManifestRow>(MANIFEST_ROWS.map((row) => [row.name, row]));

export function manifestRow(name: string): ManifestRow | undefined {
  return BY_NAME.get(name);
}

/** Every name the row answers to: its name, aliases and intercept aliases. */
export function rowNames(row: ManifestRow): string[] {
  return [row.name, ...row.aliases, ...(row.interceptAliases ?? [])];
}

type Canon = (args: string) => { name: string; args: string };

function canonFor(name: string, prefix: string): Canon {
  if (!prefix) return (args) => ({ name, args });
  return (args) => ({ name, args: args ? `${prefix} ${args}` : "" });
}

const ALIAS_CANON = new Map<string, Canon>(
  MANIFEST_ROWS.flatMap((row) =>
    Object.entries(row.canon ?? {}).map(([alias, prefix]): [string, Canon] => [alias, canonFor(row.name, prefix)]),
  ),
);

/** The rewrite for an alias whose canonical row runs locally, if any. */
export function aliasCanon(name: string): Canon | undefined {
  return ALIAS_CANON.get(name);
}

// Alias → row; a row's own name wins (BY_NAME is read first), and the first
// row declaring an alias keeps it.
const BY_ALIAS = new Map<string, ManifestRow>();
for (const row of MANIFEST_ROWS) {
  for (const alias of row.aliases) if (!BY_ALIAS.has(alias)) BY_ALIAS.set(alias, row);
}

/** The row a typed name or alias claims when that row's intercept is
 * `stage`; undefined otherwise (another stage, or not a v3 row). */
export function stageRowName(name: string, stage: InterceptStage): string | undefined {
  const row = BY_NAME.get(name) ?? BY_ALIAS.get(name);
  return row?.intercept === stage ? row.name : undefined;
}

/** The names of the rows whose intercept is `stage`. */
export function stageRowNames(stage: InterceptStage): string[] {
  return MANIFEST_ROWS.filter((row) => row.intercept === stage).map((row) => row.name);
}

const WAVE_NAMES = new Set(MANIFEST_ROWS.filter((row) => row.intercept === "wave").flatMap(rowNames));

/** True when the wave-intercept stage may claim this name. */
export function isWaveIntercepted(name: string): boolean {
  return WAVE_NAMES.has(name);
}

const IGNORED_COMPANION_ROWS = new Set(
  MANIFEST_ROWS.filter((row) => row.companionRow === "ignore").map((row) => row.name),
);

/** True when v3 owns this row wholesale and drops the companion's row. */
export function companionRowIgnored(name: string): boolean {
  return IGNORED_COMPANION_ROWS.has(name);
}

/** A row's verb set ("" is the bare command); empty for an unknown row. */
export function manifestVerbs(name: string, field: "forwardVerbs" | "localVerbs"): ReadonlySet<string> {
  return new Set(BY_NAME.get(name)?.[field] ?? []);
}

/** Ownership fields of one catalog row: bareOnly and aliases. A row with no
 * manifest aliases gets none set (an `aliases: undefined` would wipe the
 * companion row's aliases when the catalogs merge). */
function withOwnership<T extends { name: string; bareOnly?: boolean; aliases?: string[] }>(entry: T): T {
  const row = BY_NAME.get(entry.name);
  if (!row) return entry;
  const out = { ...entry };
  if (row.owner === "bare-local-args-forward") out.bareOnly = true;
  if (row.aliases.length > 0) out.aliases = [...row.aliases];
  return out;
}

export function applyManifestOwnership<T extends { name: string; bareOnly?: boolean; aliases?: string[] }>(
  entries: T[],
): T[] {
  return entries.map(withOwnership);
}
