/**
 * Bridge tool-approval requests → the approval card → the answer line.
 *
 * ADR-104 D-3: the companion answers, the console renders. Every ask shows a
 * card (queued behind an open one); the person's answer goes back as
 * `{"approval_response": {id, decision}}` with decision once | always |
 * reject, and the companion does all remembering (the session's "always").
 * This module never answers an ask by itself and never writes a rule.
 */

import { getCompanionBridge, type ApprovalWireDecision } from "./jsonl-client";
import { actions, workspace, activeTab, type ToolApprovalCard, type ToolApprovalInput } from "../stores";
import { agentActions, agentRuntime } from "../stores/agent-runtime";
import { eventTargetTab, runtimeOrNull } from "../tabs/registry";
import { cardOptions, decisionForKey, type ApprovalOption } from "../lib/approval-options";

export type ToolApprovalRequest = {
  id: string | number;
  tool: string;
  description?: string;
  session_approval_allowed?: boolean;
  /** ADR-104 D-3: the answers this ask may get (absent from older bridges). */
  options?: unknown[];
  /** Server-computed preview: unified diff + path (file_write/file_edit). */
  diff?: string;
  path?: string;
  /** ADR-092: the plan agent or team role asking ("" = the lead's own call). */
  agent?: string;
  /** "risky" | "never_auto" (MD-5: asked per call, in every mode). */
  risk_tier?: string;
  /** A content fence asked about THIS call's arguments (taint, credential,
   * G14 ask rule, protected path); `fence_reason` names it (fix 2026-10-30). */
  fenced?: boolean;
  fence_reason?: string;
};

/** MD-5: a never-auto approval is answered by a human, per call. */
export function isNeverAuto(req: ToolApprovalRequest): boolean {
  return req?.risk_tier === "never_auto";
}

/** A fenced ask is answered per call, whatever the tool (fix 2026-10-30). */
export function isFenced(req: ToolApprovalRequest): boolean {
  return req?.fenced === true;
}

/** The reason a fenced card shows (undefined for an ordinary ask). */
function fenceReasonOf(req: ToolApprovalRequest): string | undefined {
  if (!isFenced(req)) return undefined;
  const reason = typeof req.fence_reason === "string" ? req.fence_reason.trim() : "";
  return reason || "a fence on this call's arguments";
}

/**
 * Research-approvals mirror of each pending card (TUI audit r2 #13): card
 * key (tab + request id — request ids are per bridge) → the mirrored gate id.
 */
const mirroredGates = new Map<string, string>();

function mirrorKey(requestId: string | number, tabId?: string): string {
  return `${tabId ?? eventTargetTab() ?? workspace.activeChatTabId}:${String(requestId)}`;
}

/** Settle the mirrored gate of an answered card (no-op when none). */
function resolveMirroredGate(requestId: string | number, decision: ApprovalOption): void {
  const key = mirrorKey(requestId, workspace.activeChatTabId);
  const gateId = mirroredGates.get(key);
  if (!gateId) return;
  mirroredGates.delete(key);
  agentActions.resolveApproval(gateId, decision === "reject" ? "rejected" : "approved");
}

/** Every pending card of one tab store: the head, then the queue. */
function pendingCards(state: { toolApproval?: ToolApprovalCard | null; toolApprovalQueue?: ToolApprovalCard[] } | undefined): ToolApprovalCard[] {
  if (!state?.toolApproval) return [];
  return [state.toolApproval, ...(state.toolApprovalQueue ?? [])];
}

/** The pending card of `tabId` the gate mirrors (undefined when none). */
function cardForGate(tabId: string, gateId: string): ToolApprovalCard | undefined {
  const state = tabId === workspace.activeChatTabId ? activeTab() : runtimeOrNull(tabId)?.tabStore?.state;
  return pendingCards(state).find((c) => mirroredGates.get(mirrorKey(c.id, tabId)) === gateId);
}

/** A background tab whose still-pending card the gate mirrors (null when none). */
function backgroundTabHoldingGate(gateId: string): string | null {
  for (const [key, id] of mirroredGates) {
    if (id !== gateId) continue;
    const tabId = key.slice(0, key.lastIndexOf(":"));
    if (tabId !== workspace.activeChatTabId && cardForGate(tabId, gateId)) return tabId;
  }
  return null;
}

function noticeAnswerInTab(tabId: string): void {
  const title = workspace.chatTabs.find((t) => t.id === tabId)?.title ?? tabId;
  actions.sendSystemNotice(
    `[approval] that request is still open in chat tab "${title}" — switch to it and answer the card there`,
  );
}

/**
 * Approve/reject a gate from the Research approvals list or the Work pane.
 * A mirror of a pending card of the active tab (the head or a queued one)
 * answers the companion, not just the local list; a mirror of a card already
 * answered is only settled. A card still pending in a background tab is
 * answered there only. Returns false for gates that are not tool-approval
 * mirrors.
 */
export function answerToolApprovalGate(gateId: string, decision: "yes" | "no"): boolean {
  const card = cardForGate(workspace.activeChatTabId, gateId);
  if (card) {
    answerCard(card, decision === "yes" ? "once" : "reject");
    return true;
  }
  const holder = backgroundTabHoldingGate(gateId);
  if (holder) {
    noticeAnswerInTab(holder);
    return true;
  }
  const gate = agentRuntime.approvalGates.find((g) => g.id === gateId);
  if (!gate?.objectId?.startsWith("tool-approval:")) return false;
  if (gate.status === "pending") agentActions.resolveApproval(gateId, decision === "no" ? "rejected" : "approved");
  return true;
}

