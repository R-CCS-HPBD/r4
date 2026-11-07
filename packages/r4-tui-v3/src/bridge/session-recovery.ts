/**
 * Typed session errors from the bridge (c3-session, `error.data.code`) and
 * the recovery the console offers for each (c3-tui 2026-10-22).
 *
 *   session_conflict  another console saved this session first (data:
 *                     session_id, copy_id, stage). `stage` says whether the
 *                     turn ran (cycle-3 fix lane, 2026-10-22):
 *     before_turn     refused, nothing ran: the text goes back in the composer
 *                     (never over a draft typed meanwhile) and the card
 *                     reloads the SAME tab from the store, then send again.
 *     after_turn      the model and the tools already ran; only the save lost.
 *                     The transcript stays, nothing is re-queued and nothing
 *                     asks for the message again (its tools would run twice):
 *                     the card continues this tab in the unsaved copy that
 *                     holds the turn. A bridge without `stage` reads as
 *                     before_turn (the pre-fix behaviour).
 *     "" (empty)      any other save (a queue append, a toggle): that change
 *                     was not saved. A refused queue append returns its text
 *                     unless this console already sent its row as its own
 *                     turn or gave it back (stores/index.ts
 *                     failedQueuedAppend). An append with no answer (a
 *                     timeout) may have landed: its text is never handed
 *                     back, a card carries it. Wire shape: TUI_REFERENCE §12.
 *   session_corrupt   the stored row does not decode (data: session_id,
 *                     column). The card starts a new session, ALWAYS in a NEW
 *                     tab: the tab on screen is never replaced. It may hold
 *                     another session (a failed /sessions resume lands its
 *                     card on the tab it came from), or the corrupt session
 *                     itself, and then its transcript is the only intact copy
 *                     left. An in-place /new also cleared that copy and saved
 *                     "[]" over the raw row (cycle 3 verifier, round 2). The
 *                     raw row can be saved with /sessions export <id>.
 *
 * Only the error shape is relied on, never the server's prose (that is
 * written for every console version, the oldest included).
 */

import { BridgeRequestError } from "./jsonl-client";
import { actions, activeTab, workspace } from "../stores";
import type { ErrorCard } from "../types";

export interface SessionErrorInfo {
  code: "session_conflict" | "session_corrupt";
  sessionId: string;
  copyId?: string;
  column?: string;
  /** session_conflict: "after_turn" when the turn already ran. */
  stage?: string;
  message: string;
}

/** A conflict met by the end-of-turn save: the turn (model, tools) ran. */
export function turnRan(info: SessionErrorInfo): boolean {
  return info.code === "session_conflict" && info.stage === "after_turn";
}

function textField(data: Record<string, unknown>, key: string): string | undefined {
  const value = data[key];
  return typeof value === "string" && value ? value : undefined;
}

/** The typed session error carried by a rejected bridge request, if any. */
export function sessionErrorOf(err: unknown): SessionErrorInfo | null {
  if (!(err instanceof BridgeRequestError) || !err.data) return null;
  const code = err.data.code;
  const sessionId = textField(err.data, "session_id");
  if ((code !== "session_conflict" && code !== "session_corrupt") || !sessionId) return null;
  return {
    code,
    sessionId,
    copyId: textField(err.data, "copy_id"),
    column: textField(err.data, "column"),
    stage: textField(err.data, "stage"),
    message: err.message,
  };
}

/** How the card was set up: did the refused text go back in the composer. */
export interface RecoveryContext {
  restored?: boolean;
}

function conflictCard(info: SessionErrorInfo, short: string, ctx: RecoveryContext): ErrorCard {
  if (turnRan(info)) {
    return {
      what: `your turn ran, but session ${short} changed in another console — ${
        info.copyId ? "the turn and its reply are kept in an unsaved copy" : "the turn was not saved to it"
      }; do not send it again`,
      action: "needs_you",
      detail: info.message,
      verb: info.copyId ? "click: continue this tab in the unsaved copy" : "click: reload this tab",
      alt: `${info.copyId ? `the copy: /sessions resume ${info.copyId} · ` : ""}the other console's version: /sessions resume ${info.sessionId}`,
    };
  }
  return {
    what: `session ${short} changed in another console — your message was not run${
      ctx.restored ? " and is back in the composer" : ""
    }`,
    action: "needs_you",
    detail: info.message,
    verb: "click: reload this tab, then send again",
    alt: `or /sessions resume ${info.sessionId}${info.copyId ? ` · your unsaved turn: /sessions resume ${info.copyId}` : ""}`,
  };
}

/** The recovery card for a typed session error. */
export function sessionErrorCard(info: SessionErrorInfo, ctx: RecoveryContext = {}): ErrorCard {
  const short = info.sessionId.slice(0, 8);
  if (info.code === "session_conflict") return conflictCard(info, short, ctx);
  return {
    what: `session ${short} cannot be opened: its stored ${info.column ?? "row"} is corrupt (left untouched)`,
    action: "needs_you",
    detail: info.message,
    verb: "click: start a new session in a new tab (this tab stays as it is)",
    alt: `save the raw row first: /sessions export ${info.sessionId}`,
  };
}

/** Load `loadId` (default: `sessionId` itself) into the tab holding
 * `sessionId` (the active tab otherwise). */
export async function reloadSessionTab(sessionId: string, loadId: string = sessionId): Promise<boolean> {
  const tab = workspace.chatTabs.find((t) => t.sessionId === sessionId);
  if (tab && tab.id !== workspace.activeChatTabId) actions.tabActivate(tab.id);
  const { resumeSession } = await import("./session-bridge");
  return resumeSession(loadId);
}

/** A refused (before_turn) turn's text returns to an EMPTY composer; a draft
 * typed meanwhile stays, and a notice carries the refused text instead. */
function restoreRefusedText(info: SessionErrorInfo, refusedText: string | undefined): boolean {
  if (info.code !== "session_conflict" || turnRan(info) || !refusedText) return false;
  if ((activeTab().chatDraft ?? "").trim()) {
    actions.sendSystemNotice(`not run (the composer keeps your draft): ${refusedText}`);
    return false;
  }
  actions.setPendingChatDraft(refusedText);
  return true;
}

function conflictRetry(info: SessionErrorInfo): () => void {
  if (turnRan(info) && info.copyId) return () => void reloadSessionTab(info.sessionId, info.copyId);
  return () => void reloadSessionTab(info.sessionId);
}

/** Post the recovery for `info` on the active tab. A refused turn's
 * `refusedText` returns to the composer; a turn that ran keeps its rows. */
export function offerSessionRecovery(info: SessionErrorInfo, refusedText?: string): void {
  const restored = restoreRefusedText(info, refusedText);
  // A corrupt row's recovery never replaces the tab on screen, whatever it
  // holds (cycle 3 review tui-runtime#2, both the cross-tab and the in-tab case).
  const retry = info.code === "session_conflict" ? conflictRetry(info) : () => void actions.tabCreate();
  actions.sendErrorCard(sessionErrorCard(info, { restored }), retry);
}
