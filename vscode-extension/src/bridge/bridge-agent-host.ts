/**
 * The agent view's chat over the console's companion bridge (ADR-103, VP-2).
 *
 * Takes the webview's messages and answers with the webview's existing
 * protocol events (`assistantDelta`, `toolCall`, `thinkingDelta`, …), so the
 * React UI keeps working while its backend changes from `r4 acp` to the
 * bridge. Reply text folds through the console's own `lib/turn-reply.ts`.
 * No vscode import: the host is tested against a fake bridge.
 *
 * One bridge holds one session (the console runs one bridge per chat tab).
 * Until VP-3 brings tabs, switching sessions loads the other session into
 * the same bridge and is refused while a turn runs.
 */

import { TurnReplyFold, type ReplyEvent } from "../../../packages/r4-tui-v3/src/lib/turn-reply";
import { bridgeErrorHint } from "../../../packages/r4-tui-v3/src/bridge/wire";
import { queueOp } from "../../../packages/r4-tui-v3/src/lib/queue-rail";
import {
  formatShellForAgent,
  formatShellPreview,
  maskShellResult,
  shellEcho,
} from "../../../packages/r4-tui-v3/src/lib/shell-relay";
import { isTurnSave, PUBLISH_EXPLANATION } from "../../../packages/r4-tui-v3/src/lib/save-args";
import { rankMentions } from "../../../packages/r4-tui-v3/src/lib/file-mentions";
import {
  mapSandbox,
  SANDBOX_USAGE,
  sandboxChip,
  sandboxStartText,
  sandboxStatusText,
  sandboxSetText,
  type SandboxView,
} from "../../../packages/r4-tui-v3/src/lib/sandbox";
import {
  localNote,
  mapWebAccess,
  needsWebChoice,
  userWebChoice,
  NO_CAPS,
  webAccessLabel,
  type WebAccessChoice,
  type WebCaps,
  type WebChoice,
  REMOTE_NOTE,
  webCapsFromLimits,
} from "../../../packages/r4-tui-v3/src/lib/web-access";
import { venuePickerOptions, type VenuePickerOption } from "../../../packages/r4-tui-v3/src/lib/session-filters";
import { sessionTurnLine, turnRowsOf } from "../../../packages/r4-tui-v3/src/lib/session-turns";
import type { VenueChoice } from "../../../packages/r4-tui-v3/src/types";
import { REAL_CLOCK, VenueHost, type SourcePick, type VenueClock } from "./venue-host";
import { RemoteAsks, remoteId } from "./remote-asks";
import { ChatVerbs, type PromptRow } from "./chat-verbs";
import { ConsoleTools } from "./console-tools";
import { claimsMode, claimsModels, ModelMode, type ModelRow } from "./model-mode";
import { OpsVerbs } from "./ops-verbs";
import { TeamLive, TeamupVerbs } from "./teamup-verbs";
import { WorkVerbs } from "./work-verbs";
import { isQueuedRemote, RemoteTurnWatch, sendBaseTotal } from "./remote-turn";
import type { ShellRunner } from "./shell-runner";
import { toChangedFiles, toCitations, toPlanSteps, toViewMessage, type ChangeContent } from "./turn-extras";
import {
  approvalOptionsOf,
  isApprovalOption,
  type ApprovalOption,
} from "../../../packages/r4-tui-v3/src/lib/approval-options";

/** The part of `CompanionBridge` the host uses (a fake in tests). */
export interface HostBridge {
  readonly initResult: unknown;
  readonly turnInFlight: boolean;
  ensureStarted(): Promise<void>;
  reconnect(): Promise<void>;
  request(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<unknown>;
  on(event: string, listener: (payload: any) => void): unknown;
  off(event: string, listener: (payload: any) => void): unknown;
  respondApproval(id: string | number, decision: ApprovalOption): void;
  respondAskUser(id: string | number, answer: string | null): void;
  cancelTurn(): void;
}

export type HostPost = (message: Record<string, unknown>) => void;

/** What the host needs from the editor; all optional (tests pass fakes). */
export interface HostHooks {
  /** A prompt the user sent (the shared prompt-history tape). */
  onPrompt?(text: string): void;
  /** Runs `!cmd` lines in the workspace. */
  shell?: ShellRunner;
  /** Shows text in an editor tab (a cited passage). */
  openDocument?(title: string, content: string): void | Promise<void>;
  /** Opens the editor's diff view for one changed file. */
  openDiff?(path: string, before: string, after: string): void | Promise<void>;
  /** Asks the user to confirm (a modal); resolves true to go ahead. */
  confirm?(message: string, action: string): Promise<boolean>;
  /** Workspace files (relative paths) for `@` mentions. */
  workspaceFiles?(): string[];
  /** VP-3b: open a session in its own chat tab (its own bridge). */
  openTab?(sessionId: string): void | Thenable<unknown>;
  /** VP-3b: `/tab <verb>` over the chat tabs; the output card's lines. */
  tabCommand?(args: string): Thenable<{ kind: "info" | "error"; text: string }[] | undefined>;
  /** VP-2c: `/rewind` — the user's prompts, newest first (null = backed out). */
  pickPrompt?(rows: PromptRow[]): Promise<string | null>;
  /** VP-2e: `/models` — the session model (null = backed out). */
  pickModel?(ask: { models: ModelRow[]; current: string; problems: string[] }): Promise<string | null>;
  /** VP-2c: `/palette` — the editor's command palette on R4's commands. */
  openPalette?(): void | Thenable<unknown>;
  /** VP-3b: where a new session runs (the console's venue picker; null = backed out). */
  pickVenue?(options: VenuePickerOption[]): Promise<VenueChoice | null>;
  /** VP-3b: the remote source picker (null = nothing chosen). */
  pickSource?(ask: { title: string; rows: SourcePick[] }): Promise<string | null>;
  /** ADR-099 D-2: the session's web access (null = the user backed out). */
  pickWebAccess?(ask: { current: WebAccessChoice | null; caps: WebCaps; note: string }): Promise<WebChoice | null>;
}

/** A message waiting for the running turn; `echo` is the user's own text
 * (absent for a relay the user did not type, e.g. a `!cmd` result). */
interface QueuedTurn {
  text: string;
  echo?: string;
}

/** Console-owned slash verbs the host answers itself. `claims` says whether
 * this line is theirs (`/save notes.md` writes a transcript: the bridge's). */
const LOCAL_SLASH: Record<string, { claims(args: string): boolean; run(host: BridgeAgentHost, args: string): Promise<void> }> = {
  fork: { claims: (args) => !args.trim(), run: (host) => host.forkFrom(undefined) },
  changes: { claims: () => true, run: (host) => host.showChanges() },
  save: { claims: (args) => isTurnSave(args), run: (host, args) => host.saveArgs(args) },
  sandbox: { claims: () => true, run: (host, args) => host.sandboxCommand(args) },
  web: { claims: () => true, run: (host) => host.webCommand() },
  tab: { claims: () => true, run: (host, args) => host.tabCommand(args) },
  rewind: { claims: () => true, run: (host, args) => host.verbs.rewindCommand(args) },
  bg: { claims: () => true, run: async (host) => host.verbs.bg() },
  explain: { claims: () => true, run: (host, args) => host.verbs.explain(args) },
  palette: { claims: () => true, run: (host) => host.verbs.palette() },
  help: { claims: () => true, run: async (host, args) => host.tools.help(args) },
  "search-server": { claims: () => true, run: (host, args) => host.tools.searchServer(args) },
  worktree: { claims: () => true, run: (host, args) => host.tools.worktree(args) },
  models: { claims: claimsModels, run: (host) => host.modelMode.models() },
  model: { claims: claimsModels, run: (host) => host.modelMode.models() },
  mode: { claims: claimsMode, run: (host, args) => host.modelMode.mode(args) },
  loops: { claims: () => true, run: (host, args) => host.ops.loops(args) },
  traces: { claims: () => true, run: (host, args) => host.ops.traces(args) },
  provenance: { claims: () => true, run: (host, args) => host.ops.provenance(args) },
  sweep: { claims: () => true, run: (host, args) => host.ops.sweep(args) },
  teamup: { claims: () => true, run: (host, args) => host.teamup.command(args) },
  team: { claims: () => true, run: (host, args) => host.teamup.command(args) },
  teams: { claims: () => true, run: (host, args) => host.teamup.command(args) },
  work: { claims: () => true, run: (host, args) => host.work.work(args) },
  workflows: { claims: () => true, run: (host, args) => host.work.work(args) },
  infra: { claims: () => true, run: (host) => host.work.infra() },
  venue: { claims: () => true, run: (host, args) => host.venue.command(args) },
};

/** The `/sandbox` forms the console accepts (its SANDBOX_USAGE). */
const SANDBOX_FORMS: Record<string, "status" | { mode: "on" | "off"; asDefault: boolean }> = {
  "": "status",
  status: "status",
  on: { mode: "on", asDefault: false },
  off: { mode: "off", asDefault: false },
  "default on": { mode: "on", asDefault: true },
  "default off": { mode: "off", asDefault: true },
};

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

/** A refused sandbox verb as one line: the bridge's typed `data.code` + `reason`
 * (sandbox.unavailable | sandbox.self_test_failed | sandbox.refused | sandbox.remote). */
function sandboxErrorText(error: unknown): string {
  const data = record((error as { data?: unknown } | null)?.data);
  const code = str(data.code);
  if (code) return `${code}: ${str(data.reason) || "refused"}`;
  return errorText(error);
}

const TOOL_RESULTS_KEPT = 200;

/** How the agent is told where a `!cmd` ran (lib/shell-relay framing). */
const SHELL_WHERE = "the VS Code R4 view's local shell";

/** Webview modes → the session's one agent mode (ADR-104). */
const MODE_TO_BRIDGE: Record<string, string> = { ask: "chat", chat: "chat", plan: "plan", agent: "autopilot", autopilot: "autopilot" };
/** Bridge modes → the webview's selector values. */
const MODE_TO_VIEW: Record<string, string> = { chat: "ask", plan: "plan", autopilot: "agent" };

/** The Sessions filters: state (ongoing/stopped/completed/error/archived), venue (local/frontend), archives. */
export interface SessionFilter {
  state?: string;
  venue?: string;
  includeArchived?: boolean;
}

function sessionFilterParams(filter: SessionFilter): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  if (filter.state) params.state = filter.state;
  if (filter.venue) params.venue = filter.venue;
  if (filter.includeArchived) params.include_archived = true;
  return params;
}