/** Test hook — forget the Research-list mirrors. */
export function resetApprovalMirrorsForTests(): void {
  mirroredGates.clear();
}

/** A string field, or undefined when absent or empty. */
function text(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/** The card an approval_request line becomes (options derived when absent). */
function cardInput(req: ToolApprovalRequest, tool: string): ToolApprovalInput {
  return {
    id: req.id,
    tool,
    description: typeof req.description === "string" ? req.description : undefined,
    options: Array.isArray(req.options) ? req.options : undefined,
    sessionApprovalAllowed: req.session_approval_allowed !== false,
    diff: text(req.diff),
    path: text(req.path),
    agent: text(req.agent),
    neverAuto: isNeverAuto(req),
    fenceReason: fenceReasonOf(req),
  };
}

function presentApprovalCard(req: ToolApprovalRequest, tool: string): void {
  const hadCard = Boolean(activeTabOfEvent()?.toolApproval);
  const card = cardInput(req, tool);
  actions.openToolApproval(card);
  if (!hadCard) actions.setChatTurnStatus(`awaiting approval: ${tool}`);
  // Mirror into Research Approvals list (the card remains primary).
  const gateId = agentActions.requestApproval(
    "experiment_execution",
    `Tool: ${tool}`,
    card.description ?? "Companion tool approval",
    undefined,
    `tool-approval:${req.id}`,
  );
  mirroredGates.set(mirrorKey(req.id), gateId);
}

/** The store the current bridge event writes to (its own tab when pinned). */
function activeTabOfEvent(): { toolApproval?: ToolApprovalCard | null } | undefined {
  const target = eventTargetTab();
  if (target && target !== workspace.activeChatTabId) return runtimeOrNull(target)?.tabStore?.state;
  return activeTab();
}

/** One ask from the bridge: always a card (ADR-104 D-3, defect 5). */
export function handleApprovalRequest(raw: unknown): void {
  const req = raw as ToolApprovalRequest;
  if (req?.id === undefined || req?.id === null) return;
  presentApprovalCard(req, String(req.tool ?? "tool"));
}

/** Send one answer: a remote (sandbox) intent through session.venue_decide,
 * a local ask on the stdin line of the active tab's bridge — the tab whose
 * card it is (a card only shows, and is only answered, on its own tab). */
function sendDecision(card: ToolApprovalCard, decision: ApprovalOption): void {
  const id = String(card.id);
  if (id.startsWith("remote:")) {
    void import("./venue-bridge").then((m) => m.decideRemoteApproval(id.slice("remote:".length), decision));
    return;
  }
  try {
    getCompanionBridge().respondApproval(card.id, decision satisfies ApprovalWireDecision);
  } catch {
    /* still dismiss the card */
  }
}

const REMEMBER_NOTICE: Partial<Record<ApprovalOption, (tool: string) => string>> = {
  always: (tool) => `always this session: ${tool} (the companion remembers it)`,
};

function turnStatusAfter(decision: ApprovalOption): string | null {
  const next = activeTab().toolApproval;
  if (next) return `awaiting approval: ${next.tool}`;
  if (!activeTab().chatStreaming) return null;
  return decision === "reject" ? "approval denied" : "running…";
}

/** Answer one pending card of the active tab (head or queued). */
function answerCard(card: ToolApprovalCard, decision: ApprovalOption): void {
  sendDecision(card, decision);
  resolveMirroredGate(card.id, decision);
  actions.dropToolApproval(card.id);
  actions.setChatTurnStatus(turnStatusAfter(decision));
  const notice = REMEMBER_NOTICE[decision];
  if (notice) actions.sendSystemNotice(notice(card.tool));
  // M6: gate-approved/rejected inline event card (ambient, opt-out setting).
  if (workspace.ambientCardsEnabled) {
    actions.sendMgmtCard("request-op", { tool: card.tool, decision, at: new Date().toISOString() });
  }
}

/** Answer the card on screen. A decision the card does not offer (`always`
 * on a fenced ask) is ignored: returns false and the card stays. */
export function respondToolApproval(decision: ApprovalOption): boolean {
  const card = activeTab().toolApproval;
  if (!card) return false;
  if (!cardOptions(card).includes(decision)) return false;
  answerCard(card, decision);
  return true;
}

/** Route y / a / r / Enter / Esc to the card on screen (n opens the
 * feedback stage in the card itself). False: not an answer key here. */
export function dispatchToolApprovalKey(name: string): boolean {
  const card = activeTab().toolApproval;
  if (!card) return false;
  const decision = decisionForKey(name, cardOptions(card));
  if (!decision) return false;
  return respondToolApproval(decision);
}

/** Warm the live bridge (its mode is the session's). */
export async function ensureApprovalBridgeStarted(): Promise<void> {
  const bridge = getCompanionBridge();
  try {
    await bridge.ensureStarted();
  } catch {
    // Best-effort; the chat backend will report startup failures.
  }
}
