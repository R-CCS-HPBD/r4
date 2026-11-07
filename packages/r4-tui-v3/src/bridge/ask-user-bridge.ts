/**
 * Bridge ask_user requests → TUI overlay + respondAskUser.
 * Companion blocks until the UI answers; without this overlay the chat
 * stays stuck on "Calling tool: ask_user".
 */

import { getCompanionBridge } from "./jsonl-client";
import { actions, workspace, activeTab } from "../stores";

export type AskUserRequest = {
  id: string | number;
  question: string;
  options?: string[];
};

let wired = false;

/** Handle one ask_user request (wired per bridge; chat-tabs dispatch route). */
export function handleAskUserRequest(raw: unknown): void {
  const req = raw as AskUserRequest;
  if (req?.id === undefined || req?.id === null) return;
  const question = typeof req.question === "string" ? req.question : "";
  const options = Array.isArray(req.options)
    ? req.options.map((o) => String(o)).filter((o) => o.trim().length > 0)
    : [];
  actions.openAskUser({ id: req.id, question, options });
  actions.setChatTurnStatus("R4 is asking you a question…");
}

export function wireAskUserBridge(): void {
  if (wired) return;
  wired = true;
  const bridge = getCompanionBridge();
  bridge.on("ask_user", handleAskUserRequest);
}

/** True when the open question came from a remote session's worker. */
export function isRemoteAskUser(id: string | number | undefined | null): boolean {
  return id !== undefined && id !== null && String(id).startsWith("remote:");
}

/** The remote question whose card was closed because the question closed
 * elsewhere (the overlay hands its typed text to the composer). */
let closedElsewhere: string | null = null;

/** Close the open remote question's card: its question was answered in
 * another client, expired, or the session stopped (RS-1 round 2, finding 5). */
export function closeRemoteAskUserElsewhere(): void {
  const pending = activeTab().askUser;
  if (!pending || !isRemoteAskUser(pending.id)) return;
  closedElsewhere = String(pending.id);
  actions.closeAskUser();
  actions.setChatTurnStatus(null);
}

/** True (once) when the card showing `id` was closed by
 * closeRemoteAskUserElsewhere. */
export function takeClosedElsewhere(id: string | number | undefined): boolean {
  if (id === undefined || closedElsewhere !== String(id)) return false;
  closedElsewhere = null;
  return true;
}

/** An answer typed for a remote question that closed first goes back to an
 * EMPTY composer; a composer holding a draft keeps it and a notice carries
 * the answer instead (the queue rollback's convention; RS-1 round 3, N3).
 * `lead` says why the answer came back. `toComposer` false (the user left
 * the question's tab; RS-1 round 4, R2): the pending draft is global and the
 * ACTIVE composer takes it, so only the notice carries the answer. */
export function returnRemoteAnswer(answer: string, lead: string, toComposer = true): void {
  if (!toComposer || (activeTab().chatDraft ?? "").trim()) {
    actions.sendSystemNotice(`${lead} — the composer keeps your draft; your answer was: ${answer}`);
    return;
  }
  actions.setPendingChatDraft(answer);
  actions.sendSystemNotice(`${lead} — your answer is back in the composer`);
}

/**
 * A remote worker's question (ADR-099 D-8): the answer goes to the durable
 * question through `session.venue_answer`, never the stdin ask_user line. An
 * empty answer or Esc leaves it pending server-side.
 */
function respondRemoteAskUser(id: string | number, answer: string | null): void {
  const questionId = String(id).slice("remote:".length);
  const text = (answer ?? "").trim();
  void import("./venue-bridge").then((m) => {
    if (text) void m.answerRemoteQuestion(questionId, text);
    else m.setAsideRemoteQuestion(questionId);
  });
  actions.closeAskUser();
  actions.setChatTurnStatus(text ? "answer sent to the remote session…" : null);
}

export function respondAskUser(answer: string | null): void {
  const pending = activeTab().askUser;
  if (!pending) return;
  if (isRemoteAskUser(pending.id)) {
    respondRemoteAskUser(pending.id, answer);
    return;
  }
  const bridge = getCompanionBridge();
  bridge.respondAskUser(pending.id, answer);
  actions.closeAskUser();
  if (activeTab().chatStreaming) {
    actions.setChatTurnStatus(answer == null || answer === "" ? "ask_user cancelled" : "running…");
  } else {
    actions.setChatTurnStatus(null);
  }
}

/** Cancel with empty/null answer so the Companion turn can unwind. */
export function cancelAskUser(): void {
  respondAskUser(null);
}