const PLAN_ACTIONS = ["execute_auto", "execute_ask", "keep_planning"];


interface SessionRow {
  venue?: unknown;
  state?: unknown;
  sync?: unknown;
  id?: unknown;
  name?: unknown;
  model?: unknown;
  updated_at?: unknown;
  message_count?: unknown;
  project_id?: unknown;
  pinned?: unknown;
  archived_at?: unknown;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

/** A `list_sessions` row → the webview's `SessionSummary` (archived rows
 * only when the filter asked for them). */
export function toSessionSummary(row: SessionRow, includeArchived = false): Record<string, unknown> | null {
  const archived = Boolean(str(row.archived_at));
  if (!str(row.id) || (archived && !includeArchived)) return null;
  return {
    venue: opt(row.venue),
    state: opt(row.state),
    sync: opt(row.sync),
    archived,
    id: str(row.id),
    name: str(row.name) || str(row.id).slice(0, 8),
    model: opt(row.model),
    updated_at: opt(row.updated_at),
    message_count: typeof row.message_count === "number" ? row.message_count : undefined,
    project_id: opt(row.project_id),
    pinned: Boolean(row.pinned),
  };
}

/** A non-empty string, else undefined (absent from the webview's row). */
function opt(value: unknown): string | undefined {
  return str(value) || undefined;
}

/** The options an approval card offers: the ask's own list (ADR-104 D-3),
 * else derived as an older bridge meant it — the console's rule, shared. */
export function approvalOptions(request: Record<string, unknown>): ApprovalOption[] {
  return approvalOptionsOf({
    options: Array.isArray(request.options) ? request.options : undefined,
    sessionApprovalAllowed: request.session_approval_allowed === false ? false : undefined,
    neverAuto: request.risk_tier === "never_auto",
    fenced: Boolean(request.fenced),
  });
}

/** The webview card for one approval_request line. */
export function approvalCard(sessionId: string, request: Record<string, unknown>): Record<string, unknown> {
  return {
    type: "approvalRequest",
    sessionId,
    id: request.id as string | number,
    tool: str(request.tool),
    description: str(request.description),
    agent: str(request.agent) || undefined,
    path: str(request.path) || undefined,
    diff: str(request.diff) || undefined,
    fenced: Boolean(request.fenced),
    fenceReason: str(request.fence_reason) || undefined,
    options: approvalOptions(request),
  };
}

/** The message plus the server's recovery hint. The hint is read by shape as
 * well as by class: a BridgeRequestError from another bundle copy fails
 * `instanceof` but carries the same field. */
function errorText(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const raw = (error as { hint?: unknown } | null)?.hint;
  const hint = bridgeErrorHint(error) || (typeof raw === "string" ? raw.trim() : "");
  return hint ? `${message}\n→ ${hint}` : message;
}

function sessionErrorStage(error: unknown): string | undefined {
  const data = record((error as { data?: unknown })?.data);
  return data.code === "session_conflict" ? str(data.stage) || "before_turn" : undefined;
}

export class BridgeAgentHost {
  private sessionId = "";
  /** Open asks by string key, keeping the id as the bridge sent it (the
   * webview matches cards by that exact value). */
  private readonly pendingApprovals = new Map<string, string | number>();
  private readonly pendingQuestions = new Map<string, string | number>();
  private wired = false;
  /** True from a turn's request until its end-of-turn work is done. */
  private turnActive = false;
  private readonly queue: QueuedTurn[] = [];
  private changeContents = new Map<string, ChangeContent>();
  /** Full tool results by call id (the bridge's tool_result lines), for a
   * card's expand; the oldest go first past TOOL_RESULTS_KEPT. */
  private readonly toolResults = new Map<string, { name: string; content: string }>();
  private sessionFilter: SessionFilter = {};
  /** The session's sandbox, web-access choice and venue state (ADR-101, ADR-099). */
  private sandbox: SandboxView | null = null;
  private webAccess: WebAccessChoice | null = null;
  /** VP-3b: the session on the server (`/venue`, the `remote:` chip), its
   * approvals and questions, and the watch over a turn it runs. */
  readonly venue: VenueHost;
  private readonly remoteAsks: RemoteAsks;
  /** VP-2c: /rewind and the edit, /btw, /bg, /explain, /palette. */
  readonly verbs: ChatVerbs;
  /** VP-2d: the live slash catalog, /help, /search-server. */
  readonly tools: ConsoleTools;
  /** VP-2e: /models and /mode. */
  readonly modelMode: ModelMode;
  /** VP-4a: /loops, /traces, /provenance, /sweep. */
  readonly ops: OpsVerbs;
  /** VP-4b: /teamup (/team, /teams), and the live team/plan rows. */
  readonly teamup: TeamupVerbs;
  private readonly teamLive: TeamLive;
  /** VP-4c: /work (/workflows) and /infra. */
  readonly work: WorkVerbs;
  private remoteWatch: RemoteTurnWatch | null = null;
  private readonly clock: VenueClock;
  private readonly post: HostPost;
  /** L3: the first save of a run asks once; later saves do not. */
  private publishConfirmed = false;
  private lastFilesKey = "";

