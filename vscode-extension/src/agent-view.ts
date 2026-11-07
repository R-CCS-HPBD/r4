import * as vscode from "vscode";
import * as fs from "node:fs";
import { AcpRequestError, type AcpClient, type AcpNotification, type AcpRequestOptions } from "./acp-client";
import { AcpProcessSupervisor } from "./acp-process";
import { identityMismatch } from "./source-identity";
import type { AgentToolBridge, AgentToolRequest } from "./agent-tools";
import type { EditSessionController } from "./edit-session";
import type { LlmSourceConfig } from "./auth";
import type { Endpoint } from "./gateway-client";
import { GatewayClient } from "./gateway-client";
import { rankSatelliteGraphs, topicTerms } from "./topic-graph";
import {
  buildMgmtCardParams,
  MGMT_CARD_QUERY_MAP,
  MGMT_KINDS,
} from "./mgmt-card-map";
import { appendPromptHistory, promptHistory } from "./prompt-history";
import { BridgeAgentHost, type HostBridge } from "./bridge/bridge-agent-host";
import { routeBridgeViewMessage } from "./bridge/bridge-view-router";
import { createHostHooks } from "./bridge/host-hooks";
import { buildWebviewHtml } from "./webview-html";
import {
  showCompanionAskUser,
  showCompanionToolApproval,
  type CompanionAskUserParams,
  type CompanionToolApprovalParams,
} from "./vscode-companion-approval";

interface AgentSessionSummary {
  id: string;
  name: string;
  model?: string;
  mode?: string;
  updated_at?: string;
  message_count?: number;
  /** Research project the chat is tagged with (empty = untagged). */
  project_id?: string;
  /** C2: pinned (sessions.pinned_at column; pinned-first sort). */
  pinned?: boolean;
  messages?: AgentSessionMessage[];
}

interface AgentSessionMessage {
  role: string;
  content: string;
}

interface SessionsResult {
  sessions: AgentSessionSummary[];
}

interface SessionResult {
  session: AgentSessionSummary;
}

interface ChatResult {
  status: string;
  session_id: string;
  response: string;
}

interface AbortControllerLike {
  readonly signal: NonNullable<AcpRequestOptions["signal"]>;
  abort(): void;
}

interface EndpointOption {
  id: string;
  label: string;
  status: string;
}

/** Studio tab view messages (projects + UMRS workflow graph surface). */
type StudioMessage =
  | { type: "refreshStudio" }
  | { type: "refreshKnowledge" }
  | { type: "switchProject"; projectId: string }
  | { type: "createProject"; projectId: string; name?: string; gateway?: string; model?: string }
  | { type: "runWorkflow"; workflowName: string; parameters?: Record<string, unknown>; projectId?: string; inputFiles?: string[]; wait?: boolean }
  | { type: "describeWorkflow"; workflowName: string }
  | { type: "startResearchLoop"; researchGoal: string; projectId?: string; maxIterations?: number; agentic?: boolean; requires?: Record<string, unknown>; runtimeMode?: string }
  | { type: "stopResearchLoop"; loopId: string; projectId?: string }
  | { type: "ingestKnowledge"; files: string[]; sources: string[]; projectId?: string }
  | { type: "pickKnowledgeFiles" }
  | { type: "catalogSearch"; query?: string; projectId?: string }
  | { type: "setKnowledgeGraphConstraints"; constraints: { include: string[]; exclude: string[]; discovery: boolean } }
  | { type: "cancelExecution"; executionId: string }
  | { type: "approveGateStep"; executionId: string; stepName: string; approved: boolean }
  | { type: "removeWatch"; executionId: string }
  // ── System tab (skills, MCP servers, command help) ──
  | { type: "refreshSystem" }
  | { type: "setSkillActive"; sessionId: string; name: string; active: boolean }
  | { type: "checkMcpHealth"; serverId: string }
  | { type: "pinSession"; sessionId: string }
  | { type: "unpinSession"; sessionId: string }
  | { type: "compactSession"; sessionId: string; keepRecent?: number }
  // ── Management chat rows (M5): slash-verb card queries mirrored from the
  // TUI surfaces (v3 / tui-js), resolved via the bridge. ──
  | { type: "mgmtCardQuery"; requestId: string; sessionId: string; kind: string; arg?: string };

type ViewMessage =
  | { type: "ready" }
  | { type: "refresh" }
  | { type: "login" }
  // Tool card expand — fetch the full result from the session journal.
  | { type: "toolResultRequest"; sessionId: string; toolCallId: string }
  | { type: "newSession"; model?: string }
  | { type: "selectSession"; sessionId: string }
  | { type: "send"; text: string; sessionId?: string; model?: string }
  | { type: "cancel"; sessionId?: string }
  | { type: "shellCancel"; streamId: string; sessionId?: string }
  | { type: "restart" }
  | { type: "renameSession"; sessionId: string; name: string }
  | { type: "deleteSession"; sessionId: string }
  | { type: "setModel"; sessionId: string; model?: string }
  | { type: "setMode"; sessionId: string; mode: string }
  | { type: "openFile"; path: string }
  | { type: "acceptEdit"; editId: string }
  | { type: "rejectEdit"; editId: string }
  | { type: "reviewEdit"; editId: string }
  | StudioMessage;

/** Interaction modes surfaced by the webview mode selector (ask | plan | agent). */
const STUDIO_MODES = new Set(["ask", "chat", "plan", "agent", "auto", "autopilot"]);

/** Rolling user-text window per session feeding satellite topic ranking. */
const TOPIC_WINDOW_SIZE = 6;

export class R4AgentViewProvider implements vscode.WebviewViewProvider, vscode.Disposable {
  static readonly viewType = "r4.agent";

  private view: vscode.WebviewView | undefined;
  private supervisor: AcpProcessSupervisor | undefined;
  private readonly listenerDisposables: vscode.Disposable[] = [];
  private client: AcpClient | undefined;
  private lastEndpoints: EndpointOption[] = [];
  private removeNotificationListener: (() => void) | undefined;
  private removeToolMethod: (() => void) | undefined;
  private removeApprovalMethod: (() => void) | undefined;
  private removeAskUserMethod: (() => void) | undefined;
  /** Sessions flowing canonical thinking events — suppress legacy session/thinking. */
  private readonly canonicalThinkingSessions = new Set<string>();
  private toolBridge: AgentToolBridge | undefined;
  /** Session currently displayed in the webview (for selection state only). */
  private viewedSessionId: string | undefined;
  /** Active in-flight request per session id — enables concurrent sessions. */
  private readonly activeRequests = new Map<string, { abort(): void; requestId: string }>();
  /** Maps a request id back to its owning session for notification routing. */
  private readonly requestOwners = new Map<string, string>();
  /** True while a brand-new (id-less) chat request is being created. */
  private newChatInFlight = false;
  private messageToken = "";
  /** Watchlist of UMRS workflow executions polled by the Studio graph. */
  private watchedExecutions = new Set<string>();
  /** Last cached studio snapshot so new webviews rehydrate instantly. */
  private studioSnapshot: Record<string, unknown> | undefined;
  /** Poll timer while any watched execution is non-terminal. */
  private studioPollTimer: ReturnType<typeof setTimeout> | undefined;
  /** Rolling per-session user text (topic source for satellite admission). */
  private readonly topicWindows = new Map<string, string[]>();

  constructor(
    private readonly createSupervisor: () => AcpProcessSupervisor,
    private readonly getGatewayClient: () => GatewayClient | undefined,
    private readonly createToolBridge: () => Promise<AgentToolBridge | undefined>,
    /** Extension install root. When omitted the legacy inline view is used. */
    private readonly extensionUri?: vscode.Uri,
    /** All LLM-capable sources (platform + public) for model aggregation. */
    private readonly listSourceConfigs: () => LlmSourceConfig[] = () => [],
  ) {}

  /** Edit-review surface shared with the tool bridge. Optional: setter keeps
   *  unit-test constructions (which omit it) source-compatible. */
  private editSession?: EditSessionController;

  /** ADR-103 VP-2: the console's bridge, when `r4.agent.backend` is `bridge`. */
  private bridgeFactory?: () => HostBridge & { shutdown(): Promise<void> };
  private bridge?: HostBridge & { shutdown(): Promise<void> };
  private bridgeHost?: BridgeAgentHost;

  setBridgeFactory(factory: () => HostBridge & { shutdown(): Promise<void> }): void {
    this.bridgeFactory = factory;
  }

  private bridgeBackendOn(): boolean {
    const backend = vscode.workspace.getConfiguration("r4").get<string>("agent.backend", "acp");
    return backend === "bridge" && this.bridgeFactory !== undefined;
  }

  private getBridgeHost(): BridgeAgentHost {
    if (this.bridgeHost) return this.bridgeHost;
    const bridge = this.bridgeFactory!();
    this.bridge = bridge;
    const hooks = createHostHooks((text) => {
      appendPromptHistory(text);
      this.postHistory();
    });
    this.bridgeHost = new BridgeAgentHost(bridge, (message) => this.post(message as never), hooks);
    return this.bridgeHost;
  }

  /** Bridge backend first (chat core), then the ACP path for everything else. */
  private async dispatchMessage(value: unknown): Promise<void> {
    if (!this.bridgeBackendOn() || !isRecord(value) || value.messageToken !== this.messageToken) {
      await this.handleMessage(value);
      return;
    }
    try {
      const routed = await routeBridgeViewMessage(this.getBridgeHost(), value, {
        afterReady: () => this.afterBridgeReady(),
      });
      if (!routed) await this.handleMessage(value);
    } catch (error) {
      this.post({ type: "error", message: safeErrorMessage(error) });
    }
  }

  private async afterBridgeReady(): Promise<void> {
    await this.refreshInventory();
    this.postHistory();
  }

  setEditSession(editSession: EditSessionController): void {
    this.editSession = editSession;
    editSession.onDidChange((event) => {
      // Forward the edit lifecycle to the webview transcript: cards update
      // their badge (pending → accepted/rejected) and the session strip stays
      // in sync without polling.
      if (event.kind === "cleared") {
        for (const cleared of event.cleared) this.postEditState(cleared);
        return;
      }
      this.postEditState(event.edit);
    });
  }

  private postEditState(edit: {
    id: string;
    sessionId: string;
    agentCallId?: string;
    filePath: string;
    relPath: string;
    stats: { added: number; removed: number };
    status: string;
  }): void {
    this.post({
      type: "editState",
      sessionId: edit.sessionId,
      editId: edit.id,
      agentCallId: edit.agentCallId,
      path: edit.filePath,
      relPath: edit.relPath,
      added: edit.stats.added,
      removed: edit.stats.removed,
      status: edit.status === "pending" ? "staged" : edit.status,
    });
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    this.messageToken = createNonce();

    // Priority: vite dev server > bundled React build > inline HTML fallback.
    if (process.env.R4_WEBVIEW_DEV === "1") {
      view.webview.options = { enableScripts: true, localResourceRoots: [] };
      view.webview.html = this._buildDevHtml(this.messageToken);
    } else if (this.extensionUri) {
      const webviewRoot = vscode.Uri.joinPath(this.extensionUri, "dist", "webview");
      view.webview.options = { enableScripts: true, localResourceRoots: [webviewRoot] };
      view.webview.html = this._buildWebViewHtml(view.webview, webviewRoot, this.messageToken);
    } else {
      // Used by unit tests and by builds without a compiled webview.
      view.webview.options = { enableScripts: true, localResourceRoots: [] };
      view.webview.html = renderAgentView(view.webview, this.messageToken);
    }

    view.webview.onDidReceiveMessage((message: unknown) => {
      void this.dispatchMessage(message);
    });
    // D5: editor-context chip — live file#L-range chip following the cursor.
    const postSelection = () => this.postEditorContext();
    this.listenerDisposables.forEach((d) => d.dispose());
    this.listenerDisposables.length = 0;
    this.listenerDisposables.push(
      vscode.window.onDidChangeTextEditorSelection(postSelection),
      vscode.window.onDidChangeActiveTextEditor(postSelection),
    );
    postSelection();
    view.onDidDispose(() => {
      this.view = undefined;
      this.cancelAllRequests();
      this.listenerDisposables.forEach((d) => d.dispose());
      this.listenerDisposables.length = 0;
    });
  }

  /** D5: relative path + selection lines (`src/app.ts#3-9`), null when empty. */
  private editorContextChip(): string | null {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return null;
    const file = vscode.workspace.asRelativePath(editor.document.uri, false);
    const sel = editor.selection;
    if (sel.isEmpty) return `${file}`;
    const start = sel.start.line + 1;
    const end = sel.end.line + 1;
    return start === end ? `${file}#${start}` : `${file}#${start}-${end}`;
  }

  private postEditorContext(): void {
    this.post({ type: "editorContext", chip: this.editorContextChip() });
  }

