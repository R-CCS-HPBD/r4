/**
 * Which webview messages the bridge answers when `r4.agent.backend` is
 * `bridge` (ADR-103, VP-2). The chat core routes here; the panels VP-2 does
 * not cover (Studio, Knowledge, management cards, edit review) keep their
 * current paths until their waves. Messages arrive nonce-checked.
 */

import type { BridgeAgentHost } from "./bridge-agent-host";

export interface RouterHooks {
  /** After `ready`: the model inventory and prompt history the view posts itself. */
  afterReady(): Promise<void>;
}

type Route = (host: BridgeAgentHost, message: Record<string, unknown>, hooks: RouterHooks) => Promise<void> | void;

function text(message: Record<string, unknown>, key: string): string {
  const value = message[key];
  return typeof value === "string" ? value.trim() : "";
}

function id(message: Record<string, unknown>): string | number | undefined {
  const value = message.id;
  return typeof value === "string" || typeof value === "number" ? value : undefined;
}

function whenSession(fn: (host: BridgeAgentHost, sessionId: string, message: Record<string, unknown>) => Promise<void>): Route {
  return (host, message) => {
    const sessionId = text(message, "sessionId");
    return sessionId ? fn(host, sessionId, message) : undefined;
  };
}

const ROUTES: Record<string, Route> = {
  ready: async (host, _m, hooks) => {
    await host.initialize();
    await hooks.afterReady();
  },
  refresh: (host) => host.refreshSessions(),
  newSession: (host) => host.newSession(),
  selectSession: whenSession((host, sessionId) => host.selectSession(sessionId)),
  send: async (host, message) => {
    const model = text(message, "model");
    if (model) await host.setModel(model);
    await host.send(text(message, "text"));
  },
  cancel: (host) => host.cancel(),
  renameSession: whenSession((host, sessionId, m) => host.renameSession(sessionId, text(m, "name"))),
  deleteSession: whenSession((host, sessionId) => host.archiveSession(sessionId)),
  pinSession: whenSession((host, sessionId) => host.pinSession(sessionId, true)),
  unpinSession: whenSession((host, sessionId) => host.pinSession(sessionId, false)),
  compactSession: whenSession((host, sessionId, m) =>
    host.compactSession(sessionId, typeof m.keepRecent === "number" ? m.keepRecent : undefined),
  ),
  setModel: (host, message) => host.setModel(text(message, "model") || undefined),
  setMode: (host, message) => host.setMode(text(message, "mode")),
  approvalDecision: (host, message) => {
    const ask = id(message);
    if (ask !== undefined) host.answerApproval(ask, text(message, "decision"));
  },
  askUserAnswer: (host, message) => {
    const ask = id(message);
    if (ask === undefined) return;
    host.answerQuestion(ask, typeof message.answer === "string" ? message.answer : null);
  },
  confirmPlan: (host, message) => host.confirmPlan(text(message, "action")),
  // VP-2b: message verbs, citations, changed files.
  forkSession: (host, message) => host.forkFrom(text(message, "turnId") || undefined),
  saveTurn: (host, message) => {
    const part = text(message, "part");
    if (part === "prompt" || part === "answer") return host.saveTurn(text(message, "turnId"), part);
    return undefined;
  },
  openCitation: (host, message) => {
    const citation = message.citation;
    if (citation && typeof citation === "object") return host.openCitation(citation as Record<string, unknown>);
    return undefined;
  },
  openChange: (host, message) => host.openChange(text(message, "path")),
  mentionQuery: (host, message) => host.mentionCandidates(text(message, "query")),
  toolResultRequest: (host, message) => host.toolResult(text(message, "toolCallId")),
  // VP-3a: the Sessions filters (server-side) and export.
  sessionFilter: (host, message) =>
    host.setSessionFilter({
      state: text(message, "state") || undefined,
      venue: text(message, "venue") || undefined,
      includeArchived: message.includeArchived === true,
    }),
  exportSession: whenSession((host, sessionId) => host.exportSession(sessionId)),
  // VP-3b: a session in its own chat tab.
  openInTab: whenSession((host, sessionId) => host.openInTab(sessionId)),
  venueStop: whenSession((host, sessionId) => host.stopRemote(sessionId)),
  forkRow: whenSession((host, sessionId) => host.forkSession(sessionId)),
  rewindTo: async (host, message) => {
    const turnId = typeof message.turnId === "string" ? message.turnId : "";
    if (turnId && typeof message.text === "string") host.rewindTo(turnId, message.text);
  },
  sessionTurns: whenSession((host, sessionId) => host.sessionTurns(sessionId)),
  revertChange: (host, message) => {
    const path = text(message, "path");
    return path ? host.revertChange(path) : undefined;
  },
};

/** The message types the bridge host answers. */
export const BRIDGE_VIEW_TYPES: ReadonlySet<string> = new Set(Object.keys(ROUTES));

/** Run the route for `message`; false when the bridge does not answer it. */
export async function routeBridgeViewMessage(
  host: BridgeAgentHost,
  message: Record<string, unknown>,
  hooks: RouterHooks,
): Promise<boolean> {
  const route = typeof message.type === "string" ? ROUTES[message.type] : undefined;
  if (!route) return false;
  await route(host, message, hooks);
  return true;
}