  constructor(
    private readonly bridge: HostBridge,
    rawPost: HostPost,
    private readonly hooks: HostHooks = {},
    clock: VenueClock = REAL_CLOCK,
  ) {
    // Every error this host posts gets the registry's hint (the console's error card).
    this.post = (message) => {
      rawPost(message);
      if (message.type === "error") void this.tools.matchError(str(message.message), str(message.sessionId));
    };
    this.clock = clock;
    const teamNotice = (text: string, key?: string) =>
      this.post({ type: "notice", sessionId: this.sessionId, text, ...(key ? { key } : {}) });
    this.teamup = new TeamupVerbs({
      request: (method, params, timeoutMs) => this.bridge.request(method, params, timeoutMs),
      output: (command, lines) => this.postOutput(command, lines),
      notice: teamNotice,
      armed: (label) => this.post({ type: "teamArmed", sessionId: this.sessionId, label }),
    });
    this.teamLive = new TeamLive(teamNotice);
    this.work = new WorkVerbs({
      request: (method, params, timeoutMs) => this.bridge.request(method, params, timeoutMs),
      output: (command, lines) => this.postOutput(command, lines),
      draft: (text) => this.post({ type: "restoreDraft", sessionId: this.sessionId, text, force: true }),
    });
    this.ops = new OpsVerbs({
      request: (method, params, timeoutMs) => this.bridge.request(method, params, timeoutMs),
      output: (command, lines) => this.postOutput(command, lines),
    });
    this.modelMode = new ModelMode({
      request: (method, params, timeoutMs) => this.bridge.request(method, params, timeoutMs),
      output: (command, lines) => this.postOutput(command, lines),
      setModel: (model) => this.setModel(model),
      setMode: (mode) => this.setMode(MODE_TO_VIEW[mode] ?? mode),
      pickModel: this.hooks.pickModel,
    });
    this.tools = new ConsoleTools({
      request: (method, params, timeoutMs) => this.bridge.request(method, params, timeoutMs),
      post: (message) => this.post(message),
      sessionId: () => this.sessionId,
      output: (command, lines) => this.postOutput(command, lines),
    });
    this.verbs = new ChatVerbs({
      request: (method, params, timeoutMs) => this.bridge.request(method, params, timeoutMs),
      post: (message) => this.post(message),
      sessionId: () => this.sessionId,
      output: (command, lines) => this.postOutput(command, lines),
      isRemote: () => this.venue?.isRemote ?? false,
      pickPrompt: this.hooks.pickPrompt,
      openPalette: this.hooks.openPalette,
      now: () => this.clock.now(),
    });
    this.remoteAsks = new RemoteAsks({
      request: (method, params, timeoutMs) => this.bridge.request(method, params, timeoutMs),
      post: (message) => this.post(message),
      sessionId: () => this.sessionId,
    });
    this.venue = new VenueHost(
      {
        request: (method, params, timeoutMs) => this.bridge.request(method, params, timeoutMs),
        post: (message) => this.post(message),
        sessionId: () => this.sessionId,
        output: (command, lines) => this.postOutput(command, lines),
        webChoice: () => userWebChoice(this.webAccess),
        askRemoteWeb: () => this.askRemoteWeb(),
        adoptState: (state, adopted) => this.adoptVenueState(state, adopted),
        pickSource: this.hooks.pickSource,
        confirm: this.hooks.confirm,
        approvals: () => this.remoteAsks.approvalsText(),
        questions: (named) => this.remoteAsks.questionsText(named),
        onRuntime: (runtime) => this.followRemoteQuestion(runtime.phase, runtime.detail),
      },
      clock,
    );
  }

  /** The tab or view closed: no poller or watch outlives it. */
  dispose(): void {
    this.venue.dispose();
    this.remoteWatch?.stop();
  }

  /** Any attached client answers (ADR-099 D-8): a server session waiting on
   * the user (opened, resumed, polled) shows its question; a watch does its own. */
  private followRemoteQuestion(phase: string, detail: string): void {
    if (this.remoteWatch || !this.venue.isRemote) return;
    void this.remoteAsks.followQuestion(phase, detail).catch(() => undefined);
  }

  private get busy(): boolean {
    return this.turnActive || this.bridge.turnInFlight;
  }

  get currentSessionId(): string {
    return this.sessionId;
  }

  /** Every line the bridge pushes during and between turns. */
  private wire(): void {
    if (this.wired) return;
    this.wired = true;
    this.bridge.on("control_event", (line: Record<string, unknown>) => this.onControl(line));
    this.bridge.on("approval", (request: Record<string, unknown>) => this.onApproval(request));
    this.bridge.on("ask_user", (request: Record<string, unknown>) => this.onAskUser(request));
    this.bridge.on("session_title", () => void this.refreshSessions().catch(() => undefined));
    this.bridge.on("search_server_event", (event: Record<string, unknown>) => this.tools.onSearchServerEvent(event));
    this.bridge.on("team_event", (event: unknown) => this.teamLive.ingest(event));
    this.bridge.on("worktree_event", (event: Record<string, unknown>) => this.tools.onWorktreeEvent(event));
  }

  async initialize(): Promise<void> {
    this.post({ type: "status", status: "connecting" });
    this.post({ type: "backendInfo", backend: "bridge" });
    this.wire();
    await this.bridge.ensureStarted();
    if (this.sessionId && !this.bridge.turnInFlight) {
      // A reloaded webview: repaint the session on screen, not the first one.
      await this.loadInto(this.sessionId);
    } else {
      this.selectFromSnapshot(record(record(this.bridge.initResult).session), [], true);
    }
    await this.refreshSessions();
    this.post({ type: "status", status: "ready" });
    await this.tools.refreshCatalog();
  }

