/**
 * Workflow versions on the console (ADR-113 WV4, 2026-11-09). Pure, so the
 * card builders, the work pane, the list overlay and the VS Code host share
 * one wording:
 *   ref        `name@1.2.0` picks a version; a bare `name` runs the default
 *   row label  `v1.2.0 (+2)`: the default and how many other versions exist
 *   run label  `name@1.2.0 · 3f2a9c1d0b7e`: what a run pinned (version + digest)
 * Older servers send no `versions`: a row is then one version (its `version`,
 * when it has one) and nothing here invents a choice.
 */

/** The separator of `name@version` (r4_contracts `WORKFLOW_REF_SEPARATOR`). */
export const WORKFLOW_REF_SEPARATOR = "@";

/** Characters of a `source_sha256` shown on run rows. */
export const SHORT_DIGEST_CHARS = 12;

export type WorkflowRef = { name: string; version?: string };

/** `"name@1.2.0"` → `{name, version}`; `"name"` → `{name}` (mirrors r4_contracts `parse_workflow_ref`). */
export function parseWorkflowRef(ref: string): WorkflowRef {
  const text = ref.trim();
  const cut = text.lastIndexOf(WORKFLOW_REF_SEPARATOR);
  if (cut <= 0) return { name: text };
  const version = text.slice(cut + 1).trim();
  if (!version || /\s/.test(version)) return { name: text };
  return { name: text.slice(0, cut).trim(), version };
}

/** `name@version` for display; the bare name when no version is known. */
export function workflowRefLabel(name: string | null | undefined, version?: string | null): string {
  const bare = name ?? "";
  return bare && version ? `${bare}${WORKFLOW_REF_SEPARATOR}${version}` : bare;
}

/** The first characters of a code digest; "" when the server sent none. */
export function shortDigest(sha?: string | null): string {
  return sha ? sha.slice(0, SHORT_DIGEST_CHARS) : "";
}

export type VersionedRow = {
  version?: string | null;
  versions?: string[] | null;
};

/** Every version a row offers, default first when the server lists it so;
 * an older server's row is its one `version` (or none). */
export function rowVersions(row: VersionedRow): string[] {
  const listed = (row.versions ?? []).filter(Boolean);
  if (listed.length) return listed;
  return row.version ? [row.version] : [];
}

/** True when the server speaks ADR-113 (it lists `versions`). */
export function serverListsVersions(row: VersionedRow): boolean {
  return Array.isArray(row.versions) && row.versions.length > 0;
}

/** `v1.2.0 (+2)`: the default version and how many others exist; "" when unknown. */
export function versionCountLabel(row: VersionedRow): string {
  const all = rowVersions(row);
  const current = row.version || all[0];
  if (!current) return "";
  const others = all.filter((v) => v !== current).length;
  return others ? `v${current} (+${others})` : `v${current}`;
}

/** The default among SemVer-descending `versions` (ADR-113 D-3): the highest
 * release, a pre-release only when no release exists. */
export function defaultVersionOf(versions: readonly string[]): string | undefined {
  return versions.find((v) => !v.includes("-")) ?? versions[0];
}

/** `1.2.0 (default) · ▸1.1.0 · 1.0.0`: the default marked, `shown` pointed at. */
export function versionListLabel(
  versions: readonly string[],
  defaultVersion?: string | null,
  shown?: string | null,
): string {
  const mark = (v: string) => `${v === shown && v !== defaultVersion ? "▸" : ""}${v}${v === defaultVersion ? " (default)" : ""}`;
  return versions.map(mark).join(" · ");
}

/** The version `step` places after `current` in `versions`, wrapping
 * (the picker's `v` / `V`); undefined with fewer than two versions. */
export function cycleVersion(versions: readonly string[], current: string | undefined, step: number): string | undefined {
  if (versions.length < 2) return undefined;
  const at = current ? versions.indexOf(current) : -1;
  const from = at < 0 ? 0 : at;
  return versions[(((from + step) % versions.length) + versions.length) % versions.length];
}

/** A catalog row's label: `name · v1.2.0 (+2)` (the default and how many
 * others), or `name@1.1.0 · picked (+2)` when the picker chose another. */
export function catalogRowLabel(name: string, row: VersionedRow, picked?: string): string {
  if (picked && picked !== row.version) {
    const others = rowVersions(row).filter((v) => v !== picked).length;
    const more = others ? " (+" + String(others) + ")" : "";
    return workflowRefLabel(name, picked) + " · picked" + more;
  }
  const count = versionCountLabel(row);
  return count ? `${name} · ${count}` : name;
}

/** The ref a catalog row shows and runs: `name@picked` for a pick other
 * than the default, else the bare name (the server's default). */
export function catalogRowRef(name: string, row: VersionedRow, picked?: string): string {
  return picked && picked !== row.version ? workflowRefLabel(name, picked) : name;
}

/** The picker's next choice on a catalog row (`v` forward, `V` back), or
 * undefined when the row has fewer than two versions. */
export function pickNextVersion(row: VersionedRow, picked: string | undefined, step: number): string | undefined {
  return cycleVersion(rowVersions(row), picked ?? row.version ?? undefined, step);
}

export type VersionedRun = {
  workflow_name?: string | null;
  workflow_version?: string | null;
  source_sha256?: string | null;
};

/** `name@1.2.0` of a run (bare name from an older server). */
export function runRefLabel(run: VersionedRun, fallback = "workflow"): string {
  return workflowRefLabel(run.workflow_name || fallback, run.workflow_version);
}

/** `name@1.2.0 · 3f2a9c1d0b7e` (the digest only when the run pinned one). */
export function runLabelWithDigest(run: VersionedRun, fallback = "workflow"): string {
  const digest = shortDigest(run.source_sha256);
  return digest ? `${runRefLabel(run, fallback)} · ${digest}` : runRefLabel(run, fallback);
}

/** The key a run's definition is cached under: the version it ran, else its name. */
export function runDefinitionKey(run: VersionedRun): string | undefined {
  return run.workflow_name ? workflowRefLabel(run.workflow_name, run.workflow_version) : undefined;
}

/** True when a describe/package body says this version's code is gone (ADR-113 D-8). */
export function codeUnavailable(body: { code?: string | null }): boolean {
  return (body.code ?? "").toLowerCase() === "unavailable";
}
