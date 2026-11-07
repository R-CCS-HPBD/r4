/**
 * `/save [n]` — keep a chat turn in project knowledge, verbatim (knowledge
 * plan 2026-10-24, K1.6). Its own module: slash.ts is gate-frozen; the
 * wave-intercept table claims every `/save …` line through its `save *`
 * row and this handler answers or falls through.
 *
 * The bridge verb `knowledge.save` reads the text from the session's own
 * messages by turn id (never re-typed by the model) and sends it to UMRS,
 * which scans it and tiers it (K-1): your own message lands established; an
 * assistant answer lands proposed unless you vouch (`--vouch`).
 *
 *   /save                 the last answer
 *   /save 2 | t2          turn 2's answer
 *   /save 2 mine          turn 2's message (yours)
 *   /save 2 --vouch       turn 2's answer, vouched (you establish it)
 *   /save <path.md>       unchanged: the companion writes the transcript
 *
 * `s` on a focused transcript message (the message cursor) saves that
 * message: `saveMessageAt` (an answer: the turn's final reply).
 *
 * Review of K1 part B (2026-10-24): a save while a turn runs is refused (it
 * waited behind the turn, then saved the NEW answer as "last", or reported
 * a timeout and ran anyway); the pane refreshes after a save.
 *
 * L3 (maintainer, 2026-10-25): a save publishes to the project's shared
 * graph, so the first one in a console run asks once: the same `s` or
 * `/save …` again within PUBLISH_CONFIRM_WINDOW_MS goes ahead, and later
 * saves in the run are one keystroke (the Ctrl+Q press-again pattern).
 */

import { getCompanionBridge } from "../bridge";
import { bridgeErrorHint } from "../bridge/jsonl-client";
import { rpc } from "../bridge/rpc";
import { isOfflineBackend } from "../lib/console-mode";
import { actions, activeTab } from "../stores";
import type { ChatMessage } from "../types";
import { isTurnSave, PUBLISH_CONFIRM_WINDOW_MS, publishConfirmNotice } from "../lib/save-args";

// The pure parts moved to ../lib/save-args (ADR-103 D-3, 2026-11-01).
export { isTurnSave, PUBLISH_CONFIRM_WINDOW_MS, publishConfirmNotice } from "../lib/save-args";

export type SaveCommandResult = { kind: "handled" } | null;

/** One notice for a failed save; the server's remedy on its own line. */
function failureNotice(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  const hint = bridgeErrorHint(err);
  return hint ? `save failed: ${message}\n  → ${hint}` : `save failed: ${message}`;
}

export const SAVE_WHILE_RUNNING =
  "save: wait for the answer to finish, then /save (or s on the message) — a save during a turn would wait for it";

async function requestSave(params: Record<string, unknown>): Promise<void> {
  try {
    const out = await rpc(getCompanionBridge(), "knowledge.save", params, 60_000);
    actions.sendSystemNotice(out.line || `saved ${out.turn_id} as ${out.tier}`);
    const { scheduleKnowledgeRefresh } = await import("../hooks/project-refresh");
    scheduleKnowledgeRefresh();
  } catch (err) {
    actions.sendSystemNotice(failureNotice(err));
  }
}

/** True (and says why) when a turn is running: the save would queue behind
 * it (`knowledge.save` is a mutating RPC) and "last" would be the new answer. */
function refusedWhileRunning(): boolean {
  if (!activeTab().chatStreaming) return false;
  actions.sendSystemNotice(SAVE_WHILE_RUNNING);
  return true;
}


const publishConfirm = { confirmed: false, pendingKey: "", pendingAt: 0 };

/** Tests: start a console run with (true) or without (false) the confirm done. */
export function setPublishConfirmedForTests(confirmed: boolean): void {
  publishConfirm.confirmed = confirmed;
  publishConfirm.pendingKey = "";
  publishConfirm.pendingAt = 0;
}


/** True once the person has confirmed publishing in this console run; the
 * first request (per *key*) only arms the confirm and says how to finish. */
function confirmedToPublish(key: string, again: string, now = Date.now()): boolean {
  if (publishConfirm.confirmed) return true;
  const repeated =
    publishConfirm.pendingKey === key && now - publishConfirm.pendingAt <= PUBLISH_CONFIRM_WINDOW_MS;
  if (repeated) {
    publishConfirm.confirmed = true;
    return true;
  }
  publishConfirm.pendingKey = key;
  publishConfirm.pendingAt = now;
  actions.sendSystemNotice(publishConfirmNotice(again));
  return false;
}

/** `/save …`: null hands a transcript path on to the companion's /save. */
export async function handleSaveCommand(args: string): Promise<SaveCommandResult> {
  if (!isTurnSave(args)) return null;
  if (isOfflineBackend()) {
    actions.sendSystemNotice("save: keeping a turn needs a live backend");
    return { kind: "handled" };
  }
  if (refusedWhileRunning()) return { kind: "handled" };
  const line = args.trim();
  if (!confirmedToPublish(`/save ${line}`, `Repeat /save ${line}`.trimEnd())) return { kind: "handled" };
  await requestSave({ args: line });
  return { kind: "handled" };
}

/** The companion turn a transcript row belongs to: its own `turnId`, else
 * the user message that opened its turn (replies carry none). A user
 * message with no `turnId` yet (streaming, failed) ends the walk: its
 * replies are no turn the companion has stored, never the previous one. */
export function turnOfMessage(messages: readonly ChatMessage[], index: number): string | undefined {
  for (let i = index; i >= 0; i -= 1) {
    const message = messages[i];
    if (message?.turnId) return message.turnId;
    if (message?.role === "user") return undefined;
  }
  return undefined;
}

/** True when another assistant reply of the same turn follows *index*: the
 * companion saves a turn's final answer, so `s` there would save other text. */
function laterReplyInTurn(messages: readonly ChatMessage[], index: number): boolean {
  for (let i = index + 1; i < messages.length; i += 1) {
    const role = messages[i]?.role;
    if (role === "user") return false;
    if (role === "assistant") return true;
  }
  return false;
}

export const SAVE_FINAL_ANSWER_ONLY =
  "save: s keeps a turn's final answer — move to the last reply of this turn";

/** Message action `s`: save the focused message (yours → established; an
 * answer → proposed). False when the row is no chat turn. */
export function saveMessageAt(messages: readonly ChatMessage[], index: number): boolean {
  const message = messages[index];
  const turn = turnOfMessage(messages, index);
  if (!message || !turn || (message.role !== "user" && message.role !== "assistant")) return false;
  if (refusedWhileRunning()) return true;
  if (message.role === "assistant" && laterReplyInTurn(messages, index)) {
    actions.sendSystemNotice(SAVE_FINAL_ANSWER_ONLY);
    return true;
  }
  saveTurnPart(turn, message.role === "user" ? "prompt" : "answer");
  return true;
}

/** `s` on a chat turn: save that side of it once publishing is confirmed. */
function saveTurnPart(turn: string, part: "prompt" | "answer"): void {
  if (confirmedToPublish(`s ${turn} ${part}`, "Press s again")) void requestSave({ turn, part });
}