  private selectFromSnapshot(session: Record<string, unknown>, messages: unknown[], fresh: boolean): void {
    this.sessionId = str(session.id);
    this.verbs.reset();
    if (!this.sessionId) return;
    this.post({
      type: "sessionSelected",
      session: { id: this.sessionId, name: str(session.name), model: str(session.model) || undefined },
      fresh,
      messages: messages.map(toViewMessage),
    });
    const mode = MODE_TO_VIEW[str(session.interaction_mode)];
    if (mode) this.post({ type: "modeChanged", sessionId: this.sessionId, mode });
    this.adoptPolicy(session);
  }

  /** The snapshot's sandbox and web access → the notice, the chip, the label. */
  private adoptPolicy(session: Record<string, unknown>, announce = true): void {
    const venue = str(session.venue);
    this.sandbox = mapSandbox(session.sandbox);
    this.webAccess = mapWebAccess(session.web_access);
    this.venue.adoptSnapshot(session);
    this.post({
      type: "policyState",
      sessionId: this.sessionId,
      sandboxNotice: announce ? sandboxStartText(this.sandbox, venue) : "",
      sandboxChip: sandboxChip(this.sandbox, venue)?.label ?? "",
      web: webAccessLabel(this.webAccess),
    });
  }

  /** `/sandbox [status|on|off|default on|off]` (ADR-101): session.sandbox_get / _set. */
  async sandboxCommand(args: string): Promise<void> {
    const form = SANDBOX_FORMS[args.trim().toLowerCase().split(/\s+/).filter(Boolean).join(" ")];
    if (form === "status") return this.sandboxStatus();
    if (!form) return this.postOutput(`/sandbox ${args}`, [{ kind: "error", text: SANDBOX_USAGE }]);
    await this.sandboxSet(form.mode, form.asDefault, `/sandbox ${args}`);
  }

  private async sandboxSet(mode: "on" | "off", asDefault: boolean, command: string): Promise<void> {
    try {
      const result = record(await this.bridge.request("session.sandbox_set", asDefault ? { mode, default: true } : { mode }, 120_000));
      const view = mapSandbox(result.sandbox) ?? (mapSandbox({ mode }) as SandboxView);
      this.sandbox = view;
      const reply = {
        sandbox: view,
        restartedMcp: strings(result.restarted_mcp),
        stillRunning: strings(result.still_running),
        mcpError: str(result.mcp_error),
      };
      this.postOutput(command, [{ kind: "info", text: sandboxSetText(reply, asDefault) }]);
      this.postPolicyChips();
    } catch (error) {
      this.postOutput(command, [{ kind: "error", text: sandboxErrorText(error) }]);
    }
  }

  /** The status-bar chips after a change (no session-open notice). */
  private postPolicyChips(): void {
    this.post({
      type: "policyState",
      sessionId: this.sessionId,
      sandboxNotice: "",
      sandboxChip: sandboxChip(this.sandbox, "local")?.label ?? "",
      web: webAccessLabel(this.webAccess),
    });
  }

  private async sandboxStatus(): Promise<void> {
    try {
      const view = mapSandbox(record(await this.bridge.request("session.sandbox_get", {}, 15_000)).sandbox);
      if (view) this.sandbox = view;
      this.postOutput("/sandbox", [{ kind: "info", text: view ? sandboxStatusText(view) : "the bridge sent no sandbox state" }]);
    } catch (error) {
      this.postOutput("/sandbox", [{ kind: "error", text: sandboxErrorText(error) }]);
    }
  }

  private postOutput(command: string, lines: { kind: string; text: string }[]): void {
    this.post({ type: "slashOutput", sessionId: this.sessionId, command, lines });
  }

  /** `/web`: show and change the session's web access (ADR-099 D-2). */
  async webCommand(): Promise<void> {
    await this.askWebAccess();
  }

  /** Before a remote start: the question offers only what the server's caps
   * allow (`session.venue_policy`); no preview means no caps (the server checks). */
  private async askRemoteWeb(): Promise<boolean> {
    const { caps, note } = await this.remoteCaps();
    return this.askWebAccess(caps, note);
  }

  private async remoteCaps(): Promise<{ caps: WebCaps; note: string }> {
    try {
      const result = record(await this.bridge.request("session.venue_policy", {}, 20_000));
      const note = str(result.error) ? `session policy preview failed (${str(result.error)}); the server checks the choice` : REMOTE_NOTE;
      return { caps: webCapsFromLimits(result.limits), note };
    } catch (error) {
      return { caps: NO_CAPS, note: `session policy preview failed (${errorText(error)})` };
    }
  }

  /** A venue start/stop answer: its snapshot's venue and chips (no second
   * sandbox notice); an adopted copy repaints the transcript. */
  private async adoptVenueState(state: unknown, adopted: boolean): Promise<void> {
    const session = record(record(state).session);
    if (str(session.id) === this.sessionId) this.adoptPolicy(session, false);
    if (!adopted) return;
    await this.loadInto(this.sessionId);
    this.post({ type: "notice", sessionId: this.sessionId, text: "adopted the server's newer copy of this session (a remote turn finished while unwatched)" });
  }

  /** Ask (the picker), then store the answer; false when the user backed out
   * or the bridge refused (a running remote session needs a restart). */
  private async askWebAccess(caps: WebCaps = NO_CAPS, note = localNote(this.sandbox)): Promise<boolean> {
    const pick = this.hooks.pickWebAccess;
    if (!pick) return true;
    const choice = await pick({ current: this.webAccess, caps, note });
    if (!choice) return false;
    try {
      const result = record(
        await this.bridge.request("session.web_access_set", { mode: choice.mode, domains: choice.domains, source: "prompt" }, 15_000),
      );
      this.webAccess = mapWebAccess(result.web_access) ?? this.webAccess;
      this.postPolicyChips();
      if (str(result.notice)) this.post({ type: "notice", sessionId: this.sessionId, text: str(result.notice) });
      return true;
    } catch (error) {
      this.post({ type: "error", sessionId: this.sessionId, message: `web access not changed: ${errorText(error)}` });
      return false;
    }
  }

  /** A session with no web choice asks before its first turn and holds the
   * message; backing out puts it back in the composer (the console's rule). */
  private async webChoiceReady(echo: string | undefined): Promise<boolean> {
    if (!needsWebChoice(this.webAccess, this.venue.current?.state)) return true;
    if (await this.askWebAccess()) return true;
    if (echo) this.post({ type: "restoreDraft", sessionId: this.sessionId, text: echo });
    return false;
  }

  /** The Sessions filters (the console's `s` state, `v` venue, `A` archives),
   * sent to the server — never emulated here. */
  setSessionFilter(filter: SessionFilter): Promise<void> {
    this.sessionFilter = filter;
    return this.refreshSessions();
  }

  async refreshSessions(): Promise<void> {
    const params = sessionFilterParams(this.sessionFilter);
    const result = record(await this.bridge.request("list_sessions", params, 30_000));
    const rows = Array.isArray(result.sessions) ? (result.sessions as SessionRow[]) : [];
    const include = Boolean(this.sessionFilter.includeArchived);
    this.post({
      type: "sessions",
      sessions: rows.map((row) => toSessionSummary(row, include)).filter(Boolean),
      running: this.bridge.turnInFlight && this.sessionId ? [this.sessionId] : [],
    });
  }

