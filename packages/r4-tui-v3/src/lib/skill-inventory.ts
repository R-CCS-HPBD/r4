/** Deduplicated union of skill inventories by name, sorted alphabetically.
 *
 * Collision rule (the /skills churn fix): the row that KNOWS activation state
 * (active !== undefined, i.e. the bridge registry snapshot) wins over a
 * stateless CLI scan row; the loser only donates optional fields the winner
 * lacks (tools count, description, mcp flag). Without this, an 8s CLI poll
 * overwrites bridge flags, so ●/○ markers + row metadata visibly flip between
 * clicks.
 */
import type { UiSkill } from "../types";

function preferSkill(a: UiSkill, b: UiSkill): UiSkill {
  const prefersA = a.active !== undefined;
  const prefersB = b.active !== undefined;
  const [win, lose] = prefersA && !prefersB ? [a, b] : prefersB && !prefersA ? [b, a] : [b, a];
  return {
    ...win,
    hasMcp: win.hasMcp || lose.hasMcp,
    description: win.description || lose.description,
    tools: win.tools ?? lose.tools,
    source: win.source && win.source !== "unknown" ? win.source : lose.source,
  };
}

export function mergeSkillInventories(...lists: UiSkill[][]): UiSkill[] {
  const merged = new Map<string, UiSkill>();
  for (const list of lists) {
    for (const skill of list ?? []) {
      const key = skill.name.trim();
      const existing = merged.get(key);
      merged.set(key, existing ? preferSkill(existing, skill) : skill);
    }
  }
  return [...merged.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * Rows the /skills overlay should show from the merged inventory.
 *
 * Bridge-known rows (active !== undefined) are authoritative AND the only
 * ones Enter can actually toggle — activate_skill raises "unknown skill"
 * for anything else. Local CLI-only rows (state unknown) historically got
 * shown alongside, and because CLI scan / loadWorkspace snapshot / list_skills
 * all disagree on membership, the visible list and its count churned on every
 * poll+click cycle. Rule: bridge rows when any exist; CLI-only list otherwise
 * (offline / bridge down).
 */
export function visibleSkillRows(merged: UiSkill[]): UiSkill[] {
  const bridgeRows = merged.filter((s) => s.active !== undefined);
  return bridgeRows.length > 0 ? bridgeRows : merged;
}
