/**
 * Chat tabs (ADR-103 VP-3b, the console's chat-tab strip): each editor tab
 * is a WebviewPanel with its OWN bridge process and host, so turns in
 * different tabs run side by side. Cap 8, as the console's. The title is
 * the session's name, `● ` while a turn runs, `! ` when an approval or a
 * question lands while the tab is in the background. A closed tab can be
 * reopened: its session resumes from the journal (`initialize {session_id}`).
 */

import * as vscode from "vscode";

import { buildWebviewHtml, createWebviewNonce } from "../webview-html";
import { appendPromptHistory } from "../prompt-history";
import { BridgeAgentHost, type HostBridge } from "./bridge-agent-host";
import { routeBridgeViewMessage } from "./bridge-view-router";
import { createHostHooks } from "./host-hooks";
import { tabTitle, type TabMark } from "./tab-title";

export const MAX_CHAT_TABS = 8;

export type TabBridgeFactory = (initialSessionId?: string) => HostBridge & { shutdown(): Promise<void> };

interface ChatTab {
  panel: vscode.WebviewPanel;
  host: BridgeAgentHost;
  bridge: HostBridge & { shutdown(): Promise<void> };
  nonce: string;
  name: string;
  mark: TabMark;
  /** The session the tab was opened for (before its bridge has started). */
  requested: string;
}

function tabSession(tab: ChatTab): string {
  return tab.host.currentSessionId || tab.requested;
}

/** Messages that mark a background tab for attention. */
const ATTENTION = new Set(["approvalRequest", "askUserRequest", "planConfirm", "error"]);

