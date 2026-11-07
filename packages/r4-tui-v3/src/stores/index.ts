/**
 * R4 Console v3 stores — unified workspace store.
 *
 * Platform concerns are separated via the PlatformAdapter abstraction
 * in adapters/platform.ts.  The workspace store holds ALL state
 * (UI + platform), with explicit sections for clarity.
 *
 * Domain stores (agent-runtime, knowledge, etc.) import from this file
 * and access state via the unified workspace store.
 */

import { createStore, reconcile, unwrap, type SetStoreFunction } from "solid-js/store";
import { ulid } from "ulid";
import { setWorkspaceOverride } from "../lib/resolve-python";
import { companionUnavailableReply, generateLocalReply, isGatewayNetworkFailure } from "../lib/chat-reply";
import { WELCOME_MESSAGE_ID, welcomeChatContent } from "../lib/chat-welcome";
import { mapVerifyResponse } from "../lib/knowledge-verify";
import {
  formatShellForAgent,
  formatShellPreview,
  maskShellResult,
  maskShellText,
  runLocalShell,
  shellDisplayCommand,
  shellEcho,
  type ShellRunResult,
} from "../lib/local-shell";
import type { HostResources } from "../lib/resource-monitor";
import type { KnowledgeBaseState, ScienceLoopState } from "../lib/companion-panels";
import { mergeSkillInventories } from "../lib/skill-inventory";
import { cycleSessionStateFilter, cycleSessionVenueFilter } from "../lib/session-filters";
import { knowledgeActions } from "./knowledge";
import { getCompanionBridge, registerCompanionBridgeProvider } from "../bridge/jsonl-client";
import { rpc } from "../bridge/rpc";
import {
  bridgeForTab,
  bindTabsStoreHooks,
  clearTabRuntimeWiring,
  closeRuntime,
  getRuntime,
  popClosedTab,
  runtimeOrNull,
  tabWiringInstalled,
  waitForTabTurn,
  CHAT_SCOPED_FIELDS,
} from "../tabs/registry";
import {
  applyOnTab,
  eventTargetTab,
  notifyActivated,
  restoreScopedFromStore,
  type TabRuntime,
  type ChatScopedField,
  type ChatTabState,
} from "../tabs/registry";
import { applyScopedValues } from "../tabs/chat-tab-store";
import type { ActivityLine } from "../lib/tool-activity";
import {
  adjacentTabId,
  cleanSessionTitle,
  isFallbackTitle,
  isGenericSessionName,
  makeTabMeta,
  MAX_CHAT_TABS,
  nextTabAfterClose,
  promptDerivedTitle,
  type ChatTabMeta,
} from "../tabs/model";
import {
  clearTurnPinSlices,
  flushTurnSummary,
  resetTurnLines,
  retargetResourcePolling,
  shiftTurnPinSlice,
  stopResourcePolling,
} from "../bridge/companion-runtime-bridge";
import type { ChatStreamChunk, DataSource } from "../adapters/types";
import { localTimeHHMMSS } from "../lib/time";
import { ghostGateVerdict } from "../lib/ghost-gate";
import { joinQueued } from "../lib/tui-parity";
import { STOPPED_MESSAGE } from "../lib/message-queue";
import { redactSlashSecrets } from "../lib/secret-redact";
import { isOfflineBackend } from "../lib/console-mode";
import { needsWebChoice } from "../lib/web-access";

/** /btw keeps the newest exchanges only (Claude-Code parity: 5). */
const BTW_KEEP = 5;
import type {
  Space,
  ActivityTab,
  ConnectionStatus,
  NavNode,
  Agent,
  ResearchProject,
  ResearchGoal,
  ResearchTask,
  KnowledgeObject,
  Notification,
  ConsoleEvent,
  UiEndpoint,
  UiModel,
  UiJob,
  UiSkill,
  ChatMessage,
  CostState,
  ErrorCard,
  SessionRecord,
} from "../types";
import type { PlatformAdapter } from "../types/platform";
import { approvalOptionsOf, type ApprovalOption } from "../lib/approval-options";

// ── Chat Types ──────────────────────────────────────────────────────────

type ChatHistoryItem = { role: "user" | "assistant" | "system"; content: string };

export interface ChatBackend {
  /** Backend id ("companion-bridge" keeps its history in a session). */
  readonly name?: string;
  chat(message: string, history?: ChatHistoryItem[], model?: string): Promise<string>;
  chatStream?(
    message: string,
    history?: ChatHistoryItem[],
    model?: string,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatStreamChunk>;
  /** Optional hook for companion-bridge cancellation. */
  interrupt?(): void;
}

// ── Workspace State (ALL state in one store) ────────────────────────────

export type RemoteSourcePickerMode = "start" | "current" | "manage";

/** ADR-099 D-2: why the web-access question is open. new-local / new-remote:
 *  right after the venue choice of a new session; venue-current: /venue start
 *  on a session without a choice; first-submit: the first turn of a session
 *  without one (`pendingText` is the held message); slash: `/web`. */
export interface WebAccessPrompt {
  purpose: "new-local" | "new-remote" | "venue-current" | "first-submit" | "slash";
  remote: boolean;
  pendingText?: string;
  /** first-submit: the held message is machine content (a shell or slash
   *  relay): it is sent as a relay, and dropped (not put in the composer)
   *  when the question is cancelled. */
  pendingRelay?: boolean;
  /** first-submit: a held relay that goes back to the composer on cancel
   *  instead of being dropped — a slash follow-up (/retry, /code edit,
   *  /agent chat, /search): /retry already cut its turn from the session,
   *  so dropping it lost the user's prompt (RS-1 round-3 fix N1). */
  pendingKeep?: boolean;
  /** first-submit: further held messages, sent in order after
   *  `pendingText` with the same flags (a follow-up list of several). */
  pendingMore?: string[];
  /** new-local: the venue the new session records (default "local"). */
  venue?: import("../types").VenueChoice;
}

/** One pending tool approval as the card shows it (ADR-104 D-3). */
export interface ToolApprovalCard {
  id: string | number;
  tool: string;
  description?: string;
  /** The answers this ask may get (the companion's `options`). */
  options: ApprovalOption[];
  /** Server-computed unified diff of the pending change (opencode EditBody
   * parity — approve the actual edit, not just its description). */
  diff?: string;
  /** Path the diff applies to. */
  path?: string;
  /** ADR-092 slice 2/3: the plan agent or team role asking ("" or absent
   * for the lead's own call). */
  agent?: string;
  /** MD-5: a never-auto tool — asked per call, never remembered. */
  neverAuto?: boolean;
  /** Why a content fence asks about this call (absent for an ordinary ask). */
  fenceReason?: string;
}

/** What openToolApproval takes: a card, with `options` derived (as an older
 * bridge meant them) when the caller has none. */
export type ToolApprovalInput = Omit<ToolApprovalCard, "options"> & {
  options?: readonly unknown[];
  sessionApprovalAllowed?: boolean;
};

/** The card an input becomes (options settled, no legacy hint fields). */
function toApprovalCard(req: ToolApprovalInput): ToolApprovalCard {
  const { options, sessionApprovalAllowed, ...rest } = req;
  const derived = approvalOptionsOf({
    options,
    sessionApprovalAllowed,
    neverAuto: req.neverAuto,
    fenced: Boolean(req.fenceReason),
  });
  return { ...rest, options: derived };
}

/** The think-block's visible state (named for the setScopedField funnels). */
export interface ThinkingState {
  text: string;
  open: boolean;
  expanded: boolean;
  startedAtMs: number | null;
  elapsedMs: number;
}

export interface WorkspaceState {
  // ── Navigation ──
  project: ResearchProject | null;
  // (removed 2026-09-18 L2: duped with paneView — see plan F2)
  /** Research Workspace bottom panel tab (ops | strategy | explain). */
  researchBottomTab: "ops" | "strategy" | "explain";
  navTree: NavNode;
  selectedNodeId: string | null;
  selectedObjectId: string | null;

  // ── Activity ──
  activityTab: ActivityTab;
  activityOpen: boolean;

  // ── Context Panel ──
  contextPanelOpen: boolean;
  contextPanelTab: string;

  // ── Overlays ──
  commandPaletteOpen: boolean;
  /** Pre-filled query when the palette opens (e.g. "/" from chat input). */
  commandPaletteSeed: string | null;
  slashBarOpen: boolean;
  slashBarSeed: string;
  slashPickerOpen: boolean;
  slashPicker: {
    title: string;
    commandPrefix: string;
    items: Array<{ key: string; label: string; modelName?: string }>;
    /** When set, selection calls set_model instead of slash re-dispatch. */
    mode?: "slash" | "set_model";
    /** Fired whenever the highlighted row moves (before Enter) — live preview. */
    onHighlight?: (item: { key: string; label: string; modelName?: string }) => void;
    /** Fired on Esc/close-without-Enter — e.g. restore a previewed theme. */
    onCancel?: () => void;
  } | null;
  /** Highlight index of the open slash picker (store-held for remount survival). */
  slashPickerSelection: number;
  /** Mission Control Start research overlay (create / import / plan). */
  /** Guided tour wizard (/tour) — open state + current step index. */
  tourOpen: boolean;
  tourStep: number;
  startResearch: {
    kind: "create" | "import" | "plan";
    draft: string;
    nameDraft?: string;
  } | null;
  /** /connect Q&A wizard (login token / public source): the menu rows open
   *  this form; the final Enter dispatches the built slash line (masked). */
  connectWizard: { flow: "login" | "source" } | null;
  /** Numbered Start actions menu (keyboard path when mouse unavailable). */
  startActionsMenuOpen: boolean;
  /** Claude-parity: Esc Esc / /rewind menu over earlier user prompts. */
  rewindMenuOpen: boolean;
  /** Claude-parity Ctrl+O: every tool roll-up, notice and thinking block expanded. */
  verboseTranscript: boolean;
  /** Claude-parity Ctrl+B / /bg: work running in the background (shell + turns). */
  backgroundTasks: Array<{
    id: string;
    kind: "shell" | "turn";
    label: string;
    status: "running" | "done" | "failed" | "cancelled";
    startedAt: number;
    endedAt?: number;
    detail?: string;
  }>;
  /** The streaming turn the user sent to the background (Ctrl+B), if any. */
  backgroundTurnTaskId: string | null;
  /** Claude-parity /btw: side questions (5 newest), never added to the history. */
  btw: {
    open: boolean;
    index: number;
    exchanges: Array<{ id: string; question: string; answer: string | null; error?: string }>;
  };
  /** K1.4: the citation chip's passage preview (lib/citation-preview.ts). */
  citationPreview: import("../types").CitationPreviewState | null;
  notificationCenterOpen: boolean;
  helpOpen: boolean;
  quitPending: boolean;
  quitDialogOpen: boolean;

  // ── Chat ──
  pendingChatDraft: string | null;
  /** Monotonic seq — ChatPanel instances adopt drafts by seq (no null-on-consume). */
  pendingChatDraftSeq: number;
  /** Live chat input contents (for letter-shortcut gating). */
  chatDraft: string;
  /** Live verify input on Research (for letter-shortcut gating). */
  verifyDraft: string;
  /** True while Live verify input is focused (blocks Research letter shortcuts). */
  verifyInputFocused: boolean;
  /** Knowledge space (F4): catalog list vs verify prompt. Tab toggles. */
  knowledgePane: "catalog" | "verify";
  /** Research keyboard focus pane (Tab cycle) — graph | agents | verify | approvals. */
  researchFocus: "graph" | "agents" | "verify" | "approvals" | "strategy" | "explain";
  /** Pending approval selected for keyboard y/n (no mouse). */
  selectedApprovalId: string | null;
  /** Ephemeral hint on the current space (e.g. Infra shortcut feedback). */
  spaceFeedback: string | null;
  chatMessages: ChatMessage[];
  isTyping: boolean;
  chatStreaming: boolean;

  /** Mid-turn status (tool name, awaiting approval) shown in chat/header. */
  chatTurnStatus: string | null;
  /** T5 topic-15: user messages queued behind the running turn (FIFO;
   * each entry is the new message's own id). */
  pendingChatQueue: string[];
  /** C3: latest per-turn token/cost/context read-model (session/cost). */
  cost: CostState | null;
  /** D3: last /mcp inventory outcome for the status footer badge. */
  mcpStatus: "ok" | "error" | null;
  /** W1b-6: latest host resource snapshot from bridge get_resources (5s poll). */
  resources: HostResources | null;
  /** W1c: turn artifacts from bridge get_artifacts (Slurm jobs, edited files). */
  turnArtifacts: string[];
  /** W1c: science loop state from bridge get_science_loop. */
  scienceLoop: ScienceLoopState | null;
  /** W1c: knowledge base state from bridge get_knowledge. */
  knowledgeBase: KnowledgeBaseState | null;
  /** W2: chat-first launch frame (set by cli entry; any space navigation exits). */
  chatFirst: boolean;
  /** W2: splash branding overlay visible (set by cli entry, timer dismissal). */
  splashOpen: boolean;
  /** W2-3: MCP server inventory from bridge list_mcp_servers (settings lists). */
  mcpServers: import("../lib/mcp-report").McpServerEntry[] | null;
  /** W2.1: temporal selectable overlay (skills / mcp / workflow), Esc closes. */
  listOverlay: "skills" | "mcp" | "workflow" | "changes" | "governance" | "jobgraph" | "sources" | "connect" | null;
  /** T5 topic-1: active team-detail overlay id (null = hidden). */
  teamDetailId: string | null;
  /** Armed one-shot teamup (B-family, 2026-09-27): the NEXT chat message
   * becomes a goal for a fresh team (bootstrap + start), then auto-disarm. */
  teamupArmed: boolean;
  teamupArmedCrew: "research" | "hpc" | "code";
  /** Parallel role width for the armed one-shot (1 = sequential pipeline). */
  teamupArmedParallel: number;
  /** ADR-092 slice 3: `/teamup on plan:<template>` — the armed one-shot runs
   * that team plan template (team.plan_run) with the next message as its
   * goal, instead of starting a crew. null = a crew is armed (or nothing). */
  teamupArmedPlan: { template: string; args: Record<string, string> } | null;
  /** Team overlay selection state (driven by global keymap, B-family 2026-09-27). */
  teamDetailCursor: number;
  teamDetailExpanded: string | null;
  /** Workflow-execution mission overlay (step table + cost, live). */
  execDetailId: string | null;
  /** T5 topic-5: active endpoint-detail overlay id (null = hidden). */
  endpointDetailId: string | null;
  /** 2026-10-01: active workflow-DEFINITION card id (null = hidden). */
  workflowDefId: string | null;
  /** T5 topic-6: active agent-detail overlay id (null = hidden). */
  agentDetailId: string | null;
  /** Optional id the current overlay renders (e.g. job id for jobgraph). */
  overlayTargetId: string | null;
  /** W2.5: coding session mode (builtin `code` skill active via /code). */
  codingMode: boolean;
  /** V4: the one summoned detail pane — view id or null when closed. */
  paneView: import("../types").Space | "logs" | null;
  /** V4: true while keyboard focus is inside the pane. The terminal view is
   *  clicked OUT of focus and never opens with it (2026-10-03 revision). */
  paneFocus: boolean;
  /** Drag-resized detail-pane width (cols); null = the percentage default. */
  paneWidthOverride: number | null;
  /** B4: current-turn thinking block (thinking_delta/done control events). */
  thinking: ThinkingState;
  /**
   * ADR-037: LAST MEASURED tool-selection record (persists across turns).
   * Drives the status phase label (live), the per-turn roll-up line and the
   * cost-chip tool-schema suffix; the chip shows the last measured value,
   * and the label only appears while a new record actually arrives.
   */
  toolSelection: import("../types").ToolSelectionRecord | null;
  /** The approval card on screen: the head of this tab's pending asks
   * (ADR-104 D-3; the companion answers, the console only renders). */
  toolApproval: ToolApprovalCard | null;
  /** Asks that arrived while a card was open, oldest first: answering the
   * head shows the next (a second ask used to replace the first card and
   * leave its waiter blocked, ADR-104 defect 10). */
  toolApprovalQueue: ToolApprovalCard[];
  /** B1: per-tool detail lines backing each expandable keyed notice row. */
  noticeDetails: Record<string, string[]>;
  /** B1: which keyed notice rows are currently expanded. */
  noticeExpanded: Record<string, boolean>;
  /** B1: most recent summary key — Enter on an empty draft toggles it. */
  lastSummaryKey: string | null;
  /** Client-measured time-to-first-token of the LAST completed model turn. */
  lastTurnTtftMs: number | null;
  /**
   * Tool calls of the IN-FLIGHT turn, mirrored live from the bridge. When
   * the assistant message finalizes, these attach to it (msg.toolCalls) so
   * the transcript keeps strict chronological order instead of one row
   * that updates in place for all turns.
   */
  turnToolActivity: import("../lib/tool-activity").ActivityLine[];
  /** Agent's todo list — mirror of the latest todowrite call (replaced whole). */
  sessionTodos: import("../lib/session-todos").SessionTodo[];
  /** W1: cumulative file edits for the session (side-by-side diff drawer). */
  sessionChanges: import("../types").SessionFileChange[];
  /** Structured session plan steps (bridge task-graph mirror of todo_write). */
  sessionPlan: import("../types").SessionPlanStep[];
  /** W1: governance overview (ownership/quotas/audit) for the overlay. */
  governance: import("../types").GovernanceOverview | null;
  /** B5: id of the latest error card (Enter/l on empty draft target it). */
  lastErrorCardId: string | null;
  /** B7: terminal bell + title on long-turn completion (/settings bell off). */
  completionBellEnabled: boolean;
  /** B3: transcript cursor (message index) or null when the chat input owns keys. */
  messageCursor: number | null;
  /** B3: edit-and-resend target (truncates transcript after this index on send). */
  editResendFromIndex: number | null;
  /** Wave C: server interaction mode (chat | plan | autopilot). */
  /** Active chat session id (bridge snapshot session.id; G-P9-2 fork source). */
  sessionId: string;
  /** Chat tabs (2026-09-29): parallel companion sessions, one bridge each.
   *  The flat chat fields above/below always mirror the ACTIVE tab; inactive
   *  tabs' values rest in tabs/registry snapshots. */
  chatTabs: ChatTabMeta[];
  activeChatTabId: string;
  /** Session knowledge-acquisition mode: may this chat create/extend
   * knowledge, and into which graph when pinned (bridge session state). */
  knowledgeAcquisition: { mode: string; graphs: string[] };
  /** ADR-075 G2: session workflow arm (server-venue compute; armed refuses
   * the non-UMRS submit lane by name). */
  workflowArmed: boolean;
  interactionMode: string;
  /** Wave C: server permission ladder (ask_always | auto_safe | deny | trust). */
  permissionMode: string;
  /** ADR-035: badge honesty — 'server'|'legacy'|'' (empty = not TRUST). */
  trustSource: string;
  /** Wave C: plan mode awaiting confirmation (plan card visible). */
  planAwaitingConfirmation: boolean;
  /** Wave C: the request the pending plan would execute. */
  planPendingRequest: string;
  /** Wave D: server-side session records for the /sessions pane. */
  sessionsList: SessionRecord[];
  /** Wave D: cursor row in the /sessions pane (clamped on fetch). */
  sessionsCursor: number;
  /** P2/P3 (plan 2026-09-09): /sessions filter chips (s state / v venue / A archives). */
  sessionFilters: import("../types").SessionFilters;
  /** Journal reachability from list_sessions (drives the venue picker gate). */
  sessionsRemote: { available: boolean | null; reason: string };
  /** P2: session-start venue picker modal visible. */
  venuePickerOpen: boolean;
  /** P2: in-process default venue (last pick pre-selects; not persisted — see plan §8a-1). */
  lastVenueChoice: import("../types").VenueChoice;
  /** Wave 2026-09-21 (ADR-046 A2): venue of the live session ("local" | "frontend" | …). */
  sessionVenue: string;
  /** Remote runtime projection for the live session (null = not a remote session / unknown). */
  venueRuntime: import("../lib/venue-runtime").VenueRuntime | null;
  /** Remote source picker (pushed-source choice + web policy) modal visible. */
  remoteSourcePickerOpen: boolean;
  /** "start" = create a frontend session, then start its sandbox (venue
   *  picker); "current" = start the sandbox for THIS session (/venue start);
   *  "manage" = push/unpush only. */
  remoteSourcePickerMode: RemoteSourcePickerMode;
  /** ADR-099 D-2: the session's web-access choice from its snapshot
   *  (mode "" = not chosen yet; null = a bridge that predates the field). */
  webAccess: import("../lib/web-access").WebAccessChoice | null;
  /** ADR-101: the session's local tool sandbox from its snapshot
   *  (null = a bridge that predates `session.sandbox`). */
  sandbox: import("../lib/sandbox").SandboxView | null;
  /** ADR-108 D-2: the worktree this tab's session works in (null = none). */
  worktree: import("../lib/worktree").WorktreeView | null;
  /** The web-access question, when open. */
  webAccessPrompt: WebAccessPrompt | null;
  /** ADR-102 (2026-11-01): the offer to start the local search server. */
  searchServerOffer: boolean;
  /** The answer for the remote session the source picker is about to create. */
  pendingRemoteWebAccess: import("../lib/web-access").WebChoice | null;
  /** Wave E: identity context — project/profile/source from the bridge config. */
  identityProjectId: string;
  identityProfile: string;
  identitySource: string;
  /** ADR-086 identity chip: display-only account (JWT sub) + circle refs. */
  identityUsername: string;
  /** Why identityUsername may be empty: "jwt" | "api-key" | "none" ("" = bridge predates the field). */
  identityCredential: string;
  identityCircles: string[];
  /** Wave E: authz flags from /health authz (badge ⚠︎ count until clean). */
  authzFlags: number;
  /** Wave H: code view — git change sets (A/M/D). */
  changeSets: import("../lib/git-changes").GitChange[];
  /** Wave H: cursor row in the code view. */
  changeCursor: number;
  /** Wave H: armed revert (destructive) — two-step confirm (r arms, R fires). */
  revertArmPath: string | null;
  /** Wave H: running test summary (latest). */
  testRunSummary: string | null;
  /** Pending Companion ask_user clarifying question. */
  askUser: { id: string | number; question: string; options: string[] } | null;
  /** M6: emit inline request-op cards for approved/rejected gates (default on). */
  ambientCardsEnabled: boolean;
  /** True after a chat turn used canned/fallback reply (Companion down, etc.). */
  chatFallbackActive: boolean;
  /** Last chat fallback reason shown in Mode / header. */
  chatFallbackDetail: string | null;

