/**
 * Wave C — plan confirmation bridge (plan mode picker).
 * Mirrors approval-bridge: stores hold state, bridge module owns RPC actions.
 *
 * Server verbs (r4/companion/session.py confirm_plan):
 *   execute_auto  — accept: the session switches to autopilot, the plan runs
 *   execute_ask   — accept: the session switches to chat (each tool asks), the plan runs
 *   keep_planning — discard the pending plan, stay in plan mode
 */

import { ulid } from "ulid";
import { getCompanionBridge } from "./jsonl-client";
import { rpc } from "./rpc";
import { actions, setScopedField, workspace, activeTab } from "../stores";
import type { ChatMessage } from "../types";
import { applyBridgeStatePatch, mapBridgeStateToWorkspace } from "./map-state";
import { CompanionChatBackend, createCompanionChatBackend } from "../adapters/companion-chat";
import type { ChatStreamChunk } from "../adapters/types";
import { applyOnTab, waitForTabTurn } from "../tabs/registry";
import { clearTurnPinSlices, flushTurnSummary, shiftTurnPinSlice } from "./companion-runtime-bridge";
import { joinQueued } from "../lib/tui-parity";

export type PlanAction = "execute_auto" | "execute_ask" | "keep_planning";

export async function respondPlan(action: PlanAction): Promise<boolean> {
  if (action === "keep_planning") return keepPlanning();
  return executePlan(action);
}

async function keepPlanning(): Promise<boolean> {
  // The answer lands on the tab whose plan card was answered, even after a
  // tab switch (w3-tui, 2026-10-21).
  const tabId = workspace.activeChatTabId;
  const bridge = getCompanionBridge();
  try {
    const response = await rpc(bridge, "confirm_plan", { action: "keep_planning" }, 10 * 60_000);
    applyOnTab(tabId, () => applyBridgeStatePatch(mapBridgeStateToWorkspace(response?.state, undefined)));
    return true;
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    applyOnTab(tabId, () =>
      actions.sendErrorCard({
        what: `plan confirmation failed`,
        action: "dropped",
        detail,
      }),
    );
    // Be honest at the keyboard: if the server no longer awaits a plan,
    // hide the stale card instead of trapping keys.
    return false;
  }
}

/** The chat backend that carries the plan turn (the live one, or the shared companion backend). */
function planBackend(): CompanionChatBackend {
  const live = workspace.chatBackend;
  return live instanceof CompanionChatBackend ? live : createCompanionChatBackend();
}

/** Live assistant segment(s) of one plan execution (the chat stream's round rules). */
interface PlanReply {
  tabId: string;
  /** Open (streaming) segment, null between rounds. */
  segmentId: string | null;
  lastId: string | null;
  model: string | undefined;
  fallback: boolean;
}

/**
 * Execute the accepted plan (TUI audit r2 #11/#62): `confirm_plan` runs a
 * whole tool-using agent turn whose reply streams as response_* events.
 * The reply goes into the transcript like a chat turn's — streamed, split
 * at round breaks with the tool lines pinned under each round — and the
 * turn is a real in-flight turn meanwhile (streaming state, Esc cancels,
 * typed prompts queue and are sent once it ends).
 */
async function executePlan(action: "execute_auto" | "execute_ask"): Promise<boolean> {
  const reply: PlanReply = {
    tabId: workspace.activeChatTabId,
    segmentId: null,
    lastId: null,
    model: undefined,
    fallback: false,
  };
  beginPlanTurn(reply.tabId);
  let open = true;
  try {
    for await (const chunk of planBackend().planStream(action)) {
      open = await waitForTabTurn(reply.tabId);
      if (!open) break;
      applyPlanChunk(reply, chunk);
    }
  } finally {
    // A closed tab's plan turn settles nowhere — never into the active tab
    // (the chat stream's rule, TUI audit r2 #31).
    if (open) open = await waitForTabTurn(reply.tabId);
    if (open) endPlanTurn(reply);
  }
  return open && !reply.fallback;
}

function beginPlanTurn(tabId: string): void {
  // Hide the card now: a second a/m while the plan runs would send a
  // second confirm_plan behind the first.
  actions.setInteractionModeState({ planAwaitingConfirmation: false });
  setScopedField("isTyping", true);
  setScopedField("chatStreaming", true);
  actions.setTabMeta(tabId, { busy: true });
  actions.setChatTurnStatus("executing the plan…");

}