  private refuseWhileTurn(): boolean {
    if (!this.busy) return false;
    this.post({
      type: "error",
      sessionId: this.sessionId,
      message: "A turn is running in this chat. Stop it or let it finish first, or open the other session in a chat tab (⧉).",
    });
    return true;
  }

  async selectSession(sessionId: string): Promise<void> {
    if (sessionId === this.sessionId || this.refuseWhileTurn()) return;
    await this.loadInto(sessionId);
  }

  private async loadInto(sessionId: string): Promise<void> {
    const result = record(await this.bridge.request("load_session", { session_id: sessionId }, 60_000));
    this.selectFromSnapshot(record(result.session), Array.isArray(result.messages) ? result.messages : [], false);
    const notice = str(result.notice);
    if (notice) this.post({ type: "error", sessionId: this.sessionId, message: notice });
  }

  /** A new session, where the user says (the console's venue picker; Server
   * is offered only when the journal answers). Local restarts the bridge
   * (`initialize {}`; the bridge's own `/new` only clears the conversation
   * of the session it holds); Server creates it with the web answer and
   * starts it (`initialize {venue: frontend}`, then `session.venue_start`). */
  async newSession(): Promise<void> {
    if (this.refuseWhileTurn()) return;
    const venue = await this.pickVenue();
    if (venue === "frontend") return this.venue.startNew(() => this.pickNewRemoteWeb(), (web) => this.createServerSession(web));
    if (!venue) return;
    await this.bridge.reconnect();
    this.selectFromSnapshot(record(record(this.bridge.initResult).session), [], true);
    await this.refreshSessions();
  }

  /** The picker over the console's options; a disabled row says why. */
  private async pickVenue(): Promise<VenueChoice | null> {
    if (!this.hooks.pickVenue) return "local";
    const probe = await this.probeRemote();
    const options = venuePickerOptions(probe.available, probe.reason);
    const picked = await this.hooks.pickVenue(options);
    const option = options.find((o) => o.choice === picked);
    if (option?.disabled) this.post({ type: "notice", sessionId: this.sessionId, text: `${option.label}: ${option.disabledReason}` });
    return option && !option.disabled ? option.choice : null;
  }

  /** The journal probe behind the Server row (2 s, as the console's). */
  private async probeRemote(): Promise<{ available: boolean; reason: string }> {
    try {
      const result = record(await this.bridge.request("list_sessions", { limit: 1 }, 2_000));
      return { available: result.remote_available === true, reason: str(result.remote_reason) };
    } catch (error) {
      return { available: false, reason: errorText(error) };
    }
  }

  /** The web question for a session that does not exist yet (nothing stored). */
  private async pickNewRemoteWeb(): Promise<WebChoice | null> {
    if (!this.hooks.pickWebAccess) return null;
    const { caps, note } = await this.remoteCaps();
    return this.hooks.pickWebAccess({ current: null, caps, note });
  }

  private async createServerSession(web: WebChoice): Promise<boolean> {
    try {
      const params = { venue: "frontend", web_access: { mode: web.mode, domains: web.domains } };
      const result = record(await this.bridge.request("initialize", params, 30_000));
      this.selectFromSnapshot(record(result.session), [], true);
      this.post({ type: "notice", sessionId: this.sessionId, text: `new session started (frontend venue · web ${webAccessLabel(web)})` });
      await this.refreshSessions().catch(() => undefined);
      return true;
    } catch (error) {
      this.post({ type: "error", sessionId: this.sessionId, message: `new session failed: ${errorText(error)}` });
      return false;
    }
  }

  /** A Sessions row's turns: the server's turn index as an output card. */
  async sessionTurns(sessionId: string): Promise<void> {
    const command = `/sessions turns ${sessionId.slice(0, 8)}`;
    try {
      const rows = turnRowsOf(record(await this.bridge.request("session.turns", { session_id: sessionId }, 30_000)));
      const lines = rows.length ? rows.map((row) => ({ kind: "info", text: sessionTurnLine(row) })) : [{ kind: "info", text: "no stored turns" }];
      this.postOutput(command, lines);
    } catch (error) {
      this.postOutput(command, [{ kind: "error", text: `turns failed: ${errorText(error)}` }]);
    }
  }

  /** A Sessions row's fork: that session, forked, opened here. */
  forkSession(sessionId: string): Promise<void> {
    return this.forkFrom(undefined, sessionId);
  }

  /** `/tab …`: the chat tabs answer; silent verbs (new, next, …) post no card. */
  async tabCommand(args: string): Promise<void> {
    const lines = this.hooks.tabCommand
      ? await this.hooks.tabCommand(args)
      : [{ kind: "error" as const, text: "Chat tabs are not available here." }];
    if (lines?.length) this.postOutput(`/tab ${args}`.trim(), lines);
  }

  /** A Sessions row's ⏹: stop that session's runtime on the server. */
  async stopRemote(sessionId: string): Promise<void> {
    await this.venue.stop(sessionId);
    await this.refreshSessions().catch(() => undefined);
  }

  /** A session in its own chat tab (the console resumes sessions into tabs). */
  async openInTab(sessionId: string): Promise<void> {
    await this.hooks.openTab?.(sessionId);
  }

  /** A session's transcript as Markdown in an editor tab (`x` in the console). */
  async exportSession(sessionId: string): Promise<void> {
    try {
      const result = record(await this.bridge.request("export_session", { session_id: sessionId }, 60_000));
      await this.hooks.openDocument?.(`session ${sessionId.slice(0, 8)}`, str(result.markdown));
    } catch (error) {
      this.post({ type: "error", sessionId: this.sessionId, message: `export failed: ${errorText(error)}` });
    }
  }

  async renameSession(sessionId: string, name: string): Promise<void> {
    await this.bridge.request("session.rename", { session_id: sessionId, name }, 30_000);
    await this.refreshSessions();
  }

  async pinSession(sessionId: string, pinned: boolean): Promise<void> {
    await this.bridge.request(pinned ? "session.pin" : "session.unpin", { session_id: sessionId }, 30_000);
    await this.refreshSessions();
  }

  /** The bridge archives (journal kept, `/sessions` can show it again);
   * there is no hard delete in the console either. */
  async archiveSession(sessionId: string): Promise<void> {
    await this.bridge.request("session.archive", { session_id: sessionId }, 30_000);
    this.post({ type: "sessionDeleted", sessionId });
    await this.refreshSessions();
  }

  /** session.compact works on the bridge's own session. */
  async compactSession(sessionId: string, keepRecent = 6): Promise<void> {
    try {
      const result = record(await this.bridge.request("session.compact", { keep_recent: keepRecent }, 120_000));
      this.post({
        type: "compactResult",
        sessionId,
        changed: Boolean(result.changed),
        from: Number(result.from ?? 0),
        to: Number(result.to ?? 0),
        keepRecent: Number(result.keep_recent ?? 0),
        reason: str(result.reason) || undefined,
      });
    } catch (error) {
      this.post({ type: "compactResult", sessionId, changed: false, from: 0, to: 0, keepRecent: 0, error: errorText(error) });
    }
  }