  /** HTML served from the vite dev server (`R4_WEBVIEW_DEV=1` only). */
  private _buildDevHtml(nonce: string): string {
    const dev = "http://localhost:5173";
    const csp = [
      "default-src 'none'",
      `style-src ${dev} 'unsafe-inline'`,
      `script-src ${dev} 'unsafe-eval'`,
      `connect-src ${dev} ws://localhost:5173`,
      `img-src ${dev} data:`,
    ].join("; ");
    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="${csp}">
</head>
<body>
  <div id="root" data-nonce="${nonce}"></div>
  <script type="module" src="${dev}/@vite/client"></script>
  <script type="module" src="${dev}/src/main.tsx"></script>
</body>
</html>`;
  }

  dispose(): void {
    if (this.studioPollTimer) {
      clearTimeout(this.studioPollTimer);
      this.studioPollTimer = undefined;
    }
    this.cancelAllRequests();
    this.resetTransport();
    this.bridgeHost?.dispose();
    void this.bridge?.shutdown();
    this.bridge = undefined;
    this.bridgeHost = undefined;
    this.view = undefined;
  }

  /** Sign-in affordance from the WebView status bar (fresh-machine
   * bootstrap, docs/WEBVIEW_AUTH_CHAIN_FINDING.md §R6). Dispatches
   * through the canonical extension login flow so the resulting state
   * ends up in the r4 canonical credential store, then refreshes the
   * cached view state so the WebView transitions off "Gateway offline".
   */
  private async handleLoginRequest(): Promise<void> {
    await vscode.commands.executeCommand("r4.login");
    await this.refreshAll();
  }

  /** Tool card expand → ACP `sessions/tool_result` journal fetch (task 2). */
  private async handleToolResultRequest(
    message: Extract<ViewMessage, { type: "toolResultRequest" }>,
  ): Promise<void> {
    const { sessionId, toolCallId } = message;
    try {
      const client = await this.getClient();
      const result = await client.request<Record<string, unknown>>(
        "sessions/tool_result",
        { session_id: sessionId, tool_call_id: toolCallId },
      );
      this.post({
        type: "toolResult",
        sessionId,
        toolCallId,
        found: result?.found === true,
        name: typeof result?.name === "string" ? result.name : undefined,
        content: typeof result?.content === "string" ? result.content : undefined,
      });
    } catch {
      this.post({ type: "toolResult", sessionId, toolCallId, found: false });
    }
  }

  /** Webview Cancel chip → ACP shell/cancel → pgid kill. Surfaces ack back
   * to the webview so the chip can flip into a disabled state deterministically. */
  private async handleStreamCancel(message: Extract<ViewMessage, { type: "shellCancel" }>): Promise<void> {
    if (!this.client) {
      this.post({
        type: "shellStreamStatus",
        sessionId: message.sessionId,
        streamId: message.streamId,
        status: "disconnected",
        detail: "agent client unavailable",
      });
      return;
    }
    try {
      const res = await this.client.request<{ stream_id: string; status: string }>(
        "shell/cancel",
        { stream_id: message.streamId },
      );
      this.post({
        type: "shellStreamStatus",
        sessionId: message.sessionId,
        streamId: res.stream_id,
        status: res.status,
      });
    } catch (error) {
      this.post({
        type: "shellStreamStatus",
        sessionId: message.sessionId,
        streamId: message.streamId,
        status: "error",
        detail: safeErrorMessage(error),
      });
    }
  }

  private async handleMessage(value: unknown): Promise<void> {
    if (!isRecord(value) || value.messageToken !== this.messageToken) {
      this.post({ type: "error", message: "Agent view rejected an unauthenticated request." });
      return;
    }
    const message = parseViewMessage(value);
    if (!message) {
      this.post({ type: "error", message: "Agent view sent an invalid request." });
      return;
    }
    try {
      switch (message.type) {
        case "ready":
          await this.initialize();
          break;
        case "refresh":
          await this.refreshAll();
          break;
        case "login":
          await this.handleLoginRequest();
          break;
        case "newSession":
          await this.createSession(message.model);
          break;
        case "selectSession":
          await this.loadSession(message.sessionId);
          break;
        case "send":
          await this.send(message.text, message.sessionId, message.model);
          break;
        case "cancel":
          this.cancelRequest(message.sessionId);
          break;
        case "shellCancel":
          await this.handleStreamCancel(message);
          break;
        case "toolResultRequest":
          await this.handleToolResultRequest(message);
          break;
        case "restart":
          await this.restart();
          break;
        case "renameSession":
          await this.renameSession(message.sessionId, message.name);
          break;
        case "deleteSession":
          await this.deleteSession(message.sessionId);
          break;
        case "pinSession":
        case "unpinSession":
          await this.pinSession(message.sessionId, message.type === "pinSession");
          break;
        case "compactSession":
          await this.compactSession(message.sessionId, message.keepRecent);
          break;
        case "setModel":
          await this.setSessionModel(message.sessionId, message.model);
          break;
        case "setMode":
          await this.setSessionMode(message.sessionId, message.mode);
          break;
        case "openFile":
          await this.openFileAtPath(message.path);
          break;
        case "acceptEdit":
          await this.editSession?.accept(message.editId);
          break;
        case "rejectEdit":
          await this.editSession?.reject(message.editId);
          break;
        case "reviewEdit":
          await this.editSession?.reopenDiff(message.editId);
          break;
        case "refreshStudio":
          await this.refreshStudio();
          break;
        case "refreshKnowledge":
          await this.refreshKnowledge();
          break;
        case "switchProject":
          await this.switchStudioProject(message.projectId);
          break;
        case "createProject":
          await this.createStudioProject(message);
          break;
        case "runWorkflow":
          await this.runStudioWorkflow(message);
          break;
        case "describeWorkflow":
          await this.describeWorkflow(message.workflowName);
          break;
        case "startResearchLoop":
          await this.startResearchLoop(message);
          break;
        case "stopResearchLoop":
          await this.stopResearchLoop(message);
          break;
        case "ingestKnowledge":
          await this.ingestKnowledge(message);
          break;
        case "pickKnowledgeFiles":
          await this.pickKnowledgeFiles();
          break;
        case "catalogSearch":
          await this.searchCatalog(message);
          break;
        case "setKnowledgeGraphConstraints":
          await this.setKnowledgeGraphConstraints(message);
          break;
        case "cancelExecution":
          await this.cancelStudioExecution(message.executionId);
          break;
        case "approveGateStep":
          await this.approveStudioGateStep(message.executionId, message.stepName, message.approved);
          break;
        case "removeWatch":
          this.watchedExecutions.delete(message.executionId);
          await this.refreshStudio();
          break;
        case "refreshSystem":
          await this.refreshSystem();
          break;
        case "setSkillActive":
          await this.setSkillActive(message);
          break;
        case "checkMcpHealth":
          await this.checkMcpHealth(message.serverId);
          break;
        case "mgmtCardQuery":
          await this.executeMgmtCardQuery(message.requestId, message.sessionId, message.kind, message.arg);
          break;
      }
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        this.post({ type: "cancelled", sessionId: this.viewedSessionId });
      } else {
        this.post({ type: "error", message: safeErrorMessage(error) });
        // Only tear down the agent process when the transport itself is
        // broken. Application-level errors (AcpRequestError — e.g. a failed
        // session rename/delete, "Session already has an active request")
        // must not kill the whole agent: the webview stays usable and the
        // user can keep working in unaffected sessions.
        if (this.isTransportFailure(error)) {
          this.resetTransport();
          this.post({ type: "status", status: "disconnected" });
        }
      }
    }
  }

  private async initialize(): Promise<void> {
    this.post({ type: "status", status: "connecting" });
    const client = await this.getClient();
    await this.refreshSessions(client);
    await this.refreshInventory();
    this.postHistory();
    this.post({ type: "status", status: "ready" });
    // Fetch the studio snapshot (projects + active project id) at view-open
    // even when the user only uses the chat tab: the chat-side project
    // picker needs the list, and live knowledge refreshes during agent
    // turns resolve the project id from the snapshot. Fire-and-forget so a
    // slow/unreachable gateway never delays the chat becoming usable.
    void this.refreshStudio().catch(() => undefined);
  }

  private async refreshAll(): Promise<void> {
    const client = await this.getClient();
    await this.refreshSessions(client);
    await this.refreshInventory();
    void this.refreshStudio().catch(() => undefined);
  }

  private async createSession(model?: string): Promise<void> {
    const client = await this.getClient();
    const session = await this.createSessionOnServer(client, model);
    this.viewedSessionId = session.id;
    this.post({ type: "sessionSelected", session, fresh: true, messages: [] });
    await this.refreshSessions(client);
  }

  private async createSessionOnServer(
    client: AcpClient,
    model?: string,
  ): Promise<AgentSessionSummary> {
    const params: Record<string, unknown> = {
      name: `R4 Agent ${new Date().toLocaleString()}`,
    };
    if (model) params.model = model;
    const result = await client.request<SessionResult>("sessions/create", params);
    return result.session;
  }

  private async loadSession(sessionId: string): Promise<void> {
    const client = await this.getClient();
    const result = await client.request<SessionResult>("sessions/load", { session_id: sessionId });
    this.viewedSessionId = result.session.id;
    this.post({
      type: "sessionSelected",
      session: result.session,
      fresh: false,
      messages: normalizeMessages(result.session.messages),
    });
    if (isNonEmptyString(result.session.mode)) {
      this.post({
        type: "modeChanged",
        sessionId,
        mode: result.session.mode,
      });
    }
  }

  private async pinSession(sessionId: string, pinned: boolean): Promise<void> {
    const client = await this.getClient();
    await client.request<{ session_id: string; pinned: boolean }>(
      pinned ? "sessions/pin" : "sessions/unpin",
      { session_id: sessionId },
    );
    await this.refreshSessions(client);
  }

  /** C4: /compact — user-driven compaction via the ACP sessions/compact verb. */
  private async compactSession(sessionId: string, keepRecent?: number): Promise<void> {
    const client = await this.getClient();
    try {
      const result = await client.request<Record<string, unknown>>("sessions/compact", {
        session_id: sessionId,
        keep_recent: keepRecent ?? 6,
      });
      this.post({
        type: "compactResult",
        sessionId,
        changed: Boolean(result?.changed),
        from: Number(result?.from ?? 0),
        to: Number(result?.to ?? result?.message_count ?? 0),
        keepRecent: Number(result?.keep_recent ?? keepRecent ?? 6),
        reason: typeof result?.reason === "string" ? result.reason : undefined,
      });
    } catch (error) {
      this.post({
        type: "compactResult",
        sessionId,
        changed: false,
        from: 0,
        to: 0,
        keepRecent: keepRecent ?? 6,
        error: safeErrorMessage(error),
      });
    }
  }

  private async renameSession(sessionId: string, name: string): Promise<void> {
    const client = await this.getClient();
    await client.request<SessionResult>("sessions/rename", { session_id: sessionId, name });
    await this.refreshSessions(client);
  }

  private async setSessionModel(sessionId: string, model?: string): Promise<void> {
    const client = await this.getClient();
    const result = await client.request<SessionResult>("sessions/set_model", {
      session_id: sessionId,
      model: model ?? null,
    });
    this.post({ type: "modelChanged", sessionId, model: result.session.model ?? "" });
    await this.refreshSessions(client);
  }

  /**
   * Mode selector (ask | plan | agent). Propagates to the companion session
   * via ``sessions/set_mode``: ``agent`` → autopilot/TRUST (no approval
   * prompts), ``plan`` → plan-first state machine, ``ask`` → interactive.
   */
  private async setSessionMode(sessionId: string, mode: string): Promise<void> {
    const client = await this.getClient();
    await client.request<SessionResult>("sessions/set_mode", {
      session_id: sessionId,
      mode,
    });
    this.post({ type: "modeChanged", sessionId, mode });
  }

  // ── Studio: research projects + UMRS workflow graph ─────────────────────

  /**
   * Pull the full studio snapshot (projects + workflow catalog + watched
   * executions with step detail) and push it to the webview.
   */
  private async refreshStudio(): Promise<void> {
    const client = await this.getClient();
    let projects: unknown = { projects: [], current: null };
    let workflows: unknown = { workflows: [], count: 0 };
    try {
      projects = await client.request<Record<string, unknown>>("projects/list", {});
    } catch {
      /* project data unavailable (no client config yet) */
    }
    try {
      workflows = await client.request<Record<string, unknown>>("workflows/list", {});
    } catch {
      /* gateway unreachable — render empty catalog */
    }
    const executions: Array<Record<string, unknown>> = [];
    for (const executionId of [...this.watchedExecutions]) {
      const execution = await this.fetchStudioExecution(executionId);
      if (execution) executions.push(execution);
    }
    this.postStudioState({ projects, workflows, executions });
    // Knowledge + research loops are studio-side too; fire-and-forget so
    // they land right after the initial snapshot without blocking it.
    void this.refreshKnowledge().catch(() => undefined);
  }

  private _resolveStudioCacheDir(projectId: string): string {
    const root =
      process.env.R4_STATE_DIR ?? `${process.env.HOME ?? "/tmp"}/.config/r4`;
    return `${root}/projects/${projectId}`;
  }

  /**
   * Snapshot mirrors: Studio graph + research loops as local JSON caches.
   * Written after every successful refresh; read back when the gateway is
   * offline so webview panels show last-known state.
   */
  private _writeStudioCache(
    projectId: string,
    knowledgeGraph: unknown,
    researchLoops: unknown,
  ): void {
    const dir = this._resolveStudioCacheDir(projectId);
    try {
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(`${dir}/kgraph.json`, JSON.stringify(knowledgeGraph), "utf-8");
      fs.writeFileSync(`${dir}/kloops.json`, JSON.stringify(researchLoops), "utf-8");
    } catch {
      // best-effort: cache failures never block the Studio refresh event
    }
  }

  /**
   * Read back the cached Studio mirror when the gateway is offline.
   * Returns nulls for missing cache entries without raising.
   */
  private _readStudioCache(projectId: string): {
    knowledgeGraph: unknown;
    researchLoops: unknown;
  } {
    const dir = this._resolveStudioCacheDir(projectId);
    const readJson = (name: string): unknown => {
      try {
        return JSON.parse(fs.readFileSync(`${dir}/${name}`, "utf-8"));
      } catch {
        return null;
      }
    };
    const knowledgeGraph = readJson("kgraph.json");
    const researchLoops = readJson("kloops.json");
    return {
      knowledgeGraph: knowledgeGraph ?? null,
      researchLoops: researchLoops ?? { loops: [] },
    };
  }

  /** Post a studioState event after normalizing the payload shape. */
  private postStudioState(raw: {
    projects: unknown;
    workflows: unknown;
    executions: Array<Record<string, unknown>>;
  }): void {
    const projectResult = isRecord(raw.projects) ? raw.projects : {};
    const workflowResult = isRecord(raw.workflows) ? raw.workflows : {};
    const snapshot = {
      type: "studioState",
      projects: Array.isArray(projectResult.projects) ? projectResult.projects : [],
      currentProjectId: isNonEmptyString(projectResult.current) ? projectResult.current : null,
      workflows: Array.isArray(workflowResult.workflows) ? workflowResult.workflows : [],
      executions: raw.executions,
    };
    this.studioSnapshot = snapshot;
    this.post(snapshot);
    this.scheduleStudioPoll();
  }

  /** Poll watched executions every 5s while any are still running. */
  private scheduleStudioPoll(): void {
    if (this.studioPollTimer) {
      clearTimeout(this.studioPollTimer);
      this.studioPollTimer = undefined;
    }
    const snapshot = this.studioSnapshot;
    if (!snapshot) return;
    const executions = Array.isArray(snapshot.executions) ? snapshot.executions : [];
    const active = executions.some(
      (execution) =>
        isRecord(execution) &&
        !TERMINAL_EXECUTION_STATES.has(String(execution.status ?? "").toLowerCase()),
    );
    if (!active) return;
    this.studioPollTimer = setTimeout(() => {
      void this.refreshStudio().catch(() => undefined);
    }, 5_000);
  }

  /** Fetch one watched execution: typed status + step-level detail. */
  private async fetchStudioExecution(executionId: string): Promise<Record<string, unknown> | null> {
    try {
      const client = await this.getClient();
      const status = await client.request<Record<string, unknown>>("workflows/status", {
        execution_id: executionId,
      });
      let steps: unknown = [];
      try {
        const detail = await client.request<Record<string, unknown>>("workflows/detail", {
          execution_id: executionId,
        });
        steps = extractWorkflowSteps(detail);
      } catch (detailErr) {
        if (isNotFoundError(detailErr)) {
          /* older UMRS without detail endpoint — render without steps */
        } else {
          // #210: surface the fetch problem instead of masquerading missing steps
          return {
            ...status,
            steps: [],
            fetch_error: `detail unavailable: ${errorMessage(detailErr)}`,
          };
        }
      }
      return { ...status, steps };
    } catch (err) {
      if (isNotFoundError(err)) {
        // Execution truly vanished on the backend — drop from watchlist.
        this.watchedExecutions.delete(executionId);
      }
      // #210: transient errors (network blip, 5xx, timeout) must NOT silently
      // drop a running execution — keep watching / keep last-known state.
      return null;
    }
  }

  private async switchStudioProject(projectId: string): Promise<void> {
    const client = await this.getClient();
    const result = await client.request<Record<string, unknown>>("projects/switch", {
      project_id: projectId,
    });
    const workflowResult = await client
      .request<Record<string, unknown>>("workflows/list", {})
      .catch(() => ({ workflows: [] as unknown[] }));
    const executions: Array<Record<string, unknown>> = [];
    for (const executionId of [...this.watchedExecutions]) {
      const execution = await this.fetchStudioExecution(executionId);
      if (execution) executions.push(execution);
    }
    this.postStudioState({ projects: result, workflows: workflowResult, executions });
    // Active project changed — the knowledge graph + loops panels must
    // follow immediately (previously they kept showing the old project
    // until something else triggered a knowledge refresh).
    void this.refreshKnowledge().catch(() => undefined);
  }

  private async createStudioProject(message: {
    projectId: string;
    name?: string;
    gateway?: string;
    model?: string;
  }): Promise<void> {
    const client = await this.getClient();
    const params: Record<string, unknown> = { project_id: message.projectId };
    if (message.name) params.name = message.name;
    if (message.gateway) params.gateway = message.gateway;
    if (message.model) params.model = message.model;
    const result = await client.request<Record<string, unknown>>("projects/create", params);
    const workflowResult = await client
      .request<Record<string, unknown>>("workflows/list", {})
      .catch(() => ({ workflows: [] as unknown[] }));
    this.postStudioState({ projects: result, workflows: workflowResult, executions: [] });
    void this.refreshKnowledge().catch(() => undefined);
  }

  private async runStudioWorkflow(message: {
    workflowName: string;
    parameters?: Record<string, unknown>;
    projectId?: string;
    inputFiles?: string[];
    wait?: boolean;
  }): Promise<void> {
    const client = await this.getClient();
    const params: Record<string, unknown> = {
      workflow_name: message.workflowName,
      wait: Boolean(message.wait),
    };
    if (message.parameters && Object.keys(message.parameters).length > 0) {
      params.parameters = message.parameters;
    }
    if (message.inputFiles && message.inputFiles.length > 0) {
      params.input_files = message.inputFiles;
    }
    if (message.projectId) params.project_id = message.projectId;
    const result = await client.request<Record<string, unknown>>("workflows/trigger", params);
    const executionId = isNonEmptyString(result.execution_id) ? result.execution_id : "";
    if (executionId) this.watchedExecutions.add(executionId);
    await this.refreshStudio();
  }

  /**
   * Describe one workflow (steps + parameter schema) — feeds the planned
   * lane rendered in the Executions graph before the user presses Run.
   */
  private async describeWorkflow(workflowName: string): Promise<void> {
    const client = await this.getClient();
    try {
      const describe = await client.request<Record<string, unknown>>("workflows/describe", {
        workflow_name: workflowName,
      });
      this.post({ type: "workflowDescribe", workflowName, describe });
    } catch (error) {
      this.post({
        type: "workflowDescribe",
        workflowName,
        error: safeErrorMessage(error),
      });
    }
  }

  // ── System tab: skills + MCP servers + command catalog ──────────────────

  /**
   * Skills (loaded/not), MCP servers + tools, slash-command catalog.
   * Each slice fails independently so one broken surface never blanks the tab.
   */
  private async refreshSystem(): Promise<void> {
    const client = await this.getClient();
    const errors: Record<string, string> = {};

    const slice = async <T>(key: string, fn: () => Promise<T>): Promise<T | undefined> => {
      try {
        return await fn();
      } catch (error) {
        errors[key] = safeErrorMessage(error);
        return undefined;
      }
    };

    const skillsPayload = await slice("skills", () =>
      client.request<Record<string, unknown>>("skills/list", {
        session_id: this.viewedSessionId,
      }),
    );
    const commandsPayload = await slice("commands", () =>
      client.request<Record<string, unknown>>("commands/list", {}),
    );
    const serversPayload = await slice("mcpServers", () =>
      client.request<Record<string, unknown>>("mcp/servers", {}),
    );

    // Tools are listed per server; per-server failures degrade to an empty
    // tool list for that server rather than blanking the whole tab.
    let mcpTools: unknown[] = [];
    if (serversPayload && Array.isArray(serversPayload.servers)) {
      const servers = serversPayload.servers as Array<Record<string, unknown>>;
      const toolsNested = await Promise.all(
        servers.map(async (server) => {
          const name = typeof server.name === "string" ? server.name : "";
          if (!name) return [];
          try {
            const res = await client.request<Record<string, unknown>>("mcp/tools", {
              server: name,
            });
            return Array.isArray(res.tools) ? res.tools : [];
          } catch {
            return [];
          }
        }),
      );
      mcpTools = toolsNested.flat();
    }

    this.post({
      type: "systemState",
      skills: skillsPayload?.skills ?? [],
      sessionBound: skillsPayload?.session_bound === true,
      skillsSessionId:
        typeof skillsPayload?.session_id === "string" ? skillsPayload.session_id : undefined,
      commands: commandsPayload?.commands ?? [],
      mcpServers: serversPayload?.servers ?? [],
      mcpTools,
      errors,
    });
  }

  /** Toggle one skill (load/unload) on the viewed companion session. */
  private async setSkillActive(message: {
    sessionId: string;
    name: string;
    active: boolean;
  }): Promise<void> {
    const client = await this.getClient();
    const payload = await client.request<Record<string, unknown>>("skills/set_active", {
      session_id: message.sessionId,
      name: message.name,
      active: message.active,
    });
    this.post({
      type: "systemState",
      skills: payload.skills ?? [],
      sessionBound: true,
      skillsSessionId: typeof payload.session_id === "string" ? payload.session_id : undefined,
      skillsOnly: true,
    });
  }

  /** Health-check one MCP server, then refresh the full system snapshot. */
  private async checkMcpHealth(serverId: string): Promise<void> {
    const client = await this.getClient();
    try {
      await client.request<Record<string, unknown>>("mcp/server_health", {
        server_id: serverId,
      });
    } catch (error) {
      this.post({ type: "error", message: safeErrorMessage(error) });
      return;
    }
    await this.refreshSystem();
  }

  /** M5: run one card-typed management query against the bridge and post the
   *  resulting row. Errors land as status=error rows in the same transcript,
   *  mirroring the notice fallback the TUIs use. */
  private async executeMgmtCardQuery(
    requestId: string,
    sessionId: string,
    kind: string,
    arg?: string,
  ): Promise<void> {
    let params: Record<string, unknown>;
    try {
      params = buildMgmtCardParams(kind, arg);
    } catch (error) {
      this.postMgmtCard(requestId, sessionId, kind, undefined, "error", safeErrorMessage(error));
      return;
    }
    const spec = MGMT_CARD_QUERY_MAP[kind];
    if (!spec) {
      this.postMgmtCard(requestId, sessionId, kind, undefined, "error", `unknown management kind "${kind}"`);
      return;
    }
    try {
      const client = await this.getClient();
      const payload = await client.request<Record<string, unknown>>(spec.method, params);
      if (kind === "knowledge-graph" && payload) {
        // Bridge returns the full content record; the card expects a graph row list.
        this.postMgmtCard(requestId, sessionId, kind, { graphs: [payload] }, "ok");
        return;
      }
      this.postMgmtCard(requestId, sessionId, kind, payload, "ok");
    } catch (error) {
      this.postMgmtCard(requestId, sessionId, kind, undefined, "error", safeErrorMessage(error));
    }
  }

  private postMgmtCard(
    requestId: string,
    sessionId: string,
    kind: string,
    payload: unknown,
    status: "ok" | "error" | "pending",
    error?: string,
  ): void {
    this.post({
      type: "mgmtCard",
      requestId,
      sessionId,
      kind,
      payload,
      status,
      error,
      at: new Date().toISOString(),
    });
  }

  /** A3: push the shared prompt tape to the webview (oldest → newest). */
  private postHistory(): void {
    this.post({ type: "historyState", history: promptHistory() });
  }

  /**
   * Refresh only the knowledge/graph + research-loops slice of the Studio
   * state. Keeps the projects/executions parts of the snapshot untouched
   * so the workflow graph does not re-flicker when the user only probes
   * the research / knowledge views.
   */
  private async refreshKnowledge(): Promise<void> {
    const client = await this.getClient();
    let knowledgeGraph: unknown = null;
    let researchLoops: unknown = { loops: [] };
    const currentProjectId = this.studioSnapshot?.currentProjectId;
    let gatewayOnline = true;
    let verification: unknown = null;
    let reconcile: unknown = null;
    let knowledgeGraphs: unknown = null;
    let knowledgeGraphConstraints: unknown = null;
    if (isNonEmptyString(currentProjectId)) {
      try {
        // Multi-graph registry (0069) — strictly PROJECT-SCOPED. With no
        // project selected there is NO global census (audit B6): the
        // selector must not reveal other projects' graph names, and the
        // satellite fetcher must not pin foreign graphs onto the canvas
        // (constraints ride the same gate — they are the satellite input).
        knowledgeGraphs =
          (
            await client.request<Record<string, unknown>>("knowledge/graphs", {
              project_id: currentProjectId,
            })
          )?.graphs ?? [];
      } catch {
        knowledgeGraphs = null;
      }
      try {
        knowledgeGraphConstraints =
          (
            await client.request<Record<string, unknown>>(
              "knowledge/graph_constraints",
              {},
            )
          )?.constraints ?? null;
      } catch {
        knowledgeGraphConstraints = null;
      }
    } else {
      knowledgeGraphs = [];
      knowledgeGraphConstraints = null;
    }
    if (isNonEmptyString(currentProjectId)) {
      try {
        knowledgeGraph = await client.request<Record<string, unknown>>("knowledge/graph", {
          project_id: currentProjectId,
        });
      } catch {
        knowledgeGraph = null;
        gatewayOnline = false;
      }
      try {
        researchLoops = await client.request<Record<string, unknown>>("research/loops", {
          project_id: currentProjectId,
        });
      } catch {
        researchLoops = { loops: [] };
        gatewayOnline = false;
      }
      try {
        verification = await client.request<Record<string, unknown>>("knowledge/verification", {
          project_id: currentProjectId,
        });
      } catch {
        // Older gateway without verification state — panel hides the card.
        verification = null;
      }
      try {
        reconcile = await client.request<Record<string, unknown>>("knowledge/reconcile", {
          project_id: currentProjectId,
        });
      } catch {
        // Older gateway without the reconciliation route — panel hides the card.
        reconcile = null;
      }
    }
    // Fallback to the local mirror when the gateway is unreachable, and pin
    // gatewayOnline=false on the event so panels show the offline badge.
    if (!gatewayOnline && isNonEmptyString(currentProjectId)) {
      const cached = this._readStudioCache(currentProjectId);
      knowledgeGraph = cached.knowledgeGraph;
      researchLoops = cached.researchLoops;
    } else if (isNonEmptyString(currentProjectId)) {
      // Write-through before the event posts so the mirror exists the moment
      // any consumer of knowledgeState sees the freshest data.
      this._writeStudioCache(currentProjectId, knowledgeGraph, researchLoops);
    }
    // Runtime capabilities ride the research/loops payload (runtime venue,
    // durability, backends) — the loop cards render "what kind of loop".
    let loopRuntime: Record<string, unknown> | null = null;
    if (isRecord(researchLoops)) {
      const { runtime, durable, distributed, backends } = researchLoops;
      if (typeof runtime === "string" || Array.isArray(backends)) {
        loopRuntime = {
          runtime: typeof runtime === "string" ? runtime : null,
          durable: durable === true,
          distributed: distributed === true,
          backends: Array.isArray(backends) ? backends : [],
        };
      }
    }
    // Graph window satellites: selector include list ∪ (discovery && graphs
    // connected to the selected project) − default (already the main
    // constellation) − exclude. Bounded per-graph content for provenance.
    const selectedGraphContents = await this.fetchSelectedGraphContents(
      client,
      knowledgeGraphConstraints,
      knowledgeGraphs,
      isNonEmptyString(currentProjectId) ? currentProjectId : undefined,
    );
    this.post({
      type: "knowledgeState",
      knowledgeGraph,
      researchLoops,
      verification,
      reconcile,
      gatewayOnline,
      projectId: currentProjectId ?? null,
      knowledgeGraphs,
      knowledgeGraphConstraints,
      loopRuntime,
      selectedGraphContents,
    });
  }

  /**
   * Build the graph window's satellite set: the selector's include list,
   * plus (when discovery is on) every active graph connected to the selected
   * project — minus excludes and the project's default graph (already the
   * main constellation). Cap 4 to keep refreshes cheap; per-graph failures
   * are skipped.
   */
  private async fetchSelectedGraphContents(
    client: AcpClient,
    constraints: unknown,
    knowledgeGraphs: unknown,
    projectId: string | undefined,
  ): Promise<unknown[]> {
    if (!isRecord(constraints)) return [];
    const include = new Set(
      Array.isArray(constraints.include) ? constraints.include.filter(isNonEmptyString) : [],
    );
    const exclude = new Set(
      Array.isArray(constraints.exclude) ? constraints.exclude.filter(isNonEmptyString) : [],
    );
    const discovery = constraints.discovery !== false;
    const wanted = this.selectSatelliteGraphs(include, exclude, discovery, knowledgeGraphs, projectId);
    const contents: unknown[] = [];
    for (const name of wanted.slice(0, 4)) {
      try {
        const content = await client.request<Record<string, unknown>>(
          "knowledge/graph_content",
          { graph_id: name },
        );
        contents.push(content);
      } catch {
        // unknown/archived offline graph — skip this satellite silently
      }
    }
    return contents;
  }

  /** include ∪ (discovery ∧ topic-overlap ∧ same-project) − exclude − default. */
  private selectSatelliteGraphs(
    include: ReadonlySet<string>,
    exclude: ReadonlySet<string>,
    discovery: boolean,
    knowledgeGraphs: unknown,
    projectId: string | undefined,
  ): string[] {
    return rankSatelliteGraphs(knowledgeGraphs as unknown[], {
      include,
      exclude,
      discovery,
      projectId,
      terms: this.currentTopicTerms(),
    });
  }

  /**
   * Topic of the CURRENT session: rolling last-6 user messages of the
   * viewed session ranked into terms. Empty buffer ⇒ discovery mounts
   * nothing — the map stays selective-only until the session has a topic.
   */
  private currentTopicTerms(): Set<string> {
    const texts = this.viewedSessionId
      ? (this.topicWindows.get(this.viewedSessionId) ?? [])
      : [];
    return topicTerms(texts);
  }

  /** Append a user message to the session's rolling topic window. */
  private recordTopicText(sessionId: string, text: string): void {
    const window = this.topicWindows.get(sessionId) ?? [];
    window.push(text);
    if (window.length > TOPIC_WINDOW_SIZE) window.shift();
    this.topicWindows.set(sessionId, window);
  }

  /** Science catalogue rows for the Knowledge panel card (read-only). */
  private async searchCatalog(message: {
    query?: string;
    projectId?: string;
  }): Promise<void> {
    const client = await this.getClient();
    const params: Record<string, unknown> = { q: message.query ?? "" };
    if (isNonEmptyString(message.projectId)) params.project_id = message.projectId;
    try {
      const result = await client.request<Record<string, unknown>>("catalog/search", params);
      this.post({ type: "catalogState", query: message.query ?? "", payload: result });
    } catch (error) {
      this.post({
        type: "catalogState",
        query: message.query ?? "",
        error: safeErrorMessage(error),
      });
    }
  }

  private async setKnowledgeGraphConstraints(message: {
    constraints: { include: string[]; exclude: string[]; discovery: boolean };
  }): Promise<void> {
    const client = await this.getClient();
    await client.request<Record<string, unknown>>("knowledge/graph_constraints", {
      include: message.constraints.include,
      exclude: message.constraints.exclude,
      discovery: message.constraints.discovery,
    });
    await this.refreshKnowledge();
  }

  private async cancelStudioExecution(executionId: string): Promise<void> {
    const client = await this.getClient();
    await client.request<Record<string, unknown>>("workflows/cancel", {
      execution_id: executionId,
    });
    await this.refreshStudio();
  }

  /** Start a persistent research loop on the project (Studio button alias). */
  private async startResearchLoop(message: {
    researchGoal: string;
    projectId?: string;
    maxIterations?: number;
    agentic?: boolean;
    requires?: Record<string, unknown>;
    runtimeMode?: string;
  }): Promise<void> {
    const client = await this.getClient();
    const params: Record<string, unknown> = {
      research_goal: message.researchGoal,
      project_id: message.projectId,
    };
    if (typeof message.maxIterations === "number") {
      params.max_iterations = message.maxIterations;
    }
    if (message.agentic === true) params.agentic = true;
    if (message.requires && Object.keys(message.requires).length > 0) {
      params.requires = message.requires;
    }
    if (isNonEmptyString(message.runtimeMode)) {
      params.runtime_mode = message.runtimeMode;
    }
    await client.request<Record<string, unknown>>("research/loop_start", params);
    await this.refreshKnowledge();
  }

  /** Stop a research loop (idempotent; keep the panel fresh). */
  private async stopResearchLoop(message: {
    loopId: string;
    projectId?: string;
  }): Promise<void> {
    const client = await this.getClient();
    await client.request<Record<string, unknown>>("research/loop_stop", {
      loop_id: message.loopId,
      project_id: message.projectId,
    });
    await this.refreshKnowledge();
  }

  /** Ingest user-picked paths plus optional external sources into the project knowledge. */
  private async ingestKnowledge(message: {
    files: string[];
    sources: string[];
    projectId?: string;
  }): Promise<void> {
    const client = await this.getClient();
    const params: Record<string, unknown> = {};
    if (isNonEmptyString(message.projectId)) params.project_id = message.projectId;
    if (Array.isArray(message.files) && message.files.length > 0) params.files = message.files;
    if (Array.isArray(message.sources) && message.sources.length > 0) params.sources = message.sources;
    const result = await client.request<Record<string, unknown>>("knowledge/import", params);
    this.post({ type: "knowledgeIngested", result });
    await this.refreshKnowledge();
  }

  /** File picker on the user's workspace; chips return to the panel. */
  private async pickKnowledgeFiles(): Promise<void> {
    const picks = await vscode.window.showOpenDialog({
      canSelectMany: true,
      canSelectFolders: false,
      title: "Add files to project knowledge",
    });
    if (!picks || picks.length === 0) return;
    const fsPaths = picks.map((u) => u.fsPath).filter(isNonEmptyString);
    if (fsPaths.length === 0) return;
    this.post({ type: "knowledgeFilesPicked", files: fsPaths });
  }

  /** Approve/reject a waiting manual-gate step (W5) from the graph node. */
  private async approveStudioGateStep(
    executionId: string,
    stepName: string,
    approved: boolean,
  ): Promise<void> {
    const client = await this.getClient();
    await client.request<Record<string, unknown>>("workflows/approve_gate", {
      execution_id: executionId,
      step_name: stepName,
      approved,
    });
    await this.refreshStudio();
  }

  private async deleteSession(sessionId: string): Promise<void> {
    const client = await this.getClient();
    this.cancelRequest(sessionId);
    await client.request("sessions/delete", { session_id: sessionId });
    if (this.viewedSessionId === sessionId) this.viewedSessionId = undefined;
    this.post({ type: "sessionDeleted", sessionId });
    await this.refreshSessions(client);
  }

  /** Open a file in the editor when the user clicks a tool-call chip. */
  private async openFileAtPath(path: string): Promise<void> {
    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(path));
    await vscode.window.showTextDocument(document, { preview: true });
  }

  private async send(text: string, sessionId?: string, model?: string): Promise<void> {
    const targetId = sessionId ?? this.viewedSessionId;
    if (targetId && this.activeRequests.has(targetId)) {
      this.post({ type: "error", message: "This chat is already responding.", sessionId: targetId });
      return;
    }
    if (!targetId && this.newChatInFlight) {
      this.post({ type: "error", message: "A new chat is already starting." });
      return;
    }
    // Reserve the new-chat slot synchronously so rapid id-less sends can't race
    // past the guard before the first send awaits.
    if (!targetId) this.newChatInFlight = true;
    // A3: every accepted send joins the shared cross-surface prompt tape
    // (same append semantics as v3 / tui-js — dedup push-back, cap 50).
    appendPromptHistory(text);
    this.postHistory();
    const client = await this.getClient();
    // Resolve to a concrete session id up front so concurrent sends route and
    // gate correctly, and so the endpoint binding is applied before the turn.
    let resolvedId = targetId;
    if (!resolvedId) {
      try {
        const session = await this.createSessionOnServer(client, model);
        resolvedId = session.id;
        this.viewedSessionId = resolvedId;
        this.post({ type: "sessionSelected", session, fresh: true, messages: [] });
      } finally {
        this.newChatInFlight = false;
      }
    } else if (model) {
      await client.request<SessionResult>("sessions/set_model", {
        session_id: resolvedId,
        model,
      });
    }
    // The picker left on "Default model" must still leave the session with a
    // usable model bound — resolved from the session itself or the first
    // ready endpoint — so the agent never starts a turn with no model.
    if (!model) {
      const fallback = await this.resolveDefaultModel(client, resolvedId);
      if (fallback) {
        await client.request<SessionResult>("sessions/set_model", {
          session_id: resolvedId,
          model: fallback,
        });
      }
    }

    const AbortControllerClass = (
      globalThis as unknown as { AbortController: new () => AbortControllerLike }
    ).AbortController;
    const controller = new AbortControllerClass();
    const requestId = (
      globalThis as unknown as { crypto: { randomUUID(): string } }
    ).crypto.randomUUID();
    this.activeRequests.set(resolvedId, { abort: () => controller.abort(), requestId });
    this.requestOwners.set(requestId, resolvedId);
    this.post({ type: "userMessage", sessionId: resolvedId, text });
    this.post({ type: "sessionState", sessionId: resolvedId, state: "running" });
    this.recordTopicText(resolvedId, text);
    try {
      const params: Record<string, unknown> = {
        message: text,
        request_id: requestId,
        session_id: resolvedId,
      };
      const result = await client.request<ChatResult>("chat/send", params, {
        signal: controller.signal,
        // Generous cap: the agent streams session/progress keepalives during
        // healthy turns, and the ACP client resets its request timer on any
        // notification tagged with this request_id. The timeout only fires
        // for turns that go completely silent (wedged/unresponsive agent), so
        // long knowledge/research turns are no longer killed mid-run.
        timeoutMs: 30 * 60_000,
      });
      this.post({ type: "assistantComplete", sessionId: result.session_id, text: result.response });
      this.post({ type: "sessionState", sessionId: result.session_id, state: "idle" });
      // Drop the request bookkeeping BEFORE refreshSessions so the sessions
      // snapshot's "running" list no longer contains this session. Otherwise
      // the webview would re-mark a just-finished turn (including turns whose
      // response is an error string returned by the agent, e.g.
      // "Failed to get a response: …") as running, leaving the Send button
      // stuck behind a Stop button and preventing a model switch + retry in
      // the same session.
      this.activeRequests.delete(resolvedId);
      this.requestOwners.delete(requestId);
      await this.refreshSessions(client);
      // Dense chat↔Studio link: a completed turn may have created a project,
      // ingested knowledge, or started loops via agent tools. Re-pull the
      // Studio snapshot (which also refreshes knowledge/verification) so the
      // Studio tab reflects chat-side mutations without manual refresh.
      void this.refreshStudio().catch(() => undefined);
      // refreshStudio fires refreshKnowledge itself, which re-ranks satellites
      // against the now-longer topic window (recorded at send time).
    } catch (error) {
      if (error instanceof Error && error.name === "AbortError") {
        this.post({ type: "cancelled", sessionId: resolvedId });
        this.post({ type: "sessionState", sessionId: resolvedId, state: "idle" });
      } else {
        this.post({ type: "error", message: safeErrorMessage(error), sessionId: resolvedId });
        this.post({ type: "sessionState", sessionId: resolvedId, state: "error" });
        // Only tear down the agent process when the transport itself is
        // broken. An app-level chat error (AcpRequestError) leaves other
        // sessions intact and the webview connected.
        if (this.isTransportFailure(error)) {
          this.resetTransport();
          this.post({ type: "status", status: "disconnected" });
        }
      }
    } finally {
      this.activeRequests.delete(resolvedId);
      this.requestOwners.delete(requestId);
    }
  }

  /** Whether an error means the ACP transport is broken (not an app error). */
  private isTransportFailure(error: unknown): boolean {
    // A JSON-RPC error reply from the agent is an application-level failure —
    // the pipe is alive, the agent just rejected that one request. The name
    // check is a duck-typed fallback so subclasses/cross-bundle errors still
    // classify correctly.
    if (
      error instanceof AcpRequestError ||
      (error instanceof Error && error.name === "AcpRequestError")
    ) {
      return false;
    }
    // Abort/cancel is handled separately upstream.
    if (error instanceof Error && error.name === "AbortError") {
      return false;
    }
    // Everything else (spawn failure, closed pipe, JSON parse protocol
    // errors, timeouts) is treated as a transport failure so the next
    // request respawns a fresh supervisor.
    return true;
  }

  private async restart(): Promise<void> {
    this.cancelAllRequests();
    this.resetTransport();
    await this.initialize();
  }

  /** Round-3 task 7: warn when the ACP child's source tree drifted after
   *  the child started (editable installs keep loading code it compiled
   *  earlier). One line into the webview store + extension log. */
  private warnOnStaleSource(supervisor: AcpProcessSupervisor): void {
    const identity = supervisor.sourceIdentity;
    if (!identity) return;
    let stale = false;
    try {
      stale = identityMismatch(identity);
    } catch {
      stale = false;
    }
    if (!stale) return;
    const text = `r4 source changed since this ACP process started (expected digest ${identity.digest}) — reloading gives the model fresh code.`;
    console.warn(`[r4] ${text}`);
    this.post({ type: "sourceStale", text });
  }

  private async getClient(): Promise<AcpClient> {
    if (this.client) return this.client;
    this.supervisor = this.createSupervisor();
    const client = await this.supervisor.start();
    this.client = client;
    this.warnOnStaleSource(this.supervisor);
    this.removeNotificationListener = client.onNotification((notification) => {
      this.handleNotification(notification);
    });
    this.removeApprovalMethod = client.registerMethod("session/toolApproval", async (params) => {
      const decision = await showCompanionToolApproval(parseCompanionToolApproval(params));
      return { decision };
    });
    this.removeAskUserMethod = client.registerMethod("session/askUser", async (params) => {
      const answer = await showCompanionAskUser(parseCompanionAskUser(params));
      return { answer };
    });
    const toolBridge = await this.createToolBridge();
    if (toolBridge) {
      this.toolBridge = toolBridge;
      toolBridge.setStateListener((request, status) => {
        if (!request.requestId) return;
        const owner = this.requestOwners.get(request.requestId);
        if (!owner || owner !== request.sessionId) return;
        this.post({ type: "toolState", sessionId: request.sessionId, tool: request.name, status });
      });
      this.removeToolMethod = client.registerMethod("editor/toolCall", (params) =>
        toolBridge.call(parseAgentToolRequest(params)),
      );
      await client.request("editor/tools/register", { tools: toolBridge.listTools() });
    }
    return client;
  }

  private handleNotification(notification: AcpNotification): void {
    if (notification.method === "editor/toolCancel") {
      const requestId = notification.params.request_id;
      if (typeof requestId === "string") this.toolBridge?.cancel(requestId);
      return;
    }
    const sessionId = notification.params.session_id;
    const requestId = notification.params.request_id;
    if (typeof sessionId !== "string" || typeof requestId !== "string") return;
    if (this.requestOwners.get(requestId) !== sessionId) return;
    if (notification.method === "session/text") {
      const text = notification.params.text;
      if (typeof text === "string") this.post({ type: "assistantDelta", sessionId, text });
      return;
    }
    if (notification.method === "session/toolState") {
      const tool = notification.params.tool;
      const status = notification.params.status;
      if (typeof tool === "string" && typeof status === "string") {
        this.post({ type: "toolState", sessionId, tool, status });
      }
      return;
    }
    // Canonical T1 tool-call envelopes (payload carries sanitized fields);
    // legacy flat params remain supported for older r4 cores.
    if (notification.method === "session/tool_call_start") {
      const payload = (notification.params.payload ?? notification.params) as Record<string, unknown>;
      this.post({
        type: "toolCall",
        sessionId,
        toolCallId: String(payload?.tool_call_id ?? ""),
        name: String(payload?.name ?? ""),
        argsSummary: typeof payload?.args_summary === "string" ? payload.args_summary : undefined,
        status: "pending",
      });
      return;
    }
    if (notification.method === "session/tool_call_end" || notification.method === "session/tool_call_done") {
      const payload = (notification.params.payload ?? notification.params) as Record<string, unknown>;
      const doneName = String(payload?.name ?? "");
      const rawStatus = String(payload?.status ?? "completed");
      // Canonical rail reports "done"; webview cards speak "completed".
      const status = rawStatus === "done" ? "completed" : rawStatus;
      this.post({
        type: "toolCall",
        sessionId,
        toolCallId: String(payload?.tool_call_id ?? ""),
        name: doneName,
        status,
        resultPreview: payload?.result_preview,
        resultTruncated: Boolean(payload?.result_truncated),
        durationMs: payload?.duration_ms,
      });
      // Knowledge/project mutations from agent tools should land on the
      // Studio surfaces immediately (graph nodes, verification, project list)
      // — refresh without waiting for the whole turn to finish.
      if (/^(knowledge_|research_loop_|research_session)/.test(doneName)) {
        void this.refreshKnowledge().catch(() => undefined);
      } else if (/^project_/.test(doneName)) {
        void this.refreshStudio().catch(() => undefined);
      }
      return;
    }
    // C6: usage event
    if (notification.method === "session/usage") {
      this.post({
        type: "usage",
        sessionId,
        promptTokens: notification.params.prompt_tokens,
        completionTokens: notification.params.completion_tokens,
        totalTokens: notification.params.total_tokens,
      });
      return;
    }
    // Wave T2: canonical budget read-model (control-schema budget_state).
    if (notification.method === "session/budget_state") {
      const payload = notification.params.payload;
      if (payload && typeof payload === "object") {
        this.post({ type: "budgetState", sessionId, payload });
      }
      return;
    }
    // C3: per-turn token/cost/context chip read-model (the CompanionSession
    // cost tracker; dedicated method, additive like budget_state).
    if (notification.method === "session/cost") {
      const payload = notification.params.payload ?? notification.params;
      if (payload && typeof payload === "object") {
        this.post({ type: "costState", sessionId, payload });
      }
      return;
    }
    // Thinking block (partial flag preserved: live snapshots render
    // expanded until the block closes with partial=false).
    if (notification.method === "session/thinking") {
      const content = notification.params.content;
      // Canonical trace wins: suppress the legacy block for sessions flowing
      // canonical deltas — otherwise both renderers show the same reasoning.
      if (this.canonicalThinkingSessions.has(sessionId)) return;
      if (typeof content === "string") {
        this.post({
          type: "sessionThinking",
          sessionId,
          content,
          partial: Boolean(notification.params.partial),
        });
      }
      return;
    }
    // Canonical T1 transparency envelopes (payload envelope; wave T3-ui S3).
    if (notification.method === "session/thinking_delta" || notification.method === "session/thinking_done") {
      const payload = notification.params.payload as Record<string, unknown> | undefined;
      if (payload) {
        this.canonicalThinkingSessions.add(sessionId);
        this.post({
          type: notification.method === "session/thinking_delta" ? "thinkingDelta" : "thinkingDone",
          sessionId,
          payload,
        });
      }
      return;
    }
    if (notification.method !== "session/event") return;
    this.forwardAgentEvent(sessionId, notification.params.event ?? {});
  }

  /**
   * B2b-full: reclassify free-form agent event payloads into typed protocol
   * events (plan/diff/citation) when the payload shape is recognizable;
   * otherwise fall back to the generic `agentEvent` for back-compat.
   */
  private forwardAgentEvent(sessionId: string, event: Record<string, any>): void {
    const eventType = String(event.event_type ?? "");
    const data = (event.data as Record<string, any> | null) ?? {};

    if (eventType === "shell_output") {
      this.post({
        type: "shellStream",
        sessionId,
        streamId: String(data.stream_id ?? ""),
        chunk: typeof event.message === "string" ? event.message : "",
      });
      return;
    }

    if (eventType === "shell_stream_end") {
      // Terminal lifecycle marker from the streaming executor — moves the
      // panel chip out of `running` (hides the Cancel chip → no late
      // `not-found` acks from shell/cancel).
      this.post({
        type: "shellStreamStatus",
        sessionId,
        streamId: String(data.stream_id ?? ""),
        status: typeof data.status === "string" ? data.status : "done",
      });
      return;
    }

    if (eventType === "planning_start" || eventType === "planning_complete") {
      this.post({
        type: "planEvent",
        sessionId,
        plan: {
          phase: eventType === "planning_start" ? "start" : "complete",
          steps: Array.isArray(data.steps) ? data.steps.map(normalizePlanStep) : undefined,
          summary: typeof event.message === "string" ? event.message : undefined,
        },
      });
      return;
    }

    if (eventType === "step_start" || eventType === "step_complete" || eventType === "step_failed") {
      this.post({
        type: "planEvent",
        sessionId,
        plan: {
          phase: "complete",
          steps: [
            {
              id: String(data.step_id ?? data.id ?? "step"),
              description: String(event.message ?? data.description ?? ""),
              status:
                eventType === "step_start"
                  ? "running"
                  : eventType === "step_failed"
                    ? "failed"
                    : "done",
            },
          ],
        },
      });
      return;
    }

    if (eventType === "tool_selection") {
      // ADR-037 selection telemetry: route to the dedicated roll-up event
      // instead of the generic agentEvent fallback — raw records rendered
      // as "event: all N tools offered (no selection)" rows (one per model
      // call) and leaked instrumentation into the transcript.
      this.post({
        type: "toolSelection",
        sessionId,
        payload: (data as Record<string, unknown>) ?? {},
      });
      return;
    }

    if (data.diff || data.edits || data.patch) {
      this.post({
        type: "diffEvent",
        sessionId,
        diff: normalizeDiffEvent(data),
      });
      return;
    }

    if (Array.isArray(data.citations) && data.citations.length > 0) {
      this.post({
        type: "citationEvent",
        sessionId,
        citations: data.citations.map(normalizeCitation),
      });
      return;
    }

    this.post({ type: "agentEvent", sessionId, event });
  }

  private async refreshSessions(client: AcpClient): Promise<void> {
    const result = await client.request<SessionsResult>("sessions/list");
    this.post({
      type: "sessions",
      sessions: normalizeSessions(result.sessions),
      running: [...this.activeRequests.keys()],
    });
  }

  private async refreshInventory(): Promise<void> {
    this.post(await this.computeInventory());
  }

  /** The model picker's inventory (also posted to each chat tab, VP-3b). */
  private async computeInventory(): Promise<Record<string, unknown>> {
    const configs = this.listSourceConfigs();
    // Aggregate ready models across EVERY configured source — including the
    // single-source case. Each option is tagged with the r4 cross-source
    // notation ``model@source`` so the ACP agent routes the chat to the
    // exact source (and its credentials) the user picked, instead of
    // falling back to the r4 config's default gateway. Without the
    // ``@source`` suffix, a lone active public source would be routed to
    // the platform gateway and surface as "Authentication failed" /
    // "Non-JSON response" because the r4 config only overlays platform
    // sources (see r4/src/r4/config.py from_env).
    if (configs.length > 0) {
      const endpoints = await collectSourceEndpoints(configs);
      const options = dedupeEndpointOptions(endpoints);
      this.lastEndpoints = options;
      return { type: "inventory", endpoints: options, connected: endpoints.length > 0 };
    }

    // Fallback for setups with no sources.json (env-only / r4.gatewayUrl
    // setting). Models carry no @source suffix; the r4 agent routes them
    // through its own config (R4_GATEWAY_URL / R4_ENDPOINT_URL).
    const gateway = this.getGatewayClient();
    if (!gateway) {
      this.lastEndpoints = [];
      return { type: "inventory", endpoints: [], connected: false };
    }
    try {
      const endpoints = await gateway.listEndpoints();
      const readyEndpoints = endpoints.filter(
        (endpoint) => endpoint.status.toLowerCase() === "ready",
      );
      const options = dedupeEndpointOptions(readyEndpoints.map((ep) => toEndpointOption(ep)));
      this.lastEndpoints = options;
      return { type: "inventory", endpoints: options, connected: true };
    } catch {
      this.lastEndpoints = [];
      return { type: "inventory", endpoints: [], connected: false };
    }
  }

  /** What a chat tab needs at start: the model inventory and the prompt tape. */
  async startupMessages(): Promise<Record<string, unknown>[]> {
    return [await this.computeInventory(), { type: "historyState", history: promptHistory() }];
  }

  /**
   * Resolve a usable chat model when the picker is left on "Default model".
   * Order: the session's own assigned model → first ready endpoint.
   * Without this, the chat reaches the agent with no model and the user only
   * sees an opaque "Internal error" instead of an answer.
   */
  private async resolveDefaultModel(
    client: AcpClient,
    sessionId: string,
  ): Promise<string | undefined> {
    try {
      const result = await client.request<SessionResult>("sessions/load", { session_id: sessionId });
      if (isNonEmptyString(result.session.model)) return result.session.model;
    } catch {
      // Fall through to endpoint defaults — the load failure is non-fatal
      // because the endpoint list below may still hold a usable model.
    }
    const ready = this.lastEndpoints.find((ep) => ep.id && ep.status.toLowerCase() === "ready");
    return ready?.id;
  }

  private cancelRequest(sessionId?: string): void {
    const target = sessionId ?? this.viewedSessionId;
    if (!target) return;
    const active = this.activeRequests.get(target);
    if (!active) return;
    // Aborting mid-write races the tool handler: discard the aborted turn's
    // staged proposals immediately so cancelled work never overwrites an
    // accepted edit later.
    void this.editSession?.cancelSession(target);
    active.abort();
  }

  private cancelAllRequests(): void {
    for (const active of this.activeRequests.values()) active.abort();
    this.activeRequests.clear();
    if (this.editSession) {
      // Cancel-all: drop every staged proposal — the aborted turns that staged
      // them will never deliver their results.
      for (const edit of this.editSession.store.listPending()) {
        void this.editSession.reject(edit.id);
      }
    }
    this.requestOwners.clear();
    this.newChatInFlight = false;
  }

  private resetTransport(): void {
    this.removeNotificationListener?.();
    this.removeNotificationListener = undefined;
    this.removeToolMethod?.();
    this.removeToolMethod = undefined;
    this.removeApprovalMethod?.();
    this.removeApprovalMethod = undefined;
    this.removeAskUserMethod?.();
    this.removeAskUserMethod = undefined;
    this.toolBridge = undefined;
    this.supervisor?.dispose();
    this.supervisor = undefined;
    this.client = undefined;
    this.activeRequests.clear();
    this.requestOwners.clear();
    this.newChatInFlight = false;
  }

  private post(message: Record<string, unknown>): void {
    void this.view?.webview.postMessage(message);
  }

  /**
   * Build the HTML document for the bundled React webview.
   *
   * The message token is handed to the app through a `data-nonce` attribute
   * rather than an inline script, so `script-src` can stay nonce-locked with
   * no `'unsafe-inline'`.
   */
  private _buildWebViewHtml(
    webview: vscode.Webview,
    root: vscode.Uri,
    nonce: string,
  ): string {
    return buildWebviewHtml(webview, root, nonce);
  }
}

function parseViewMessage(value: unknown): ViewMessage | undefined {
  if (!isRecord(value) || typeof value.type !== "string") return undefined;
  const parser = viewMessageParsers[value.type];
  return parser ? parser(value) : undefined;
}

type ViewMessageParser = (value: Record<string, unknown>) => ViewMessage | undefined;

type SimpleViewMessageType = Extract<
  ViewMessage["type"],
  "ready" | "refresh" | "login" | "restart" | "refreshStudio" | "refreshKnowledge" | "pickKnowledgeFiles"
>;

function parseSimpleMessage(type: SimpleViewMessageType): () => ViewMessage {
  return () => ({ type } as ViewMessage);
}

/** Cancel matches with or without a sessionId. */
function parseCancelMessage(value: Record<string, unknown>): ViewMessage | undefined {
  return isNonEmptyString(value.sessionId)
    ? { type: "cancel", sessionId: value.sessionId.trim() }
    : { type: "cancel" };
}

/** shellCancel requires streamId; sessionId is an optional hint for the status post. */
function parseShellCancelMessage(value: Record<string, unknown>): ViewMessage | undefined {
  return isNonEmptyString(value.streamId)
    ? { type: "shellCancel", streamId: value.streamId.trim(), sessionId: isNonEmptyString(value.sessionId) ? value.sessionId.trim() : undefined }
    : undefined;
}

/** newSession matches with or without a model. */
function parseNewSessionMessage(value: Record<string, unknown>): ViewMessage | undefined {
  return isNonEmptyString(value.model)
    ? { type: "newSession", model: value.model.trim() }
    : { type: "newSession" };
}

/** selectSession requires a sessionId. */
function parseSelectSessionMessage(value: Record<string, unknown>): ViewMessage | undefined {
  return isNonEmptyString(value.sessionId)
    ? { type: "selectSession", sessionId: value.sessionId.trim() }
    : undefined;
}

/** Send requires non-empty text within the 100k char budget; ids are optional. */
function parseSendMessage(value: Record<string, unknown>): ViewMessage | undefined {
  if (!isNonEmptyString(value.text) || value.text.length > 100_000) return undefined;
  const message: ViewMessage = { type: "send", text: value.text.trim() };
  if (isNonEmptyString(value.sessionId)) message.sessionId = value.sessionId.trim();
  if (isNonEmptyString(value.model)) message.model = value.model.trim();
  return message;
}

/** renameSession requires both sessionId and name. */
function parseRenameSessionMessage(value: Record<string, unknown>): ViewMessage | undefined {
  if (!isNonEmptyString(value.sessionId) || !isNonEmptyString(value.name)) return undefined;
  return { type: "renameSession", sessionId: value.sessionId.trim(), name: value.name.trim().slice(0, 120) };
}

/** deleteSession requires a sessionId. */
function parseDeleteSessionMessage(value: Record<string, unknown>): ViewMessage | undefined {
  return isNonEmptyString(value.sessionId)
    ? { type: "deleteSession", sessionId: value.sessionId.trim() }
    : undefined;
}

/** C2: pin/unpin carry only a sessionId (store-direct call). */
function parsePinSessionMessage(kind: 'pinSession' | 'unpinSession') {
  return (value: Record<string, unknown>): ViewMessage | undefined =>
    isNonEmptyString(value.sessionId)
      ? { type: kind, sessionId: value.sessionId.trim() }
      : undefined;
}

/** C4: /compact carries sessionId (+ optional keepRecent). */
function parseCompactSessionMessage(value: Record<string, unknown>): ViewMessage | undefined {
  if (!isNonEmptyString(value.sessionId)) return undefined;
  const keepRecent =
    typeof value.keepRecent === "number" && value.keepRecent > 0
      ? Math.floor(value.keepRecent)
      : undefined;
  return { type: "compactSession", sessionId: value.sessionId.trim(), keepRecent };
}

/** setModel requires a sessionId; model may be omitted to clear. */
function parseSetModelMessage(value: Record<string, unknown>): ViewMessage | undefined {
  if (!isNonEmptyString(value.sessionId)) return undefined;
  return {
    type: "setModel",
    sessionId: value.sessionId.trim(),
    model: isNonEmptyString(value.model) ? value.model.trim() : undefined,
  };
}

/** setMode requires a sessionId and a known mode. */
function parseSetModeMessage(value: Record<string, unknown>): ViewMessage | undefined {
  if (!isNonEmptyString(value.sessionId) || !isNonEmptyString(value.mode)) return undefined;
  const mode = value.mode.trim().toLowerCase();
  if (!STUDIO_MODES.has(mode)) return undefined;
  return { type: "setMode", sessionId: value.sessionId.trim(), mode };
}

/** switchProject requires a projectId. */
function parseSwitchProjectMessage(value: Record<string, unknown>): ViewMessage | undefined {
  return isNonEmptyString(value.projectId)
    ? { type: "switchProject", projectId: value.projectId.trim() }
    : undefined;
}

/** createProject requires a projectId; name/gateway/model optional. */
function parseCreateProjectMessage(value: Record<string, unknown>): ViewMessage | undefined {
  if (!isNonEmptyString(value.projectId)) return undefined;
  const message: ViewMessage = { type: "createProject", projectId: value.projectId.trim() };
  if (isNonEmptyString(value.name)) message.name = value.name.trim();
  if (isNonEmptyString(value.gateway)) message.gateway = value.gateway.trim();
  if (isNonEmptyString(value.model)) message.model = value.model.trim();
  return message;
}

/** runWorkflow requires a workflowName; parameters must be an object. */
function parseRunWorkflowMessage(value: Record<string, unknown>): ViewMessage | undefined {
  if (!isNonEmptyString(value.workflowName)) return undefined;
  const message: ViewMessage = { type: "runWorkflow", workflowName: value.workflowName.trim() };
  if (isRecord(value.parameters)) message.parameters = value.parameters;
  if (isNonEmptyString(value.projectId)) message.projectId = value.projectId.trim();
  if (Array.isArray(value.inputFiles)) {
    message.inputFiles = value.inputFiles.filter(isNonEmptyString);
  }
  if (typeof value.wait === "boolean") message.wait = value.wait;
  return message;
}

/** cancelExecution / removeWatch require an executionId. */
function parseExecutionOnlyMessage(
  type: "cancelExecution" | "removeWatch",
): (value: Record<string, unknown>) => ViewMessage | undefined {
  return (value) =>
    isNonEmptyString(value.executionId)
      ? ({ type, executionId: value.executionId.trim() } as ViewMessage)
      : undefined;
}

/** Edit-review actions carry just an edit id. */
function parseEditIdMessage(
  type: "acceptEdit" | "rejectEdit" | "reviewEdit",
): (value: Record<string, unknown>) => ViewMessage | undefined {
  return (value) =>
    isNonEmptyString(value.editId)
      ? ({ type, editId: value.editId.trim() } as ViewMessage)
      : undefined;
}

/** startResearchLoop needs a goal; project id + max-iterations are optional. */
function parseStartResearchLoopMessage(value: Record<string, unknown>): ViewMessage | undefined {
  if (!isNonEmptyString(value.researchGoal)) return undefined;
  const message: ViewMessage = {
    type: "startResearchLoop",
    researchGoal: value.researchGoal.trim(),
  };
  if (isNonEmptyString(value.projectId)) message.projectId = value.projectId.trim();
  if (typeof value.maxIterations === "number" && value.maxIterations > 0) {
    message.maxIterations = Math.floor(value.maxIterations);
  }
  return message;
}

/** stopResearchLoop needs the loop_id; project id is optional. */
function parseStopResearchLoopMessage(value: Record<string, unknown>): ViewMessage | undefined {
  if (!isNonEmptyString(value.loopId)) return undefined;
  const message: ViewMessage = { type: "stopResearchLoop", loopId: value.loopId.trim() };
  if (isNonEmptyString(value.projectId)) message.projectId = value.projectId.trim();
  return message;
}

/** ingestKnowledge accepts file paths and/or free-text sources. */
function parseIngestKnowledgeMessage(value: Record<string, unknown>): ViewMessage | undefined {
  const files = Array.isArray(value.files)
    ? value.files.filter(isNonEmptyString)
    : [];
  const sources = Array.isArray(value.sources)
    ? value.sources.filter(isNonEmptyString)
    : [];
  if (files.length === 0 && sources.length === 0) return undefined;
  const message: ViewMessage = { type: "ingestKnowledge", files, sources };
  if (isNonEmptyString(value.projectId)) message.projectId = value.projectId;
  return message;
}

/** catalogSearch: free-form query (may be empty = full project listing). */
function parseCatalogSearchMessage(value: Record<string, unknown>): ViewMessage {
  const message: ViewMessage = {
    type: "catalogSearch",
    query: typeof value.query === "string" ? value.query.slice(0, 400) : "",
  };
  if (isNonEmptyString(value.projectId)) message.projectId = value.projectId.trim();
  return message;
}

/** approveGateStep requires executionId + stepName; approved defaults to true. */
function parseApproveGateMessage(value: Record<string, unknown>): ViewMessage | undefined {
  if (!isNonEmptyString(value.executionId) || !isNonEmptyString(value.stepName)) return undefined;
  return {
    type: "approveGateStep",
    executionId: value.executionId.trim(),
    stepName: value.stepName.trim(),
    approved: typeof value.approved === "boolean" ? value.approved : true,
  };
}

// ── Management card queries (M5) ───────────────────────────────────────────
// Map + kind set live in the pure module ./mgmt-card-map so the unit test
// can load them without importing vscode.

/** mgmtCardQuery — the acknowledged card row keys on (requestId, sessionId). */
function parseMgmtCardQueryMessage(value: Record<string, unknown>): ViewMessage | undefined {
  if (!isNonEmptyString(value.requestId) || !isNonEmptyString(value.sessionId)) return undefined;
  const kind = typeof value.kind === "string" ? value.kind.trim() : "";
  if (!MGMT_KINDS.has(kind)) return undefined;
  const arg = typeof value.arg === "string" && value.arg.trim() ? value.arg.trim() : undefined;
  return { type: "mgmtCardQuery", requestId: value.requestId.trim(), sessionId: value.sessionId.trim(), kind, arg };
}

/** openFile requires a bounded local path. */
function parseOpenFileMessage(value: Record<string, unknown>): ViewMessage | undefined {
  if (!isNonEmptyString(value.path) || value.path.length > 4096) return undefined;
  return { type: "openFile", path: value.path.trim() };
}

/** Multi-graph selector constraints — bounded string arrays + discovery flag. */
function parseSetKnowledgeGraphConstraintsMessage(
  value: Record<string, unknown>,
): ViewMessage | undefined {
  const constraints = isRecord(value.constraints) ? value.constraints : undefined;
  if (!constraints) return undefined;
  const include = Array.isArray(constraints.include) ? constraints.include : [];
  const exclude = Array.isArray(constraints.exclude) ? constraints.exclude : [];
  return {
    type: "setKnowledgeGraphConstraints",
    constraints: {
      include: include.filter((g): g is string => typeof g === "string").slice(0, 50),
      exclude: exclude.filter((g): g is string => typeof g === "string").slice(0, 50),
      discovery: constraints.discovery !== false,
    },
  };
}

const viewMessageParsers: Record<string, ViewMessageParser> = {
  ready: parseSimpleMessage("ready"),
  refresh: parseSimpleMessage("refresh"),
  login: parseSimpleMessage("login"),
  restart: parseSimpleMessage("restart"),
  refreshStudio: parseSimpleMessage("refreshStudio"),
  refreshKnowledge: parseSimpleMessage("refreshKnowledge"),
  shellCancel: parseShellCancelMessage,
  startResearchLoop: parseStartResearchLoopMessage,
  stopResearchLoop: parseStopResearchLoopMessage,
  ingestKnowledge: parseIngestKnowledgeMessage,
  pickKnowledgeFiles: parseSimpleMessage("pickKnowledgeFiles"),
  catalogSearch: parseCatalogSearchMessage,
  setKnowledgeGraphConstraints: parseSetKnowledgeGraphConstraintsMessage,
  cancel: parseCancelMessage,
  newSession: parseNewSessionMessage,
  openFile: parseOpenFileMessage,
  acceptEdit: parseEditIdMessage("acceptEdit"),
  rejectEdit: parseEditIdMessage("rejectEdit"),
  reviewEdit: parseEditIdMessage("reviewEdit"),
  selectSession: parseSelectSessionMessage,
  send: parseSendMessage,
  renameSession: parseRenameSessionMessage,
  deleteSession: parseDeleteSessionMessage,
  pinSession: parsePinSessionMessage('pinSession'),
  unpinSession: parsePinSessionMessage('unpinSession'),
  compactSession: parseCompactSessionMessage,
  setModel: parseSetModelMessage,
  setMode: parseSetModeMessage,
  switchProject: parseSwitchProjectMessage,
  createProject: parseCreateProjectMessage,
  runWorkflow: parseRunWorkflowMessage,
  cancelExecution: parseExecutionOnlyMessage("cancelExecution"),
  removeWatch: parseExecutionOnlyMessage("removeWatch"),
  approveGateStep: parseApproveGateMessage,
  mgmtCardQuery: parseMgmtCardQueryMessage,
  toolResultRequest: parseToolResultRequestMessage,
};

/** toolResultRequest requires session + call id (expand fetch for task 2). */
function parseToolResultRequestMessage(value: Record<string, unknown>): ViewMessage | undefined {
  return isNonEmptyString(value.sessionId) && isNonEmptyString(value.toolCallId)
    ? { type: "toolResultRequest", sessionId: value.sessionId.trim(), toolCallId: value.toolCallId.trim() }
    : undefined;
}

/** Execution statuses that stop the studio poll timer. */
const TERMINAL_EXECUTION_STATES = new Set([
  "completed",
  "succeeded",
  "done",
  "failed",
  "error",
  "cancelled",
  "terminated",
  "stopped",
]);

/** Pull step-level statuses out of a workflows/detail payload. */
function extractWorkflowSteps(detail: unknown): unknown[] {
  if (!isRecord(detail)) return [];
  const execution = isRecord(detail.execution) ? detail.execution : detail;
  const results = isRecord(execution.results) ? execution.results : {};
  const raw = results.steps ?? (execution as Record<string, unknown>).steps;
  if (Array.isArray(raw)) return raw;
  if (isRecord(raw)) {
    // Dict form: { stepName: {status, ...} } → array with injected name.
    return Object.entries(raw).map(([name, value]) =>
      isRecord(value) ? { name, ...value } : { name, status: String(value) },
    );
  }
  return [];
}

function parseCompanionAskUser(value: Record<string, unknown>): CompanionAskUserParams {
  if (!isNonEmptyString(value.session_id) || !isNonEmptyString(value.question)) {
    throw new Error("Invalid companion ask_user request");
  }
  const options = Array.isArray(value.options)
    ? value.options.filter(isNonEmptyString).map((o) => o.trim())
    : [];
  return {
    session_id: value.session_id.trim(),
    request_id: isNonEmptyString(value.request_id) ? value.request_id.trim() : undefined,
    question: value.question.trim(),
    options,
  };
}

function parseCompanionToolApproval(value: Record<string, unknown>): CompanionToolApprovalParams {
  if (
    !isNonEmptyString(value.session_id)
    || !isNonEmptyString(value.request_id)
    || !isNonEmptyString(value.approval_id)
    || !isNonEmptyString(value.tool)
  ) {
    throw new Error("Invalid companion tool approval request");
  }
  const args = isRecord(value.arguments) ? value.arguments : undefined;
  return {
    session_id: value.session_id.trim(),
    request_id: value.request_id.trim(),
    approval_id: value.approval_id.trim(),
    tool: value.tool.trim(),
    description: isNonEmptyString(value.description) ? value.description.trim() : "",
    arguments: args,
  };
}

function parseAgentToolRequest(value: Record<string, unknown>): AgentToolRequest {
  if (!isNonEmptyString(value.session_id) || !isNonEmptyString(value.name) || !isRecord(value.arguments)) {
    throw new Error("Invalid editor tool request");
  }
  return {
    sessionId: value.session_id.trim(),
    requestId: isNonEmptyString(value.request_id) ? value.request_id.trim() : undefined,
    toolCallId: isNonEmptyString(value.tool_call_id) ? value.tool_call_id.trim() : undefined,
    name: value.name.trim(),
    arguments: value.arguments,
  };
}

function normalizeSessions(value: unknown): AgentSessionSummary[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (session): session is AgentSessionSummary =>
        isRecord(session) && isNonEmptyString(session.id) && isNonEmptyString(session.name),
    )
    .map((session) => ({
      id: session.id,
      name: session.name,
      model: isNonEmptyString(session.model) ? session.model : undefined,
      updated_at: isNonEmptyString(session.updated_at) ? session.updated_at : undefined,
      message_count: typeof session.message_count === "number" ? session.message_count : undefined,
      // Tagged by the companion store — drives chat-list project scoping.
      project_id: isNonEmptyString(session.project_id) ? session.project_id : undefined,
      // C2: pinned flag from the store's pinned_at column (sort already
      // applied store-side: pinned first).
      pinned: session.pinned === true,
    }));
}

function normalizeMessages(value: unknown): AgentSessionMessage[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (message): message is AgentSessionMessage =>
      isRecord(message) && typeof message.role === "string" && typeof message.content === "string",
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// ── B2b-full: plan/diff/citation normalizers ────────────────────────────────

interface PlanStepView {
  id: string;
  description: string;
  status: "pending" | "running" | "done" | "failed";
}

function normalizePlanStatus(value: unknown): PlanStepView["status"] {
  const rawStatus = String(value ?? "pending");
  return ["pending", "running", "done", "failed"].includes(rawStatus)
    ? (rawStatus as PlanStepView["status"])
    : "pending";
}

function normalizePlanStep(value: unknown, index: number): PlanStepView {
  const step = isRecord(value) ? value : {};
  return {
    id: isNonEmptyString(step.id) ? step.id : `step-${index + 1}`,
    description: String(step.description ?? step.title ?? ""),
    status: normalizePlanStatus(step.status),
  };
}

interface DiffView {
  path: string;
  action: "create" | "modify" | "delete";
  hunks?: { header: string; lines: { kind: "add" | "del" | "context"; text: string }[] }[];
  summary?: string;
}

function normalizeDiffEvent(data: Record<string, unknown>): DiffView {
  const diff = isRecord(data.diff) ? data.diff : data;
  const actionRaw = String(diff.action ?? diff.kind ?? "modify");
  const action: DiffView["action"] = ["create", "modify", "delete"].includes(actionRaw)
    ? (actionRaw as DiffView["action"])
    : "modify";
  const hunks = Array.isArray(diff.hunks)
    ? diff.hunks
        .filter(isRecord)
        .map((hunk) => ({
          header: String(hunk.header ?? ""),
          lines: (Array.isArray(hunk.lines) ? hunk.lines : [])
            .filter(isRecord)
            .map((line) => ({
              kind: (["add", "del", "context"].includes(String(line.kind))
                ? String(line.kind)
                : "context") as "add" | "del" | "context",
              text: String(line.text ?? ""),
            })),
        }))
    : undefined;
  return {
    path: String(diff.path ?? diff.file ?? ""),
    action,
    hunks,
    summary: isNonEmptyString(data.summary as string) ? (data.summary as string) : undefined,
  };
}

interface CitationView {
  source: string;
  title?: string;
  excerpt?: string;
  score?: number;
  uri?: string;
}

/** Trimmed string when the value is a usable non-empty string, else undefined. */
function nonEmptyField(value: unknown): string | undefined {
  return isNonEmptyString(value) ? value : undefined;
}

/** Prefer explicit excerpt; fall back to a truncated text field. */
function citationExcerpt(c: Record<string, unknown>): string | undefined {
  if (isNonEmptyString(c.excerpt)) return c.excerpt;
  return isNonEmptyString(c.text) ? String(c.text).slice(0, 200) : undefined;
}

function normalizeCitation(value: unknown): CitationView {
  const c = isRecord(value) ? value : {};
  return {
    source: String(c.source ?? c.artifact_id ?? c.id ?? ""),
    title: nonEmptyField(c.title),
    excerpt: citationExcerpt(c),
    score: typeof c.score === "number" ? c.score : undefined,
    uri: nonEmptyField(c.uri),
  };
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function safeErrorMessage(error: unknown): string {
  if (!(error instanceof Error)) return "R4 Agent request failed.";
  return error.message.replace(/[\r\n]+/g, " ").slice(0, 500);
}

/** #210: distinguish a true 404/not-found from transient fetch failures so
 *  watchlists only drop entities that are actually gone. */
function isNotFoundError(error: unknown): boolean {
  if (error instanceof AcpRequestError) {
    return /not found|unknown|does not exist/i.test(error.message);
  }
  const message =
    error instanceof Error ? error.message : typeof error === "string" ? error : "";
  return /404|not found|unknown execution|does not exist/i.test(message);
}

/** #210: best-effort error text for fetch-degradation hints shown in Studio. */
function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

function toEndpointOption(endpoint: Endpoint, sourceName?: string): EndpointOption {
  const model = endpoint.served_model_name ?? endpoint.model;
  const variant = isNonEmptyString(endpoint.variant) ? ` (${endpoint.variant})` : "";
  // `model@source` is the r4 cross-source routing notation, so a selected
  // option round-trips to the ACP agent as an unambiguous destination.
  const id = sourceName ? `${model}@${sourceName}` : model;
  const suffix = sourceName ? ` \u00b7 ${sourceName}` : "";
  return { id, label: `${model}${variant}${suffix}`, status: endpoint.status };
}

/** Probe every configured source for its ready models (best-effort). */
async function collectSourceEndpoints(
  configs: LlmSourceConfig[],
): Promise<EndpointOption[]> {
  const results = await Promise.allSettled(
    configs.map(async (cfg) => {
      const client = new GatewayClient({
        baseUrl: cfg.url,
        token: cfg.token,
        kind: cfg.kind,
      });
      const endpoints = await client.listEndpoints();
      return endpoints
        .filter((ep) => ep.status.toLowerCase() === "ready")
        .map((ep) => toEndpointOption(ep, cfg.name));
    }),
  );
  const options: EndpointOption[] = [];
  for (const result of results) {
    if (result.status === "fulfilled") {
      options.push(...result.value);
    }
  }
  return options;
}

function dedupeEndpointOptions(options: EndpointOption[]): EndpointOption[] {
  const seen = new Map<string, EndpointOption>();
  for (const option of options) {
    if (!seen.has(option.id)) seen.set(option.id, option);
  }
  return [...seen.values()];
}

function renderAgentView(webview: vscode.Webview, messageToken: string): string {
  const nonce = createNonce();
  const csp = [
    "default-src 'none'",
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `script-src 'nonce-${nonce}'`,
  ].join("; ");
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="${csp}">
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; color: var(--vscode-foreground); background: var(--vscode-sideBar-background); font: 13px/1.5 var(--vscode-font-family); }
    main { display: grid; grid-template-rows: auto auto minmax(160px, 1fr) auto; height: 100vh; }
    .bar { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-bottom: 1px solid var(--vscode-panel-border); }
    .bar .grow { flex: 1 1 auto; min-width: 0; }
    .bar .title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .bar .sub { color: var(--vscode-descriptionForeground); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    select, textarea, button, input { color: var(--vscode-input-foreground); background: var(--vscode-input-background); border: 1px solid var(--vscode-input-border, transparent); font: inherit; }
    select { min-width: 0; max-width: 160px; padding: 4px 6px; }
    button { display: inline-grid; place-items: center; min-width: 26px; min-height: 26px; padding: 3px 7px; cursor: pointer; }
    button:hover { background: var(--vscode-toolbar-hoverBackground); }
    button.primary { color: var(--vscode-button-foreground); background: var(--vscode-button-background); border-color: transparent; }
    button:disabled { cursor: default; opacity: .5; }
    #sessions { max-height: 34vh; overflow-y: auto; border-bottom: 1px solid var(--vscode-panel-border); }
    #sessions.empty { display: none; }
    .session { display: grid; grid-template-columns: auto minmax(0,1fr) auto auto; align-items: center; gap: 6px; padding: 6px 8px; cursor: pointer; border-left: 2px solid transparent; }
    .session:hover { background: var(--vscode-list-hoverBackground); }
    .session.active { background: var(--vscode-list-activeSelectionBackground); color: var(--vscode-list-activeSelectionForeground); border-left-color: var(--vscode-focusBorder); }
    .session .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--vscode-descriptionForeground); }
    .session .dot.running { background: var(--vscode-progressBar-background); animation: pulse 1.2s ease-in-out infinite; }
    .session .dot.error { background: var(--vscode-errorForeground); }
    .session .dot.idle { background: var(--vscode-charts-green, #4caf50); }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
    .session .meta { min-width: 0; }
    .session .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .session .desc { color: var(--vscode-descriptionForeground); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .session.active .desc { color: inherit; opacity: .85; }
    .session .icon { min-width: 22px; min-height: 22px; opacity: .7; background: transparent; border-color: transparent; }
    .session .icon:hover { opacity: 1; }
    #transcript { overflow-y: auto; padding: 10px; }
    .empty-state { color: var(--vscode-descriptionForeground); text-align: center; margin-top: 24px; }
    .message { margin: 0 0 14px; overflow-wrap: anywhere; }
    .message .role { color: var(--vscode-descriptionForeground); font-size: 11px; font-weight: 600; text-transform: uppercase; margin-bottom: 2px; }
    .message .content { white-space: pre-wrap; }
    .message-user { border-left: 3px solid var(--vscode-charts-blue); padding-left: 8px; background: color-mix(in srgb, var(--vscode-charts-blue) 8%, transparent); border-radius: 0 4px 4px 0; }
    .message-response { padding-left: 0; }
    .message-response .content { white-space: pre-wrap; line-height: 1.55; }
    details.thinking { margin: 0 0 10px; border-left: 3px solid var(--vscode-progressBar-background); padding-left: 8px; background: color-mix(in srgb, var(--vscode-progressBar-background) 6%, transparent); border-radius: 0 4px 4px 0; }
    details.thinking summary { cursor: pointer; font-size: 11px; font-weight: 600; color: var(--vscode-progressBar-background); text-transform: uppercase; list-style: none; user-select: none; }
    details.thinking summary::before { content: '▶ '; }
    details.thinking[open] summary::before { content: '▼ '; }
    details.thinking .content { white-space: pre-wrap; font-size: 12px; color: var(--vscode-descriptionForeground); font-style: italic; padding-top: 4px; }
    .role { margin-bottom: 2px; }
    .role { color: var(--vscode-descriptionForeground); font-size: 11px; font-weight: 600; text-transform: uppercase; }
    .event { margin: 0 0 8px; padding-left: 8px; color: var(--vscode-descriptionForeground); border-left: 2px solid var(--vscode-progressBar-background); font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .event.error { color: var(--vscode-errorForeground); border-left-color: var(--vscode-errorForeground); }
    form { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 7px; padding: 8px; border-top: 1px solid var(--vscode-panel-border); }
    textarea { width: 100%; min-height: 58px; max-height: 180px; resize: vertical; padding: 7px; }
    .actions { display: grid; align-content: end; gap: 5px; }
    #status { grid-column: 1 / -1; min-height: 15px; color: var(--vscode-descriptionForeground); font-size: 11px; }
  </style>
</head>
<body>
<main>
  <div class="bar">
    <button id="new" class="primary" title="New chat" aria-label="New chat">+ New</button>
    <div class="grow"></div>
    <button id="refresh" title="Refresh" aria-label="Refresh">&#8635;</button>
    <button id="restart" title="Restart agent" aria-label="Restart agent">Restart</button>
  </div>
  <div id="sessions" class="empty" role="listbox" aria-label="Chat sessions"></div>
  <section id="transcript" aria-live="polite"></section>
  <form id="composer">
    <div class="bar" style="grid-column: 1 / -1; padding: 0 0 6px; border: none;">
      <div class="grow">
        <div id="hdrTitle" class="title">New chat</div>
        <div id="hdrSub" class="sub">Gateway not connected</div>
      </div>
      <select id="endpoint" aria-label="Endpoint"><option value="">Default</option></select>
    </div>
    <textarea id="prompt" aria-label="Message" placeholder="Ask R4 Agent" maxlength="100000"></textarea>
    <div class="actions"><button id="send" class="primary" type="submit">Send</button><button id="cancel" type="button" disabled>Stop</button></div>
    <div id="status">Connecting</div>
  </form>
</main>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  const messageToken = ${JSON.stringify(messageToken)};
  const post = (message) => vscode.postMessage(Object.assign({}, message, { messageToken }));

  const state = {
    current: undefined,
    sessions: [],
    running: new Set(),
    states: new Map(),      // sessionId -> 'running' | 'idle' | 'error'
    transcripts: new Map(), // sessionId -> [ entries ]
    streaming: new Map(),   // sessionId -> entry with mutable text
    endpoints: [],
    connected: false,
  };

  const el = (id) => document.getElementById(id);
  const sessionsBox = el('sessions');
  const transcript = el('transcript');
  const endpoint = el('endpoint');
  const prompt = el('prompt');
  const send = el('send');
  const cancel = el('cancel');
  const status = el('status');
  const hdrTitle = el('hdrTitle');
  const hdrSub = el('hdrSub');

  function summary(id) { return state.sessions.find((s) => s.id === id); }
  function stateOf(id) {
    if (state.states.has(id)) return state.states.get(id);
    return state.running.has(id) ? 'running' : 'idle';
  }
  function entriesOf(id) {
    let list = state.transcripts.get(id);
    if (!list) { list = []; state.transcripts.set(id, list); }
    return list;
  }
  function relTime(iso) {
    if (!iso) return '';
    const then = Date.parse(iso); if (isNaN(then)) return '';
    const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (secs < 60) return secs + 's ago';
    const mins = Math.round(secs / 60); if (mins < 60) return mins + 'm ago';
    const hrs = Math.round(mins / 60); if (hrs < 24) return hrs + 'h ago';
    return Math.round(hrs / 24) + 'd ago';
  }

  function renderSessions() {
    sessionsBox.classList.toggle('empty', state.sessions.length === 0);
    sessionsBox.replaceChildren();
    for (const s of state.sessions) {
      const row = document.createElement('div');
      row.className = 'session' + (s.id === state.current ? ' active' : '');
      row.setAttribute('role', 'option');
      const dot = document.createElement('span'); dot.className = 'dot ' + stateOf(s.id);
      const meta = document.createElement('div'); meta.className = 'meta';
      const name = document.createElement('div'); name.className = 'name'; name.textContent = s.name;
      const desc = document.createElement('div'); desc.className = 'desc';
      const bits = [];
      if (s.model) bits.push(s.model);
      if (typeof s.message_count === 'number') bits.push(s.message_count + ' msg');
      const rel = relTime(s.updated_at); if (rel) bits.push(rel);
      desc.textContent = bits.join(' \u00b7 ') || 'No messages';
      meta.append(name, desc);
      const rename = document.createElement('button'); rename.className = 'icon'; rename.title = 'Rename'; rename.textContent = '\u270e';
      const del = document.createElement('button'); del.className = 'icon'; del.title = 'Delete'; del.textContent = '\u2715';
      row.addEventListener('click', () => { if (s.id !== state.current) post({ type: 'selectSession', sessionId: s.id }); });
      rename.addEventListener('click', (e) => {
        e.stopPropagation();
        const next = window.prompt('Rename chat', s.name);
        if (next && next.trim()) post({ type: 'renameSession', sessionId: s.id, name: next.trim() });
      });
      del.addEventListener('click', (e) => {
        e.stopPropagation();
        post({ type: 'deleteSession', sessionId: s.id });
      });
      row.append(dot, meta, rename, del);
      sessionsBox.append(row);
    }
  }

  function renderHeader() {
    const s = state.current ? summary(state.current) : undefined;
    hdrTitle.textContent = s ? s.name : 'New chat';
    if (!state.connected) hdrSub.textContent = 'Gateway not connected';
    else hdrSub.textContent = state.endpoints.length ? (state.endpoints.length + ' endpoint(s) ready') : 'No ready endpoints';
    const selected = s && s.model ? s.model : '';
    endpoint.replaceChildren(new Option('Default endpoint', ''));
    let matched = selected === '';
    for (const ep of state.endpoints) {
      const label = ep.label + (ep.status && ep.status.toLowerCase() !== 'ready' ? ' [' + ep.status + ']' : '');
      endpoint.add(new Option(label, ep.id));
      if (ep.id === selected) matched = true;
    }
    if (selected && !matched) endpoint.add(new Option(selected + ' (offline)', selected));
    endpoint.value = selected;
  }

  function renderTranscript() {
    transcript.replaceChildren();
    if (!state.current) {
      const empty = document.createElement('div'); empty.className = 'empty-state';
      empty.textContent = 'Start a new chat or pick a session on the left.';
      transcript.append(empty);
      return;
    }
    for (const entry of entriesOf(state.current)) appendEntry(entry, false);
    const stream = state.streaming.get(state.current);
    if (stream) appendEntry(stream, false);
    transcript.scrollTop = transcript.scrollHeight;
  }

  function appendEntry(entry, scroll) {
    const item = document.createElement('div');
    if (entry.kind === 'thinking') {
      item.className = 'thinking';
      item.setAttribute('role', 'note');
      const details = document.createElement('details');
      details.className = 'thinking';
      const summary = document.createElement('summary');
      summary.textContent = 'Thinking';
      const ct = document.createElement('div'); ct.className = 'content'; ct.textContent = entry.content;
      details.append(summary, ct);
      item.append(details);
    } else if (entry.kind === 'msg') {
      item.className = 'message message-' + entry.role.toLowerCase();
      const role = document.createElement('div'); role.className = 'role'; role.textContent = entry.role;
      const ct = document.createElement('div'); ct.className = 'content'; ct.textContent = entry.text;
      item.append(role, ct);
    } else {
      item.className = entry.error ? 'event error' : 'event';
      item.textContent = entry.text;
    }
    transcript.append(item);
    if (scroll) transcript.scrollTop = transcript.scrollHeight;
  }

  function pushEntry(sessionId, entry) {
    entriesOf(sessionId).push(entry);
    if (sessionId === state.current) appendEntry(entry, true);
  }

  function updateComposer() {
    const running = state.current ? stateOf(state.current) === 'running' : false;
    send.disabled = running;
    cancel.disabled = !running;
  }

  // ---- composer wiring ----
  el('composer').addEventListener('submit', (event) => {
    event.preventDefault();
    const text = prompt.value.trim(); if (!text) return;
    post({ type: 'send', text, sessionId: state.current, model: endpoint.value || undefined });
    prompt.value = '';
  });
  // IME guard: while composing (Japanese/Chinese/Korean input), Enter commits the
  // candidate rather than sending. 'isComposing' covers modern browsers; keyCode
  // 229 is the legacy signal some IMEs still emit. 'compositionend' fires before
  // keydown in a few IMEs, so we also ignore the keystroke immediately after it.
  let composing = false;
  let composedAt = 0;
  prompt.addEventListener('compositionstart', () => { composing = true; });
  prompt.addEventListener('compositionend', () => { composing = false; composedAt = Date.now(); });
  prompt.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    if (composing || event.isComposing || event.keyCode === 229) return;
    if (Date.now() - composedAt < 100) return;
    event.preventDefault();
    el('composer').requestSubmit();
  });
  el('new').addEventListener('click', () => post({ type: 'newSession', model: endpoint.value || undefined }));
  el('refresh').addEventListener('click', () => post({ type: 'refresh' }));
  el('restart').addEventListener('click', () => post({ type: 'restart' }));
  cancel.addEventListener('click', () => post({ type: 'cancel', sessionId: state.current }));
  endpoint.addEventListener('change', () => {
    if (state.current) post({ type: 'setModel', sessionId: state.current, model: endpoint.value || undefined });
  });

  // ---- inbound messages ----
  window.addEventListener('message', ({ data }) => {
    if (!data || typeof data.type !== 'string') return;
    switch (data.type) {
      case 'status':
        status.textContent = data.status;
        break;
      case 'sessions': {
        state.sessions = data.sessions || [];
        state.running = new Set(data.running || []);
        renderSessions(); renderHeader(); updateComposer();
        break;
      }
      case 'inventory':
        state.endpoints = data.endpoints || [];
        state.connected = !!data.connected;
        renderHeader();
        break;
      case 'sessionSelected': {
        const id = data.session.id;
        state.current = id;
        if (data.fresh) { state.transcripts.set(id, []); state.streaming.delete(id); }
        else {
          const list = [];
          for (const m of data.messages || []) list.push({ kind: 'msg', role: m.role === 'user' ? 'You' : 'R4 Agent', text: m.content });
          state.transcripts.set(id, list); state.streaming.delete(id);
        }
        renderSessions(); renderHeader(); renderTranscript(); updateComposer();
        break;
      }
      case 'sessionState':
        state.states.set(data.sessionId, data.state);
        if (data.state === 'running') state.running.add(data.sessionId); else state.running.delete(data.sessionId);
        renderSessions(); updateComposer();
        break;
      case 'userMessage':
        state.streaming.delete(data.sessionId);
        pushEntry(data.sessionId, { kind: 'msg', role: 'You', text: data.text });
        break;
      case 'assistantDelta': {
        let chunk = data.text || '';
        chunk = chunk.replace(new RegExp('\`+think\`+', 'g'), '').replace(new RegExp('\`+/think\`+', 'g'), '');
        let stream = state.streaming.get(data.sessionId);
        if (!stream) {
          stream = { kind: 'msg', role: 'R4 Agent', text: '' };
          state.streaming.set(data.sessionId, stream);
          entriesOf(data.sessionId).push(stream);
          if (data.sessionId === state.current) appendEntry(stream, true);
        }
        stream.text += chunk;
        if (data.sessionId === state.current) renderTranscript();
        break;
      }
      case 'sessionThinking': {
        const content = data.content || '';
        if (!content) break;
        const stream = state.streaming.get(data.sessionId);
        if (stream && data.sessionId === state.current) {
          const entries = entriesOf(data.sessionId);
          const streamIdx = entries.length - 1;
          if (streamIdx >= 0 && entries[streamIdx] === stream) {
            const thought = { kind: 'thinking', content: content } as const;
            entries.splice(streamIdx, 0, thought);
            renderTranscript();
            return;
          }
        }
        pushEntry(data.sessionId, { kind: 'thinking', content: content });
        break;
      }

      case 'assistantComplete': {
        let text = data.text || '';
        text = text.replace(new RegExp('\`+think\`+', 'g'), '').replace(new RegExp('\`+/think\`+', 'g'), '');
        const stream = state.streaming.get(data.sessionId);
        if (stream) { stream.text = text; state.streaming.delete(data.sessionId); }
        else pushEntry(data.sessionId, { kind: 'msg', role: 'R4 Agent', text: text });
        if (data.sessionId === state.current) renderTranscript();
        break;
      }
      case 'toolState':
        pushEntry(data.sessionId, { kind: 'event', text: 'Tool ' + data.tool + ': ' + data.status });
        break;
      case 'agentEvent': {
        const ev = data.event || {};
        const kind = String(ev.event_type || 'event');
        const label = kind.startsWith('planning_') ? 'Thinking'
          : kind.startsWith('tool_') ? 'Tool'
          : (kind.includes('failed') || kind === 'error') ? 'Error' : 'Progress';
        const detail = ev.message || JSON.stringify(ev.data || ev);
        pushEntry(data.sessionId, { kind: 'event', text: label + ': ' + detail, error: label === 'Error' });
        break;
      }
      case 'modelChanged': {
        const s = summary(data.sessionId); if (s) s.model = data.model || undefined;
        renderSessions(); if (data.sessionId === state.current) renderHeader();
        break;
      }
      case 'sessionDeleted':
        state.transcripts.delete(data.sessionId);
        state.streaming.delete(data.sessionId);
        state.states.delete(data.sessionId);
        state.running.delete(data.sessionId);
        if (state.current === data.sessionId) { state.current = undefined; renderTranscript(); }
        renderSessions(); renderHeader(); updateComposer();
        break;
      case 'cancelled':
        if (data.sessionId) pushEntry(data.sessionId, { kind: 'event', text: 'Request cancelled.' });
        break;
      case 'error':
        if (data.sessionId) pushEntry(data.sessionId, { kind: 'event', text: data.message, error: true });
        else status.textContent = data.message;
        break;
    }
  });

  renderTranscript();
  post({ type: 'ready' });
</script>
</body>
</html>`;
}

function createNonce(): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let nonce = "";
  for (let index = 0; index < 32; index += 1) {
    nonce += alphabet.charAt(Math.floor(Math.random() * alphabet.length));
  }
  return nonce;
}