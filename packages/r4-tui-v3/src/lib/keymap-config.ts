/**
 * D1: user keymap overrides.
 * Config file: ${R4_CONFIG_HOME or ~/.config/r4}/v3-keymap.json — a flat
 * `{ "cmd.id": "key+chord" }` map. Overrides are ADDITIVE (defaults are
 * never removed) and silently ignored when invalid so a broken file can
 * never wedge the console.
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

export type KeymapOverrides = Record<string, string>;

export function keymapConfigPath(home: string = homedir()): string {
  const base = process.env.R4_CONFIG_HOME ?? join(home, ".config", "r4");
  return join(base, "v3-keymap.json");
}

/** Read + validate the overrides file; returns {} on any problem. */
export function loadKeymapOverrides(path: string = keymapConfigPath()): KeymapOverrides {
  try {
    if (!existsSync(path)) return {};
    const parsed = JSON.parse(readFileSync(path, "utf8")) as unknown;
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    const out: KeymapOverrides = {};
    for (const [cmd, key] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof cmd !== "string" || typeof key !== "string") continue;
      const trimmed = key.trim();
      if (trimmed) out[cmd] = trimmed;
    }
    return out;
  } catch {
    return {};
  }
}

export interface KeyBindingLike {
  key: string;
  cmd: string;
  [extra: string]: unknown;
}

/**
 * Merge overrides into the base bindings (additive, deduped).
 * Unknown command ids are dropped so the layer never registers dead cmds.
 */
export function mergeKeymapOverrides<T extends KeyBindingLike>(
  base: T[],
  overrides: KeymapOverrides,
  knownCmds: Set<string>,
): T[] {
  const seen = new Set(base.map((b) => `${b.key}|${b.cmd}`));
  const extra: KeyBindingLike[] = [];
  for (const [cmd, key] of Object.entries(overrides)) {
    if (!knownCmds.has(cmd)) continue;
    if (seen.has(`${key}|${cmd}`)) continue;
    seen.add(`${key}|${cmd}`);
    extra.push({ key, cmd });
  }
  return [...base, ...(extra as T[])];
}