  async setModel(model: string | undefined): Promise<void> {
    if (!model) return;
    await this.bridge.request("set_model", { model }, 60_000);
    this.post({ type: "modelChanged", sessionId: this.sessionId, model });
  }

  async setMode(mode: string): Promise<void> {
    const target = MODE_TO_BRIDGE[mode];
    if (!target) return;
    const result = record(await this.bridge.request("set_interaction_mode", { mode: target }, 30_000));
    const view = MODE_TO_VIEW[str(result.mode) || target] ?? mode;
    this.post({ type: "modeChanged", sessionId: this.sessionId, mode: view });
  }

  /** Enter in the composer: a slash line goes to the bridge's slash
   * dispatcher (every console registry verb), anything else is a turn. */
  async send(text: string): Promise<void> {
    const line = text.trim();
    if (!line) return;
    // /btw is a side question: it works while a turn runs (not in the history).
    if (/^\/btw(\s|$)/i.test(line)) return this.verbs.btw(line.slice(4));
    if (this.fireArmedTeam(line)) return;
    if (line.startsWith("/") && this.busy) {
      this.post({ type: "error", sessionId: this.sessionId, message: "A turn is running: wait for it, or stop it, before a slash command." });
      return;
    }
    this.hooks.onPrompt?.(line);
    if (line.startsWith("!")) await this.runShell(line.slice(1).trim());
    else if (this.busy) await this.enqueue({ text: line, echo: line });
    else if (line.startsWith("/")) await this.runSlashLine(line);
    else if ((await this.webChoiceReady(line)) && (await this.verbs.cutBeforeSend(line))) await this.runTurn("send_message", { text: line }, line);
  }

  /** `/teamup on` armed the next message: it becomes the team's goal instead
   * of a turn (disarmed as it fires; the start runs in the background — a
   * plan's card may wait for the human up to 15 minutes). */
  private fireArmedTeam(line: string): boolean {
    if (!this.teamup.isArmed || line.startsWith("/") || line.startsWith("!")) return false;
    const arm = this.teamup.takeArm();
    if (!arm) return false;
    this.hooks.onPrompt?.(line);
    this.post({ type: "userMessage", sessionId: this.sessionId, text: line });
    void this.teamup.fire(arm, line);
    return true;
  }

  /** A message's ✎ (the console's edit-and-resend): back to the composer;
   * the next send cuts the history at this turn first. */
  rewindTo(turnId: string, text: string): void {
    if (this.refuseWhileTurn()) return;
    this.verbs.begin(turnId, text);
  }

  /** Console-owned verbs answer here (the console intercepts them before
   * the bridge too); every other slash line goes to the bridge. */
  private async runSlashLine(line: string): Promise<void> {
    const [verb, ...rest] = line.slice(1).split(/\s+/);
    const args = rest.join(" ");
    const local = LOCAL_SLASH[verb.toLowerCase()];
    if (local && local.claims(args)) {
      this.post({ type: "userMessage", sessionId: this.sessionId, text: line });
      await local.run(this, args);
      return;
    }
    await this.runSlash(line);
  }

  /** `/changes`: the changed-files panel, refreshed and opened. */
  async showChanges(): Promise<void> {
    this.lastFilesKey = "";
    await this.refreshChanges();
    this.post({ type: "dockFocus", sessionId: this.sessionId, panel: "files" });
  }

  /** `/save [n] [mine] [--vouch]`: the bridge reads the turn from the line. */
  async saveArgs(args: string): Promise<void> {
    if (this.refuseWhileTurn() || !(await this.confirmPublish())) return;
    await this.requestSave({ args: args.trim() });
  }

  /** L3: a save publishes to the project's shared graph, so the first one
   * in a run asks once (the console's press-again; here a modal). */
  private async confirmPublish(): Promise<boolean> {
    if (this.publishConfirmed || !this.hooks.confirm) return true;
    const message = `${PUBLISH_EXPLANATION} Later saves in this window will not ask.`;
    this.publishConfirmed = await this.hooks.confirm(message, "Publish");
    return this.publishConfirmed;
  }

  private async requestSave(params: Record<string, unknown>): Promise<void> {
    try {
      const out = record(await this.bridge.request("knowledge.save", params, 60_000));
      this.post({ type: "notice", sessionId: this.sessionId, text: str(out.line) || `saved ${str(out.turn_id)} as ${str(out.tier)}` });
    } catch (error) {
      this.post({ type: "error", sessionId: this.sessionId, message: `save failed: ${errorText(error)}` });
    }
  }

  /** `@` in the composer: the best workspace matches (the console's ranking). */
  mentionCandidates(query: string): void {
    const files = this.hooks.workspaceFiles?.() ?? [];
    const items = rankMentions(files, query, 8).map((c) => c.path);
    this.post({ type: "mentionCandidates", sessionId: this.sessionId, query, items });
  }

  /** A message typed while a turn runs waits on the session's queue (the
   * console's rail: server lane first, then the session-local verbs). Only a
   * definite refusal gives the text back; an unanswered append may have
   * landed, so it stays queued and says so (TUI_REFERENCE §12). */
  private async enqueue(item: QueuedTurn): Promise<void> {
    const outcome = await queueOp(this.bridge, "append", { content: item.text });
    if (outcome.applied || outcome.unconfirmed) {
      this.queue.push(item);
      this.postQueue();
      if (outcome.unconfirmed) {
        this.post({ type: "error", sessionId: this.sessionId, message: "queue append unconfirmed — check the transcript before resending" });
      }
      return;
    }
    this.post({ type: "error", sessionId: this.sessionId, message: `queue append failed: ${errorText(outcome.error)}` });
    if (item.echo) this.post({ type: "restoreDraft", sessionId: this.sessionId, text: item.echo });
  }

  private postQueue(): void {
    this.post({ type: "queueState", sessionId: this.sessionId, items: this.queue.map((q) => q.echo ?? "(shell result)") });
  }

  /** The turn ended: the next queued message becomes the next turn. */
  private async drainQueue(): Promise<void> {
    const next = this.queue.shift();
    if (!next) return;
    this.postQueue();
    await queueOp(this.bridge, "pop");
    await this.runTurn("send_message", { text: next.text }, next.echo);
  }

  /** A stop gives the queued text back to the composer and clears the queue. */
  private async returnQueue(): Promise<void> {
    if (this.queue.length === 0) return;
    const typed = this.queue.map((q) => q.echo).filter((t): t is string => Boolean(t));
    this.queue.length = 0;
    this.postQueue();
    await queueOp(this.bridge, "clear");
    if (typed.length) this.post({ type: "restoreDraft", sessionId: this.sessionId, text: typed.join("\n") });
  }

  /** `!cmd`: the user's shell runs it in the workspace; the output shows as
   * a shell cell and, unless stopped, goes to the agent as a turn (behind a
   * running one), its keys masked (lib/shell-relay). */
  private async runShell(command: string): Promise<void> {
    const shell = this.hooks.shell;
    if (!command || !shell) return;
    const sessionId = this.sessionId;
    const id = `sh-${Date.now()}`;
    this.post({ type: "shellStarted", sessionId, id, echo: shellEcho(command) });
    const task = this.verbs.shellStarted(command);
    const result = maskShellResult(await shell.run(command));
    this.verbs.shellEnded(task, shellStatus(result), `exit ${result.exitCode}`);
    this.post({ type: "shellResult", sessionId, id, text: formatShellPreview(result), exitCode: result.exitCode });
    if (result.interrupted) return;
    const relay = { text: formatShellForAgent(result, SHELL_WHERE) };
    if (this.busy) await this.enqueue(relay);
    else if (await this.webChoiceReady(undefined)) await this.runTurn("send_message", relay, undefined);
  }

