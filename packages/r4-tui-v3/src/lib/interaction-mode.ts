/**
 * The session mode (ADR-104 D-1): chat → plan → autopilot. Shift+Tab rotates
 * it and `/mode <m>` sets it, both through the bridge verb
 * `set_interaction_mode` ({mode} → {mode, output, state}): the companion
 * derives what may run from the mode, this console shows the badge.
 *
 * The answer lands on the tab that asked (a switch answered after a tab
 * switch badged the other tab, w3-tui 2026-10-21); a failure is an error
 * notice, never a success line.
 */

import { workspace, actions, activeTab } from "../stores";
import { getCompanionBridge } from "../bridge";
import { rpc } from "../bridge/rpc";
import { applyOnTab } from "../tabs/registry";
import { isOfflineBackend } from "./console-mode";

const MODE_ORDER = ["chat", "plan", "autopilot"] as const;

export type InteractionModeName = (typeof MODE_ORDER)[number];

/** Next mode after `current` in the rotation; unknown modes reset to chat. */
export function nextInteractionMode(current: string): InteractionModeName {
  const idx = MODE_ORDER.indexOf(current.toLowerCase() as InteractionModeName);
  return idx === -1 ? MODE_ORDER[0] : MODE_ORDER[(idx + 1) % MODE_ORDER.length];
}

/** One of the three mode words (what `/mode <m>` switches to). */
export function isModeName(value: string): value is InteractionModeName {
  return (MODE_ORDER as readonly string[]).includes(value);
}

/** The server's notice lines (strings or {text}); empty entries dropped. */
export function modeOutputLines(output: unknown): string[] {
  if (!Array.isArray(output)) return [];
  return output
    .map((item) => (typeof item === "string" ? item : String((item as { text?: unknown })?.text ?? "")))
    .filter((line) => line.trim().length > 0);
}

export type ModeSwitchOutcome = { ok: true; mode: string } | { ok: false; error: string };

/**
 * Ask the active tab's companion to switch to `mode`, then apply the answer
 * on that tab: the snapshot (badge, plan state) and the server's lines
 * (fallback `mode → <m>`). On failure: an error notice on that tab.
 */
export async function switchInteractionMode(mode: string): Promise<ModeSwitchOutcome> {
  const tabId = workspace.activeChatTabId;
  try {
    const result = await rpc(getCompanionBridge(), "set_interaction_mode", { mode }, 30_000);
    const { applyBridgeStatePatch, mapBridgeStateToWorkspace } = await import("../bridge/map-state");
    const applied = typeof result?.mode === "string" && result.mode ? result.mode : mode;
    const lines = modeOutputLines(result?.output);
    applyOnTab(tabId, () => {
      if (result?.state) applyBridgeStatePatch(mapBridgeStateToWorkspace(result.state, undefined));
      actions.sendSystemNotice(lines.length > 0 ? lines.join("\n") : `mode → ${applied}`);
    });
    return { ok: true, mode: applied };
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    applyOnTab(tabId, () => actions.sendSystemNotice(`mode unchanged — switch to ${mode} failed: ${error}`));
    return { ok: false, error };
  }
}

/** `/mode` without a mode word (bare: the companion describes the mode;
 * other args: its own verbs), sent intact, the snapshot applied on the tab
 * that asked. */
export async function forwardModeLine(line: string): Promise<{ kind: "handled" } | { kind: "error"; message: string }> {
  const tabId = workspace.activeChatTabId;
  try {
    const result = await rpc(getCompanionBridge(), "dispatch_slash_command", { command: line }, 30_000);
    const { applyBridgeStatePatch, mapBridgeStateToWorkspace } = await import("../bridge/map-state");
    const lines = modeOutputLines(result?.output);
    applyOnTab(tabId, () => {
      if (result?.state) applyBridgeStatePatch(mapBridgeStateToWorkspace(result.state, undefined));
      if (lines.length > 0) actions.sendSystemNotice(lines.join("\n"));
    });
    return { kind: "handled" };
  } catch (err) {
    return { kind: "error", message: `mode failed: ${err instanceof Error ? err.message : String(err)}` };
  }
}

/** Shift+Tab: rotate to the next mode via the bridge. */
export async function rotateInteractionMode(): Promise<ModeSwitchOutcome> {
  if (isOfflineBackend()) {
    actions.sendSystemNotice("mode rotation requires a live backend (demo/offline)");
    return { ok: false, error: "offline" };
  }
  return switchInteractionMode(nextInteractionMode(activeTab().interactionMode || "chat"));
}
