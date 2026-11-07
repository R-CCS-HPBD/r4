/**
 * ChatTabStore — plan H-7, stage 1: one per-tab chat-scoped state store.
 *
 * Today the module-singleton `workspace` always mirrors the ACTIVE tab's
 * chat-scoped fields; background tabs rest as plain snapshots in
 * `tabs/registry.ts`. This module introduces the per-tab store the registry
 * holds for every tab and the mirror the snapshot swap keeps in sync —
 * behavior is unchanged (readers still see the flat singleton). Stages 2–3
 * will point readers and bridge events at the owning tab's store and stage 4
 * deletes the swap; this file is the shared foundation.
 *
 * The field list lives here (registry re-exports it for existing importers).
 * Scoped fields are session- or transcript-shaped; global inventory
 * (endpoints, jobs, skills, connection, work-pane metadata…) stays global.
 */

import { createStore, type SetStoreFunction, type Store } from "solid-js/store";
// Type-only (erased at runtime): ChatTabState picks the scoped subset of the
// singleton's state — registry.ts imports this module's runtime parts, and
// stores/index.ts imports registry's, so a *runtime* import of the singleton
// here would cycle.
import type { WorkspaceState } from "../stores/index";

/** Chat-scoped workspace fields saved/restored per tab on activation swap.
 *  Anything session- or transcript-shaped belongs here; global inventory
 *  (endpoints, jobs, skills, connection…) stays global. Project selection
 *  is chat-scoped too (since the 2026-09-30 audit) and the per-tab bridge
 *  process carries it in R4_PROJECT_ID (2026-10-01) — switching in one
 *  tab never moves another tab's project. */
export const CHAT_SCOPED_FIELDS = [
  "chatMessages",
  "isTyping",
  "chatStreaming",
  "chatTurnStatus",
  "pendingChatQueue",
  "chatDraft",
  "cost",
  "thinking",
  "toolApproval",
  "toolApprovalQueue",
  "askUser",
  "planAwaitingConfirmation",
  "planPendingRequest",
  "turnToolActivity",
  "sessionTodos",
  "turnArtifacts",
  "sessionId",
  "currentModel",
  "interactionMode",
  "permissionMode",
  "trustSource",
  "knowledgeAcquisition",
  "sessionVenue",
  "venueRuntime",
  // ADR-099 D-2: each tab's session has its own web-access choice.
  "webAccess",
  // ADR-101: and its own local tool sandbox (notice row, sbx chip, /sandbox).
  "sandbox",
  // ADR-108 D-2: and the worktree it works in (/worktree, wt chip).
  "worktree",
  // Session-shaped identity/project context (2026-09-30 audit): each tab's
  // bridge init_state writes these; without the swap, tab B inherits tab A's
  // project badge, approval rules scope, AND the buildProjectChatContext
  // prefix of A's project (wrong-project turn context).
  "identityProjectId",
  "identityProfile",
  "identityCredential", // pairs with identityProfile per tab (2026-10-07)
  "identitySource",
  "authzFlags",
  "project",
  // 2026-10-01 (15): work-pane scope toggle repaints scoped row lists.
  "workScopeAll",
  "messageCursor",
  "lastTurnTtftMs",
  "lastSummaryKey",
  "noticeDetails",
  "noticeExpanded",
  "lastErrorCardId",
  "chatFallbackActive",
  "chatFallbackDetail",
  "mcpStatus",
  "editResendFromIndex",
  "backgroundTurnTaskId",
  // Session-shaped feeds each tab's own bridge answers (TUI audit r2
  // #27/#37/#38, 2026-10-17): the `wf:on` chip, the Plan graph, /changes,
  // the tools chip and the context panel's science loop / knowledge base
  // showed the previous tab's values until that tab's next refresh.
  "workflowArmed",
  "sessionPlan",
  "sessionChanges",
  "toolSelection",
  "scienceLoop",
  "knowledgeBase",
] as const;

/** Fields that only mean something while the tab's bridge runs a turn. A
 *  closed tab's bridge is gone, so its reopen snapshot drops them (defaults
 *  apply): a reopened tab must never come back "streaming" with nothing to
 *  finish it, or with an approval card the dead process asked for (TUI
 *  audit r2 #35). */
export const TURN_TRANSIENT_FIELDS = [
  "isTyping",
  "chatStreaming",
  "chatTurnStatus",
  "toolApproval",
  "toolApprovalQueue",
  "askUser",
  "planAwaitingConfirmation",
  "planPendingRequest",
  "backgroundTurnTaskId",
  "turnToolActivity",
  "pendingChatQueue",
  "thinking",
] as const;

export type ChatScopedField = (typeof CHAT_SCOPED_FIELDS)[number];

/** The chat-scoped slice of the singleton workspace state, held per tab. */
export type ChatTabState = Pick<WorkspaceState, ChatScopedField>;

/** One tab's chat-scoped store (state + setter, solid store semantics). */
export interface ChatTabStore {
  readonly state: Store<ChatTabState>;
  readonly set: SetStoreFunction<ChatTabState>;
}

export function createChatTabStore(initial: ChatTabState): ChatTabStore {
  const [state, set] = createStore<ChatTabState>(initial);
  return { state, set };
}

/** Mirror a scoped-values map into a tab store (same keys, same values).
 *  Used by the registry's snapshot swap to keep each tab's store equal to
 *  what the flat singleton holds while that tab is active. */
export function applyScopedValues(
  store: ChatTabStore | null | undefined,
  values: Record<string, unknown>,
): void {
  if (!store || !values) return;
  for (const field of CHAT_SCOPED_FIELDS) {
    if (field in values) store.set(field as never, values[field] as never);
  }
}
