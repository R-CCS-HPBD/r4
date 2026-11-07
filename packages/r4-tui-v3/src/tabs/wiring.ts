/**
 * Chat tabs wiring — the live event dispatch table + per-bridge attacher.
 *
 * Installed once from app mount (wireChatTabsOnce). Everything here is the
 * single place that knows which bridge handler answers which channel, so the
 * store and the registry stay free of handler imports (no import cycles).
 *
 * Channel taxonomy (registry.ts GLOBAL_CHANNELS):
 * - project_job_event / team_event / stderr / protocol_error / unmatched /
 *   source_stale → dispatched immediately regardless of the owning tab;
 * - exit, turn_settled → tab-lifetime bookkeeping (registry);
 * - everything else (event, turn_stopped, control_event, approval, ask_user,
 *   init_state) is chat-scoped: live when the owning tab is active, buffered
 *   and replayed on activation otherwise.
 */

import type { CompanionBridgeClient } from "../bridge/jsonl-client";
import {
  handleInitState,
  handleTeamEvent,
  handleTurnStopped,
  onBridgeEvent,
  onControlEvent,
  onProjectJobForAgents,
  retargetResourcePolling,
} from "../bridge/companion-runtime-bridge";
import { handleApprovalRequest } from "../bridge/approval-bridge";
import { handleAskUserRequest } from "../bridge/ask-user-bridge";
import { bindProjectJobEvents } from "../commands/project-job-events";
import { handleSearchServerEvent } from "../commands/search-server-command";
import { handleWorktreeEvent } from "../commands/worktree-command";
import { actions, workspace } from "../stores";
import {
  bridgeForTab,
  registerBridgeAttacher,
  registerTabDispatchers,
  routeTabEvent,
} from "./registry";

export const TAB_WATCHED_CHANNELS = [
  "event",
  "turn_stopped",
  "control_event",
  "approval",
  "ask_user",
  "init_state",
  "project_job_event",
  "team_event",
  "search_server_event",
  // ADR-108 W1c: the tab's worktree changed (the user, the agent, or auto mode).
  "worktree_event",
  "session_title",
  "source_stale",
  "protocol_error",
  "unmatched",
  "stderr",
  "exit",
  // Turn ended on the bridge (emitted by the chat adapter): settles a
  // background tab's busy glyph and Ctrl+B task without its transcript.
  "turn_settled",
] as const;

function dispatchChatEvent(channel: string, payload: unknown, tabId: string): void {
  switch (channel) {
    case "event":
      onBridgeEvent(payload as Record<string, unknown>);
      return;
    case "turn_stopped":
      handleTurnStopped();
      return;
    case "control_event":
      onControlEvent(payload);
      return;
    case "approval":
      handleApprovalRequest(payload);
      return;
    case "ask_user":
      handleAskUserRequest(payload);
      return;
    case "worktree_event":
      handleWorktreeEvent(payload);
      return;
    case "init_state":
      handleInitState(payload);
      // The poller feeds chat-scoped panels — it must run on the ACTIVE tab's
      // bridge (a background init_state may not move it).
      if (tabId === workspace.activeChatTabId) {
        retargetResourcePolling(bridgeForTab(tabId));
      }
      return;
    default:
      return;
  }
}

function dispatchGlobalEvent(channel: string, payload: unknown): void {
  switch (channel) {
    case "project_job_event":
      // The job notices come from bindProjectJobEvents on the same bridge
      // (attachTabBridge): calling the handler here too posted every
      // "started" line twice (TUI audit r2 #23).
      onProjectJobForAgents(payload);
      return;
    case "team_event":
      handleTeamEvent(payload);
      return;
    case "search_server_event":
      // ADR-102: a local search server start's steps and its end.
      handleSearchServerEvent(payload);
      return;
    case "session_title": {
      // Auto session title from the companion bridge (first turn of a
      // default-named session): patch the owning tab's strip cell wherever
      // it sits — inventory-shaped, never buffered with the transcript.
      const row = payload as { session_id?: unknown; name?: unknown } | null;
      if (row && typeof row.session_id === "string" && typeof row.name === "string") {
        actions.tabSetTitleForSession(row.session_id, row.name);
      }
      return;
    }
    case "source_stale":
      actions.sendSystemNotice(`[stale] ${String(payload ?? "")}`);
      return;
    default:
      return;
  }
}

/** Attach the full channel set to one tab's bridge. */
function attachTabBridge(bridge: CompanionBridgeClient, tabId: string): void {
  for (const channel of TAB_WATCHED_CHANNELS) {
    bridge.on(channel, (payload: unknown) => routeTabEvent(tabId, channel, payload));
  }
  // Project-job tracking keeps its own per-bridge bookkeeping (dedupe sets).
  bindProjectJobEvents(bridge, { withExitHandler: false });
}

/**
 * Install the tab dispatch table + per-bridge attacher. Not guarded by a
 * one-shot flag on purpose: test files reset the wiring table via
 * resetWorkspace between mounts, so every App (re)mount must re-register.
 * Registration itself is an idempotent assignment — cheap to repeat.
 */
export function wireChatTabsOnce(): void {
  registerTabDispatchers({ chat: dispatchChatEvent, global: dispatchGlobalEvent });
  registerBridgeAttacher(attachTabBridge);
}