  /** Plan card: `execute_auto` (autopilot), `execute_ask` (chat) or `keep_planning`. */
  async confirmPlan(action: string): Promise<void> {
    if (!PLAN_ACTIONS.includes(action)) return;
    await this.runTurn("confirm_plan", { action }, undefined);
  }

  private async runSlash(line: string): Promise<void> {
    const sessionId = this.sessionId;
    this.post({ type: "userMessage", sessionId, text: line });
    try {
      const result = record(await this.bridge.request("dispatch_slash_command", { command: line }, Number.POSITIVE_INFINITY));
      if (result.cleared) this.selectFromSnapshot(record(record(result.state).session), [], true);
      const lines = Array.isArray(result.output)
        ? result.output.map((o) => ({ kind: str(record(o).kind) || "info", text: str(record(o).text) })).filter((l) => l.text)
        : [];
      // /retry cuts its turn from the session: drop the same rows (the console does).
      const dropped = Array.isArray(result.dropped_turn_ids) ? result.dropped_turn_ids.map(str).filter(Boolean) : [];
      if (dropped.length) this.post({ type: "dropTurns", sessionId: this.sessionId, turnIds: dropped });
      if (!result.handled && lines.length === 0) {
        this.post({ type: "error", sessionId: this.sessionId, message: `Command not handled: ${line}` });
      } else {
        this.post({ type: "slashOutput", sessionId: this.sessionId, command: line, lines });
      }
      const followUps = Array.isArray(result.follow_up_messages) ? result.follow_up_messages.map(str).filter(Boolean) : [];
      for (const message of followUps) await this.runTurn("send_message", { text: message }, message);
    } catch (error) {
      this.post({ type: "error", sessionId, message: errorText(error) });
    }
    await this.refreshSessions().catch(() => undefined);
  }

  private async runTurn(method: "send_message" | "confirm_plan", params: Record<string, unknown>, echo: string | undefined): Promise<void> {
    const sessionId = this.sessionId;
    if (echo !== undefined) this.post({ type: "userMessage", sessionId, text: echo });
    this.post({ type: "sessionState", sessionId, state: "running" });
    const fold = new TurnReplyFold();
    const onEvent = (event: ReplyEvent) => {
      this.keepToolResult(event);
      for (const piece of fold.fold(event)) {
        if (piece.kind === "delta") this.post({ type: "assistantDelta", sessionId, text: piece.text });
        else if (piece.kind === "turnOpened") this.post({ type: "turnOpened", sessionId, turnId: piece.turnId });
      }
    };
    this.turnActive = true;
    this.bridge.on("event", onEvent);
    try {
      const result = record(await this.bridge.request(method, params, Number.POSITIVE_INFINITY));
      if (isQueuedRemote(result)) await this.watchRemote(sessionId, result);
      else this.finishTurn(sessionId, result);
    } catch (error) {
      this.failTurn(sessionId, error, echo);
    } finally {
      this.bridge.off("event", onEvent);
      this.clearPending(sessionId);
    }
    await this.afterTurn();
    this.turnActive = false;
    await this.drainQueue();
  }

  /** A server session queued the turn: watch it until the journal has the
   * answer. The worker pops the session queue itself, so the local copy of
   * the queue is display only and clears when the watch ends. */
  private async watchRemote(sessionId: string, result: Record<string, unknown>): Promise<void> {
    const watch = new RemoteTurnWatch(
      {
        request: (method, params, timeoutMs) => this.bridge.request(method, params, timeoutMs),
        post: (message) => this.post(message),
        sessionId: () => this.sessionId,
        setRuntime: (runtime) => this.venue.setRuntime(runtime),
        sleep: (ms) => this.clock.sleep(ms),
      },
      this.remoteAsks,
      sessionId,
      sendBaseTotal(result),
    );
    this.remoteWatch = watch;
    try {
      const end = await watch.run();
      if (end === "stopped" || end === "left") this.post({ type: "cancelled", sessionId });
      this.post({ type: "sessionState", sessionId, state: end === "failed" ? "error" : "idle" });
    } finally {
      this.remoteWatch = null;
      this.remoteAsks.clear();
      this.queue.length = 0;
      this.postQueue();
    }
  }

  private keepToolResult(event: ReplyEvent): void {
    if (event.type !== "tool_result") return;
    const data = record(event.data);
    const id = str(data.tool_call_id);
    if (!id) return;
    this.toolResults.set(id, { name: str(data.name), content: str(data.result) || str(event.message) });
    if (this.toolResults.size > TOOL_RESULTS_KEPT) this.toolResults.delete(this.toolResults.keys().next().value!);
  }

  /** A tool card's expand: the full result this window saw, else not found. */
  toolResult(toolCallId: string): void {
    const kept = this.toolResults.get(toolCallId);
    this.post({
      type: "toolResult",
      sessionId: this.sessionId,
      toolCallId,
      found: Boolean(kept),
      ...(kept ? { name: kept.name, content: kept.content } : {}),
    });
  }

  /** End-of-turn views: sessions, changed files, plan (each best-effort). */
  private async afterTurn(): Promise<void> {
    await this.refreshSessions().catch(() => undefined);
    await this.refreshChanges().catch(() => undefined);
    await this.refreshPlan().catch(() => undefined);
  }

  /** The session's changed files (the console's /changes); posted when they change. */
  async refreshChanges(): Promise<void> {
    const { files, contents } = toChangedFiles(await this.bridge.request("get_session_changes", {}, 30_000));
    this.changeContents = contents;
    const key = JSON.stringify(files);
    if (key === this.lastFilesKey) return;
    this.lastFilesKey = key;
    this.post({ type: "filesChanged", sessionId: this.sessionId, files });
  }

  /** The session plan (todo_write mirrored into the task graph). */
  async refreshPlan(): Promise<void> {
    const steps = toPlanSteps(await this.bridge.request("get_session_plan", {}, 30_000));
    this.post({ type: "planState", sessionId: this.sessionId, steps });
  }

  /** One changed file in the editor's diff view (before → after). */
  async openChange(path: string): Promise<void> {
    const content = this.changeContents.get(path);
    if (content && this.hooks.openDiff) await this.hooks.openDiff(path, content.before, content.after);
  }

  /** Put one changed file back (the console's /changes r → R). */
  async revertChange(path: string): Promise<void> {
    if (this.refuseWhileTurn()) return;
    const result = record(await this.bridge.request("revert_session_change", { path }, 60_000));
    const done = result.reverted ? (result.deleted ? "removed (it did not exist before)" : "reverted") : "not reverted";
    this.post({ type: "notice", sessionId: this.sessionId, text: `${path}: ${done}` });
    await this.refreshChanges();
  }

