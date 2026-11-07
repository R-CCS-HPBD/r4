/**
 * Wave F — catalog row verbs.
 */

import { getCompanionBridge } from "./jsonl-client";
import { actions, workspace } from "../stores";
import { applyBridgeStatePatch, mapBridgeStateToWorkspace } from "./map-state";
import type { CatalogRow } from "../lib/catalog-rows";
import { applyOnTab } from "../tabs/registry";

/** Skill row click = toggle active; other kinds route through their owners.
 * The next state comes from the skill's bridge-reported `active` flag — a
 * remembered last click re-activated a skill already on (defaults, /skills,
 * a resumed session) instead of turning it off (TUI audit 2026-10-17). */
export async function toggleSkill(row: CatalogRow): Promise<void> {
  if (row.kind === "skill") {
    await toggleSkillRow(row.id);
    return;
  }
  if (row.kind === "code_change") {
    actions.openPane("codeview");
    return;
  }
  if (row.kind === "session") {
    const { resumeSession } = await import("./session-bridge");
    await resumeSession(row.id);
    return;
  }
  // mcp_server — display row; on/off semantics need a server toggle verb.
  actions.sendSystemNotice(`${row.id}: MCP on/off arrives with server toggle verbs (registration today = add/remove)`);
}

/** activate_skill on the active tab's bridge; the snapshot and the notice
 * land on that tab even when the answer arrives after a tab switch (it
 * patched the then-active tab's session, model and mode — w3-tui). */
async function toggleSkillRow(name: string): Promise<void> {
  const tabId = workspace.activeChatTabId;
  const nextActive = !workspace.skills.find((s) => s.name === name)?.active;
  try {
    const state = await getCompanionBridge().request("activate_skill", { name, active: nextActive }, 30_000);
    applyOnTab(tabId, () => {
      applyBridgeStatePatch(mapBridgeStateToWorkspace(state, undefined));
      actions.sendSystemNotice(`skill ${nextActive ? "on" : "off"}: ${name}`);
    });
  } catch (err) {
    applyOnTab(tabId, () =>
      actions.sendErrorCard({
        what: `skill ${name} toggle failed`,
        action: "dropped",
        detail: err instanceof Error ? err.message : String(err),
      }),
    );
  }
}