  // ── Domain Data ──
  agents: Agent[];
  goals: ResearchGoal[];
  tasks: ResearchTask[];
  knowledge: KnowledgeObject[];
  notifications: Notification[];
  events: ConsoleEvent[];
  unreadNotifications: number;

  // ── Platform / Connection ──
  connection: ConnectionStatus;
  currentModel: string | null;
  endpoints: UiEndpoint[];
  models: UiModel[];
  jobs: UiJob[];
  skills: UiSkill[];
  /** F4 (asset index) — owner-scoped rows via the snapshot/patch rails.
   * Tombstoned shape-keys are REMOVED at reducer time; never fabricated. */
  assets: import("../types").UiAsset[];
  /** F4: the snapshot's server-published hash token, routed verbatim into the
   * next patch call (never mutated locally). */
  assetsSnapshotHash: string | null;
  /** F4: base sha map the reducer maintains verbatim (`<kind>/<id> → shapeHash`);
   * exact data pass for each patch call. */
  assetsBase: Record<string, string>;
  backendLoading: boolean;
  backendError: string | null;
  /** Work-pane scope (2026-10-01 (15)): false — only the ACTIVE project's
   * rows are shown (strict: untagged/foreign rows hidden); true — all
   * projects, opt-in via the `A` key ("only when requested"). */
  workScopeAll: boolean;
  /** True when OfflineDataSource / demo fixtures are active. */
  demoMode: boolean;
  mascotState: "idle" | "listening" | "thinking" | "running" | "success" | "error";
  chatBackend: ChatBackend | null;
  platform: PlatformAdapter | null;
  /** Active data source, used by commands/routes to trigger backend actions. */
  dataSource: DataSource | null;
  /** Rolling samples of backend load (running jobs + active endpoints). */
  loadHistory: number[];
  /** Backend action / terminal output lines for the activity console. */
  terminalLines: string[];
}

const SPACE_NODE_IDS: Record<Space, string> = {
  dashboard: "dashboard",
  research: "research",
  tasks: "tasks",
  work: "work",
  knowledge: "knowledge",
  infrastructure: "infrastructure",
  loop: "loop",
  settings: "settings",
  sessions: "sessions",
  codeview: "codeview",
  catalog: "catalog",
  term: "term",
};

const initialNavTreeTemplate: NavNode = {
  id: "root",
  label: "R4",
  icon: "◆",
  expanded: true,
  children: [
    { id: "dashboard", label: "Dashboard", icon: "▣", route: "dashboard" },
    { id: "research", label: "Research", icon: "🔬", route: "research" },
    { id: "tasks", label: "Tasks", icon: "▤", route: "tasks" },
    { id: "work", label: "Workflows (UMRS)", icon: "▶", route: "work" },
    { id: "knowledge", label: "Knowledge", icon: "✦", route: "knowledge" },
    { id: "infrastructure", label: "Infrastructure", icon: "⬢", route: "infrastructure" },
    { id: "loop", label: "Workflow builder", icon: "⟳", route: "loop" },
    { id: "settings", label: "Settings", icon: "⚙", route: "settings" },
  ],
};

function createInitialNavTree(): NavNode {
  return structuredClone(initialNavTreeTemplate);
}

/** Fresh defaults for reset — must not reuse the live store object (Solid mutates in place). */
export function createInitialWorkspaceState(): WorkspaceState {
  const firstTab = makeTabMeta(1);
  return {
    project: null,
    researchBottomTab: "ops",
    navTree: createInitialNavTree(),
    selectedNodeId: "dashboard",
    selectedObjectId: null,
    activityTab: "agent-logs",
    activityOpen: false,
    contextPanelOpen: true,
    contextPanelTab: "overview",
    commandPaletteOpen: false,
    commandPaletteSeed: null,
    slashBarOpen: false,
    slashBarSeed: "",
    slashPickerOpen: false,
    slashPicker: null,
    slashPickerSelection: 0,
    tourOpen: false,
    tourStep: 0,
    startResearch: null,
    connectWizard: null,
    startActionsMenuOpen: false,
    rewindMenuOpen: false,
    verboseTranscript: false,
    btw: { open: false, index: 0, exchanges: [] },
    citationPreview: null,
    backgroundTasks: [],
    backgroundTurnTaskId: null,
    pendingChatDraft: null,
    pendingChatDraftSeq: 0,
    chatDraft: "",
    verifyDraft: "",
    verifyInputFocused: false,
    knowledgePane: "catalog",
    researchFocus: "graph",
    selectedApprovalId: null,
    spaceFeedback: null,
    notificationCenterOpen: false,
    helpOpen: false,
    quitPending: false,
    quitDialogOpen: false,
    agents: [],
    goals: [],
    tasks: [],
    knowledge: [],
    notifications: [],
    events: [],
    chatMessages: [
      {
        id: WELCOME_MESSAGE_ID,
        role: "assistant",
        content: welcomeChatContent({ demoMode: false }),
        timestamp: new Date().toISOString(),
      },
    ],
    unreadNotifications: 0,
    connection: { gateway: "connecting", umrs: "connecting", hpc: "connecting" },
    currentModel: null,
    endpoints: [],
    models: [],
    jobs: [],
    skills: [],
    assets: [],
    assetsSnapshotHash: null,
    assetsBase: {},
    backendLoading: false,
    workScopeAll: false,
    backendError: null,
    demoMode: false,
    mascotState: "idle",
    isTyping: false,
    chatStreaming: false,
    lastTurnTtftMs: null,
    chatTurnStatus: null,
    pendingChatQueue: [],
    cost: null,
    mcpStatus: null,
    resources: null,
    turnArtifacts: [],
    scienceLoop: null,
    knowledgeBase: null,
    chatFirst: false,
    splashOpen: false,
    mcpServers: null,
    listOverlay: null,
    teamDetailId: null,
    teamupArmed: false,
    teamupArmedCrew: "research",
    teamupArmedParallel: 1,
    teamupArmedPlan: null,
    teamDetailCursor: 0,
    teamDetailExpanded: null,
    execDetailId: null,
    endpointDetailId: null,
    workflowDefId: null,
    agentDetailId: null,
    overlayTargetId: null,
    codingMode: false,
    paneView: null,
    paneFocus: false,
    paneWidthOverride: null,
    thinking: { text: "", open: false, expanded: false, startedAtMs: null, elapsedMs: 0 },
    turnToolActivity: [],
    sessionTodos: [],
    sessionChanges: [],
    sessionPlan: [],
    governance: null,
    toolSelection: null,
    toolApproval: null,
    toolApprovalQueue: [],
    askUser: null,
    noticeDetails: {},
    noticeExpanded: {},
    lastSummaryKey: null,
    lastErrorCardId: null,
    completionBellEnabled: true,
    messageCursor: null,
    editResendFromIndex: null,
    sessionId: "",
    chatTabs: [firstTab],
    activeChatTabId: firstTab.id,
    knowledgeAcquisition: { mode: "open", graphs: [] },
    workflowArmed: false,
    interactionMode: "chat",
    permissionMode: "ask_always",
    trustSource: "",
    planAwaitingConfirmation: false,
    planPendingRequest: "",
    sessionsList: [],
    sessionsCursor: 0,
    sessionFilters: { state: "all", venue: "all", includeArchived: false },
    sessionsRemote: { available: null, reason: "" },
    venuePickerOpen: false,
    lastVenueChoice: "local",
    sessionVenue: "local",
    venueRuntime: null,
    remoteSourcePickerOpen: false,
    remoteSourcePickerMode: "start",
    webAccess: null,
    sandbox: null,
    worktree: null,
    webAccessPrompt: null,
    searchServerOffer: false,
    pendingRemoteWebAccess: null,
    identityProjectId: "",
    identityProfile: "default",
    identitySource: "",
    identityUsername: "",
    identityCredential: "",
    identityCircles: [],
    authzFlags: 0,
    changeSets: [],
    changeCursor: 0,
    revertArmPath: null,
    testRunSummary: null,
    chatFallbackActive: false,
    chatFallbackDetail: null,
    ambientCardsEnabled: true,
    chatBackend: null,
    platform: null,
    dataSource: null,
    loadHistory: [],
    terminalLines: [],
  };
}

const [workspace, rawSetWorkspace] = createStore<WorkspaceState>(createInitialWorkspaceState());
export { workspace };

/**
 * H-7 stage 3: the write-through wrapper. EVERY `setWorkspace("<scoped>",
 * …)` — actions, adapters, tests, anything — mirrors into the active tab's
 * own store (same variadic args), so the flipped `activeTab()` backing never
 * serves stale state. Non-scoped calls pass straight through. The mirror is
 * idempotent with the swap's boundary mirrors (same values, both writes).
 */
export const setWorkspace: SetStoreFunction<WorkspaceState> = ((...args: unknown[]) => {
  const field = args[0];
  const scoped =
    typeof field === "string" && (CHAT_SCOPED_FIELDS as readonly string[]).includes(field);
  // Stage 3b: under a background event target, scoped writes go ONLY into the
  // owning tab's store — never into the live tab's singleton (or its mirrors).
  const override = scoped ? eventTargetTab() : null;
  if (override && override !== workspace.activeChatTabId) {
    const rt = getRuntime(override);
    writeThrough(rt, field, args);
    // Snapshot parity: the rest snapshot must carry the same post-write
    // state (reopen/rehydrate reads it when no store exists) — mirror the
    // field from the store's post-write value, decoupled like the store.
    if (rt.snapshot && rt.tabStore && typeof field === "string") {
      const fresh = unwrap(rt.tabStore.state[field as keyof typeof rt.tabStore.state] as never);
      rt.snapshot[field] = fresh == null ? fresh : structuredClone(fresh);
    }
    return;
  }
  (rawSetWorkspace as (...a: unknown[]) => void)(...(args as []));
  if (scoped) {
    // getRuntime (not runtimeOrNull): the first tab's runtime is created lazily
    // — the funnel must not silently drop mirrors before any wiring exists.
    writeThrough(getRuntime(override ?? workspace.activeChatTabId), field, args);
  }
}) as SetStoreFunction<WorkspaceState>;

/** Mirror one setter call into a tab store (decoupled against proxy aliasing). */
function writeThrough(rt: TabRuntime, field: unknown, args: unknown[]): void {
  if (!rt.tabStore) return;
  if (args.length === 2 && typeof args[1] !== "function") {
    // Plain value form: never write a live reference into a second store —
    // solid proxies alias it across the stores and field reads go cross-tab.
    // Clone the reactively-stripped value so each store owns its own copy.
    const value = args[1];
    const copy =
      value !== null && typeof value === "object"
        ? structuredClone(unwrap(value))
        : value;
    (rt.tabStore.set as (...a: unknown[]) => void)(field, copy);
    return;
  }
  // Path / updater / predicate forms compute per-store values independently.
  (rt.tabStore.set as (...a: unknown[]) => void)(...args);
}

/**
 * H-7 stage 3: the named funnel for chat-scoped writes, delegating to the
 * wrapped `setWorkspace` (which mirrors into the active tab's store). The
 * name marks intent in the actions layer; imports of raw `setWorkspace` can
 * never go stale, so nothing bypasses the mirror.
 */
export function setScopedField<K extends ChatScopedField>(
  field: K,
  ...args: unknown[]
): void {
  (setWorkspace as (...a: unknown[]) => void)(field, ...args);
}

/**
 * H-7 stage 2: the migration accessor for chat-scoped fields. Read
 * chat-scoped state through `activeTab()` instead of `workspace.` — during
 * stage 2 the backing is the same singleton slice (a cast: every read is
 * byte-identical to reading the singleton directly), and stage 3 flips it
 * to the active tab's own `ChatTabStore` without readers changing. Global
 * fields (endpoints, jobs, skills, pane chrome…) keep reading `workspace`.
 *
 * The accessor returns the reactive store object itself, so Solid tracking
 * behaves exactly as with the singleton.
 */
export function activeTab(): ChatTabState {
  // Render-stable: ALWAYS the active tab's store (never the event target).
  // This is deliberate postmortem knowledge (H-7 close, 2026-10-19): when
  // activeTab() honored the event target, a fine-grained render effect that
  // evaluated inside one background dispatch (withEventTarget) bound to the
  // background tab's store and never repainted (pty-live journey 'tab
  // switch during a turn' fails exactly that way). Reads inside a handler
  // dispatched under withEventTarget that must see the EVENT's tab go
  // through eventScopedTab() instead.
  const rt = runtimeOrNull(workspace.activeChatTabId);
  if (rt?.tabStore) return rt.tabStore.state;
  return workspace as unknown as ChatTabState;
}

/** Reads for code dispatched under withEventTarget: the EVENT'S own tab
 *  when one is pinned, the active tab otherwise. NEVER reachable from
 *  render code (render binds at evaluation time; see activeTab above). */
export function eventScopedTab(): ChatTabState {
  const targetId = eventTargetTab();
  if (targetId) {
    const target = runtimeOrNull(targetId)?.tabStore;
    if (target) return target.state;
  }
  const rt = runtimeOrNull(workspace.activeChatTabId);
  if (rt?.tabStore) return rt.tabStore.state;
  return workspace as unknown as ChatTabState;
}

/**
 * Effective expansion state of one notice/tool row. An explicit per-row
 * boolean ALWAYS wins; `verboseTranscript` (Ctrl+O) is only the DEFAULT.
 * This is what makes every collapse button functional in verbose mode —
 * before it, verbose forced every row expanded and collapse clicks were
 * dead (maintainer report 2026-09-30).
 */
export function rowExpanded(key: string, defaultExpanded = false): boolean {
  const explicit = workspace.noticeExpanded[key];
  return explicit ?? (workspace.verboseTranscript || defaultExpanded);
}

/** B5: retry callbacks keyed by error-card message id. */
const errorCardRetries = new Map<string, () => void>();

// ── Chat Abort Controller ──────────────────────────────────────────────

/** Per-tab in-flight turn abort controllers (chat tabs: turns run concurrently). */
const chatAborts = new Map<string, AbortController>();

/** Sessions already renamed by the aggressive placeholder pass (once/run). */
const aggressiveRenamedSessions = new Set<string>();

/**
 * Build a one-shot project-context message prepended to each chat turn so the
 * Companion answers about the ACTIVE project instead of describing the AI4S
 * platform. F4/verify lists the local project store; lab_catalog_search is the
 * governed Gateway catalog (may or may not contain the same files).
 */
function buildProjectChatContext(): string | null {
  const project = workspace.project;
  if (!project) return null;
  const files = workspace.knowledge.map((k) => k.name).filter(Boolean);
  const fileList = files.length > 0 ? files.slice(0, 30).join(", ") : "(none listed yet)";
  return [
    `[Project context] Active project: ${project.id} ("${project.name}").`,
    `Local project-store files (F4 / r4 project verify): ${fileList}.`,
    "Answer questions about THIS project.",
    "If lab_catalog_search returns no hits, do not retry the same query — that tool is the governed Gateway catalog, a different store. Use knowledge_show or r4 project verify for local RC1 artifacts.",
    "Do not describe the AI4S platform unless the user asks.",
  ].join(" ");
}

/** True when the given id still exists in the active project's objects. */
function currentObjectIsInProject(id: string | null): boolean {
  return (
    !!id &&
    (workspace.knowledge.some((k) => k.id === id) ||
      workspace.tasks.some((t) => t.id === id) ||
      workspace.goals.some((g) => g.id === id))
  );
}

/** Entering F4 with a stale selection: fall back to first knowledge/task row. */
function ensureKnowledgeSelection(): void {
  if (currentObjectIsInProject(workspace.selectedObjectId)) return;
  const first = workspace.knowledge[0] ?? workspace.tasks[0];
  if (first) setWorkspace("selectedObjectId", first.id);
}

// ── Chat streaming helpers ──────────────────────────────────────────────

type ChatStreamProgress = {
  transcript?: ChatMessage[];
  assistantId: string | null;
  modelName: string | undefined;
  fallback: boolean;
  citations: (typeof workspace.chatMessages)[number]["citations"];
  /** Every assistant segment id opened this turn (round breaks split rounds). */
  segmentIds: string[];
  /** Owning chat tab (absent for tab-less callers). */
  tabId?: string;
  /** The user row this turn answers (tagged with the session turn id). */
  userMessageId?: string;
  /** The turn ended with a typed session error (refused, or its save lost):
   *  its recovery card stands in for a missing reply, and the tab's queue
   *  is not popped onto a session the bridge will refuse again. */
  refused?: boolean;
};

/** Pin the accumulated turn tool activity under one assistant segment. */
/** Pin flushed tool lines under one segment. Slice mode (default) consumes
 *  exactly the oldest flushed-but-unpinned slice OFF THE HEAD — each
 *  roundBreak's pin pairs with its own round's flush in arrival order,
 *  even after a background stretch queued several (#33). `all` drains the
 *  whole unpinned tail (turn finalization: trailing textless rounds). */
function pinTurnToolActivityTo(id: string, opts?: { all?: boolean }): void {
  if (workspace.turnToolActivity.length === 0) return;
  // Slice 0 means no tracked flush is pending (lines appended directly,
  // e.g. a backend driving setTurnToolActivity itself): drain the tail —
  // invocation order is the append order either way.
  const take = opts?.all ? workspace.turnToolActivity.length : shiftTurnPinSlice() || workspace.turnToolActivity.length;
  if (take === 0) return;
  const calls = workspace.turnToolActivity.slice(0, take);
  setScopedField("chatMessages", (m: ChatMessage) => m.id === id, "toolCalls", calls);
  // Enter-on-empty-draft targets the group's top-level expand row.
  actions.setNoticeDetails(
    `group:${id}`,
    calls.map((line) => line.text),
  );
  const rest = workspace.turnToolActivity.slice(take);
  if (opts?.all) clearTurnPinSlices();
  setScopedField("turnToolActivity", rest);
}

/**
 * Every tool line of the turn opened by user message `turnId`: the lines
 * already pinned under its assistant segments plus the unpinned tail. The
 * pinning above empties `turnToolActivity` before the turn finishes, so the
 * completion tail and the ghost gate read the turn from here (2026-10-17).
 */
function turnToolLines(turnId: string): import("../lib/tool-activity").ActivityLine[] {
  const start = workspace.chatMessages.findIndex((m) => m.id === turnId);
  const pinned = start < 0
    ? []
    : workspace.chatMessages.slice(start + 1).flatMap((m) => m.toolCalls ?? []);
  return [...pinned, ...workspace.turnToolActivity];
}

/** Apply one streamed chunk to the assistant bubble (create first, append rest). */
function applyStreamMeta(progress: ChatStreamProgress, chunk: ChatStreamChunk): void {
  if (chunk.transcript) progress.transcript = chunk.transcript;
  if (chunk.model) progress.modelName = chunk.model;
  if (chunk.fallback) progress.fallback = true;
  if (chunk.citations && chunk.citations.length > 0) progress.citations = chunk.citations;
}

function handleStreamRoundBreak(progress: ChatStreamProgress): void {
  // Model round boundary: close the current segment WITH the tool activity
  // that followed its text pinned underneath, then let the next delta open
  // a fresh segment. Keeps text and tool work in invocation-time order.
  const id = progress.assistantId;
  if (!id) return;
  setScopedField("chatMessages", (m: ChatMessage) => m.id === id, "streaming", false);
  pinTurnToolActivityTo(id);
  progress.assistantId = null;
}

function appendStreamDelta(progress: ChatStreamProgress, chunk: ChatStreamChunk): void {
  if (progress.assistantId) {
    const id = progress.assistantId;
    setScopedField("chatMessages", (m: ChatMessage) => m.id === id, "content", (c: string) => c + chunk.delta);
    return;
  }
  const id = ulid();
  progress.assistantId = id;
  progress.segmentIds.push(id);
  setScopedField("chatMessages", (list: ChatMessage[]) => [
    ...list,
    {
      id,
      role: "assistant" as const,
      content: chunk.delta!,
      timestamp: new Date().toISOString(),
      model: progress.modelName,
      streaming: true,
      fallback: progress.fallback,
      citations: progress.citations,
    },
  ]);
}

/** send_message named the session turn the user row opened (r2 #17). */
function tagUserTurn(progress: ChatStreamProgress, turnId: string | undefined): void {
  const id = progress.userMessageId;
  if (!turnId || !id) return;
  setScopedField("chatMessages", (m: ChatMessage) => m.id === id, "turnId", turnId);
}

/** A turn ended by a typed session error. Refused up front (a conflict
 * before the turn ran): its user row leaves the transcript — it was never
 * saved — and its text returns to the composer with the recovery card. A
 * conflict met by the end-of-turn save (stage after_turn) comes after the
 * model and the tools ran: the rows stay and nothing asks for it again
 * (cycle 3 review: a resend ran every tool twice). Either way the tab's
 * queue waits for the reload (bridge/session-recovery). */
function handleSessionErrorChunk(progress: ChatStreamProgress, info: NonNullable<ChatStreamChunk["sessionError"]>): void {
  progress.refused = true;
  const ran = info.code === "session_conflict" && info.stage === "after_turn";
  const id = ran ? undefined : progress.userMessageId;
  const refused = id ? workspace.chatMessages.find((m) => m.id === id)?.content : undefined;
  if (id && info.code === "session_conflict") {
    setScopedField("chatMessages", (list: ChatMessage[]) => list.filter((m) => m.id !== id));
  }
  void import("../bridge/session-recovery").then(({ offerSessionRecovery }) => offerSessionRecovery(info, refused));
}

/** Queue appends still waiting for the bridge's answer, and the ones among
 * them this console already SENT (popQueuedTurn) or handed back or dropped
 * on purpose (drainQueued, clearChat). A failed append for a settled row
 * never rolls back. Both sets lose the id once its append answers, and a row
 * is marked settled only while its append is in flight, so neither set
 * outlives the appends (cycle 3 verifier round 5: one id leaked per queued
 * message whose append answered first). */
const appendsInFlight = new Set<string>();
const settledQueuedIds = new Set<string>();

function settleQueued(id: string): void {
  if (appendsInFlight.has(id)) settledQueuedIds.add(id);
}

function appendAnswered(id: string): void {
  appendsInFlight.delete(id);
  settledQueuedIds.delete(id);
}

/** Test hook: the append bookkeeping's sizes (both 0 once appends answered). */
export function queueAppendBookkeepingForTests(): { inFlight: number; settled: number } {
  return { inFlight: appendsInFlight.size, settled: settledQueuedIds.size };
}

/** A queue append that was not applied. Only while this tab still owns the
 * flat store (another tab's rows are never touched), and never for a row
 * this console already SETTLED: when the append fails after the turn ended,
 * popQueuedTurn already sent its text as its own turn, and returning text
 * that ran asked for a resend that ran its tools twice (cycle 3 verifier
 * round 3); a take-back, send-now, local interrupt or clear already gave the
 * text back or dropped it on purpose. A definite refusal rolls back; an
 * append with no answer (a timeout) may have been applied, so nothing is
 * rolled back and the card says so (round 5). */
function failedQueuedAppend(turnTabId: string, queuedMsg: ChatMessage, outcome: { error?: unknown; unconfirmed?: boolean }): void {
  if (workspace.activeChatTabId !== turnTabId) return;
  if (settledQueuedIds.has(queuedMsg.id)) return;
  if (outcome.unconfirmed) noteUnconfirmedAppend(queuedMsg, outcome.error);
  else rollBackQueuedAppend(queuedMsg, outcome.error);
}

/** The append got no answer: in the frontend venue the server may hold the
 * text and run it, so handing it back invited a resend that ran twice
 * (cycle 3 verifier round 5). The row stays, the composer is not touched,
 * and the card carries the text and says to check before resending. It is
 * keyed, so a remote turn's transcript replacing the rows keeps it. */
function noteUnconfirmedAppend(queuedMsg: ChatMessage, error: unknown): void {
  const why = error instanceof Error ? error.message : "no answer from the bridge";
  const where = workspace.sessionVenue === "frontend"
    ? "the server may have queued it and will run it"
    : "it stays queued here, but a resume may not replay it";
  actions.sendErrorCard({
    what: "queue append unconfirmed — it may have been queued; check the transcript before resending",
    action: "needs_you",
    detail: `${why}; ${where}. Text: ${queuedMsg.content}`,
  });
  const cardId = workspace.lastErrorCardId;
  setScopedField("chatMessages", (m: ChatMessage) => m.id === cardId, "noticeKey", `queue-unconfirmed@${queuedMsg.id}`);
}

/** A queue append the bridge refused (offline, or the session changed in
 * another console while the turn ran: its save lost the compare-and-swap).
 * The text was never queued nor stored, so its row leaves the transcript (as
 * a plain user row it read as sent) and the text returns to an EMPTY
 * composer, else a notice carries it (cycle 3 verifier round 2). This holds
 * even when the row left pendingChatQueue unsent: a frontend turn's
 * transcript or a frontend interrupt clears the queue without sending, and
 * skipping those lost the typed text silently (round 4). */
function rollBackQueuedAppend(queuedMsg: ChatMessage, error: unknown): void {
  setScopedField("pendingChatQueue", (ids: string[]) => ids.filter((id) => id !== queuedMsg.id));
  setScopedField("chatMessages", (list: ChatMessage[]) => list.filter((m) => m.id !== queuedMsg.id));
  const draft = (activeTab().chatDraft ?? "").trim();
  if (draft) actions.sendSystemNotice(`not queued (the composer keeps your draft): ${queuedMsg.content}`);
  else actions.setPendingChatDraft(queuedMsg.content);
  const back = draft ? "" : " — it is back in the composer";
  void import("../bridge/session-recovery").then(({ sessionErrorOf }) => {
    const info = sessionErrorOf(error);
    const detail = info
      ? `Message was not queued: ${info.message}`
      : "Message was not queued; retry when connected.";
    actions.sendErrorCard({ what: `queue append failed${back}`, action: "dropped", detail });
  });
}

function applyStreamChunk(progress: ChatStreamProgress, chunk: ChatStreamChunk): void {
  applyStreamMeta(progress, chunk);
  tagUserTurn(progress, chunk.turnId);
  if (chunk.sessionError) {
    handleSessionErrorChunk(progress, chunk.sessionError);
    return;
  }
  if (chunk.roundBreak) {
    // Events route into the owning tab live and in order; the pin answers
    // its own round without a replay leg (post stage-4).
    handleStreamRoundBreak(progress);
    return;
  }
  if (typeof chunk.delta !== "string" || chunk.delta.length === 0) return;
  appendStreamDelta(progress, chunk);
}

/** Aborted turn: close the open segment honestly, pin nothing — the
 *  still-running bridge side owns the leftover lines until turn_stopped. */
function finalizeAbortedStream(progress: ChatStreamProgress, finish: (text: string) => void): void {
  const id = progress.assistantId;
  if (id) {
    setScopedField("chatMessages", (m: ChatMessage) => m.id === id, "streaming", false);
  }
  const lastId = markTurnStopped(id ?? progress.segmentIds[progress.segmentIds.length - 1]);
  finish(workspace.chatMessages.find((m) => m.id === lastId)?.content ?? "");
}

/**
 * An interrupted turn says so, in the words the bridge stores for the model
 * (response_for_cancelled_turn): the partial reply gains a trailing
 * "(Generation stopped.)", or that marker is the turn's reply when nothing
 * streamed. A partial reply read as a complete answer, and an Esc right after
 * Enter left a bare user row (stabilization sweep 2026-10-21, chat#9/#2).
 * Returns the marked row's id.
 */
function markTurnStopped(lastId: string | undefined): string {
  if (lastId) {
    setScopedField(
      "chatMessages",
      (m: ChatMessage) => m.id === lastId,
      "content",
      (content: string) => `${content.trimEnd()}\n\n${STOPPED_MESSAGE}`,
    );
    return lastId;
  }
  const row: ChatMessage = { id: ulid(), role: "assistant", content: STOPPED_MESSAGE, timestamp: new Date().toISOString() };
  setScopedField("chatMessages", (list: ChatMessage[]) => [...list, row]);
  return row.id;
}

/** Stream errored or produced nothing — surface the honest canned reply. */
function failStreamChat(detail: string, finish: (text: string) => void): void {
  const text = companionUnavailableReply(detail);
  appendAssistantMessage(text, { model: "offline", fallback: true });
  actions.setChatFallback(true, detail);
  finish(text);
}

/** Final fallback bookkeeping for a completed stream. */
function closeStreamFallbackFlag(id: string, fallback: boolean): void {
  if (!fallback) {
    actions.setChatFallback(false);
    return;
  }
  setScopedField("chatMessages", (m: ChatMessage) => m.id === id, "fallback", true);
  const content = workspace.chatMessages.find((m) => m.id === id)?.content ?? "";
  actions.setChatFallback(true, content);
}

/** Close out the streamed assistant message (flags, citations, finish banner). */
function finalizeStreamMessage(
  progress: ChatStreamProgress,
  finish: (text: string) => void,
  opts?: { aborted?: boolean },
): void {
  // Flush the tail slice before any leftover pin below (stage 4): the
  // trailing textless round's tools pin under the last text segment. On
  // abort the accumulator stays untouched — an interrupted turn's lines
  // flush late (turn_stopped) and must never pin under the next turn.
  if (opts?.aborted) {
    finalizeAbortedStream(progress, finish);
    return;
  }
  flushTurnSummary();
  const id = progress.assistantId;
  if (!id) {
    // No open segment: either an empty stream or the last chunk was a round
    // break that just closed the final segment (trailing textless round).
    if (progress.segmentIds.length === 0) {
      // A refused turn has its recovery card instead of a reply.
      if (progress.refused) finish("");
      else failStreamChat("empty chat stream", finish);
      return;
    }
    if (progress.modelName) {
      for (const segId of progress.segmentIds) {
        setScopedField("chatMessages", (m: ChatMessage) => m.id === segId, "model", progress.modelName);
      }
    }
    const lastId = progress.segmentIds[progress.segmentIds.length - 1]!;
    // Tools from the trailing textless round still pin under the last text.
    pinTurnToolActivityTo(lastId, { all: true });
    closeStreamFallbackFlag(lastId, progress.fallback);
    finish(workspace.chatMessages.find((m) => m.id === lastId)?.content ?? "");
    return;
  }
  setScopedField("chatMessages", (m: ChatMessage) => m.id === id, "streaming", false);
  // Attribute every segment of this turn to the model, not only the last.
  if (progress.modelName) {
    for (const segId of progress.segmentIds) {
      setScopedField("chatMessages", (m: ChatMessage) => m.id === segId, "model", progress.modelName);
    }
  }
  // Pin the turn's remaining tool activity under this final reply —
  // chronological grouping (round breaks already pinned earlier segments).
  pinTurnToolActivityTo(id, { all: true });
  closeStreamFallbackFlag(id, progress.fallback);
  if (progress.citations && progress.citations.length > 0) {
    setScopedField("chatMessages", (m: ChatMessage) => m.id === id, "citations", progress.citations);
  }
  finish(workspace.chatMessages.find((m) => m.id === id)?.content ?? "");
}

/** Keep pumping? Waits out a background stretch first; false once the turn
 *  was aborted or its tab closed meanwhile. */
async function pumpMayContinue(abort: AbortController, tabId?: string): Promise<boolean> {
  if (abort.signal.aborted) return false;
  const open = tabId ? await waitForTabTurn(tabId) : true;
  return open && !abort.signal.aborted;
}

/**
 * Before a turn settles into the flat store: wait until its tab is the
 * active one (the flat store is that tab's state only then). True when the
 * tab was closed — the turn then settles nowhere: closing a background tab
 * finalized its turn into the ACTIVE tab (canned failure row, cleared
 * streaming flags, popped and re-sent that tab's queue; TUI audit r2 #31).
 */
async function turnOwnerGone(tabId?: string): Promise<boolean> {
  if (!tabId) return false;
  return !(await waitForTabTurn(tabId));
}

/** T5 topic-15+: the next queued message becomes the next turn — durable
 * persistence rides the bridge's session.queue_pop so cross-resume replay
 * stays byte-identical. */
function popQueuedTurn(): void {
  const [nextQueued, ...rest] = workspace.pendingChatQueue;
  if (!nextQueued) return;
  settleQueued(nextQueued);
  setScopedField("pendingChatQueue", rest);
  void import("../lib/chat-queue-rail").then(({ queueRemote }) => queueRemote("pop"));
  // The queued bubble becomes the new turn's user row: sendChatMessage
  // appends that row, so the placeholder goes (it used to stay, and
  // every queued message showed twice; found by the r2 glue lane).
  const popped = workspace.chatMessages.find((m) => m.id === nextQueued);
  setScopedField("chatMessages", (list: ChatMessage[]) => list.filter((m) => m.id !== nextQueued));
  if (popped) actions.sendChatMessage(popped.content);
}

/** T5 topic-12: completion tail — fired only when the turn actually ran
 * tool work (capability-detected: never a banner on text-only turns,
 * which keeps transcript rows honest over steady conversations). */
function sendTurnCompletionTail(turnId: string, actionsCount: number, elapsedMs: number): void {
  if (actionsCount <= 0) return;
  const summaryParts: string[] = [
    `${actionsCount} action${actionsCount === 1 ? "" : "s"}`,
    elapsedMs > 1000 ? `${(elapsedMs / 1000).toFixed(1)}s` : "",
  ];
  const suffix = summaryParts.filter(Boolean).join(" · ");
  actions.sendTurnComplete(turnId, suffix ? `done (${suffix})` : "done");
}

/** T5 topic-19 tail: ghost-update gate — completion claims without a
 * single write-family tool firing the same turn surface the banner
 * honestly. Capability-detected: never fires on fallback rows
 * ([Offline]/[Chat unavailable]), which are the canned refusal strings
 * rather than an LLM-authored claim. */
function flagGhostUpdate(turnId: string, turnLines: import("../lib/tool-activity").ActivityLine[], finalText: string): void {
  try {
    if (finalText.startsWith("[Offline]") || finalText.startsWith("[Chat unavailable]")) return;
    const gate = ghostGateVerdict(turnLines, finalText);
    if (gate.verdict === "ghost-suspect" && gate.reason) {
      actions.upsertSystemNotice(`ghost-gate:${turnId}`, `⚠ ${gate.reason}`);
    }
  } catch {
    // never crash completion on the gate evaluator
  }
}

/** Consume a live chat stream into the workspace store. */
async function runChatStream(
  backend: ChatBackend,
  wireContent: string,
  historyWithContext: ChatHistoryItem[],
  model: string | undefined,
  abort: AbortController,
  finish: (text: string, opts?: { holdQueue?: boolean }) => void,
  /** Owning chat tab (2026-09-29): the pump suspends while the tab is in the
   *  background — the bridge generator buffers deltas meanwhile, so the turn
   *  truthfully pauses and resumes without loss. */
  tabId?: string,
  userMessageId?: string,
): Promise<void> {
  const progress: ChatStreamProgress = {
    assistantId: null,
    modelName: undefined,
    fallback: false,
    citations: undefined,
    segmentIds: [],
    tabId,
    userMessageId,
  };
  setScopedField("lastTurnTtftMs", null);
  try {
    for await (const chunk of backend.chatStream!(wireContent, historyWithContext, model, abort.signal)) {
      if (!(await pumpMayContinue(abort, tabId))) break;
      if (chunk.ttftMs != null && workspace.lastTurnTtftMs == null) {
        setScopedField("lastTurnTtftMs", chunk.ttftMs);
      }
      applyStreamChunk(progress, chunk);
    }
  } catch (err) {
    if (await turnOwnerGone(tabId)) return;
    failStreamChat(err instanceof Error ? err.message : String(err), finish);
    return;
  }
  if (await turnOwnerGone(tabId)) return;
  const settle = (text: string) => finish(text, { holdQueue: progress.refused });
  finalizeStreamMessage(progress, settle, { aborted: abort.signal.aborted });
  if (progress.transcript && !abort.signal.aborted) {
    const notices = workspace.chatMessages.filter((message) => message.noticeKey);
    setScopedField("chatMessages", [...progress.transcript, ...notices]);
    setScopedField("pendingChatQueue", []);
  }
}

/** The chat backend keeps its own history in a companion session. */
function keepsCompanionSession(): boolean {
  return workspace.chatBackend?.name === "companion-bridge" && !isOfflineBackend();
}

/** Re-key the notice row at `at` to `key@<row id>`, details included; the
 * live key is then free for the next occurrence's row. */
function archiveKeyedNotice(key: string, at: number): void {
  const row = workspace.chatMessages[at]!;
  const archived = `${key}@${row.id}`;
  const details = workspace.noticeDetails[key];
  if (details) setScopedField("noticeDetails", archived, details);
  setScopedField("noticeDetails", key, undefined as unknown as string[]);
  setScopedField("chatMessages", at, "noticeKey", archived);
}

/** `session.truncate`: null once the companion history is cut at `turnId`,
 * else the failure (reported by the caller, in the owning tab). */
async function cutCompanionHistory(turnId: string): Promise<unknown> {
  try {
    await getCompanionBridge().request("session.truncate", { turn_id: turnId }, 30_000);
    return null;
  } catch (err) {
    return err ?? new Error("session.truncate failed");
  }
}

/** Tabs with a session.truncate in flight: a second Enter is ignored. */
const rewindsInFlight = new Set<string>();

// A refusal is permanent (slash row, remote venue, missing turn id), so it
// cancels the edit itself — keeping the marker dead-locked the tab: every
// later Enter re-hit the refusal and the message vanished (2026-10-19).
const REWIND_KEEP = "nothing was cut or sent — the edit is cancelled; your draft stays, Enter posts it as a new turn";

/** Why the companion history cannot be cut at this row, or null. */
function rewindRefusal(row: ChatMessage | undefined): string | null {
  if (workspace.sessionVenue === "frontend") {
    return `cannot rewind a remote session: its context lives on the server — fork the session to run it locally (/session fork); ${REWIND_KEEP}`;
  }
  if (row?.role === "user" && row.content.startsWith("/")) {
    return `cannot rewind to a slash command: it is not a chat turn; ${REWIND_KEEP}`;
  }
  if (!row?.turnId) return `cannot rewind: this turn has no session id — ${REWIND_KEEP}`;
  return null;
}

/**
 * Cut the companion history at the edited row's turn (r2 #17). One rewind
 * per tab at a time; the outcome lands in the tab that asked, once it is
 * active again — false when it was closed meanwhile (r2 #17 review).
 */
async function rewindCompanionTurn(idx: number): Promise<boolean> {
  const row = workspace.chatMessages[idx];
  const refusal = rewindRefusal(row);
  if (refusal) {
    actions.sendSystemNotice(refusal);
    actions.cancelEditResend();
    return false;
  }
  const tabId = workspace.activeChatTabId;
  if (rewindsInFlight.has(tabId)) return false;
  rewindsInFlight.add(tabId);
  const failure = await cutCompanionHistory(row!.turnId!).finally(() => rewindsInFlight.delete(tabId));
  if (!(await waitForTabTurn(tabId))) return false;
  if (failure === null) return true;
  actions.sendErrorCard({
    what: "rewind failed — nothing was cut or sent",
    action: "dropped",
    detail: failure instanceof Error ? failure.message : String(failure),
  });
  return false;
}

/**
 * Interrupt drops the local queue VISIBLY: the queued bubbles leave the
 * transcript (the model never received them; they used to turn into plain
 * "sent" rows), the text returns to an empty composer, and one notice says
 * so (stabilization sweep 2026-10-21, chat#3).
 */
function dropQueuedOnInterrupt(): void {
  const dropped = actions.drainQueued();
  setScopedField("pendingChatQueue", []);
  void import("../lib/chat-queue-rail").then(({ queueRemote }) => queueRemote("clear"));
  if (dropped.length === 0) return;
  const noun = dropped.length === 1 ? "prompt" : "prompts";
  if ((activeTab().chatDraft ?? "").trim()) {
    actions.sendSystemNotice(`interrupted — ${dropped.length} queued ${noun} not sent: ${joinQueued(dropped)}`);
    return;
  }
  actions.setPendingChatDraft(joinQueued(dropped));
  actions.sendSystemNotice(`interrupted — ${dropped.length} queued ${noun} not sent; back in the composer`);
}

/** Every message a web-access question holds, in send order. */
export function heldMessages(prompt: WebAccessPrompt): string[] {
  return [prompt.pendingText ?? "", ...(prompt.pendingMore ?? [])].filter((t) => t.trim() !== "");
}

/** True when a held message goes back to the composer (typed text and slash
 * follow-ups); a shell or terminal share is machine output and is dropped. */
export function heldReturnsToComposer(prompt: WebAccessPrompt): boolean {
  return !prompt.pendingRelay || prompt.pendingKeep === true;
}

/** The active tab still needs the agent's web access before a turn (D-2). */
function tabNeedsWebChoice(): boolean {
  return needsWebChoice(eventScopedTab().webAccess, eventScopedTab().venueRuntime?.state);
}

/** Hold messages behind the web-access question. A compatible open question
 * (same flags) takes them after its own; any other open first-submit
 * question is cancelled first, so its held message is returned, not lost. */
function holdForWebChoice(texts: string[], relay: boolean, keep: boolean): void {
  const open = workspace.webAccessPrompt;
  if (open?.purpose === "first-submit" && !!open.pendingRelay === relay && !!open.pendingKeep === keep) {
    setWorkspace("webAccessPrompt", { ...open, pendingMore: [...(open.pendingMore ?? []), ...texts] });
    return;
  }
  if (open?.purpose === "first-submit") actions.closeWebAccessPrompt();
  const [first, ...more] = texts;
  actions.openWebAccessPrompt({
    purpose: "first-submit",
    remote: false,
    pendingText: first,
    pendingRelay: relay,
    ...(keep ? { pendingKeep: true } : {}),
    ...(more.length ? { pendingMore: more } : {}),
  });
}

/** An unanswered question's held messages: back to an empty composer (or
 * named in the notice when the composer has a draft), or dropped. Also the
 * failed-answer path (RS-1 round 4, web R4-1: it overwrote the draft).
 * `toComposer` false (the user left the question's tab): the pending draft
 * is global and the ACTIVE composer takes it, so only a notice carries it. */
export function returnHeld(open: WebAccessPrompt, toComposer = true): void {
  const texts = heldMessages(open);
  if (texts.length === 0) return;
  if (!heldReturnsToComposer(open)) {
    actions.sendSystemNotice("not sent to the agent — choose the agent's web access (/web), then share it again");
    return;
  }
  if (!toComposer || (activeTab().chatDraft ?? "").trim()) {
    actions.sendSystemNotice(`not sent — choose the agent's web access (/web) to send: ${joinQueued(texts)}`);
    return;
  }
  actions.setPendingChatDraft(joinQueued(texts));
  actions.sendSystemNotice("message kept in the composer — choose the agent's web access to send it (/web)");
}

// ── Actions ─────────────────────────────────────────────────────────────

export const actions = {
  resetWorkspace() {
    for (const abort of chatAborts.values()) abort.abort();
    chatAborts.clear();
    knowledgeActions.reset();
    // Test-hygiene: drop the tab wiring table a previous App mount left
    // registered (live app never resets the workspace).
    clearTabRuntimeWiring();
    setWorkspace(reconcile(createInitialWorkspaceState()));
  },

  setProject(project: ResearchProject | null) {
    setScopedField("project", project);
  },

  /** 2026-10-01 (15): opt-in all-projects scope for the work pane. */
  toggleWorkScopeAll(): void {
    setScopedField("workScopeAll", !workspace.workScopeAll);
  },

  setSpace(space: Space) {
    // L2 (2026-09-18): `space` column removed — paneView is the single truth;
    // legacy call sites (keymap goto handlers etc.) route here and alias
    // straight through to openPane so every writer walks one path.
    actions.openPane(space);
    setWorkspace("spaceFeedback", null);
    // W2: any navigation leaves the chat-first launch frame (F-key, palette, etc.).
    setWorkspace("chatFirst", false);
    // The draft is never cleared here: the composer keeps showing it, and a
    // bare store clear made Ctrl+C arm exit and a tab switch drop the text
    // (TUI audit r2 #67). Letter shortcuts are paneFocus-gated now.
    setWorkspace("knowledgePane", "catalog");
    setWorkspace("verifyInputFocused", false);
    if (space !== "research") {
      setWorkspace("researchFocus", "graph");
    }
    if (space === "knowledge") {
      ensureKnowledgeSelection();
    }
    // Top-level nav node ids match space keys; revisit if child nav nodes are added.
    setWorkspace("selectedNodeId", SPACE_NODE_IDS[space]);
  },

  setResearchBottomTab(tab: "ops" | "strategy" | "explain") {
    setWorkspace("researchBottomTab", tab);
  },

  setKnowledgePane(pane: "catalog" | "verify") {
    setWorkspace("knowledgePane", pane);
    setWorkspace("verifyInputFocused", pane === "verify");
    if (pane !== "verify") {
      setWorkspace("verifyDraft", "");
    }
  },

  selectNode(id: string | null) {
    setWorkspace("selectedNodeId", id);
  },

  selectObject(id: string | null) {
    setWorkspace("selectedObjectId", id);
  },

  setActivityTab(tab: ActivityTab) {
    setWorkspace("activityTab", tab);
  },

  toggleActivityConsole() {
    setWorkspace("activityOpen", (open) => !open);
  },

  setContextPanelTab(tab: string) {
    setWorkspace("contextPanelTab", tab);
  },

  toggleContextPanel() {
    setWorkspace("contextPanelOpen", (open) => !open);
  },

  openCommandPalette(seed?: string) {
    // Never keep the leading "/" — it made the query "//skills" (zero matches).
    setWorkspace("commandPaletteSeed", seed ? seed.replace(/^[/]/, "") : null);
    // And the typed "/" that opened this palette must not linger as a draft —
    // stale seeds surface as dead windows (`›/` stuck in the composer).
    if ((workspace.chatDraft ?? "").trim() === "/") {
      actions.clearChatDraft();
    }
    setWorkspace("commandPaletteOpen", true);
  },

  closeCommandPalette() {
    setWorkspace("commandPaletteOpen", false);
    setWorkspace("commandPaletteSeed", null);
  },

  openSlashBar(seed = "") {
    // Character class (lizard mis-tokenizes the /^\// literal).
    setWorkspace("slashBarSeed", seed.replace(/^[/]/, ""));
    setWorkspace("slashBarOpen", true);
  },

  closeSlashBar() {
    setWorkspace("slashBarOpen", false);
    setWorkspace("slashBarSeed", "");
  },

  setPendingChatDraft(text: string) {
    setWorkspace("pendingChatDraft", text);
    setWorkspace("pendingChatDraftSeq", workspace.pendingChatDraftSeq + 1);
  },

  setChatDraft(text: string) {
    setScopedField("chatDraft", text);
  },

  setVerifyDraft(text: string) {
    setWorkspace("verifyDraft", text);
  },

  setSpaceFeedback(message: string | null) {
    setWorkspace("spaceFeedback", message);
  },

  setVerifyInputFocused(focused: boolean) {
    setWorkspace("verifyInputFocused", focused);
  },

  setResearchFocus(focus: "graph" | "agents" | "verify" | "approvals" | "strategy" | "explain") {
    setWorkspace("researchFocus", focus);
  },

  setSelectedApprovalId(id: string | null) {
    setWorkspace("selectedApprovalId", id);
  },

  consumePendingChatDraft(): string | null {
    const draft = workspace.pendingChatDraft;
    if (draft === null) return null;
    setWorkspace("pendingChatDraft", null);
    return draft;
  },

  clearChatDraft() {
    // Bump the pending-draft seq so the chat panel's adopt-effect syncs its
    // own local signal — a bare chatDraft write is invisible to it (was:
    // a "/" stranded in the composer after the palette round-trip).
    setScopedField("chatDraft", "");
    setWorkspace("pendingChatDraft", "");
    setWorkspace("pendingChatDraftSeq", (seq) => seq + 1);
  },

  recordUserSlashCommand(line: string) {
    // S4: a key typed into the line (`/login <token>`, `--api-key <k>`)
    // never reaches the transcript — the echo is masked, dispatch is not.
    const text = redactSlashSecrets(line.trim());
    if (!text) return;
    setScopedField("chatMessages", (list: ChatMessage[]) => [
      ...list,
      {
        id: ulid(),
        role: "user",
        content: text,
        timestamp: new Date().toISOString(),
      },
    ]);
  },

  retractLastUserSlashCommand(expected: string) {
    const text = redactSlashSecrets(expected.trim());
    if (!text) return;
    setScopedField("chatMessages", (list: ChatMessage[]) => {
      for (let i = list.length - 1; i >= 0; i -= 1) {
        const msg = list[i];
        if (msg?.role === "user" && msg.content === text) {
          return [...list.slice(0, i), ...list.slice(i + 1)];
        }
      }
      return list;
    });
  },

  openSlashPicker(picker: NonNullable<WorkspaceState["slashPicker"]>) {
    setWorkspace("slashPicker", picker);
    setWorkspace("slashPickerOpen", true);
    setWorkspace("slashPickerSelection", 0);
  },

  /** Selection index for the slash picker — store-held so the overlay survives
   * the theme-epoch keyed remount (a live-preview step rebuilds the tree;
   * component-local state would reset to 0 on every arrow key). */
  setSlashPickerSelection(index: number) {
    setWorkspace("slashPickerSelection", index);
  },

  closeSlashPicker() {
    // Esc reaches pickers ONLY via the global keymap → closeAllOverlays → this
    // action (the focused KeyTrap never sees the key). Cancel hooks therefore
    // live here: any close path that did not explicitly clear the hook (see
    // runSelection's Enter-keeps semantics) is a cancel.
    workspace.slashPicker?.onCancel?.();
    setWorkspace("slashPickerOpen", false);
    setWorkspace("slashPicker", null);
    setWorkspace("slashPickerSelection", 0);
  },

  sendSystemNotice(content: string) {
    const text = content.trim();
    if (!text) return;
    setScopedField("chatMessages", (list: ChatMessage[]) => [
      ...list,
      {
        id: ulid(),
        role: "system",
        content: text,
        timestamp: new Date().toISOString(),
      },
    ]);
  },

  /**
   * Replace the most recent system notice carrying `key` in place, or append
   * a new one. Used for live-updating lines (e.g. the cumulative tool-activity
   * summary) so repeated updates mutate ONE transcript row instead of pushing
   * a new row for every interim state.
   */
  upsertSystemNotice(key: string, content: string) {
    const text = content.trim();
    if (!text) return;
    setScopedField("chatMessages", (list: ChatMessage[]) => {
      for (let i = list.length - 1; i >= 0; i--) {
        const msg = list[i];
        if (msg!.role === "system" && msg!.noticeKey === key) {
          const next = list.slice();
          next[i] = { ...msg!, content: text, timestamp: new Date().toISOString() };
          return next;
        }
      }
      return [
        ...list,
        {
          id: ulid(),
          role: "system" as const,
          content: text,
          timestamp: new Date().toISOString(),
          noticeKey: key,
        },
      ];
    });
  },

  /**
   * A keyed notice that recurs, one row per occurrence (each context
   * compaction). The keyed row is updated while it is still in the current
   * exchange (no user row after it), so a /compact reply and its bridge
   * event share one line. An older row keeps its text and details under an
   * archived key, and the new line lands at the bottom instead of editing a
   * row that scrolled far up (TUI audit r2 #20 review).
   */
  upsertExchangeNotice(key: string, content: string) {
    const list = workspace.chatMessages;
    const at = list.findLastIndex((m) => m.role === "system" && m.noticeKey === key);
    if (at >= 0 && list.slice(at + 1).some((m) => m.role === "user")) archiveKeyedNotice(key, at);
    actions.upsertSystemNotice(key, content);
  },

  /**
   * B1: per-tool detail lines backing an expandable keyed notice row.
   * Called from the bridge on every summary flush; the latest key becomes
   * the Enter-on-empty-draft target.
   */
  setNoticeDetails(key: string, lines: string[]) {
    setScopedField("noticeDetails", key, lines);
    setScopedField("lastSummaryKey", key);
  },

  /** Expand/collapse a keyed notice row (Enter or click on the row). */
  toggleNoticeExpanded(key?: string) {
    const target = key ?? workspace.lastSummaryKey;
    if (!target) return false;
    if (!workspace.noticeDetails[target]) return false;
    setScopedField("noticeExpanded", target, !workspace.noticeExpanded[target]);
    return true;
  },

  /** Turn tool activity not yet pinned under a segment. An empty list
   *  clears it (a pin consumed it); a non-empty one APPENDS: every flush is
   *  one round's lines (the bridge resets its accumulator per flush), and a
   *  flush that lands before the previous one was pinned (textless rounds,
   *  background replay) must not erase it (TUI audit r2 #33). */
  setTurnToolActivity(lines: import("../lib/tool-activity").ActivityLine[]) {
    setScopedField("turnToolActivity", (current: ActivityLine[]) => (lines.length === 0 ? [] : [...current, ...lines]));
  },

  /** Mirror the agent's todo list (REPLACES whole — todowrite is a full rewrite). */
  setSessionTodos(todos: import("../lib/session-todos").SessionTodo[]) {
    setScopedField("sessionTodos", todos);
  },

  /** W1: cumulative session file-edit snapshot from the bridge. */
  setSessionChanges(changes: import("../types").SessionFileChange[]) {
    setScopedField("sessionChanges", changes);
  },

  /** Structured session plan snapshot from the bridge (Plan tab graph). */
  setSessionPlan(steps: import("../types").SessionPlanStep[]) {
    setScopedField("sessionPlan", steps);
  },

  /** W1: governance overview snapshot (tolerant sections). */
  setGovernanceOverview(overview: import("../types").GovernanceOverview | null) {
    setWorkspace("governance", overview);
  },

  /** Toggle one tool-call expand key without requiring registered details. */
  toggleToolCallExpanded(key: string) {
    setScopedField("noticeExpanded", key, !rowExpanded(key));
  },

  /** Toggle with the same effective-default semantics as rowExpanded — a
   * click inside verbose mode COLLAPSES that row (before: verbose forced
   * every row expanded and collapse clicks were dead). */
  toggleRowExpanded(key: string, defaultExpanded = false) {
    setScopedField("noticeExpanded", key, !rowExpanded(key, defaultExpanded));
  },

  /** B5: structured error card in the transcript (what / action / verb). */
  sendErrorCard(card: ErrorCard, onRetry?: () => void) {
    const id = ulid();
    if (onRetry) errorCardRetries.set(id, onRetry);
    setScopedField("chatMessages", (list: ChatMessage[]) => [
      ...list,
      {
        id,
        role: "system",
        content: card.what,
        timestamp: new Date().toISOString(),
        errorCard: { ...card, retryable: card.retryable ?? Boolean(onRetry) },
      },
    ]);
    setScopedField("lastErrorCardId", id);
  },

  /**
   * B5 keyboard grammar (empty draft): retry the latest error card if
   * retryable, else offer /logs. Returns true when the key was consumed.
   */
  resolveLastErrorKey(): boolean {
    const id = workspace.lastErrorCardId;
    if (!id) return false;
    const msg = workspace.chatMessages.find((m) => m.id === id);
    if (!msg?.errorCard) return false;
    const retry = errorCardRetries.get(id);
    if (msg.errorCard.retryable && retry) {
      setScopedField("lastErrorCardId", null); // one-shot: consumed
      retry();
      return true;
    }
    actions.openPane("logs");
    return true;
  },

  /** B5: click on the card = same verb as Enter on the empty draft. */
  activateErrorCard(id: string) {
    if (workspace.lastErrorCardId === id) {
      actions.resolveLastErrorKey();
      return;
    }
    setScopedField("lastErrorCardId", id);
    actions.resolveLastErrorKey();
  },

  // ── B3: message actions ────────────────────────────────────────────

  /** Enter the transcript cursor (starts at the latest message). */
  enterMessageCursor() {
    const last = workspace.chatMessages.length - 1;
    if (last < 0) return false;
    setScopedField("messageCursor", last);
    return true;
  },

  /** Leave cursor mode (chat input owns keys again). */
  exitMessageCursor() {
    setScopedField("messageCursor", null);
  },

  /** Move the cursor clamped to transcript bounds. */
  moveMessageCursor(delta: number) {
    const total = workspace.chatMessages.length;
    if (total === 0) return;
    const current = workspace.messageCursor ?? total - 1;
    const next = Math.min(total - 1, Math.max(0, current + delta));
    setScopedField("messageCursor", next);
  },

  /** Copy a transcript row's body; reports back the transport used. */
  async copyMessageContent(index: number): Promise<boolean> {
    const msg = workspace.chatMessages[index];
    if (!msg) return false;
    const { copyText } = await import("../lib/clipboard");
    const result = copyText(msg.content);
    actions.sendSystemNotice(result.ok ? `copied (${result.via})` : `copy failed: ${result.error ?? "unknown"}`);
    return result.ok;
  },

  /** Retry = resend that user message as a new turn (no truncation). */
  retryMessage(index: number): boolean {
    const msg = workspace.chatMessages[index];
    if (!msg || msg.role !== "user") return false;
    if (workspace.chatStreaming) {
      actions.sendSystemNotice("turn in progress — wait for the current reply to finish.");
      return true;
    }
    actions.exitMessageCursor();
    actions.sendChatMessage(msg.content);
    return true;
  },

  /** Edit-and-resend: seed the input with the original and mark truncation. */
  beginEditResend(index: number): boolean {
    const msg = workspace.chatMessages[index];
    if (!msg || msg.role !== "user") return false;
    setScopedField("editResendFromIndex", index);
    actions.setPendingChatDraft(msg.content);
    actions.exitMessageCursor();
    actions.sendSystemNotice("editing — send to replace everything after this message (Esc also clears)");
    return true;
  },

  /**
   * Truncate for edit-and-resend: keep messages up to (not incl.) the target,
   * clear the marker. Called from chat submit before dispatching the new turn.
   */
  truncateTranscriptForResend() {
    const idx = workspace.editResendFromIndex;
    if (idx === null) return;
    setScopedField("chatMessages", (list: ChatMessage[]) => list.slice(0, idx));
    setScopedField("editResendFromIndex", null);
  },

  /**
   * Remove the transcript rows of companion turns a command cut from the
   * session (dispatch_slash_command `dropped_turn_ids`, today /retry): each
   * turn's user row (by its turnId) and every row up to the next user row.
   */
  dropTranscriptTurns(turnIds: string[]): void {
    for (const turnId of turnIds) {
      const list = workspace.chatMessages;
      const start = list.findIndex((m) => m.role === "user" && m.turnId === turnId);
      if (start < 0) continue;
      const next = list.findIndex((m, i) => i > start && m.role === "user");
      setScopedField("chatMessages", [...list.slice(0, start), ...(next < 0 ? [] : list.slice(next))]);
    }
  },

  /** Cancel a pending edit-and-resend: drop the marker, keep the transcript
   *  and the composer draft. Esc's contract since the first notice promised
   *  it; a permanently refused rewind exits the same way (2026-10-19). */
  cancelEditResend() {
    setScopedField("editResendFromIndex", null);
  },

  /**
   * Edit-and-resend on a companion session: cut the companion's own history
   * first (`session.truncate` by the edited row's turn id), then the
   * transcript. Slicing only the transcript left the model answering with
   * the discarded turns still in its context (TUI audit r2 #17). True when
   * the caller may send; on false nothing was cut. A refused (never-valid)
   * row already dropped the marker; only a failed session.truncate keeps it,
   * so a retry can succeed.
   */
  async rewindForResend(): Promise<boolean> {
    const idx = workspace.editResendFromIndex;
    if (idx === null) return true;
    if (keepsCompanionSession() && !(await rewindCompanionTurn(idx))) return false;
    actions.truncateTranscriptForResend();
    return true;
  },

  /** Surface Companion/chat outage in header — not as a chat bubble. */
  setChatFallback(active: boolean, detail: string | null = null) {
    setScopedField("chatFallbackActive", active);
    if (!active) {
      setScopedField("chatFallbackDetail", null);
      return;
    }
    const raw = detail ?? "";
    const label = isGatewayNetworkFailure(raw) || raw.includes("LLM/gateway network failed")
      ? "gateway TLS timeout"
      : (raw.slice(0, 80) || "companion unavailable");
    setScopedField("chatFallbackDetail", label);
  },

  async dispatchSlashFromChat(line: string) {
    let command = line.trim();
    if (!command.startsWith("/")) {
      return { kind: "error" as const, message: "Slash commands must start with /" };
    }

    actions.clearChatDraft();
    actions.closeSlashBar();

    const { resolveSlashPicker, parseUpPickerIntent, parseDownPickerIntent, parseModelsPickerIntent, modelsProbeProblems, endpointPickerFailure } = await import(
      "../commands/slash-pickers"
    );

    const picker = await resolveSlashPicker(command);
    if (picker) {
      actions.recordUserSlashCommand(command);
      actions.openSlashPicker(picker);
      return { kind: "handled" as const };
    }

    if (parseModelsPickerIntent(command)) {
      actions.recordUserSlashCommand(command);
      // Name failed source probes — a swallowed 401/URL error must not read
      // as "nothing provisioned" (2026-10-20: it sent the user to /endpoint
      // up, when their public source's key was the problem all along).
      const probeIssues = modelsProbeProblems();
      actions.sendSystemNotice(
        probeIssues.length > 0
          ? `no models — ${probeIssues.join("; ")}`
          : "(no provisioned models — use /endpoint up <model> or /health)",
      );
      return { kind: "handled" as const };
    }

    if (parseUpPickerIntent(command) || parseDownPickerIntent(command)) {
      actions.recordUserSlashCommand(command);
      // A failed gateway call is named, not shown as an empty list (slash-a#0).
      actions.sendSystemNotice(
        endpointPickerFailure() ||
          (parseUpPickerIntent(command)
            ? "No registry models found — try /endpoint up <model> or /health"
            : "No active endpoints to stop"),
      );
      return { kind: "handled" as const };
    }

    const { dispatchSlashCommand } = await import("../commands/slash");
    actions.recordUserSlashCommand(command);
    const result = await dispatchSlashCommand(command);
    if (result.kind === "insert") {
      actions.retractLastUserSlashCommand(command);
      actions.setPendingChatDraft(result.text);
      return { kind: "handled" as const };
    }

    if (result.kind === "error") {
      actions.sendSystemNotice(result.message);
    }
    return result;
  },

  openNotificationCenter() {
    setWorkspace("notificationCenterOpen", true);
  },

  closeNotificationCenter() {
    setWorkspace("notificationCenterOpen", false);
  },

  openHelp() {
    setWorkspace("helpOpen", true);
  },

  /** B4: append a thinking_delta chunk; starts the block timer if closed. */
  appendThinkingDelta(delta: string) {
    const now = Date.now();
    setScopedField("thinking", (current: ThinkingState) => ({
      text: current.text + delta,
      open: true,
      expanded: current.expanded,
      startedAtMs: current.startedAtMs ?? now,
      elapsedMs: current.startedAtMs == null ? 0 : now - current.startedAtMs,
    }));
  },

  /** B4: thinking_done — close the block, freeze the timer. */
  closeThinking() {
    setScopedField("thinking", (current: ThinkingState) => ({ ...current, open: false }));
  },

  toggleThinkingExpanded() {
    setScopedField("thinking", (current: ThinkingState) => ({ ...current, expanded: !current.expanded }));
  },

  resetThinking() {
    setScopedField("thinking", { text: "", open: false, expanded: false, startedAtMs: null, elapsedMs: 0 });
  },

  /** ADR-037: apply the latest selection record for the in-flight turn. */
  applyToolSelection(record: import("../types").ToolSelectionRecord) {
    setScopedField("toolSelection", record);
  },

  resetToolSelection() {
    setScopedField("toolSelection", null);
  },

  /** D3: status footer badge — set after /mcp inventory attempts. */
  setMcpStatus(value: "ok" | "error" | null) {
    setScopedField("mcpStatus", value);
  },

  setCostState(value: CostState | null) {
    setScopedField("cost", value);
  },

  /** B7: /settings bell on|off — turn-completion bell opt-out. */
  setCompletionBellEnabled(value: boolean) {
    setWorkspace("completionBellEnabled", value);
  },

  setAmbientCardsEnabled(value: boolean) {
    setWorkspace("ambientCardsEnabled", value);
  },

  /** Discard all visible messages from index on (used by `/undo`, client-side). */
  discardVisibleMessagesAfter(index: number) {
    setScopedField("chatMessages", (list: ChatMessage[]) => list.slice(0, Math.max(0, index)));
  },

  /** Insert a structured management card into the transcript (mgmt-card.v1). */
  sendMgmtCard(type: string, payload: unknown) {
    const message: ChatMessage = {
      id: ulid(),
      role: "system",
      content: "",
      timestamp: new Date().toISOString(),
      mgmtCard: { v: 1, type, payload },
    };
    setScopedField("chatMessages", (list: ChatMessage[]) => [...list, message]);
  },

  closeHelp() {
    setWorkspace("helpOpen", false);
  },

  toggleHelp() {
    setWorkspace("helpOpen", (open) => !open);
  },

  closeAllOverlays() {
    if (workspace.chatStreaming) {
      actions.interruptChat();
    }
    actions.closeModalOverlays();
    if (workspace.paneView !== null) {
      setWorkspace("paneView", null);
      setWorkspace("paneFocus", false);
    }
  },

  /** The overlay half of closeAllOverlays (stab-focus, 2026-10-21): every
   * modal surface closes (a pending agent question is declined), but the
   * running turn, its queued prompts and the docked pane stay. A modal
   * layer's Esc runs this, never the full sweep: dismissing Help or the
   * palette mid-turn used to cancel the turn and close the pane underneath.
   * A pending tool approval stays pending (ADR-104 defect 10): only Esc on
   * the card itself rejects it (keymap/store-claims, the card's own layer). */
  closeModalOverlays() {
    if (workspace.askUser) {
      const askId = String(workspace.askUser.id);
      if (askId.startsWith("remote:")) {
        // A remote question stays pending server-side (ADR-099 D-8): Esc sets
        // it aside (answer later), never a stdin line (RS-1 round 2).
        void import("../bridge/venue-bridge").then((m) => m.setAsideRemoteQuestion(askId.slice("remote:".length)));
      } else {
        try {
          getCompanionBridge().respondAskUser(workspace.askUser.id, null);
        } catch {
          /* ignore */
        }
      }
      setScopedField("askUser", null);
      setScopedField("chatTurnStatus", null);
    }
    setWorkspace("commandPaletteOpen", false);
    setWorkspace("commandPaletteSeed", null);
    setWorkspace("slashBarOpen", false);
    setWorkspace("slashBarSeed", "");
    // Via the action, not raw setWorkspace: picker onCancel (theme preview
    // restore) hooks every Esc route, not only the picker's own handler.
    actions.closeSlashPicker();
    // V4 S2 freeze fix: the temporal picker and the detail pane are part of the
    // modal layer cleanup — a lingering focused KeyTrap from a "closed" surface
    // deadlocked every key (user: Esc did nothing after /skills).
    setWorkspace("listOverlay", null);
    setWorkspace("rewindMenuOpen", false);
    // T5 topic-1: dismiss the team-detail overlay on the same sweep rule.
    setWorkspace("teamDetailId", null);
    // T5 topic-5: same sweep for the endpoint-detail overlay.
    setWorkspace("endpointDetailId", null);
    // 2026-10-01: same sweep for the workflow-definition card.
    setWorkspace("workflowDefId", null);
    // T5 topic-6: same sweep for the agent-detail overlay.
    setWorkspace("agentDetailId", null);
    // TUI sweep batch: same for the workflow-execution mission overlay.
    setWorkspace("execDetailId", null);
    setWorkspace("startResearch", null);
    setWorkspace("connectWizard", null);
    setWorkspace("startActionsMenuOpen", false);
    setWorkspace("notificationCenterOpen", false);
    setWorkspace("helpOpen", false);
    setWorkspace("quitDialogOpen", false);
    // Wave 2026-09-21: the venue + remote-source pickers join the sweep (G-RS-25).
    setWorkspace("venuePickerOpen", false);
    setWorkspace("remoteSourcePickerOpen", false);
    // ADR-099: the web-access question too (a held first message goes back
    // into the composer, never lost).
    actions.closeWebAccessPrompt();
    setWorkspace("searchServerOffer", false);
    // Every sweep caller (not only the Esc keymap) closes the tour (r2 #1).
    setWorkspace("tourOpen", false);
    if (workspace.verifyInputFocused) {
      setWorkspace("verifyInputFocused", false);
      setWorkspace("researchFocus", "graph");
    }
  },

  openStartActionsMenu() {
    // Blur Live verify so its focus trap cannot fight the overlay or collapse panes.
    if (workspace.verifyInputFocused) {
      setWorkspace("verifyInputFocused", false);
      setWorkspace("verifyDraft", "");
      if (workspace.researchFocus === "verify") {
        setWorkspace("researchFocus", "graph");
      }
    }
    setWorkspace("startActionsMenuOpen", true);
  },

  closeStartActionsMenu() {
    setWorkspace("startActionsMenuOpen", false);
  },

  // ── Claude-Code parity (docs/development/r4-tui-claude-parity-2026-09-24.md) ──

  /** Esc Esc (empty prompt) / /rewind: pick an earlier prompt to roll back to. */
  openRewindMenu(): boolean {
    const any = workspace.chatMessages.some((m) => m.role === "user" && !m.queued);
    if (!any) {
      actions.sendSystemNotice("rewind: no earlier prompt in this conversation");
      return false;
    }
    setWorkspace("rewindMenuOpen", true);
    return true;
  },

  closeRewindMenu() {
    setWorkspace("rewindMenuOpen", false);
  },

  /** Roll back to a user prompt: it returns to the input and sending replaces
   * everything from it onward (the edit-and-resend truncation). */
  rewindTo(index: number) {
    setWorkspace("rewindMenuOpen", false);
    if (actions.beginEditResend(index)) {
      actions.sendSystemNotice(
        "rewound — the prompt is back in the input; sending replaces the conversation from there",
      );
    }
  },

  /** /btw <question>: answered from the current conversation, no tools, not
   * recorded; works while a turn runs (the bridge answers on a worker). */
  async askBtw(question: string): Promise<void> {
    const q = question.trim();
    if (!q) {
      setWorkspace("btw", "open", workspace.btw.exchanges.length > 0);
      return;
    }
    const id = ulid();
    const kept = [...workspace.btw.exchanges, { id, question: q, answer: null }].slice(-BTW_KEEP);
    setWorkspace("btw", { open: true, index: kept.length - 1, exchanges: kept });
    const settle = (patch: { answer: string | null; error?: string }) =>
      setWorkspace("btw", "exchanges", (x) => x.id === id, patch);
    try {
      const result = await rpc(getCompanionBridge(), "session.side_question", { question: q }, 120_000);
      settle({ answer: result?.answer || "(no answer)" });
    } catch (err) {
      settle({ answer: null, error: err instanceof Error ? err.message : String(err) });
    }
  },

  /** Register background work; returns its id (kept to the newest 20). */
  addBackgroundTask(kind: "shell" | "turn", label: string): string {
    const id = ulid();
    setWorkspace("backgroundTasks", (list) =>
      [...list, { id, kind, label, status: "running" as const, startedAt: Date.now() }].slice(-20),
    );
    return id;
  },

  finishBackgroundTask(id: string, status: "done" | "failed" | "cancelled", detail?: string) {
    setWorkspace("backgroundTasks", (t) => t.id === id, { status, endedAt: Date.now(), detail });
  },

  /** Ctrl+B: send the streaming turn to the background — it keeps running,
   * the user is notified (and belled) when it finishes. One session runs one
   * turn at a time, so new prompts still queue behind it. */
  backgroundCurrentTurn(): boolean {
    if (!workspace.chatStreaming) {
      actions.sendSystemNotice(
        "Ctrl+B: nothing running to background (`!cmd` shell commands always run in the background — /bg lists them)",
      );
      return false;
    }
    if (workspace.backgroundTurnTaskId) return true;
    const lastPrompt = [...workspace.chatMessages].reverse().find((m) => m.role === "user" && !m.queued);
    const id = actions.addBackgroundTask("turn", (lastPrompt?.content ?? "turn").slice(0, 60));
    setScopedField("backgroundTurnTaskId", id);
    actions.sendSystemNotice(
      "turn continues in the background — you'll be notified when it finishes (/bg lists background work)",
    );
    return true;
  },

  /** Turn end: a backgrounded turn (Ctrl+B) is marked done, announced, belled. */
  settleBackgroundTurn(finalText: string) {
    const backgroundId = workspace.backgroundTurnTaskId;
    if (!backgroundId) return;
    setScopedField("backgroundTurnTaskId", null);
    actions.finishBackgroundTask(backgroundId, "done", finalText.slice(0, 80));
    actions.sendSystemNotice("✓ background turn finished");
    try {
      process.stdout.write("\x07");
    } catch {
      /* no terminal */
    }
  },

  closeBtw() {
    setWorkspace("btw", "open", false);
  },

  stepBtw(delta: number) {
    const count = workspace.btw.exchanges.length;
    if (count === 0) return;
    setWorkspace("btw", "index", (i) => Math.min(count - 1, Math.max(0, i + delta)));
  },

  clearBtw() {
    setWorkspace("btw", { open: false, index: 0, exchanges: [] });
  },

  /** Ctrl+O: expand/collapse every tool roll-up, notice and thinking block. */
  toggleVerboseTranscript() {
    const next = !workspace.verboseTranscript;
    setWorkspace("verboseTranscript", next);
    setScopedField("thinking", (current: ThinkingState) => ({ ...current, expanded: next }));
  },

  /** Queued prompts (paint-order) and the removal of their bubbles + rail. */
  drainQueued(): string[] {
    const queued = workspace.chatMessages.filter((m) => m.role === "user" && m.queued);
    if (queued.length === 0) return [];
    const ids = new Set(queued.map((m) => m.id));
    for (const id of ids) settleQueued(id);
    setScopedField("chatMessages", (list: ChatMessage[]) => list.filter((m) => !ids.has(m.id)));
    setScopedField("pendingChatQueue", []);
    void import("../lib/chat-queue-rail").then(({ queueRemote }) => queueRemote("clear"));
    return queued.map((m) => m.content);
  },

  /** ↑ on an empty prompt: take the queued prompts back into the input. */
  takeBackQueued(): boolean {
    const contents = actions.drainQueued();
    if (contents.length === 0) return false;
    actions.setPendingChatDraft(joinQueued(contents));
    actions.sendSystemNotice(`took back ${contents.length} queued prompt(s)`);
    return true;
  },

  /** Ctrl+Enter: interrupt the running turn and send the queued prompts now. */
  sendQueuedNow(): boolean {
    const contents = actions.drainQueued();
    if (contents.length === 0) {
      actions.sendSystemNotice("nothing queued");
      return false;
    }
    if (workspace.chatStreaming) actions.interruptChat();
    actions.sendChatMessage(joinQueued(contents));
    return true;
  },

  openStartResearch(kind: "create" | "import" | "plan") {
    setWorkspace("startActionsMenuOpen", false);
    setWorkspace("startResearch", { kind, draft: "", nameDraft: kind === "create" ? "" : undefined });
  },

  /** The /connect menu rows open the Q&A form instead of seeding the
   *  composer with a half-typed slash line (token/flags by hand). */
  openConnectWizard(flow: "login" | "source") {
    setWorkspace("listOverlay", null);
    setWorkspace("connectWizard", { flow });
  },

  closeConnectWizard() {
    setWorkspace("connectWizard", null);
  },

  closeStartResearch() {
    setWorkspace("startResearch", null);
  },

  setStartResearchDraft(draft: string) {
    const cur = workspace.startResearch;
    if (!cur) return;
    setWorkspace("startResearch", { ...cur, draft });
  },

  setStartResearchNameDraft(nameDraft: string) {
    const cur = workspace.startResearch;
    if (!cur || cur.kind !== "create") return;
    setWorkspace("startResearch", { ...cur, nameDraft });
  },

  /** Show an ask, or queue it behind the open card (ADR-104 D-3). The same
   * id twice (a re-surfaced remote intent) is one ask. Writes go to the
   * event's tab when one is pinned (a background tab's ask stays there). */
  openToolApproval(req: ToolApprovalInput) {
    const card = toApprovalCard(req);
    const tab = eventScopedTab();
    const head = tab.toolApproval;
    if (!head) {
      setScopedField("toolApproval", card);
      return;
    }
    const queue = tab.toolApprovalQueue ?? [];
    const key = String(card.id);
    if (String(head.id) === key || queue.some((c) => String(c.id) === key)) return;
    setScopedField("toolApprovalQueue", [...queue, card]);
  },

  /** The head card was answered: the next queued ask (if any) shows. */
  closeToolApproval() {
    const queue = activeTab().toolApprovalQueue ?? [];
    // Null first: a store write of one object onto another merges, and the
    // next card must not inherit the answered one's diff or fence line.
    setScopedField("toolApproval", null);
    if (queue.length === 0) return;
    setScopedField("toolApproval", queue[0]);
    setScopedField("toolApprovalQueue", queue.slice(1));
  },

  /** Drop one ask by id (head or queued) — it was answered elsewhere. */
  dropToolApproval(id: string | number) {
    const key = String(id);
    if (activeTab().toolApproval && String(activeTab().toolApproval!.id) === key) {
      actions.closeToolApproval();
      return;
    }
    const queue = activeTab().toolApprovalQueue ?? [];
    setScopedField(
      "toolApprovalQueue",
      queue.filter((c) => String(c.id) !== key),
    );
  },

  openAskUser(req: { id: string | number; question: string; options: string[] }) {
    setScopedField("askUser", req);
  },

  closeAskUser() {
    setScopedField("askUser", null);
  },

  setChatTurnStatus(status: string | null) {
    setScopedField("chatTurnStatus", status);
  },

  /** T5 topic-12/17: completion tail at turn end (one upserted row per turn id,
   * report + elapsed context; never a silent thread; banner prefixed with the
   * proseAssistant-tinted ✓ glyph so pass rows decode visually). */
  sendTurnComplete(turnId: string, summary: string): void {
    actions.upsertSystemNotice(`turn-done:${turnId}`, `✓ done — ${summary}`);
  },

  setResources(resources: HostResources | null) {
    setWorkspace("resources", resources);
  },

  setTurnArtifacts(artifacts: string[]) {
    setScopedField("turnArtifacts", artifacts);
  },

  setScienceLoop(loop: ScienceLoopState | null) {
    setScopedField("scienceLoop", loop);
  },

  setKnowledgeBase(kb: KnowledgeBaseState | null) {
    setScopedField("knowledgeBase", kb);
  },

  setChatFirst(first: boolean) {
    setWorkspace("chatFirst", first);
  },

  openSplash() {
    setWorkspace("splashOpen", true);
  },

  /** /tour wizard — open at a step (default: resume where the user left). */
  openTour(step?: number) {
    setWorkspace("tourOpen", true);
    if (step !== undefined) setWorkspace("tourStep", Math.max(0, step));
  },

  closeTour() {
    setWorkspace("tourOpen", false);
  },

  setTourStep(step: number) {
    setWorkspace("tourStep", Math.max(0, Math.min(step, 99)));
  },

  closeSplash() {
    setWorkspace("splashOpen", false);
  },

  setMcpServers(servers: WorkspaceState["mcpServers"]) {
    setWorkspace("mcpServers", servers);
  },

  openListOverlay(
    kind: "skills" | "mcp" | "workflow" | "changes" | "governance" | "jobgraph" | "sources" | "connect",
    targetId?: string | null,
  ) {
    setWorkspace("listOverlay", kind);
    setWorkspace("overlayTargetId", targetId ?? null);
  },

  /** P3 cited drill-in: open the Knowledge pane at a citation's artifact when
   * the local catalog knows it; otherwise land on the pane with an honest
   * note (no fake selection). */
  openCitedInKnowledge(artifactId: string) {
    actions.openPane("knowledge");
    if (workspace.knowledge.some((k) => k.id === artifactId)) {
      actions.selectObject(artifactId);
      return;
    }
    actions.sendSystemNotice(
      `citation ${artifactId.slice(0, 8)} — Knowledge pane opened (row not in the local catalog)`,
    );
  },

  closeListOverlay() {
    setWorkspace("listOverlay", null);
    setWorkspace("overlayTargetId", null);
  },

  /** Wave E: identity context from the bridge config. */
  setIdentityState(patch: {
    projectId?: string;
    profile?: string;
    sourceName?: string;
    username?: string;
    credential?: string;
  }) {
    if (patch.projectId !== undefined) setScopedField("identityProjectId", patch.projectId);
    if (patch.profile !== undefined) setScopedField("identityProfile", patch.profile);
    if (patch.sourceName !== undefined) setScopedField("identitySource", patch.sourceName);
    if (patch.username !== undefined) setWorkspace("identityUsername", patch.username);
    if (patch.credential !== undefined) setScopedField("identityCredential", patch.credential);
  },

  /** ADR-086 identity chip: the caller's circle refs (display-only). */
  setIdentityCircles(refs: string[]) {
    setWorkspace("identityCircles", refs);
  },

  /** Wave D: apply fetched session records + clamp the pane cursor. */
  setSessionsList(records: SessionRecord[]) {
    setWorkspace("sessionsList", records);
    setWorkspace("sessionsCursor", (c) => Math.min(c, Math.max(0, records.length - 1)));
  },

  /** Wave H: apply git change sets + clamp the pane cursor. */
  setChangeSets(changes: import("../lib/git-changes").GitChange[]) {
    setWorkspace("changeSets", changes);
    setWorkspace("changeCursor", (c) => Math.min(c, Math.max(0, changes.length - 1)));
  },

  /** Wave H: move the code-view cursor (clamped). */
  moveChangeCursor(delta: number) {
    const total = workspace.changeSets.length;
    if (total === 0) return;
    setWorkspace("changeCursor", (c) => Math.min(total - 1, Math.max(0, c + delta)));
  },

  /** Wave H: two-step destructive revert — r arms, R confirms within the arm. */
  armRevert(path: string): boolean {
    if (workspace.revertArmPath === path) return false;
    setWorkspace("revertArmPath", path);
    return true;
  },

  confirmRevert(fn: (path: string) => { ok: boolean; error?: string }): boolean {
    const path = workspace.revertArmPath;
    if (!path) return false;
    setWorkspace("revertArmPath", null);
    const res = fn(path);
    actions.sendSystemNotice(res.ok ? `reverted ${path}` : `revert failed: ${res.error ?? "unknown"}`);
    return true;
  },

  /** Wave H: latest test run summary; animation stays off (rule: animate nothing). */
  setTestRunSummary(text: string | null) {
    setWorkspace("testRunSummary", text);
  },

  /** Wave D: move the /sessions pane cursor (clamped). */
  moveSessionCursor(delta: number) {
    const total = workspace.sessionsList.length;
    if (total === 0) return;
    setWorkspace("sessionsCursor", (c) => Math.min(total - 1, Math.max(0, c + delta)));
  },

  /** P3: s chip — cycle the state filter (all → ongoing → … → archived). */
  cycleSessionStateFilter(): import("../types").SessionStateFilter {
    const next = cycleSessionStateFilter(workspace.sessionFilters.state);
    setWorkspace("sessionFilters", "state", next);
    return next;
  },

  /** P3: v chip — cycle the venue filter (all → local → frontend). */
  cycleSessionVenueFilter(): import("../types").SessionVenueFilter {
    const next = cycleSessionVenueFilter(workspace.sessionFilters.venue);
    setWorkspace("sessionFilters", "venue", next);
    return next;
  },

  /** P3: A chip — toggle archive visibility (tombstones one keystroke away). */
  toggleSessionArchived(): boolean {
    const next = !workspace.sessionFilters.includeArchived;
    setWorkspace("sessionFilters", "includeArchived", next);
    return next;
  },

  /** P2/P3: journal reachability reported by list_sessions. */
  setSessionsRemoteStatus(status: { available: boolean; reason: string }) {
    setWorkspace("sessionsRemote", status);
  },

  /** P2: open the session-start venue picker (probe runs inside the overlay). */
  openVenuePicker() {
    setWorkspace("venuePickerOpen", true);
  },

  closeVenuePicker() {
    setWorkspace("venuePickerOpen", false);
  },

  /** P2: remember the last pick as the in-process default (no persistence —
   *  no general client config file exists; plan §8a-1 config lands with r4 CLI). */
  setLastVenueChoice(choice: import("../types").VenueChoice) {
    setWorkspace("lastVenueChoice", choice);
  },

  /** Wave 2026-09-21: venue of the live session (from every bridge snapshot). */
  setSessionVenue(venue: string) {
    setScopedField("sessionVenue", venue || "local");
  },

  /** Remote runtime projection (null clears the status chip). */
  setVenueRuntime(runtime: import("../lib/venue-runtime").VenueRuntime | null) {
    setScopedField("venueRuntime", runtime);
  },

  openRemoteSourcePicker(mode: RemoteSourcePickerMode = "start") {
    setWorkspace("remoteSourcePickerMode", mode);
    setWorkspace("remoteSourcePickerOpen", true);
  },

  closeRemoteSourcePicker() {
    setWorkspace("remoteSourcePickerOpen", false);
    // ADR-099: the answer belonged to that start; a later one asks again.
    setWorkspace("pendingRemoteWebAccess", null);
  },

  /** ADR-099 D-2: the session's web-access choice (snapshot / set answer). */
  setWebAccess(choice: import("../lib/web-access").WebAccessChoice | null) {
    setScopedField("webAccess", choice);
  },

  /** ADR-101: the session's local tool sandbox (snapshot / `session.sandbox_set`). */
  setSandbox(state: import("../lib/sandbox").SandboxView | null) {
    setScopedField("sandbox", state);
  },

  /** ADR-108 D-2: the worktree this tab works in (`/worktree`). */
  setWorktree(view: import("../lib/worktree").WorktreeView | null) {
    setScopedField("worktree", view);
  },

  /** Open the web-access question (one at a time; a newer one replaces it). */
  openWebAccessPrompt(prompt: WebAccessPrompt) {
    setWorkspace("webAccessPrompt", prompt);
  },

  /** ADR-102: after /web Public with no search server (local, engine present). */
  openSearchServerOffer() {
    setWorkspace("searchServerOffer", true);
  },

  closeSearchServerOffer() {
    setWorkspace("searchServerOffer", false);
  },

  /** Esc on the offer: nothing starts; one line says how to start it later. */
  declineSearchServerOffer() {
    setWorkspace("searchServerOffer", false);
    actions.sendSystemNotice("search server: not started — /search-server up starts it any time");
  },

  /** Close the question. Unanswered (`sent` false: Esc, the sweep), a held
   * message returns to the composer and a new session is not started. */
  closeWebAccessPrompt(opts: { sent?: boolean } = {}) {
    const open = workspace.webAccessPrompt;
    if (!open) return;
    setWorkspace("webAccessPrompt", null);
    if (opts.sent) return;
    returnHeld(open);
    if (open.purpose === "new-local" || open.purpose === "new-remote") {
      actions.sendSystemNotice("no session started — the agent's web access was not chosen");
    }
  },

  setPendingRemoteWebAccess(choice: import("../lib/web-access").WebChoice | null) {
    setWorkspace("pendingRemoteWebAccess", choice);
  },

  /** ADR-075 G2: session workflow arm from a bridge snapshot (undefined = keep). */
  setWorkflowArmed(armed: boolean | undefined) {
    if (armed !== undefined) setScopedField("workflowArmed", armed);
  },

  /** Session knowledge-acquisition mode from a bridge snapshot (undefined = keep). */
  setKnowledgeAcquisition(acq: { mode: string; graphs: string[] } | undefined) {
    if (acq) setScopedField("knowledgeAcquisition", { mode: acq.mode, graphs: [...acq.graphs] });
  },

  /** Wave C: update the mode/permission/plan state from a bridge snapshot. */
  setInteractionModeState(patch: {
    sessionId?: string;
    interactionMode?: string;
    permissionMode?: string;
    trustSource?: string;
    planAwaitingConfirmation?: boolean;
    planPendingRequest?: string;
  }) {
    if (patch.interactionMode !== undefined) setScopedField("interactionMode", patch.interactionMode);
    if (patch.permissionMode !== undefined) setScopedField("permissionMode", patch.permissionMode);
    if (patch.trustSource !== undefined) setScopedField("trustSource", patch.trustSource);
    if (patch.planAwaitingConfirmation !== undefined) {
      setScopedField("planAwaitingConfirmation", patch.planAwaitingConfirmation);
    }
    if (patch.planPendingRequest !== undefined) {
      setScopedField("planPendingRequest", patch.planPendingRequest);
    }
  },

  setCodingMode(on: boolean) {
    setWorkspace("codingMode", on);
  },

  /** V4: open the detail pane on a view. EVERY view opens UNFOCUSED — the
   *  chat composer keeps the keys (user report 2026-10-06: a docked pane
   *  must not disable the chat). Focus moves into the pane explicitly:
   *  Tab (views without a Tab consumer — research/knowledge own Tab),
   *  or a click on the pane. The terminal view additionally keeps its
   *  click-only contract (no key ever moves its focus, 2026-10-03). */
  openPane(view: Exclude<WorkspaceState["paneView"], null>) {
    setWorkspace("paneView", view);
    setWorkspace("paneFocus", false);
    if (view !== "logs") {
      // L2: one bookkeeping model — the helper chain used to run through
      // `setSpace`; fold it here so pane-open is the only navigation vocab.
      setWorkspace("spaceFeedback", null);
      setWorkspace("chatFirst", false);
      // chatDraft stays (see setSpace; TUI audit r2 #67).
      setWorkspace("knowledgePane", "catalog");
      setWorkspace("verifyInputFocused", false);
      if (view !== "research") {
        setWorkspace("researchFocus", "graph");
      }
      if (view === "knowledge") {
        ensureKnowledgeSelection();
      }
      setWorkspace("selectedNodeId", SPACE_NODE_IDS[view as Space]);
    }
  },

  closePane() {
    if (workspace.paneView === null) return;
    setWorkspace("paneView", null);
    setWorkspace("paneFocus", false);
  },

  /** Toggle focus into/out of the detail pane. The terminal view never uses
   *  this (click-only focus, 2026-10-03). */
  togglePaneFocus() {
    if (workspace.paneView === null) return;
    if (workspace.paneView === "term") return;
    setWorkspace("paneFocus", !workspace.paneFocus);
  },

  /** Drag-resize of the detail pane border: set explicit column width
   *  (clamped by lib/pane-layout), or null to return to the percentage
   *  default. */
  setPaneWidthOverride(cols: number | null) {
    setWorkspace("paneWidthOverride", cols);
  },

  /** T5 topic-1: team-detail overlay id setter (null = hidden). */
  setTeamupArmed(on: boolean, crew ?: "research" | "hpc" | "code", parallel ?: number) {
    setWorkspace("teamupArmed", on);
    // Arming a crew (or disarming) never leaves a plan template armed.
    setWorkspace("teamupArmedPlan", null);
    if (crew) setWorkspace("teamupArmedCrew", crew);
    if (parallel !== undefined) setWorkspace("teamupArmedParallel", Math.max(1, parallel));
  },

  /** ADR-092 slice 3: arm a team plan template for the next message. */
  setTeamupArmedPlan(template: string, args: Record<string, string> = {}) {
    setWorkspace("teamupArmed", true);
    setWorkspace("teamupArmedPlan", { template, args: { ...args } });
  },

  setTeamDetailId(id: string | null) {
    setWorkspace("teamDetailId", id);
    // Selection always restarts on a new target (never inherits a stale cursor).
    setWorkspace("teamDetailCursor", 0);
    setWorkspace("teamDetailExpanded", null);
  },

  /** Team overlay cursor/toggle driven from the global keymap (B-family). */
  teamOverlayMoveCursor(delta: number) {
    const next = Math.max(0, workspace.teamDetailCursor + delta);
    setWorkspace("teamDetailCursor", next);
  },
  teamOverlayToggleExpanded(role: string) {
    setWorkspace(
      "teamDetailExpanded",
      workspace.teamDetailExpanded === role ? null : role,
    );
  },

  /** Workflow-execution mission overlay id setter (null = hidden). */
  setExecDetailId(id: string | null) {
    setWorkspace("execDetailId", id);
  },

  /** T5 topic-5: endpoint-detail overlay id setter (null = hidden). */
  setEndpointDetailId(id: string | null) {
    setWorkspace("endpointDetailId", id);
  },

  /** 2026-10-01: workflow-definition card id setter (null = hidden). */
  setWorkflowDefId(id: string | null) {
    setWorkspace("workflowDefId", id);
  },

  /** T5 topic-6: agent-detail overlay id setter (null = hidden). */
  setAgentDetailId(id: string | null) {
    setWorkspace("agentDetailId", id);
  },

  /** F4 asset rails: apply a full snapshot (bootfit) — replaces the entire
   * local base with the state's own rows, resets the stable base map, keeps
   * the server-published hash token verbatim for the next patch. */
  applyAssetsSnapshot(
    rows: ReadonlyArray<Readonly<import("../types").UiAsset>>,
    snapshotHash: string | null,
  ) {
    const base: Record<string, string> = {};
    for (const row of rows) {
      if (row.shapeHash) base[row.shapeKey] = row.shapeHash;
    }
    setWorkspace("assets", reconcile([...rows]));
    setWorkspace("assetsBase", reconcile(base));
    setWorkspace("assetsSnapshotHash", snapshotHash);
  },

  /** F4 asset rails: merge a patch payload — upserts arriving rows AND
   * removes tombstoned shape-keys; never fabricates. */
  applyAssetsPatch(
    rows: ReadonlyArray<Readonly<import("../types").UiAsset>>,
    tombstoned: ReadonlyArray<string>,
    snapshotHash: string | null,
  ) {
    const byKey = new Map(workspace.assets.map((a) => [a.shapeKey, a]));
    for (const row of rows) byKey.set(row.shapeKey, row);
    for (const key of tombstoned) byKey.delete(key);
    const base: Record<string, string> = {};
    for (const row of byKey.values()) {
      if (row.shapeHash) base[row.shapeKey] = row.shapeHash;
    }
    setWorkspace("assets", reconcile([...byKey.values()]));
    setWorkspace("assetsBase", reconcile(base));
    setWorkspace("assetsSnapshotHash", snapshotHash);
  },

  requestQuit() {
    if (workspace.quitPending) {
      void import("../lib/exit-cleanup").then((m) => m.safeExit(0));
    }
    setWorkspace("quitPending", true);
    setWorkspace("quitDialogOpen", true);
    setTimeout(() => {
      setWorkspace("quitPending", false);
      setWorkspace("quitDialogOpen", false);
    }, 3000);
  },

  cancelQuit() {
    setWorkspace("quitPending", false);
    setWorkspace("quitDialogOpen", false);
  },

  // ── Connection / Platform ──
  setConnection(status: Partial<ConnectionStatus>) {
    setWorkspace("connection", (prev) => ({ ...prev, ...status }));
  },

  setBackendState(state: { endpoints?: UiEndpoint[]; models?: UiModel[]; jobs?: UiJob[]; skills?: UiSkill[]; loading?: boolean; error?: string | null }) {
    if (state.endpoints !== undefined) setWorkspace("endpoints", reconcile(state.endpoints));
    if (state.models !== undefined) setWorkspace("models", reconcile(state.models));
    if (state.jobs !== undefined) setWorkspace("jobs", reconcile(state.jobs));
    if (state.skills !== undefined) {
      // Merge by name: CLI inventory (user/local skills) must not flatten the
      // bridge registry (19 builtins incl. umrs/code/research); both are truth.
      // PLAIN ASSIGN, never reconcile(): reconcile merges arrays BY INDEX, so
      // a shorter merged list would keep stale tail rows alive — that residue
      // was the /skills "list changes on every click" churn (verified live via
      // PTY; bridge registry is perfectly stable at 27 rows).
      setWorkspace("skills", mergeSkillInventories(workspace.skills, state.skills));
    }
    if (state.loading !== undefined) setWorkspace("backendLoading", state.loading);
    if (state.error !== undefined) setWorkspace("backendError", state.error);
  },

  /**
   * Bridge-authoritative skill inventory (list_skills / activate_skill
   * snapshots): REPLACE bridge-flagged rows, keep CLI-merged rows that carry
   * no state (active === undefined) so nothing the bridge never reported
   * silently vanishes.
   */
  setSkillInventory(bridgeSkills: UiSkill[]) {
    const withoutBridgeState = workspace.skills.filter((s) => s.active === undefined);
    // Plain assign — see setBackendState note above (reconcile keeps stale tails).
    setWorkspace(
      "skills",
      mergeSkillInventories(withoutBridgeState, bridgeSkills),
    );
  },

  setDemoMode(enabled: boolean) {
    setWorkspace("demoMode", enabled);
    // Keep the sticky welcome line honest for the active mode.
    setScopedField("chatMessages", (m: ChatMessage) => m.id === WELCOME_MESSAGE_ID, "content", welcomeChatContent({ demoMode: enabled }));
  },

  setMascotState(state: WorkspaceState["mascotState"]) {
    setWorkspace("mascotState", state);
  },

  setSessionId(sessionId: string) {
    setScopedField("sessionId", sessionId);
    // Tab meta follows the owning tab's live session id (strip label +
    // dedupe): the event target's when one is pinned — a background patch
    // re-keyed the ACTIVE tab's meta to another tab's session.
    const tabId = eventTargetTab() ?? workspace.activeChatTabId;
    if (tabId) actions.setTabMeta(tabId, { sessionId });
  },

  setCurrentModel(model: string | null) {
    setScopedField("currentModel", model);
  },

  setChatBackend(backend: ChatBackend | null) {
    setWorkspace("chatBackend", backend);
  },

  setPlatform(platform: PlatformAdapter | null) {
    setWorkspace("platform", platform);
  },

  setDataSource(source: DataSource | null) {
    setWorkspace("dataSource", source);
  },

  pushLoadSample(sample: number) {
    setWorkspace("loadHistory", (h) => [...h, sample].slice(-24));
  },

  // ── Domain Data ──
  setAgents(agents: Agent[]) {
    setWorkspace("agents", reconcile(agents));
  },

  setGoals(goals: ResearchGoal[]) {
    setWorkspace("goals", reconcile(goals));
  },

  setTasks(tasks: ResearchTask[]) {
    setWorkspace("tasks", reconcile(tasks));
  },

  setKnowledge(list: KnowledgeObject[]) {
    setWorkspace("knowledge", reconcile(list));
  },

  addGoal(goal: ResearchGoal) {
    setWorkspace("goals", (list) => [...list, goal]);
  },

  addTask(task: ResearchTask) {
    setWorkspace("tasks", (list) => [...list, task]);
  },

  addKnowledge(obj: KnowledgeObject) {
    setWorkspace("knowledge", (list) => [...list, obj]);
  },

  addNotification(notification: Notification) {
    setWorkspace("notifications", (list) => [notification, ...list]);
    setWorkspace("unreadNotifications", (n) => n + 1);
  },

  markNotificationRead(id: string) {
    setWorkspace("notifications", (n) => n.id === id, "read", true);
  },

  addEvent(event: ConsoleEvent) {
    setWorkspace("events", (list) => [event, ...list].slice(0, 1000));
  },

  appendTerminalLine(line: string) {
    const stamp = localTimeHHMMSS(new Date().toISOString());
    setWorkspace("terminalLines", (list) => [`[${stamp}] ${line}`, ...list].slice(0, 200));
  },

  notify(severity: Notification["severity"], title: string, message: string) {
    actions.addNotification({
      id: ulid(),
      title,
      message,
      severity,
      read: false,
      timestamp: new Date().toISOString(),
    });
  },

  // ── Chat ────────────────────────────────────────────────────────────

  /**
   * Shared chat-text routing — the SAME grammar as the console chat input
   * (chat.tsx `submit`): `!cmd` runs in the local shell (full output shown
   * in the transcript AND relayed into the model as a turn so the agent
   * responds — 2026-10-01), `/cmd` dispatches through the slash router,
   * anything else becomes a model turn. Surfaces that accept chat text
   * outside ChatPanel (the title screen) MUST route through here so behavior
   * stays equal — sending a slash command to sendChatMessage ships it to the
   * LLM bridge as plain text (the title-screen `/sessions` 502).
   * Keep in sync with chat.tsx `submit`.
   */
  async submitChatText(text: string) {
    const trimmed = text.trim();
    if (!trimmed) return;
    if (trimmed.startsWith("!")) {
      const command = trimmed.slice(1);
      // The result belongs to the tab that ran it, whatever is active when
      // the shell finishes (static-sweep#4).
      const tabId = workspace.activeChatTabId;
      // S4: keys typed on the line are masked in every echo and the relay.
      actions.sendSystemNotice(shellEcho(command));
      try {
        actions.finishLocalShell(tabId, command, maskShellResult(await runLocalShell(command)));
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        actions.finishLocalShell(tabId, command, { error: maskShellText(command, message) });
      }
      return;
    }
    if (trimmed.startsWith("/")) {
      await actions.dispatchSlashFromChat(trimmed);
      return;
    }
    if (!(await actions.rewindForResend())) return;
    actions.sendChatMessage(trimmed);
  },

  /**
   * A `!cmd` finished: its preview and failure card land on the tab that RAN
   * it (id captured before the await), never on the tab active when the
   * shell finished — that put one tab's output, and a model turn on it, into
   * another tab's session (static-sweep#4). Nothing lands on a closed tab.
   */
  finishLocalShell(tabId: string, command: string, outcome: ShellRunResult | { error: string }) {
    applyOnTab(tabId, () => deliverShellOutcome(tabId, command, outcome));
  },

  /**
   * `relay` (2026-10-01): machine-generated content (e.g. a `!cmd` shell
   * result ingested for the agent) — never names the tab and never consumes
   * an armed teamup one-shot; everything else (transcript row, queue,
   * streaming) is identical to a typed prompt.
   */
  /** A slash command's follow-up turns (/retry, /code edit, /agent chat,
   * /search): relays, sent in order. On a session without a web-access
   * choice all of them wait for one question, and a cancel puts them back
   * in the composer — /retry already cut its turn (RS-1 round-3 fix N1). */
  sendFollowUps(texts: string[]) {
    const live = texts.filter((t) => t.trim() !== "");
    if (live.length === 0) return;
    if (tabNeedsWebChoice()) {
      holdForWebChoice(live, true, true);
      return;
    }
    for (const text of live) actions.sendChatMessage(text, { relay: true });
  },

  sendChatMessage(content: string, opts?: { relay?: boolean }) {
    // ADR-099 D-2: a session without a web-access choice (a fresh boot, one
    // saved before RS-1) asks before ANY turn — typed, edit-resend, retry,
    // queue flushes, slash and shell relays (RS-1 round-2 fix W2-5: only the
    // typed send asked). The message waits for the answer.
    if (content.trim() && tabNeedsWebChoice()) {
      holdForWebChoice([content], opts?.relay === true, false);
      return;
    }
    const now = new Date().toISOString();
    const turnTabId = workspace.activeChatTabId;

    // B-family (2026-09-27): armed one-shot fires the goal as a team start
    // instead of a chat turn — one message, then auto-disarm.
    if (workspace.teamupArmed && content.trim() && !opts?.relay) {
      const crew = workspace.teamupArmedCrew;
      const parallelRoles = workspace.teamupArmedParallel;
      const armedPlan = workspace.teamupArmedPlan;
      actions.setTeamupArmed(false);
      // A team goal is still the first user message — it names the tab.
      actions.noteFirstTabPrompt(turnTabId, content);
      const userMsg: ChatMessage = { id: ulid(), role: "user", content, timestamp: now };
      setScopedField("chatMessages", (list: ChatMessage[]) => [...list, userMsg]);
      const goal = content;
      if (armedPlan) {
        // ADR-092 slice 3: the message is the plan's goal; the bridge asks
        // the human with the plan's card (never-auto) before anything runs.
        void import("./teams").then(({ teamsActions }) =>
          teamsActions.startPlan(armedPlan.template, goal, armedPlan.args),
        );
        return;
      }
      void import("./teams").then(async ({ teamsActions }) => {
        const started = await teamsActions.startCrew(goal, { crew, parallelRoles });
        const { crewStartText } = await import("../lib/teamup-text");
        actions.sendSystemNotice(crewStartText(crew, Boolean(started)));
      });
      return;
    }


    // Chat tabs: the strip adopts the first typed prompt as the tab title
    // immediately (client-side, clean); the server name refines it later.
    // Relayed machine content (shell results) never names a tab.
    if (!opts?.relay) actions.noteFirstTabPrompt(turnTabId, content);

    // T5 topic-15+: queue behind the running turn — durable via session
    // metadata through the bridge so a TUI-side resume replays the queue
    // (in-memory head tracked only for paint; bridge remains the authority).
    if (workspace.chatStreaming) {
      const queuedMsg: ChatMessage = {
        id: ulid(),
        role: "user",
        content,
        timestamp: now,
        queued: true,
      };
      setScopedField("chatMessages", (list: ChatMessage[]) => [...list, queuedMsg]);
      setScopedField("pendingChatQueue", (list: string[]) => [...list, queuedMsg.id]);
      // durable append via F9 rail (remote-first lane; server-side cross-client shared rows)
      appendsInFlight.add(queuedMsg.id);
      void import("../lib/chat-queue-rail").then(({ queueRemote }) =>
        queueRemote("append", {
          content,
          clientId: workspace.identitySource || undefined,
          remoteOnly: workspace.sessionVenue === "frontend",
        }).then((result) => {
          if (!result.applied) failedQueuedAppend(turnTabId, queuedMsg, result);
        }),
      ).finally(() => appendAnswered(queuedMsg.id));
      return;
    }

    const userMsg: ChatMessage = { id: ulid(), role: "user", content, timestamp: now };
    setScopedField("chatMessages", (list: ChatMessage[]) => [...list, userMsg]);
    actions.setMascotState("thinking");
    setScopedField("isTyping", true);
    setScopedField("chatStreaming", true);
    actions.setTabMeta(turnTabId, { busy: true });
    actions.resetThinking();
    // setTurnToolActivity APPENDS (r2 #33): lines an interrupted turn
    // flushed after its pump finished must not open this turn's first round,
    // nor may the bridge-side round accumulator still holding them.
    actions.setTurnToolActivity([]);
    resetTurnLines();

    // T5 topic-12: turn work-session marker — turn id kept for the completion
    // tail only (start-of-turn banner made test rows brittle in the midstream
    // window; the live chip already carries in-timer progress).
    const turnId = userMsg.id;
    const turnStartedAtMs = Date.now();

    const turn = workspace.chatMessages.filter((m) => m.role === "user").length;
    // Omit welcome banner from model history — it is UI chrome, not conversation.
    const history: ChatHistoryItem[] = workspace.chatMessages
      .filter((m) => m.id !== WELCOME_MESSAGE_ID && (m.role === "user" || m.role === "assistant"))
      .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }));
    // Make chat project-aware. The Companion bridge IGNORES history (it keeps
    // its own session), so the context must ride on the outgoing message text.
    // Prepend it to the wire content (visible bubble stays clean) AND to history
    // for backends that do read history (r4-cli direct).
    const projectContext = buildProjectChatContext();
    const historyWithContext: ChatHistoryItem[] = projectContext
      ? [{ role: "user", content: projectContext }, ...history]
      : history;
    const wireContent = projectContext ? `${projectContext}\n\n${content}` : content;
    const backend = workspace.chatBackend;
    const model = workspace.currentModel ?? undefined;
    const remoteTurn = workspace.sessionVenue === "frontend";

    const finish = (finalText: string, opts?: { holdQueue?: boolean }) => {
      // Read before the queue pop below starts the next turn.
      const turnLines = turnToolLines(turnId);
      actions.settleBackgroundTurn(finalText);
      setScopedField("isTyping", false);
      setScopedField("chatStreaming", false);
      setScopedField("chatTurnStatus", null);
      chatAborts.delete(turnTabId);
      actions.setTabMeta(turnTabId, { busy: false });
      // Server auto-names the session on the first turn — refresh the titles
      // (throttled inside; skipped offline).
      void import("../bridge/session-bridge").then(({ refreshSessionsForTabTitles }) => {
        if (!workspace.demoMode) refreshSessionsForTabTitles();
      }).catch(() => undefined);

      // T5 topic-15+: pop the queue. Not after a typed session error: the
      // stale tab would refuse every queued message in turn, each one
      // overwriting the last one's draft (cycle 3 review).
      if (!remoteTurn && !opts?.holdQueue) popQueuedTurn();
      // Do not invent Tasks/Knowledge from chat keywords — that looked like real work (V-03).
      actions.addEvent({
        id: ulid(),
        timestamp: new Date().toISOString(),
        type: "agent",
        actor: "r4",
        action: "chat-reply",
        detail: finalText.slice(0, 60),
      });
      sendTurnCompletionTail(turnId, turnLines.length, Date.now() - turnStartedAtMs);
      flagGhostUpdate(turnId, turnLines, finalText);

      actions.setMascotState("success");
      setTimeout(() => actions.setMascotState("idle"), 1500);
    };

    if (!backend) {
      Promise.resolve().then(() => {
        const text = generateLocalReply(content, turn, "offline");
        appendAssistantMessage(text, { model: "offline", fallback: true });
        setScopedField("chatFallbackActive", true);
        setScopedField("chatFallbackDetail", "no chat backend");
        finish(text);
      });
      return;
    }

    if (backend.chatStream) {
      const abort = new AbortController();
      chatAborts.set(turnTabId, abort);
      void runChatStream(backend, wireContent, historyWithContext, model, abort, finish, turnTabId, userMsg.id);
      return;
    }

    Promise.resolve(
      backend.chat(wireContent, historyWithContext, model).catch((err) =>
        companionUnavailableReply(err instanceof Error ? err.message : String(err)),
      ),
    ).then(async (replyText) => {
      // The reply settles into its own tab (TUI audit r2 #31).
      if (await turnOwnerGone(turnTabId)) return;
      const text = replyText || companionUnavailableReply("empty reply");
      const isFallback = text.startsWith("[Chat unavailable]") || text.startsWith("[Offline]");
      appendAssistantMessage(text, { model: isFallback ? "offline" : model, fallback: isFallback });
      setScopedField("chatFallbackActive", isFallback);
      setScopedField("chatFallbackDetail", isFallback ? "chat fallback" : null);
      finish(text);
    });
  },

  interruptChat() {
    if (workspace.backgroundTurnTaskId) {
      actions.finishBackgroundTask(workspace.backgroundTurnTaskId, "cancelled");
      setScopedField("backgroundTurnTaskId", null);
    }
    if (workspace.askUser) {
      try {
        getCompanionBridge().respondAskUser(workspace.askUser.id, null);
      } catch {
        /* ignore */
      }
      setScopedField("askUser", null);
    }
    const activeAbort = chatAborts.get(workspace.activeChatTabId);
    if (activeAbort) {
      activeAbort.abort();
      chatAborts.delete(workspace.activeChatTabId);
    }
    workspace.chatBackend?.interrupt?.();
    try {
      getCompanionBridge().cancelTurn();
      getCompanionBridge().abortInFlightSend();
    } catch {
      /* ignore */
    }
    setScopedField("chatStreaming", false);
    setScopedField("isTyping", false);
    setScopedField("chatTurnStatus", null);
    setScopedField("chatMessages", (m: ChatMessage) => m.role === "assistant" && m.streaming === true, "streaming", false);
    // T5 topic-15+: clear queued turns too — an explicit user interrupt kills
    // the whole queue honestly (never silently re-fires under interrupt); the
    // durable head also clears on the bridge side so resumptions replay zero.
    if (workspace.sessionVenue !== "frontend") {
      dropQueuedOnInterrupt();
    } else {
      setScopedField("pendingChatQueue", []);
      setScopedField("chatMessages", (m: ChatMessage) => m.role === "user" && m.queued === true, "queued", false);
    }
    actions.setTabMeta(workspace.activeChatTabId, { busy: false });
    actions.setMascotState("idle");
  },

  clearChat() {
    for (const id of workspace.pendingChatQueue) settleQueued(id);
    setScopedField("pendingChatQueue", []);
    void import("../lib/chat-queue-rail").then(({ queueRemote }) => queueRemote("clear"));
    setScopedField("chatMessages", [
      {
        id: WELCOME_MESSAGE_ID,
        role: "assistant",
        content: welcomeChatContent({ demoMode: workspace.demoMode }),
        timestamp: new Date().toISOString(),
      },
    ]);
  },

  // ── Backend Actions ────────────────────────────────────────────────

  async runBackendAction(
    label: string,
    op: (source: DataSource) => Promise<{ ok: boolean; message: string }> | undefined,
  ): Promise<void> {
    const source = workspace.dataSource;
    if (!source) {
      actions.notify("warning", label, "No backend connected.");
      return;
    }
    actions.addEvent({
      id: ulid(),
      timestamp: new Date().toISOString(),
      type: "tool",
      actor: "console",
      action: label,
    });
    actions.setMascotState("running");
    try {
      const result = await op(source);
      if (!result) {
        const msg = "Not supported by the active backend.";
        actions.appendTerminalLine(`${label}: ${msg}`);
        actions.notify("warning", label, msg);
        actions.setMascotState("idle");
        return;
      }
      actions.appendTerminalLine(`${label}: ${result.message}`);
      actions.addEvent({
        id: ulid(),
        timestamp: new Date().toISOString(),
        type: "tool",
        actor: "console",
        action: result.ok ? "ok" : "error",
        detail: result.message,
      });
      actions.notify(result.ok ? "success" : "error", label, result.message);
      actions.setMascotState(result.ok ? "success" : "error");
    } catch (err) {
      actions.notify("error", label, err instanceof Error ? err.message : String(err));
      actions.setMascotState("error");
    } finally {
      setTimeout(() => actions.setMascotState("idle"), 1500);
    }
  },

  provisionEndpoint(model: string, tier?: string) {
    return actions.runBackendAction(`Provision ${model}`, (s) => s.provisionEndpoint?.(model, tier));
  },

  stopEndpoint(id: string) {
    return actions.runBackendAction(`Stop endpoint ${id}`, (s) => s.stopEndpoint?.(id));
  },

  cancelJob(id: string) {
    return actions.runBackendAction(`Cancel job ${id}`, (s) => s.cancelJob?.(id));
  },

  retryJob(id: string) {
    return actions.runBackendAction(`Retry job ${id}`, (s) => s.retryJob?.(id));
  },

  showJobLogs(id: string) {
    const source = workspace.dataSource;
    if (!source?.getJobLogs) {
      actions.notify("warning", "Job logs", "Not supported by the active backend.");
      actions.setSpaceFeedback("Job details not supported by the active backend.");
      actions.sendSystemNotice("Job details not supported by the active backend.");
      return Promise.resolve();
    }
    if (!workspace.activityOpen) actions.toggleActivityConsole();
    actions.setActivityTab("terminal");
    actions.setMascotState("running");
    actions.setSpaceFeedback(`Loading job details for ${id.slice(0, 8)}…`);
    return source
      .getJobLogs(id, 40)
      .then((logs) => {
        for (const line of logs.split("\n").slice(-40)) {
          if (line.trim()) actions.appendTerminalLine(line);
        }
        actions.appendTerminalLine(`— end of job details for ${id}`);
        actions.notify("success", "Job details", `Loaded details for ${id}`);
        actions.setSpaceFeedback(`Job details loaded — Activity Terminal (F9)`);
        actions.sendSystemNotice(`Loaded job details for ${id} — Activity Terminal (F9)`);
        actions.setMascotState("success");
      })
      .catch((err) => {
        const message = err instanceof Error ? err.message : String(err);
        actions.notify("error", "Job details", message);
        actions.setSpaceFeedback(`Job details failed: ${message}`);
        actions.setMascotState("error");
      })
      .finally(() => {
        setTimeout(() => actions.setMascotState("idle"), 1500);
      });
  },

  async verifyKnowledge(question: string) {
    const projectId = workspace.project?.id;
    if (!projectId) {
      actions.notify("warning", "Knowledge verify", "No active project — use /project switch first.");
      return;
    }
    const source = workspace.dataSource;
    if (!source?.verifyKnowledge) {
      actions.notify("warning", "Knowledge verify", "Not supported by the active backend.");
      return;
    }
    knowledgeActions.setLoading(true);
    knowledgeActions.setError(null);
    try {
      const result = await source.verifyKnowledge(projectId, question);
      if (result.error) {
        knowledgeActions.setError(String(result.error));
        actions.notify("error", "Knowledge verify", String(result.error));
        return;
      }
      const verification = mapVerifyResponse(question, result);
      knowledgeActions.setVerification(verification);
      const offlineTag = workspace.demoMode ? " (demo fixture)" : "";
      actions.notify("success", "Knowledge verify", `${verification.answer.slice(0, 120)}${offlineTag}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      knowledgeActions.setError(message);
      actions.notify("error", "Knowledge verify", message);
    } finally {
      knowledgeActions.setLoading(false);
    }
  },

  // ── Chat tabs (2026-09-29, opencode-v2 parity) ───────────────────────
  //
  // One tab = one companion session = one bridge process. The flat chat
  // fields always mirror the ACTIVE tab; background tabs hold snapshots +
  // buffered bridge events in tabs/registry.ts. Turns keep running behind
  // the strip: a backgrounded tab's pump suspends on a gate and resumes
  // exactly where it paused once the tab is activated again.

  /** Patch one tab's strip metadata. */
  setTabMeta(tabId: string, patch: Partial<ChatTabMeta>) {
    setWorkspace("chatTabs", (t) => t.id === tabId, (t) => ({ ...t, ...patch }));
  },

  /** Refresh tab titles from the loaded sessions list. Real server names win
   *  over the `chat N` fallback; systematic placeholder names ("Session
   *  YYYY-MM-DD HH:MM", "(unnamed)") never overwrite a good title — instead
   *  the aggressive pass renames the SERVER session from the first user
   *  prompt (once per session per run), so every surface improves. */
  syncChatTabTitles() {
    const byId = new Map(workspace.sessionsList.map((r) => [r.id, r]));
    for (const tab of workspace.chatTabs) {
      if (!tab.sessionId) continue;
      const record = byId.get(tab.sessionId);
      if (!record) continue;
      const name = cleanSessionTitle(record.name ?? "");
      if (name && !isGenericSessionName(name)) {
        if (tab.title !== name) actions.setTabMeta(tab.id, { title: name });
        continue;
      }
      actions.aggressiveRenameSession(tab);
    }
  },

  /** First-user-prompt title for a tab, from its own transcript (live flat
   *  for the active tab, registry snapshot for background ones). */
  firstTabPromptTitle(tabId: string): string {
    const messages: ChatMessage[] | undefined =
      tabId === workspace.activeChatTabId
        ? workspace.chatMessages
        : (runtimeOrNull(tabId)?.snapshot?.chatMessages as ChatMessage[] | undefined);
    const firstUser = (messages ?? []).find((m) => m.role === "user" && !m.queued);
    if (firstUser) {
      const title = promptDerivedTitle(firstUser.content);
      if (title) return title;
    }
    const current = workspace.chatTabs.find((t) => t.id === tabId)?.title ?? "";
    return isFallbackTitle(current) ? "" : current;
  },

  /**
   * Aggressive rename (maintainer call, 2026-09-30): a session still wearing
   * the server's create-time placeholder gets renamed SERVER-side from its
   * first user prompt — once per session per run (renameSession's refetch
   * re-runs sync and the new name adopts through the normal path).
   */
  aggressiveRenameSession(tab: ChatTabMeta) {
    if (!tab.sessionId || aggressiveRenamedSessions.has(tab.sessionId)) return;
    const candidate = actions.firstTabPromptTitle(tab.id);
    if (!candidate || isGenericSessionName(candidate) || candidate.trimStart().startsWith("/")) return;
    aggressiveRenamedSessions.add(tab.sessionId);
    if (workspace.demoMode) return;
    void import("../bridge/session-bridge")
      .then((m) => m.renameSession(tab.sessionId, candidate))
      .catch(() => {
        aggressiveRenamedSessions.delete(tab.sessionId);
      });
  },

  /** Provisional title from the first typed prompt (immediate, client-side);
   *  never overrides a real title; slash commands never name a tab (they are
   *  verbs, not conversation). */
  noteFirstTabPrompt(tabId: string, content: string) {
    const tab = workspace.chatTabs.find((t) => t.id === tabId);
    if (!tab || !isFallbackTitle(tab.title)) return;
    if (content.trimStart().startsWith("/")) return;
    const title = promptDerivedTitle(content);
    if (title) actions.setTabMeta(tabId, { title });
  },

  /** Create a tab and switch to it. Returns the new tab id (null at cap). */
  tabCreate(opts?: { sessionId?: string; title?: string; snapshot?: Record<string, unknown> | null }): string | null {
    if (workspace.chatTabs.length >= MAX_CHAT_TABS) {
      actions.sendSystemNotice(`tab limit reached (${MAX_CHAT_TABS}) — close one first`);
      return null;
    }
    const meta = makeTabMeta(workspace.chatTabs.length + 1);
    meta.title = freeFallbackTitle(workspace.chatTabs.length + 1);
    if (opts?.sessionId) meta.sessionId = opts.sessionId;
    if (opts?.title) meta.title = opts.title;
    const prev = workspace.activeChatTabId;
    setWorkspace("chatTabs", (list) => [...list, meta]);
    if (opts?.snapshot) {
      getRuntime(meta.id).snapshot = opts.snapshot;
      applyScopedValues(getRuntime(meta.id).tabStore, opts.snapshot);
    } else if (prev) {
      // Per-tab projects (2026-10-01): a fresh tab inherits the PREVIOUS
      // tab's project selection (project / identityProjectId / provider /
      // source) — read from that tab's own store. Clone the values: the raw
      // reads are Solid proxies and assigning one into another store ALIASES
      // the underlying node (a later write in the new tab reconciled straight
      // into the previous tab's project — tab-project-scope pin).
      const prevState = runtimeOrNull(prev)?.tabStore?.state;
      if (prevState) {
        const inherit: Record<string, unknown> = {};
        for (const field of ["project", "identityProjectId", "identityProfile", "identitySource"] as const) {
          const value = (prevState as Record<string, unknown>)[field];
          if (value != null) inherit[field] = structuredClone(unwrap(value as never));
        }
        if (Object.keys(inherit).length > 0) {
          getRuntime(meta.id).snapshot = inherit;
          applyScopedValues(getRuntime(meta.id).tabStore, inherit);
        }
      }
    }
    setWorkspace("activeChatTabId", meta.id);
    restoreScopedFromStore(meta.id);
    notifyActivated(meta.id);
    followActiveTabPoller(meta.id);
    return meta.id;
  },

  /** Switch the active tab (no-op for the current/unknown tab). */
  tabActivate(tabId: string) {
    if (tabId === workspace.activeChatTabId) return;
    if (!workspace.chatTabs.some((t) => t.id === tabId)) return;
    const prev = workspace.activeChatTabId;
    setWorkspace("activeChatTabId", tabId);
    actions.setTabMeta(tabId, { needsAttention: false, absorbed: 0 });
    restoreScopedFromStore(tabId);
    notifyActivated(tabId);
    followActiveTabPoller(tabId);
    // A1 lazy resume (2026-10-05): a restored tab first activated resumes
    // its bound session exactly like a manual tabOpenSession would.
    const rtForResume = runtimeOrNull(tabId);
    const target = workspace.chatTabs.find((t) => t.id === tabId);
    if (rtForResume?.restoredPending && (target?.sessionId ?? "") && !workspace.demoMode) {
      rtForResume.restoredPending = false;
      void import("../bridge/session-bridge").then(({ resumeSession }) =>
        resumeSession(target!.sessionId!),
      ).catch(() => undefined);
    }
    // Titles follow server names — a switch is when staleness is most visible.
    void import("../bridge/session-bridge").then(({ refreshSessionsForTabTitles }) => {
      if (!workspace.demoMode) refreshSessionsForTabTitles();
    }).catch(() => undefined);
  },

  /** Cycle the strip (dir +1 / -1, wraps). */
  tabCycle(dir: 1 | -1) {
    const order = workspace.chatTabs.map((t) => t.id);
    const next = adjacentTabId(order, workspace.activeChatTabId, dir);
    if (next) actions.tabActivate(next);
  },

  /** Jump to the 1-based tab index. */
  tabJump(index: number) {
    const target = workspace.chatTabs[index - 1];
    if (target) actions.tabActivate(target.id);
  },

  /** A tab whose session was swapped in place (new session, failed
   *  restore) drops the old conversation's title for a free `chat N`: kept,
   *  the stale title got pushed onto the new session server-side. */
  resetTabTitle(tabId: string) {
    const index = workspace.chatTabs.findIndex((t) => t.id === tabId);
    if (index >= 0) actions.setTabMeta(tabId, { title: freeFallbackTitle(index + 1, tabId) });
  },

  /** Close a tab (default: active). The last tab refuses to close.
   *  `reopenable: false` keeps it off the reopen stack (a failed open). */
  async tabClose(tabId?: string, opts?: { reopenable?: boolean }): Promise<boolean> {
    const id = tabId ?? workspace.activeChatTabId;
    const meta = workspace.chatTabs.find((t) => t.id === id);
    if (!meta) return false;
    if (workspace.chatTabs.length <= 1) {
      actions.sendSystemNotice("last chat tab — use /new to restart the session instead");
      return false;
    }
    const order = workspace.chatTabs.map((t) => t.id);
    const index = order.indexOf(id);
    const wasActive = id === workspace.activeChatTabId;
    const nextId = nextTabAfterClose(order, id);
    // In-flight turn dies with the tab's bridge (process shutdown); its pump
    // sees the tab closed and settles nowhere (runChatStream).
    chatAborts.get(id)?.abort();
    chatAborts.delete(id);
    cancelClosingTabTask(id);
    await closeRuntime(meta, index, opts);
    setWorkspace("chatTabs", (list) => list.filter((t) => t.id !== id));
    if (wasActive && nextId) {
      setWorkspace("activeChatTabId", nextId);
      actions.setTabMeta(nextId, { needsAttention: false, absorbed: 0 });
      restoreScopedFromStore(nextId);
      notifyActivated(nextId);
      followActiveTabPoller(nextId);
    } else {
      dropBackgroundTurnAccumulators(id);
    }
    return true;
  },

  /** Reopen the most recently closed tab (session resume, server-authoritative). */
  async tabReopenClosed(): Promise<boolean> {
    const open = new Set(workspace.chatTabs.map((t) => t.sessionId).filter(Boolean));
    const entry = popClosedTab(open);
    if (!entry) {
      actions.sendSystemNotice("no closed tab to reopen");
      return false;
    }
    const tabId = actions.tabCreate({
      sessionId: entry.meta.sessionId,
      title: entry.meta.title,
      snapshot: entry.snapshot,
    });
    if (!tabId) return false;
    // Server replay wins over the close-time snapshot when a session id exists:
    // the bridge replays the journal (F10 window rules), never client memory.
    if (entry.meta.sessionId && !workspace.demoMode) {
      const { resumeSession } = await import("../bridge/session-bridge");
      await resumeSession(entry.meta.sessionId);
    }
    return true;
  },

  /** Apply a bridge-announced session title to the owning tab's strip cell. */
  tabSetTitleForSession(sessionId: string, title: string) {
    const name = title.trim().slice(0, 60);
    if (!sessionId || !name) return;
    const tab = workspace.chatTabs.find((t) => t.sessionId === sessionId);
    if (tab) actions.setTabMeta(tab.id, { title: name });
  },

  /** Open a companion session in a tab. The tab that already holds it is
   * RELOADED from the store (c3: after a session_conflict that is the
   * recovery — "already open in a tab" left the stale copy in place). */
  async tabOpenSession(sessionId: string, title?: string): Promise<boolean> {
    const existing = workspace.chatTabs.find((t) => t.sessionId === sessionId);
    if (existing) {
      actions.tabActivate(existing.id);
      if (workspace.demoMode) return true;
      const { resumeSession } = await import("../bridge/session-bridge");
      return resumeSession(sessionId);
    }
    const prevTabId = workspace.activeChatTabId;
    const tabId = actions.tabCreate({ sessionId, title });
    if (!tabId) return false;
    if (workspace.demoMode) return true;
    const { loadSessionIntoTab } = await import("../bridge/session-bridge");
    const failure = await loadSessionIntoTab(sessionId, tabId);
    if (failure === null) return true;
    await dropFailedOpenTab(tabId, prevTabId, failure);
    return false;
  },
};

/** 2026-10-01: the agent must SEE `!cmd` output — the full result is relayed
 *  as a chat turn (relay: never names the tab, never consumes an armed
 *  teamup one-shot; queues behind a running turn). Not when the user stopped
 *  it (Ctrl+C), and not from a tab switched away from: the relay can only
 *  start a turn on the ACTIVE tab's agent. */
function deliverShellOutcome(tabId: string, command: string, outcome: ShellRunResult | { error: string }): void {
  if ("error" in outcome) {
    actions.sendErrorCard({ what: `$ ${shellDisplayCommand(command)} failed`, action: "dropped", detail: outcome.error });
    return;
  }
  actions.sendSystemNotice(formatShellPreview(outcome));
  if (outcome.interrupted) return;
  if (tabId && tabId !== workspace.activeChatTabId) {
    actions.sendSystemNotice("not sent to the model — you switched tabs while it ran; run it again here to share it");
    return;
  }
  actions.sendChatMessage(formatShellForAgent(outcome), { relay: true });
}

/** A `chat N` fallback no OTHER open tab wears: the positional number when
 *  free, else one past the highest in use (the count-based number repeated a
 *  surviving tab's title after a close — 'chat 3' twice). */
function freeFallbackTitle(position: number, exceptTabId = ""): string {
  const used = new Set<number>();
  for (const tab of workspace.chatTabs) {
    const match = tab.id === exceptTabId ? null : /^chat (\d+)$/.exec(tab.title.trim());
    if (match) used.add(Number(match[1]));
  }
  if (!used.has(position)) return `chat ${position}`;
  return `chat ${Math.max(workspace.chatTabs.length, ...used) + 1}`;
}

/** A failed /sessions resume leaves the layout as it was: the tab opened
 *  for it closes (never onto the reopen stack), focus returns to the tab
 *  the user came from (unless they moved on), and the error lands there. */
async function dropFailedOpenTab(
  tabId: string,
  prevTabId: string,
  failure: import("../bridge/session-bridge").LoadFailure,
): Promise<void> {
  const wasActive = workspace.activeChatTabId === tabId;
  await actions.tabClose(tabId, { reopenable: false });
  if (wasActive && prevTabId) actions.tabActivate(prevTabId);
  const { postLoadFailure } = await import("../bridge/session-bridge");
  postLoadFailure(failure);
}

function appendAssistantMessage(
  content: string,
  opts: { model?: string; fallback?: boolean } = {},
) {
  const reply: ChatMessage = {
    id: ulid(),
    role: "assistant",
    content,
    timestamp: new Date().toISOString(),
    model: opts.model,
    fallback: opts.fallback,
  };
  // Pin the turn's tool activity (chronological grouping with the reply).
  if (workspace.turnToolActivity.length > 0) {
    const calls = workspace.turnToolActivity;
    reply.toolCalls = calls;
    actions.setNoticeDetails(
      `group:${reply.id}`,
      calls.map((line) => line.text),
    );
    actions.setTurnToolActivity([]);
  }
  setScopedField("chatMessages", (list: ChatMessage[]) => [...list, reply]);
}

/**
 * The 5 s resource poller follows the active tab's bridge. Before, a tab
 * without a live bridge (a fresh Ctrl+N tab, a restored tab) left it on the
 * previous tab's bridge, so the new tab's panels showed the other session's
 * science loop and knowledge base (TUI audit r2 #38). Such a tab's bridge
 * is not minted here (its spawn env must follow later project choices); the
 * poller idles until that bridge's init_state retargets it (tabs/wiring).
 */
function followActiveTabPoller(tabId: string): void {
  if (!tabWiringInstalled()) return; // tab-less test contexts share one bridge
  const bridge = runtimeOrNull(tabId)?.bridge;
  if (bridge) {
    retargetResourcePolling(bridge);
    return;
  }
  stopResourcePolling();
}

/**
 * Forget a closed BACKGROUND tab's stashed tool accumulators without
 * touching the live set, which belongs to the active tab (whose own stash
 * entry is empty by design). Resetting them wiped the active turn's tool
 * lines, call args and agent row (TUI audit r2 #32). The two swaps park the
 * live set under the active id while the closed entry is taken out, then
 * restore it.
 */
function dropBackgroundTurnAccumulators(closedId: string): void {
  const activeId = workspace.activeChatTabId;
  if (!activeId) return;
}

/** A chat-scoped field of any tab: the live store for the active tab, the
 *  saved snapshot for a background one. */
function scopedFieldOfTab(tabId: string, field: string): unknown {
  if (tabId === workspace.activeChatTabId) {
    return (workspace as unknown as Record<string, unknown>)[field];
  }
  return runtimeOrNull(tabId)?.snapshot?.[field];
}

/** A closed tab's Ctrl+B turn dies with its bridge: mark it cancelled. */
function cancelClosingTabTask(tabId: string): void {
  const taskId = scopedFieldOfTab(tabId, "backgroundTurnTaskId");
  if (typeof taskId === "string" && taskId) actions.finishBackgroundTask(taskId, "cancelled");
}

/**
 * A background tab's turn ended on its bridge (`turn_settled`). Its stream
 * pump stays parked until the tab is visited (the transcript writes belong
 * to that tab's store), but the tab meta and a Ctrl+B task settle now and
 * into that tab's own snapshot, never the active tab (TUI audit r2 #36).
 */
function settleBackgroundTabTurn(tabId: string): void {
  if (tabId === workspace.activeChatTabId) return;
  const meta = workspace.chatTabs.find((t) => t.id === tabId);
  if (!meta?.busy) return;
  actions.setTabMeta(tabId, { busy: false });
  // Stage 4: the owning tab's store is canonical at all times — the
  // background-turn task id lives there, not in a swap-era snapshot.
  const taskId = runtimeOrNull(tabId)?.tabStore?.state.backgroundTurnTaskId;
  if (typeof taskId !== "string" || !taskId) return;
  applyScopedValues(runtimeOrNull(tabId)?.tabStore, { backgroundTurnTaskId: null });
  actions.finishBackgroundTask(taskId, "done", meta.title);
  actions.notify("success", "background turn finished", `tab "${meta.title}" — switch to it for the reply`);
  try {
    process.stdout.write("\x07");
  } catch {
    /* no terminal */
  }
}

/** Backward-compatible reset function */
export function resetWorkspace() {
  actions.resetWorkspace();
}

// ── Chat tabs: store ↔ registry bindings (module init, cycle-safe) ──────

/** Default (fresh-tab) values for every chat-scoped field. */
function defaultScopedChatFields(): Record<string, unknown> {
  const initial = createInitialWorkspaceState() as unknown as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const field of CHAT_SCOPED_FIELDS) out[field] = initial[field];
  return out;
}

/** The same defaults, typed as one tab's ChatTabState (H-7 stage 1). */
function initialChatTabState(): ChatTabState {
  return defaultScopedChatFields() as unknown as ChatTabState;
}

bindTabsStoreHooks({
  isActive: (tabId) => workspace.activeChatTabId === tabId,
  createTabState: initialChatTabState,
  readScopedFields: () => {
    const values = workspace as unknown as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const field of CHAT_SCOPED_FIELDS) {
      // unwrap strips the store proxies (structuredClone refuses them).
      const raw = unwrap(values[field]);
      out[field] = raw == null ? raw : structuredClone(raw);
    }
    return out;
  },
  applyScopedFields: (values) => {
    const merged = { ...defaultScopedChatFields(), ...(values ?? {}) };
    for (const field of CHAT_SCOPED_FIELDS) {
      setWorkspace(field as never, merged[field] as never);
    }
  },
  patchTabMeta: (tabId, patch) => actions.setTabMeta(tabId, patch),
  noteAttention: (tabId, channel) => {
    const attention = channel === "approval" || channel === "ask_user";
    const meta = workspace.chatTabs.find((t) => t.id === tabId);
    if (!meta) return;
    actions.setTabMeta(tabId, {
      absorbed: meta.absorbed + 1,
      ...(attention ? { needsAttention: true } : {}),
    });
  },
  onTurnSettled: (tabId) => settleBackgroundTabTurn(tabId),
  onTabBridgeExit: (tabId, _message) => {
    actions.setTabMeta(tabId, { busy: false, needsAttention: true });
    if (workspace.activeChatTabId === tabId) {
      actions.setChatFallback(true, "companion bridge exited");
    }
  },
  bridgeEnvForTab: (tabId) => {
    // Per-tab projects (2026-10-01): the tab's selection rides R4_PROJECT_ID
    // in the bridge spawn env; the python bridge then treats that process
    // env as the tab-local current project (no global state.json write).
    const projectId = projectIdForTab(tabId);
    const env: Record<string, string> = {};
    if (projectId) env.R4_PROJECT_ID = projectId;
    return env;
  },
  sessionIdForTab: (tabId) => workspace.chatTabs.find((t) => t.id === tabId)?.sessionId ?? "",
});

/** Resolve the project a tab's bridge should boot into (active tab reads the
 *  live store; a background tab reads its saved snapshot). */
function projectIdForTab(tabId: string): string {
  if (tabId === workspace.activeChatTabId) {
    return workspace.project?.id ?? workspace.identityProjectId ?? "";
  }
  const snap = runtimeOrNull(tabId)?.snapshot;
  if (!snap) return "";
  const project = snap["project"] as { id?: string } | null | undefined;
  const identity = snap["identityProjectId"];
  return project?.id ?? (typeof identity === "string" ? identity : "");
}

// The canonical bridge accessor answers for the ACTIVE tab (pre-tabs call
// sites keep working; background tabs keep their own bridges). An explicit
// tabId answers for that tab (plan H-7 stage 5).
registerCompanionBridgeProvider((tabId?: string) =>
  bridgeForTab(tabId ?? workspace.activeChatTabId),
);

// ── Re-export other stores ─────────────────────────────────────────────

export { agentRuntime, agentActions } from "./agent-runtime";
export { knowledgeStore, knowledgeActions } from "./knowledge";
export { strategyStore, strategyActions } from "./research-strategy";
export { blackboardStore, blackboardActions } from "./scientific-blackboard";
export { loopStore, loopActions } from "./autonomous-loop";
export { subscriptionStore, subscriptionActions } from "./event-subscription";
export { workflowStore, workflowActions } from "./workflow";
export { umrsWorkflowStore, umrsWorkflowActions } from "./umrs-workflow";

// ADR-108 D-2: the console's own file features (git changes, `!` shell,
// @-mentions) follow the active tab's worktree (lib/resolve-python).
setWorkspaceOverride(() => activeTab().worktree?.root ?? "");
