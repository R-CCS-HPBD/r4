/**
 * D1: which-key overlay read-model — grouped view of registered bindings.
 * Pure so the display contract stays unit-testable.
 */

export interface WhichKeyInput {
  key: string;
  cmd: string;
  space?: string;
}

export interface WhichKeyGroup {
  /** Command namespace with optional space tag, e.g. `research@research`. */
  group: string;
  entries: Array<{ key: string; cmd: string }>;
}

/** Group bindings by command namespace (prefix before the first dot + space). */
export function groupBindings(bindings: WhichKeyInput[]): WhichKeyGroup[] {
  const buckets = new Map<string, Map<string, string>>();
  for (const binding of bindings) {
    const ns = binding.cmd.includes(".")
      ? binding.cmd.slice(0, binding.cmd.indexOf("."))
      : "misc";
    const group = binding.space ? `${ns}@${binding.space}` : ns;
    let bucket = buckets.get(group);
    if (!bucket) {
      bucket = new Map();
      buckets.set(group, bucket);
    }
    // Dedup same key within a group (first wins).
    if (!bucket.has(binding.key)) bucket.set(binding.key, binding.cmd);
  }
  return [...buckets.entries()]
    .map(([group, keyCmds]) => ({
      group,
      entries: [...keyCmds.entries()]
        .map(([key, cmd]) => ({ key, cmd }))
        .sort((a, b) => a.key.localeCompare(b.key)),
    }))
    .sort((a, b) => a.group.localeCompare(b.group));
}

/** Flat display rows: group headers (`◇ group`) then `key → cmd` lines. */
export function whichKeyRows(groups: WhichKeyGroup[], maxPerGroup = 4): string[] {
  const rows: string[] = [];
  for (const group of groups) {
    rows.push(`◇ ${group.group}`);
    const shown = group.entries.slice(0, maxPerGroup);
    for (const entry of shown) {
      rows.push(`  ${entry.key} → ${entry.cmd}`);
    }
    const hidden = group.entries.length - shown.length;
    if (hidden > 0) rows.push(`  … +${hidden} more`);
  }
  return rows;
}