function applyPlanChunk(reply: PlanReply, chunk: ChatStreamChunk): void {
  if (chunk.model) reply.model = chunk.model;
  if (chunk.fallback) reply.fallback = true;
  if (chunk.roundBreak) {
    closePlanSegment(reply);
    return;
  }
  if (chunk.delta) appendPlanDelta(reply, chunk.delta);
}

function appendPlanDelta(reply: PlanReply, delta: string): void {
  const open = reply.segmentId;
  if (open) {
    setScopedField("chatMessages", (m: ChatMessage) => m.id === open, "content", (c: string) => c + delta);
    return;
  }
  const id = ulid();
  reply.segmentId = id;
  reply.lastId = id;
  setScopedField("chatMessages", (list: ChatMessage[]) => [
    ...list,
    {
      id,
      role: "assistant" as const,
      content: delta,
      timestamp: new Date().toISOString(),
      model: reply.model,
      streaming: true,
      fallback: reply.fallback,
    },
  ]);
}

/** Close the open segment with the round's tool lines pinned under it. */
function closePlanSegment(reply: PlanReply): void {
  const id = reply.segmentId;
  if (!id) return;
  reply.segmentId = null;
  setScopedField("chatMessages", (m: ChatMessage) => m.id === id, "streaming", false);
  pinToolActivity(id);
}

/** Pin exactly the oldest flushed-but-unpinned slice under this segment —
 *  the same FIFO pairing as the chat stream's roundBreak pin (#33). */
function pinToolActivity(id: string): void {
  const field = activeTab().turnToolActivity;
  const take = shiftTurnPinSlice() || field.length;
  const calls = field.slice(0, take);
  if (calls.length === 0) return;
  setScopedField("chatMessages", (m: ChatMessage) => m.id === id, "toolCalls", calls);
  actions.setNoticeDetails(
    `group:${id}`,
    calls.map((line) => line.text),
  );
  setScopedField("turnToolActivity", field.slice(take));
}

/** Turn end: flush the tail slice, then pin the whole unpinned remainder. */
function pinToolActivityAll(id: string): void {
  flushTurnSummary();
  const calls = activeTab().turnToolActivity;
  if (calls.length === 0) return;
  clearTurnPinSlices();
  setScopedField("chatMessages", (m: ChatMessage) => m.id === id, "toolCalls", calls);
  actions.setNoticeDetails(
    `group:${id}`,
    calls.map((line) => line.text),
  );
  setScopedField("turnToolActivity", []);
}

function endPlanTurn(reply: PlanReply): void {
  closePlanSegment(reply);
  // Tools of a trailing textless round still pin under the last text.
  if (reply.lastId) pinToolActivityAll(reply.lastId);
  settlePlanTurnFlags(reply);
  setScopedField("isTyping", false);
  setScopedField("chatStreaming", false);
  actions.setChatTurnStatus(null);
  actions.setTabMeta(reply.tabId, { busy: false });
  // Prompts typed while the plan ran were queued behind it — send them now.
  const queued = actions.drainQueued();
  if (queued.length > 0) actions.sendChatMessage(joinQueued(queued));
}

/** The chat turn's finish bookkeeping: fallback flag and a Ctrl+B task. */
function settlePlanTurnFlags(reply: PlanReply): void {
  const id = reply.lastId;
  const content = id ? (activeTab().chatMessages.find((m) => m.id === id)?.content ?? "") : "";
  if (reply.fallback && id) {
    setScopedField("chatMessages", (m: ChatMessage) => m.id === id, "fallback", true);
    actions.setChatFallback(true, content);
  } else if (!reply.fallback) {
    actions.setChatFallback(false);
  }
  actions.settleBackgroundTurn(content);
}

/** Map the plan card's single-letter verb to the server's confirm action. */
export function planVerbToAction(key: string): PlanAction | null {
  switch (key) {
    case "a":
      return "execute_auto";
    case "m":
      return "execute_ask";
    case "n":
      return "keep_planning";
    default:
      return null;
  }
}