  /** Fork a session (this one unless named; from a turn when given) and open the fork here. */
  async forkFrom(turnId: string | undefined, sessionId = this.sessionId): Promise<void> {
    if (this.refuseWhileTurn()) return;
    const params: Record<string, unknown> = { session_id: sessionId };
    if (turnId) params.from_turn = turnId;
    const result = record(await this.bridge.request("session.fork", params, 60_000));
    const forked = str(result.session_id);
    if (!forked) return;
    await this.loadInto(forked);
    this.post({ type: "notice", sessionId: forked, text: `forked into "${str(result.name)}" (${Number(result.messages ?? 0)} messages)` });
    await this.refreshSessions();
  }

  /** Keep one turn in project knowledge (K1.6): your message or the answer. */
  async saveTurn(turnId: string, part: "prompt" | "answer"): Promise<void> {
    if (!turnId || this.refuseWhileTurn() || !(await this.confirmPublish())) return;
    await this.requestSave({ turn: turnId, part });
  }

  /** A cited passage, fetched under the server's access check, in an editor tab. */
  async openCitation(citation: Record<string, unknown>): Promise<void> {
    const params: Record<string, unknown> = { sid: str(citation.sid) };
    if (str(citation.sourceId)) params.source_id = str(citation.sourceId);
    if (str(citation.projectId)) params.project_id = str(citation.projectId);
    try {
      const passage = record(await this.bridge.request("citation.open", params, 30_000));
      const title = str(passage.title) || str(citation.title) || "cited passage";
      await this.hooks.openDocument?.(title, `# ${title}\n\n${str(passage.text)}`);
    } catch (error) {
      this.post({ type: "error", sessionId: this.sessionId, message: `could not open the passage: ${errorText(error)}` });
    }
  }

  private finishTurn(sessionId: string, result: Record<string, unknown>): void {
    if (result.cancelled) {
      this.post({ type: "cancelled", sessionId });
    } else {
      this.post({ type: "assistantComplete", sessionId, text: str(result.response) });
    }
    const citations = toCitations(result.citations);
    if (citations.length) this.post({ type: "citations", sessionId, turnId: str(result.turn_id), citations });
    this.post({ type: "sessionState", sessionId, state: "idle" });
    const session = record(record(result.state).session);
    if (session.plan_awaiting_confirmation) this.post({ type: "planConfirm", sessionId, request: str(session.plan_pending_request) });
  }

  /** A typed `session_conflict` says whether the turn ran (TUI_REFERENCE §12):
   * `before_turn` gives the text back; `after_turn` must never be resent. */
  private failTurn(sessionId: string, error: unknown, echo: string | undefined): void {
    const stage = sessionErrorStage(error);
    let message = errorText(error);
    if (stage === "after_turn") message += "\nThe turn ran and was kept as a copy; do not send it again.";
    this.post({ type: "error", sessionId, message });
    if (stage === "before_turn" && echo) this.post({ type: "restoreDraft", sessionId, text: echo });
    this.post({ type: "sessionState", sessionId, state: "error" });
  }

  /** Stop: a running `!cmd`, the turn, and the queue (its text comes back). */
  cancel(): void {
    this.hooks.shell?.stop();
    this.remoteWatch?.stop();
    this.bridge.cancelTurn();
    void this.returnQueue().catch(() => undefined);
  }

  private onControl(line: Record<string, unknown>): void {
    const sessionId = this.sessionId;
    const type = str(line.type);
    const payload = record(line.payload);
    if (type === "thinking_delta" || type === "thinking_done") {
      this.post({ type: type === "thinking_delta" ? "thinkingDelta" : "thinkingDone", sessionId, payload });
    } else if (type === "tool_call_start") {
      this.post(toolCallStart(sessionId, payload));
    } else if (type === "tool_call_end") {
      this.post(toolCallEnd(sessionId, payload));
    } else if (type === "cost") {
      this.post({ type: "costState", sessionId, payload });
    } else if (type === "budget_state") {
      this.post({ type: "budgetState", sessionId, payload });
    } else if (type === "mode_change") {
      const mode = MODE_TO_VIEW[str(payload.mode)];
      if (mode) this.post({ type: "modeChanged", sessionId, mode });
    }
  }

  private onApproval(request: Record<string, unknown>): void {
    this.pendingApprovals.set(String(request.id), request.id as string | number);
    this.post(approvalCard(this.sessionId, request));
  }

  private onAskUser(request: Record<string, unknown>): void {
    this.pendingQuestions.set(String(request.id), request.id as string | number);
    this.post({
      type: "askUserRequest",
      sessionId: this.sessionId,
      id: request.id as string | number,
      question: str(request.question),
      options: Array.isArray(request.options) ? request.options.map(str).filter(Boolean) : [],
    });
  }

  /** The card's answer. Only a pending ask is answered, and only with a
   * decision the wire knows. */
  answerApproval(id: string | number, decision: string): void {
    const remote = remoteId(id);
    if (remote) {
      void this.remoteAsks.decide(remote, decision);
      return;
    }
    if (!this.pendingApprovals.delete(String(id))) return;
    const answer: ApprovalOption = isApprovalOption(decision) ? decision : "reject";
    this.bridge.respondApproval(id, answer);
    this.post({ type: "approvalResolved", sessionId: this.sessionId, id, decision: answer });
  }

  answerQuestion(id: string | number, answer: string | null): void {
    const remote = remoteId(id);
    if (remote) {
      void this.remoteAsks.answer(remote, answer);
      return;
    }
    if (!this.pendingQuestions.delete(String(id))) return;
    this.bridge.respondAskUser(id, answer);
    this.post({ type: "askUserResolved", sessionId: this.sessionId, id });
  }

  /** A turn that ended leaves no card open (the bridge stopped waiting). */
  private clearPending(sessionId: string): void {
    for (const id of this.pendingApprovals.values()) this.post({ type: "approvalResolved", sessionId, id, decision: "expired" });
    for (const id of this.pendingQuestions.values()) this.post({ type: "askUserResolved", sessionId, id });
    this.pendingApprovals.clear();
    this.pendingQuestions.clear();
  }
}

/** A finished `!cmd` as the background list names it. */
function shellStatus(result: { interrupted?: boolean; exitCode: number | null }): "done" | "failed" | "cancelled" {
  if (result.interrupted) return "cancelled";
  return result.exitCode === 0 ? "done" : "failed";
}

function toolCallStart(sessionId: string, payload: Record<string, unknown>): Record<string, unknown> {
  return {
    type: "toolCall",
    sessionId,
    toolCallId: str(payload.tool_call_id),
    name: str(payload.name),
    argsSummary: str(payload.args_summary) || undefined,
    status: "pending",
  };
}

function toolCallEnd(sessionId: string, payload: Record<string, unknown>): Record<string, unknown> {
  const raw = str(payload.status) || "completed";
  return {
    type: "toolCall",
    sessionId,
    toolCallId: str(payload.tool_call_id),
    name: str(payload.name),
    // The canonical rail reports "done"; webview cards speak "completed".
    status: raw === "done" ? "completed" : raw,
    resultPreview: payload.result_preview,
    resultTruncated: Boolean(payload.result_truncated),
    durationMs: payload.duration_ms,
  };
}