export class ChatPanels implements vscode.Disposable {
  private readonly tabs = new Set<ChatTab>();
  /** Sessions of closed tabs, newest last (reopen takes the newest). */
  private readonly closed: string[] = [];

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly bridgeFactory: TabBridgeFactory,
    /** The model inventory and prompt tape a fresh tab starts with. */
    private readonly startupMessages: () => Promise<Record<string, unknown>[]>,
  ) {}

  get count(): number {
    return this.tabs.size;
  }

  /** A new tab: a fresh session, or `sessionId` resumed in its own bridge. */
  open(sessionId?: string): boolean {
    // A session already in a tab is shown, even when every slot is taken.
    const existing = sessionId ? [...this.tabs].find((t) => tabSession(t) === sessionId) : undefined;
    if (existing) {
      existing.panel.reveal();
      return true;
    }
    if (this.tabs.size >= MAX_CHAT_TABS) {
      void vscode.window.showWarningMessage(`R4: at most ${MAX_CHAT_TABS} chat tabs (each runs its own bridge). Close one first.`);
      return false;
    }
    this.tabs.add(this.createTab(sessionId));
    return true;
  }

  /** Reopen the most recently closed tab (its session, from the journal). */
  reopen(quiet = false): boolean {
    const sessionId = this.closed.pop();
    if (!sessionId) {
      if (!quiet) void vscode.window.showInformationMessage("R4: no closed chat tab to reopen.");
      return false;
    }
    return this.open(sessionId);
  }

  /** `/tab [list|new|close [n]|next|prev|reopen|<n>]`, the console's verbs;
   * "the active tab" is the focused chat tab. Returns the output card lines. */
  command(args: string): TabLine[] {
    const [verb = "", arg = ""] = args.trim().split(/\s+/);
    const run = TAB_VERBS[verb.toLowerCase()];
    if (run) return run(this, arg);
    if (/^\d+$/.test(verb)) return this.activate(Number(verb));
    return [{ kind: "error", text: TAB_USAGE }];
  }

  list(): TabLine[] {
    const tabs = [...this.tabs];
    if (!tabs.length) return [{ kind: "info", text: "No chat tabs open. /tab new opens one (the sidebar is its own session)." }];
    return tabs.map((t, i) => ({ kind: "info", text: `${t.panel.active ? "▸" : " "} ${i + 1}. ${t.panel.title}` }));
  }

  activate(n: number): TabLine[] {
    const tab = [...this.tabs][n - 1];
    if (!tab) return [{ kind: "error", text: `No chat tab ${n} (${this.tabs.size} open).` }];
    tab.panel.reveal();
    return [];
  }

  /** Close tab `n`, or the focused one. */
  closeTab(arg: string): TabLine[] {
    const tabs = [...this.tabs];
    const tab = arg ? tabs[Number(arg) - 1] : tabs.find((t) => t.panel.active);
    if (!tab) return [{ kind: "error", text: arg ? `No chat tab ${arg}.` : "No chat tab is focused: /tab close <n>." }];
    tab.panel.dispose();
    return [];
  }

  /** Focus the next (+1) or previous (-1) tab, wrapping. */
  step(delta: number): TabLine[] {
    const tabs = [...this.tabs];
    if (!tabs.length) return this.list();
    const at = tabs.findIndex((t) => t.panel.active);
    const next = (at + delta + tabs.length) % tabs.length;
    return this.activate(at < 0 ? 1 : next + 1);
  }

  private createTab(sessionId: string | undefined): ChatTab {
    const root = vscode.Uri.joinPath(this.extensionUri, "dist", "webview");
    const panel = vscode.window.createWebviewPanel("r4.chatTab", "R4 chat", vscode.ViewColumn.Active, {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [root],
    });
    const nonce = createWebviewNonce();
    panel.webview.html = buildWebviewHtml(panel.webview, root, nonce);
    const bridge = this.bridgeFactory(sessionId);
    const tab = { panel, bridge, nonce, name: "R4 chat", mark: "" as TabMark, requested: sessionId ?? "" } as ChatTab;
    const post = (message: Record<string, unknown>) => this.post(tab, message);
    tab.host = new BridgeAgentHost(bridge, post, createHostHooks((text) => appendPromptHistory(text)));
    panel.webview.onDidReceiveMessage((message: unknown) => void this.dispatch(tab, message));
    panel.onDidChangeViewState(() => {
      if (panel.active && tab.mark === "!") this.retitle(tab, "");
    });
    panel.onDidDispose(() => this.close(tab));
    return tab;
  }

  /** Everything the host posts goes to the tab's webview; the title follows. */
  private post(tab: ChatTab, message: Record<string, unknown>): void {
    void tab.panel.webview.postMessage(message);
    this.followTitle(tab, message);
  }

  private followTitle(tab: ChatTab, message: Record<string, unknown>): void {
    tab.name = nameFrom(message, tab.host.currentSessionId) || tab.name;
    this.retitle(tab, nextMark(tab, message));
  }

  private retitle(tab: ChatTab, mark: TabMark): void {
    tab.mark = mark;
    tab.panel.title = tabTitle(tab.name, mark);
  }

  private async dispatch(tab: ChatTab, value: unknown): Promise<void> {
    const message = value as Record<string, unknown> | null;
    if (!message || typeof message !== "object" || message.messageToken !== tab.nonce) return;
    try {
      const routed = await routeBridgeViewMessage(tab.host, message, {
        afterReady: async () => {
          for (const startup of await this.startupMessages()) void tab.panel.webview.postMessage(startup);
        },
      });
      if (!routed) this.notInTab(tab, String(message.type ?? ""));
    } catch (error) {
      void tab.panel.webview.postMessage({ type: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }

  /** Studio, Knowledge and the management cards live in the sidebar agent view. */
  private notInTab(tab: ChatTab, type: string): void {
    if (!type || type === "editorContext") return;
    void tab.panel.webview.postMessage({
      type: "error",
      message: "This part of R4 is in the sidebar's R4 Agent view; chat tabs carry the conversation.",
    });
  }

  private close(tab: ChatTab): void {
    if (!this.tabs.delete(tab)) return;
    const sessionId = tabSession(tab);
    if (sessionId) this.closed.push(sessionId);
    if (this.closed.length > MAX_CHAT_TABS) this.closed.shift();
    tab.host.dispose();
    void tab.bridge.shutdown();
  }

  dispose(): void {
    for (const tab of [...this.tabs]) tab.panel.dispose();
  }
}

export interface TabLine {
  kind: "info" | "error";
  text: string;
}

export const TAB_USAGE = "/tab [list|new|close [n]|next|prev|reopen|<n>]";

const TAB_VERBS: Record<string, (tabs: ChatPanels, arg: string) => TabLine[]> = {
  "": (tabs) => tabs.list(),
  list: (tabs) => tabs.list(),
  new: (tabs) => (tabs.open() ? [] : [{ kind: "error", text: `At most ${MAX_CHAT_TABS} chat tabs.` }]),
  close: (tabs, arg) => tabs.closeTab(arg),
  next: (tabs) => tabs.step(1),
  prev: (tabs) => tabs.step(-1),
  reopen: (tabs) => (tabs.reopen(true) ? [] : [{ kind: "info", text: "No closed chat tab to reopen." }]),
};

/** The session's name, when the message carries it ("" otherwise). */
function nameFrom(message: Record<string, unknown>, sessionId: string): string {
  if (message.type === "sessionSelected") return String((message.session as { name?: unknown })?.name ?? "");
  if (message.type === "sessions") return sessionName(message, sessionId);
  return "";
}

/** ● while a turn runs (cleared when it ends); ! for attention in the background. */
function nextMark(tab: ChatTab, message: Record<string, unknown>): TabMark {
  const type = String(message.type ?? "");
  if (type === "sessionState") {
    if (message.state === "running") return "●";
    return tab.mark === "●" ? "" : tab.mark;
  }
  if (ATTENTION.has(type) && !tab.panel.active) return "!";
  return tab.mark;
}

function sessionName(message: Record<string, unknown>, sessionId: string): string {
  const rows = Array.isArray(message.sessions) ? (message.sessions as { id?: unknown; name?: unknown }[]) : [];
  const row = rows.find((r) => r.id === sessionId);
  return typeof row?.name === "string" ? row.name : "";
}
