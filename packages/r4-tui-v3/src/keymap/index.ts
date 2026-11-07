/**
 * R4 Console v3 keymap.
 *
 * Key ownership (plan H-6, 2026-10-18; docs/r4/TUI_REFERENCE.md "Key
 * ownership"): this listener runs BEFORE the focused renderable, so every
 * handler returns a boolean — `false` yields the key (the keymap neither
 * consumes nor preventDefaults it) and the next matching binding or the
 * focused component gets it. Who else owns a key is never read from
 * component state here: handlers ask the claims registry
 * (`key-claims.ts`), fed by store-owned layers (`store-claims.ts`) and by
 * components (`claimKeys`).
 */

import type { CliRenderer, KeyEvent, Renderable, Selection } from "@opentui/core";
import { CliRenderEvents } from "@opentui/core";
import { termSession } from "../terminal/pty-session";
import { createDefaultOpenTuiKeymap } from "@opentui/keymap/opentui";
import type { Keymap } from "@opentui/keymap";
import { workspace, actions, activeTab } from "../stores";
import { teamsStore, teamsActions } from "../stores/teams";
import type { Space } from "../types";
import {
  handleInfrastructureShortcut,
  handleResearchCancelJob,
  handleResearchGraphShortcut,
  handleResearchRunDemo,
  handleResearchStartMenu,
  handleTaskShortcut,
  letterShortcutsBlocked,
} from "../lib/space-shortcuts";
import {
  cycleResearchFocus,
  focusLiveVerify,
  handleOpsApprovalKey,
  handleResearchContextJsonToggle,
  handleResearchFocusNav,
  handleStrategyDemoKey,
} from "../lib/research-keyboard";
import {
  ACTIVITY_TABS,
  ACTIVITY_TAB_DIGIT_KEYS,
  ACTIVITY_TAB_CYCLE_KEYS,
  ACTIVITY_TOGGLE_KEYS,
  activityTrayVisible,
  cycleActivityTab,
  gotoActivityTab,
  showActivityTray,
  toggleActivityTray,
} from "../lib/activity-tabs";
import { loadKeymapOverrides, mergeKeymapOverrides } from "../lib/keymap-config";
import { slashInFlight } from "../lib/slash-in-flight";
import { localShellRunning, stopLocalShells } from "../lib/local-shell";
import { getCompanionBridge } from "../bridge/jsonl-client";
import { paneLayout } from "../lib/pane-layout";
import { listChanges, revertPath } from "../lib/git-changes";
import { editorForEnv } from "../lib/external-editor";
import { copyText } from "../lib/clipboard";
import { rotateInteractionMode } from "../lib/interaction-mode";
import { sessionVerbBlockedForRemoteOnly } from "../lib/session-filters";
import { spawnSync } from "node:child_process";
import {
  CTRL_C_WINDOW_MS,
  CTRL_D_WINDOW_MS,
  ctrlCAction,
  exitChord,
} from "../lib/tui-parity";
import { redrawScreen } from "../lib/redraw";
import { isDemoVisuals } from "../lib/console-mode";
import { scienceActions, scienceStore, selectedProposal } from "../stores/science";
import { openRunningWorkflowGraph } from "../lib/workflow-graph-open";
import { CLAIM_RANK, claimantsAbove, keyClaimed, runClaim, type ClaimKey } from "./key-claims";
import "./store-claims";

/** Every keymap command: true = consumed, false = yield the key. */
export type KeyHandler = () => boolean;
type Handlers = Record<string, KeyHandler>;

/** Run a side effect and consume the key. */
/** Layers Alt+W may draw over: its own overlay (retargets it) and the
 * splash (every overlay draws above it and takes Esc first). */
const WORKFLOW_GRAPH_OVER = new Set(["exec-detail", "splash"]);

/** The open dialog Alt+W must not cover, by its claim owner: any Esc
 * claimant above the local rank (modals, detail cards, pickers, the tool
 * approval and its feedback stage). Undefined when none is open. */
export function workflowGraphBlocker(): string | undefined {
  return claimantsAbove("escape", CLAIM_RANK.local).find((c) => !WORKFLOW_GRAPH_OVER.has(c.owner))?.owner;
}

function consume(run: () => unknown): KeyHandler {
  return () => {
    run();
    return true;
  };
}

/**
 * The Esc ladder (exported for direct black-box pins, 2026-09-27 B4): the
 * highest-ranked active `escape` claimant decides — transient layer →
 * splash → self-contained overlays → detail overlays → modal sweep → local
 * Esc owners (yield) → pane. Returns true only when a layer closed; false
 * lets the key fall through to the focused renderable.
 */
export function overlayCloseSweep(): boolean {
  return runClaim("escape");
}

/** The server graph panel shows the proposal inbox (knowledge pane, not
 * Verify, a server graph loaded, real data) and nothing covers it: while the
 * passage preview is open a click on the pane gave it focus, and ] / a / r
 * then decided proposals the person could not see (review of K1 part A). */
function proposalInboxShown(): boolean {
  if (workspace.paneView !== "knowledge" || workspace.knowledgePane === "verify") return false;
  if (workspace.citationPreview != null) return false;
  return Boolean(scienceStore.graphContent) && !isDemoVisuals();
}

/** Knowledge-pane proposal keys a / r: decide the SELECTED pending agent
 * graph proposal (the first until [ / ] or a click moves the selection).
 * They always took `proposals[0]` until K1.5 (knowledge plan 2026-10-24);
 * round 2: the panel's own trap was mounted unfocused, so the keys live here. */
function decideSelectedProposal(approved: boolean): boolean {
  if (!proposalInboxShown()) return false;
  const chosen = selectedProposal();
  if (!chosen) return false;
  void scienceActions.decideProposal(chosen.proposalId, approved);
  return true;
}

/** Knowledge-pane [ / ]: move the proposal selection. */
function cycleProposalSelection(delta: number): boolean {
  return proposalInboxShown() && scienceActions.cycleProposal(delta);
}

// Exit-chord state (Ctrl+C / Ctrl+D double press; module-level, one TUI).
let ctrlCArmedAt: number | null = null;
let ctrlDArmedAt: number | null = null;

/** Wave H: recompute git change sets into the store (sync; runs on cwd). */
function refreshChangesFromGit(): number {
  const changes = listChanges();
  actions.setChangeSets(changes ?? []);
  return changes?.length ?? 0;
}

/** P3: local-row gate for /sessions verbs — remote-only rows (server journal)
 *  carry no local row; the verb re-tells the reason instead of dead-pressing.
 *  RESUME is exempt: the bridge pull-fallback pulls remote-only rows into the
 *  local store server-side before loading. Reason line names ONLY the blocked
 *  verb — never implies resume is blocked. */
function sessionsRowNeedsLocal(record: { id: string; sync?: string } | undefined, verb: string): boolean {
  if (!record || !sessionVerbBlockedForRemoteOnly(record, verb)) return true;
  actions.sendSystemNotice(
    `session ${record.id.slice(0, 8)} is remote-only (server journal) — ${verb} needs a local row (↵ resume works — pulls from journal)`,
  );
  return false;
}

export type R4Keymap = Keymap<Renderable, KeyEvent>;

/**
 * Pane-scoped verbs (A4): pane letters (research / infra / tasks / knowledge)
 * only fire while the detail pane OWNS focus (Tab into the pane). Replaces the
 * old "idle" gate keyed on chat-draft emptiness — with an empty draft those
 * letters could fire while the user was typing in chat. A `letters` claim
 * (overlay, verify input, composer search) still yields them.
 */
function whenPaneFocused(run: () => boolean): KeyHandler {
  return () => {
    if (!workspace.paneFocus) return false;
    if (letterShortcutsBlocked()) return false;
    return run();
  };
}

/** whenPaneFocused, and only while `view` is the docked pane. */
function inPane(view: string, run: () => boolean): KeyHandler {
  return whenPaneFocused(() => (workspace.paneView === view ? run() : false));
}

/** Ctrl+1..7 / F1..F7 space order (matches Command Center nav). */
export const SPACE_SHORTCUTS: Space[] = [
  "dashboard",
  "research",
  "tasks",
  "knowledge",
  "infrastructure",
  "loop",
  "settings",
];

/** F1..F7 — reliable on Windows / WSL (Ctrl+digit is tab-switch in Windows Terminal). */
export const SPACE_FN_KEYS = SPACE_SHORTCUTS.map((_, idx) => `f${idx + 1}`);

/** Ctrl+Shift+digit — secondary fallback when F-keys are remapped. */
export const SPACE_FALLBACK_KEYS = SPACE_SHORTCUTS.map((_, idx) => `ctrl+shift+${idx + 1}`);

/** Space jump alternatives are F1..F7 + ctrl+shift+1..7 only.
 *  RETIRED (2026-09-15): comma chords (v,N / g,N) — the chord prefix ate the
 *  first letter when typed with an empty draft ("cannot type g/v"). */
export const RESEARCH_TAB_ALTGR_KEYS = [
  "ctrl+alt+shift+1",
  "ctrl+alt+shift+2",
  "ctrl+alt+shift+3",
] as const;

/** Research bottom tabs fallback — modifier-only (alt-digits). */
export const RESEARCH_TAB_FALLBACK_KEYS = ["alt+1", "alt+2", "alt+3"] as const;

/** @deprecated Research tabs no longer use F7–F9 (F7 = Settings). Kept empty for test/export stability. */
export const RESEARCH_TAB_FN_KEYS = [] as const;

export interface R4Binding {
  key: string;
  cmd: string;
  space?: string;
  idle?: boolean;
  /** Matches only while some claimant holds this claim key. */
  claim?: ClaimKey;
  /** OpenTUI binding fields are open-ended (KeyBindingLike). */
  [field: string]: unknown;
}

function bindingEntries(keys: string[], cmd: string, extra?: Omit<R4Binding, "key" | "cmd">): R4Binding[] {
  return keys.map((key) => ({ key, cmd, ...extra }));
}

async function execOverlayTraces(id: string): Promise<void> {
  if (!id) return;
  const { postTraceNotice } = await import("../lib/traces-notice");
  try {
    await postTraceNotice(actions, "traces.execution", id, "traces");
  } catch (err) {
    actions.sendSystemNotice(`traces failed: ${err instanceof Error ? err.message : String(err)}`);
  }
}

async function execOverlayCancel(id: string): Promise<void> {
  if (!id) return;
  const { umrsWorkflowActions } = await import("../stores/umrs-workflow");
  try {
    const exec = await umrsWorkflowActions.cancel(id);
    actions.sendSystemNotice(
      `cancel ${id.slice(0, 12)} → ${String((exec as unknown as Record<string, unknown>).status ?? "requested")}`,
    );
  } catch (err) {
    actions.sendSystemNotice(`cancel failed: ${err instanceof Error ? err.message : String(err)}`);
  }
}

function notifyCopy(text: string): void {
  const result = copyText(text);
  actions.sendSystemNotice(
    result.ok
      ? `copied ${text.length} chars to clipboard (${result.via})`
      : `copy failed: ${result.error ?? "unknown error"}`,
  );
}

/** The text a finished selection puts on the clipboard. Inside the terminal
 * pane this is WRAP-AWARE (terminal/copy.ts via the PTY session): each xterm
 * row is its own renderable, and OpenTUI's per-renderable "\n" join breaks a
 * soft-wrapped long line into fragments — the 2026-11-04 JWT paste incident.
 * Drags touching anything else keep OpenTUI's text (never mix coordinate
 * models). */
function copyableText(selection: Selection): string {
  return (
    termSession.copySelectionText(selection.anchor, selection.focus) ?? selection.getSelectedText()
  ).trim();
}

/** Auto-copy on mouse-up: finishSelection() emits "selection" exactly once
 * per completed drag. Copy immediately — no keypress — because macOS
 * terminals never deliver Command+C to the app (Command is terminal-side),
 * and ctrl+C is SIGINT. ctrl+alt+m (mouse.toggle) remains for native
 * selection mode. */
function wireSelectionCopy(renderer: CliRenderer): void {
  renderer.on(CliRenderEvents.SELECTION, (selection: Selection | null) => {
    if (!selection || selection.isDragging) return;
    const text = copyableText(selection);
    if (!text) return;
    renderer.clearSelection();
    notifyCopy(text);
  });
}

/** Binding fields: space / idle / claim gate the match set. */
function registerFields(keymap: R4Keymap): void {
  keymap.registerLayerFields({
    name(value, ctx) {
      ctx.attr("name", String(value).trim());
    },
  });
  keymap.registerBindingFields({
    space(value, ctx) {
      ctx.require("app.space", value);
    },
    idle(value, ctx) {
      if (value) ctx.require("app.idle", true);
    },
    claim(value, ctx) {
      if (value) ctx.require(`claim.${String(value)}`, true);
    },
  });
}

function teamAgentRows(): string[] {
  const status = teamsStore.status[workspace.teamDetailId ?? ""];
  return Object.keys((status?.agents ?? {}) as Record<string, unknown>);
}

function teamCursorRole(): string | undefined {
  const rows = teamAgentRows();
  return rows[workspace.teamDetailCursor % Math.max(1, rows.length)];
}

function teamVerb(verb: "run" | "pause" | "resume" | "cancel" | "report" | "tailor"): KeyHandler {
  return consume(() => void teamsActions[verb](workspace.teamDetailId ?? ""));
}

/** Team / workflow-execution overlay verbs (bindings carry `claim`). */
function overlayVerbHandlers(): Handlers {
  return {
    "team-overlay.down": consume(() => actions.teamOverlayMoveCursor(1)),
    "team-overlay.up": consume(() => actions.teamOverlayMoveCursor(-1)),
    "team-overlay.toggle": () => {
      const role = teamCursorRole();
      if (!role) return false;
      actions.teamOverlayToggleExpanded(role);
      return true;
    },
    "team-overlay.run": teamVerb("run"),
    "team-overlay.pause": teamVerb("pause"),
    "team-overlay.resume": teamVerb("resume"),
    "team-overlay.cancel": teamVerb("cancel"),
    "team-overlay.logs": consume(() => void teamsActions.logs(workspace.teamDetailId ?? "", 12)),
    "team-overlay.report": teamVerb("report"),
    "team-overlay.tailor": teamVerb("tailor"),
    "team-overlay.open-agent": consume(() => {
      const role = teamCursorRole();
      if (role) void teamsActions.openAgentTab(workspace.teamDetailId ?? "", role);
    }),
    "exec-overlay.traces": consume(() => void execOverlayTraces(workspace.execDetailId ?? "")),
    "exec-overlay.cancel": consume(() => void execOverlayCancel(workspace.execDetailId ?? "")),
  };
}

/** Activity tray keys show the /logs pane — the only place the tray
 * renders (lib/activity-tabs). */
function toggleActivityTab(tab: "notifications" | "todos" | "events"): KeyHandler {
  return () => toggleActivityTray(tab);
}

/** Tab cycle chords: show the tray, then step its tab. */
function cycleVisibleActivityTab(delta: -1 | 1): KeyHandler {
  return () => {
    showActivityTray();
    return cycleActivityTab(delta);
  };
}

/** Palette, help, panels, the Esc ladder and the Activity tray. */
function chromeHandlers(): Handlers {
  return {
    // A focused overlay trap owns Ctrl+P list navigation (round 2).
    "palette.open": () => {
      if (keyClaimed("ctrl+p")) return false;
      actions.openCommandPalette();
      return true;
    },
    // V4 S2: Ctrl+. (the old slash bar trigger) opens the same palette, /-seeded.
    "slash.open": consume(() => actions.openCommandPalette("/")),
    "context-panel.toggle": consume(() => actions.toggleContextPanel()),
    "activity-console.toggle": () => toggleActivityTray(),
    "event-timeline.toggle": toggleActivityTab("events"),
    // Open Activity Console on Notifications (no separate ghost overlay).
    "notifications.toggle": toggleActivityTab("notifications"),
    // Ctrl+T: the agent's to-do checklist (Activity tray, todos tab).
    "todos.toggle": toggleActivityTab("todos"),
    "overlay.close": overlayCloseSweep,
    "help.toggle": consume(() => actions.toggleHelp()),
    "app.quit": consume(() => actions.requestQuit()),
    "transcript.verbose.toggle": consume(() => {
      actions.toggleVerboseTranscript();
      actions.sendSystemNotice(
        workspace.verboseTranscript ? "verbose transcript on (Ctrl+O)" : "verbose transcript off",
      );
    }),
    "thinking.toggle": consume(() => actions.toggleThinkingExpanded()),
    "screen.redraw": () => redrawScreen(),
    // Alt+W (2026-10-24): the live DAG of the most recent running workflow
    // execution — the status-bar badge's jump; a notice when none runs, or
    // while a dialog is open (it would keep Enter / y / n under the overlay).
    "workflow.graph": consume(() => openRunningWorkflowGraph(workflowGraphBlocker())),
    // Cycle chords are modifiers — OK while typing.
    "activity.tab.prev": cycleVisibleActivityTab(-1),
    "activity.tab.next": cycleVisibleActivityTab(1),
    // Terminal pane (2026-10-03, revised same day): its focus moves BY
    // MOUSE CLICK ONLY — no key toggles it, so there is no term.* command
    // here at all. The pane itself opens through the slash verb /term.
  };
}

function armExit(which: "c" | "d"): void {
  const now = Date.now();
  const chord =
    which === "c"
      ? exitChord(ctrlCArmedAt, now, CTRL_C_WINDOW_MS)
      : exitChord(ctrlDArmedAt, now, CTRL_D_WINDOW_MS);
  if (which === "c") ctrlCArmedAt = chord.armedAt;
  else ctrlDArmedAt = chord.armedAt;
  if (chord.exit) void import("../lib/exit-cleanup").then((m) => m.safeExit(0));
  actions.sendSystemNotice(`Press Ctrl+${which.toUpperCase()} again to exit`);
}

/** Ctrl+C: interrupt the turn → stop a slash command → clear the draft →
 * double press exits (Claude-Code parity). */
function interruptKey(): boolean {
  const action = ctrlCAction({
    streaming: activeTab().chatStreaming,
    draft: activeTab().chatDraft,
    slashRunning: slashInFlight(),
    shellRunning: localShellRunning(),
  });
  if (action === "interrupt") {
    actions.interruptChat();
  } else if (action === "stop-slash") {
    getCompanionBridge().cancelTurn();
    actions.sendSystemNotice("stopping the running slash command…");
  } else if (action === "stop-shell") {
    stopLocalShells();
  } else if (action === "clear") {
    actions.setPendingChatDraft("");
  } else {
    armExit("c");
  }
  return true;
}

/** Turn control and chat tabs (Claude-Code / opencode parity). */
function turnHandlers(): Handlers {
  return {
    "input.interrupt": interruptKey,
    // Ctrl+D on an empty prompt: double press exits. A focused pane makes
    // the binding match with a draft present (r2 #67), so the draft is
    // checked here: shell semantics, a half-typed line never exits.
    "input.eof": () => {
      if ((activeTab().chatDraft ?? "").trim()) return false;
      armExit("d");
      return true;
    },
    // Ctrl+B is also the input's native cursor-left and a trap's list key:
    // act only while a turn streams (or explain on an empty prompt).
    "turn.background": () => {
      if (keyClaimed("ctrl+b")) return false;
      if (!activeTab().chatStreaming && activeTab().chatDraft.trim()) return false;
      actions.backgroundCurrentTurn();
      return true;
    },
    // Chat tabs (2026-09-29, opencode-v2 parity): parallel session tabs.
    "tab.new": () => {
      if (keyClaimed("ctrl+n")) return false;
      actions.tabCreate();
      return true;
    },
    "tab.close": consume(() => void actions.tabClose()),
    "tab.reopenClosed": consume(() => void actions.tabReopenClosed()),
    "tab.next": consume(() => actions.tabCycle(1)),
    "tab.prev": consume(() => actions.tabCycle(-1)),
    // The approval card claims `approval` while it is on screen; in its
    // feedback stage it yields y / a / Enter to the feedback input (round 2).
    "approval.yes": () => runClaim("approval", "y"),
    "approval.always": () => runClaim("approval", "a"),
    "approval.no": () => runClaim("approval", "n"),
  };
}

/** selection/copy verb (ctrl+y): yank an active mouse-drag selection
 * (OSC52 + pbcopy). Without this, drag-selected text is stranded on
 * screen — nothing pastes it out. */
function copySelection(renderer: CliRenderer): boolean {
  if (!renderer.hasSelection) return false;
  const selection = renderer.getSelection();
  const text = selection ? copyableText(selection) : "";
  renderer.clearSelection();
  if (!text) return false;
  notifyCopy(text);
  return true;
}

/** Transcript mouse-free copy (G): enter the message cursor from anywhere
 * (native terminal selection is captured by mouse reporting), then j/k
 * moves, y yanks via OSC52 + pbcopy (clipboard.ts), Esc exits. */
function enterTranscriptCursor(): boolean {
  // Ctrl+G is the composer's reverse-i-search cancel (round 2).
  if (keyClaimed("ctrl+g")) return false;
  if (activeTab().messageCursor === null && !actions.enterMessageCursor()) {
    actions.sendSystemNotice("transcript empty — nothing to select");
  }
  return true;
}

/** Pane focus (2026-10-06 revision): panes open UNFOCUSED and the chat
 * composer stays live. Tab moves the keys into the docked pane and back
 * out. Views whose space OWNS Tab for its own cycle (research focus,
 * knowledge catalog/verify) keep precedence — their bindings sit earlier in
 * the list. The terminal view never participates (click-only contract). */
function togglePaneFocus(renderer: CliRenderer): boolean {
  if (workspace.paneView === null || workspace.paneView === "term") return false;
  // Not idle-gated (r2 #67): a draft must not trap the keys in the
  // composer. Overlays, verify inputs, the Ctrl+R search, the @-mention
  // popup and the infra lists claim Tab.
  if (keyClaimed("tab")) return false;
  // Sheet layout (<100 cols): the pane covers the WHOLE screen — chat is
  // invisible, so the pane must keep the keys. Toggling out would strand a
  // blind composer.
  if (paneLayout(renderer.width) === "sheet") return false;
  actions.togglePaneFocus();
  return true;
}

/** shift+tab: interaction-mode rotation (copilot-cli parity). Research
 * space owns shift+tab for pane focus — defer instead of hijacking it. */
function rotateMode(): boolean {
  if (workspace.paneView === "research") return false;
  void rotateInteractionMode().catch((err) => {
    actions.sendSystemNotice(`mode rotation failed: ${err instanceof Error ? err.message : String(err)}`);
  });
  return true;
}

/** mouse-reporting toggle (ctrl+alt+m). Mouse ON = TUI clicks/menus work but
 * terminal owns no drag; mouse OFF = native terminal selection works but
 * TUI mouse handlers stop. Escape hatch for copy-paste workflows. */
function toggleMouse(renderer: CliRenderer): boolean {
  renderer.useMouse = !renderer.useMouse;
  actions.sendSystemNotice(
    renderer.useMouse
      ? "mouse ON — TUI click/drag active; native selection suspended"
      : "mouse OFF — native terminal selection active; TUI clicks suspended (ctrl+alt+m restores)",
  );
  return true;
}

/** Transcript cursor, selection copy, mouse, mode and pane focus. */
function transcriptHandlers(renderer: CliRenderer): Handlers {
  return {
    "transcript.cursor.enter": enterTranscriptCursor,
    "transcript.cursor.prev": consume(() => actions.moveMessageCursor(-1)),
    "transcript.cursor.next": consume(() => actions.moveMessageCursor(1)),
    "transcript.yank": () => {
      const cursor = activeTab().messageCursor;
      if (cursor === null) return false;
      void actions.copyMessageContent(cursor);
      return true;
    },
    "selection.copy": () => copySelection(renderer),
    "mode.rotate": rotateMode,
    "pane.focus.toggle": () => togglePaneFocus(renderer),
    "mouse.toggle": () => toggleMouse(renderer),
  };
}

function researchTab(tab: "strategy" | "explain" | "ops", focus: "strategy" | "explain" | "approvals"): KeyHandler {
  return inPane("research", () => {
    actions.setResearchBottomTab(tab);
    actions.setResearchFocus(focus);
    return true;
  });
}

/** Research pane verbs (pane-focused). */
function researchHandlers(): Handlers {
  return {
    "research.tab.strategy": researchTab("strategy", "strategy"),
    "research.tab.explain": researchTab("explain", "explain"),
    "research.tab.ops": researchTab("ops", "approvals"),
    "research.runDemo": whenPaneFocused(() => handleResearchRunDemo()),
    "research.cancelJob": whenPaneFocused(() => handleResearchCancelJob()),
    "research.startMenu": whenPaneFocused(() => handleResearchStartMenu()),
    "research.graph.ask": whenPaneFocused(() => handleResearchGraphShortcut("enter")),
    "research.graph.knowledge": whenPaneFocused(() => handleResearchGraphShortcut("k")),
    // Unfocused pane: Tab falls through to pane.focus.toggle (round 2) —
    // cycling here would light Live verify while the keys stay in chat.
    // Not whenPaneFocused: the verify input claims letters, yet Tab must
    // still leave verify.
    "research.focus.next": () => (workspace.paneFocus ? cycleResearchFocus(1) : false),
    "research.focus.prev": () => (workspace.paneFocus ? cycleResearchFocus(-1) : false),
    "research.verify.focus": whenPaneFocused(() => focusLiveVerify()),
    "research.nav.up": whenPaneFocused(() => handleResearchFocusNav(-1)),
    "research.nav.down": whenPaneFocused(() => handleResearchFocusNav(1)),
    "research.strategy.rank": whenPaneFocused(() => handleStrategyDemoKey("n")),
    "research.strategy.eval": whenPaneFocused(() => handleStrategyDemoKey("e")),
    "research.blackboard.write": whenPaneFocused(() => handleStrategyDemoKey("w")),
    "research.loop.start": whenPaneFocused(() => handleStrategyDemoKey("i")),
    "research.loop.stop": whenPaneFocused(() => handleStrategyDemoKey("x")),
    "research.loop.iter": whenPaneFocused(() => handleStrategyDemoKey("o")),
    "research.ops.approve": whenPaneFocused(() => handleOpsApprovalKey("y")),
    // n: ops reject (ops tab) or strategy rank (strategy tab) — handlers gate on bottom tab
    "research.letter.n": whenPaneFocused(() => handleOpsApprovalKey("n") || handleStrategyDemoKey("n")),
    "research.ops.cycleNext": whenPaneFocused(() => handleOpsApprovalKey("]")),
    "research.ops.cyclePrev": whenPaneFocused(() => handleOpsApprovalKey("[")),
    "research.context.json": whenPaneFocused(() => handleResearchContextJsonToggle()),
  };
}

/** Infrastructure, tasks and knowledge pane verbs (pane-focused). */
function infraTasksKnowledgeHandlers(): Handlers {
  return {
    "infra.stop": whenPaneFocused(() => handleInfrastructureShortcut("s")),
    "infra.cancel": whenPaneFocused(() => handleInfrastructureShortcut("c")),
    "infra.retry": whenPaneFocused(() => handleInfrastructureShortcut("r")),
    "infra.logs": whenPaneFocused(() => handleInfrastructureShortcut("l")),
    "tasks.ask": whenPaneFocused(() => handleTaskShortcut("a")),
    "tasks.research": whenPaneFocused(() => handleTaskShortcut("r")),
    "tasks.knowledge": whenPaneFocused(() => handleTaskShortcut("k")),
    "knowledge.tab": inPane("knowledge", () => {
      actions.setKnowledgePane(workspace.knowledgePane === "verify" ? "catalog" : "verify");
      return true;
    }),
    "knowledge.proposal.approve": whenPaneFocused(() => decideSelectedProposal(true)),
    "knowledge.proposal.reject": whenPaneFocused(() => decideSelectedProposal(false)),
    "knowledge.proposal.next": whenPaneFocused(() => cycleProposalSelection(1)),
    "knowledge.proposal.prev": whenPaneFocused(() => cycleProposalSelection(-1)),
    "knowledge.verify.focus": inPane("knowledge", () => {
      actions.setKnowledgePane("verify");
      return true;
    }),
  };
}

type SessionRecord = (typeof workspace.sessionsList)[number];

/** A sessions-pane verb on the cursor row; false (yield) without a row. */
function sessionVerb(run: (record: SessionRecord) => void, verb?: string): KeyHandler {
  return inPane("sessions", () => {
    const record = workspace.sessionsList[workspace.sessionsCursor];
    if (!record) return false;
    if (verb && !sessionsRowNeedsLocal(record, verb)) return true;
    run(record);
    return true;
  });
}

function sessionsBridge(): Promise<typeof import("../bridge/session-bridge")> {
  return import("../bridge/session-bridge");
}

/** P3: filter chips — cycle then refetch; filters ride the wire so remote
 * rows are never emulated client-side. */
function sessionFilter(cycle: () => string): KeyHandler {
  return inPane("sessions", () => {
    actions.sendSystemNotice(cycle());
    void sessionsBridge().then((m) => m.fetchSessions());
    return true;
  });
}

/** Wave 2026-09-21: t tears the remote runtime of a Server session down. */
function stopSession(): boolean {
  const record = workspace.sessionsList[workspace.sessionsCursor];
  if (!record) return true;
  if (record.venue !== "frontend") {
    actions.sendSystemNotice("stop applies to Server (frontend) sessions only");
    return true;
  }
  void import("../bridge/venue-bridge").then((m) => m.stopRemoteSession(record.id));
  return true;
}

/** r: seed the rename draft and hand the keys to the composer (stab-focus,
 * 2026-10-21). The FULL id: session.rename needs an exact id, an 8-char
 * prefix always failed. Pane focus is released so the typed name lands in
 * the draft — left on the pane, its letters ran verbs (d archived). */
function renameSessionDraft(record: { id: string }): void {
  actions.setPendingChatDraft(`/sessions rename ${record.id} `);
  if (workspace.paneFocus) actions.togglePaneFocus();
}

/** Wave D: sessions pane verbs (pane-focused, A4 grammar). */
function sessionsHandlers(): Handlers {
  return {
    "sessions.nav.up": inPane("sessions", consume(() => actions.moveSessionCursor(-1))),
    "sessions.nav.down": inPane("sessions", consume(() => actions.moveSessionCursor(1))),
    // Chat tabs: opening a session gives it its own tab (dedupes if open).
    "sessions.resume": sessionVerb((r) => void actions.tabOpenSession(r.id, r.name)),
    // P2: venue is chosen at session start — the picker gates Server on the
    // journal probe (disabled-with-reason, never a dead button).
    "sessions.new": inPane("sessions", consume(() => actions.openVenuePicker())),
    "sessions.filter.state": sessionFilter(() => `sessions state filter → ${actions.cycleSessionStateFilter()}`),
    "sessions.filter.venue": sessionFilter(() => `sessions venue filter → ${actions.cycleSessionVenueFilter()}`),
    "sessions.filter.archived": sessionFilter(
      () => `sessions archives ${actions.toggleSessionArchived() ? "shown" : "hidden"}`,
    ),
    "sessions.rename": sessionVerb(renameSessionDraft, "rename"),
    "sessions.pin": sessionVerb((r) => void sessionsBridge().then((m) => m.pinSession(r.id, !r.pinned)), "pin"),
    "sessions.archive": sessionVerb((r) => void sessionsBridge().then((m) => m.archiveSession(r.id)), "archive"),
    "sessions.stop": inPane("sessions", stopSession),
    "sessions.export": sessionVerb((r) => void sessionsBridge().then((m) => m.exportSession(r.id)), "export"),
  };
}

function openChangeInEditor(renderer: CliRenderer): boolean {
  const change = workspace.changeSets[workspace.changeCursor];
  if (!change) return false;
  const choice = editorForEnv(process.env as { VISUAL?: string; EDITOR?: string });
  if (!choice) {
    actions.sendSystemNotice("o in code view: $VISUAL or $EDITOR not set.");
    return true;
  }
  renderer.suspend();
  spawnSync(choice.editor, [change.path], { stdio: "inherit" });
  renderer.resume();
  actions.sendSystemNotice(`${choice.source} closed — ${change.path}`);
  return true;
}

function armRevert(): boolean {
  const change = workspace.changeSets[workspace.changeCursor];
  if (!change) return false;
  actions.armRevert(change.path);
  actions.sendSystemNotice(`revert ${change.path}: press R to confirm (destructive), Esc cancels`);
  return true;
}

function runTests(): boolean {
  const out = spawnSync("bun", ["test"], { maxBuffer: 4 * 1024 * 1024 });
  const tail = [
    ...String(out.stdout).split("\n").slice(-3),
    ...String(out.stderr).split("\n").filter(Boolean).slice(-2),
  ].join(" | ");
  actions.setTestRunSummary(tail || `(bun test exited ${out.status ?? "?"})`);
  actions.sendSystemNotice(`bun test → status ${out.status ?? "?"} (3-line tail in pane)`);
  return true;
}

/** Wave H: code view verbs (pane-focused). */
function codeviewHandlers(renderer: CliRenderer): Handlers {
  return {
    "codeview.nav.up": inPane("codeview", consume(() => actions.moveChangeCursor(-1))),
    "codeview.nav.down": inPane("codeview", consume(() => actions.moveChangeCursor(1))),
    "codeview.open.editor": inPane("codeview", () => openChangeInEditor(renderer)),
    "codeview.revert.arm": inPane("codeview", armRevert),
    "codeview.revert.exec": inPane("codeview", () => {
      if (!workspace.revertArmPath) return false;
      actions.confirmRevert(revertPath);
      refreshChangesFromGit();
      return true;
    }),
    "codeview.run.tests": inPane("codeview", runTests),
  };
}

/** space.goto.* (no default bindings) and the Activity tray digits. */
function spaceAndActivityHandlers(): Handlers {
  const handlers: Handlers = {};
  for (const space of SPACE_SHORTCUTS) {
    handlers[`space.goto.${space}`] = consume(() => actions.setSpace(space));
  }
  for (const tab of ACTIVITY_TABS) {
    // Round 2: the digits act on the VISIBLE tray (the /logs pane) once Tab
    // moved focus into it; an unfocused docked pane leaves digits to the
    // composer as typed text.
    handlers[`activity.tab.${tab}`] = whenPaneFocused(() => (activityTrayVisible() ? gotoActivityTab(tab) : false));
  }
  return handlers;
}

/** Every keymap command, by name. */
function buildHandlers(renderer: CliRenderer): Handlers {
  return {
    ...chromeHandlers(),
    ...overlayVerbHandlers(),
    ...turnHandlers(),
    ...transcriptHandlers(renderer),
    ...researchHandlers(),
    ...infraTasksKnowledgeHandlers(),
    ...sessionsHandlers(),
    ...codeviewHandlers(renderer),
    ...spaceAndActivityHandlers(),
  };
}

/** Letter / single-key shortcuts that must not match while the user is typing. */
const idleLetter = { idle: true as const };

function chromeBindings(): R4Binding[] {
  return [
    { key: "ctrl+p", cmd: "palette.open" },
    // OpenTUI names `/` as "slash", `.` as "period". Ctrl+/ often never reaches WSL terminals.
    { key: "ctrl+slash", cmd: "slash.open" },
    { key: "ctrl+/", cmd: "slash.open" },
    { key: "ctrl+period", cmd: "slash.open" },
    { key: "ctrl+.", cmd: "slash.open" },
    // Ctrl+H often arrives as backspace (\x08) in terminals — use F12 / Ctrl+Shift+H
    { key: "f12", cmd: "help.toggle" },
    { key: "ctrl+shift+h", cmd: "help.toggle" },
    // Do not bind bare "?" — it steals the character from chat/inputs.
    { key: "escape", cmd: "overlay.close" },
    { key: "ctrl+q", cmd: "app.quit" },
    // Claude-Code parity keys (not idle-gated unless noted).
    { key: "ctrl+c", cmd: "input.interrupt" },
    { key: "ctrl+d", cmd: "input.eof", idle: true },
    { key: "ctrl+o", cmd: "transcript.verbose.toggle" },
    { key: "ctrl+t", cmd: "todos.toggle" },
    { key: "alt+t", cmd: "thinking.toggle" },
    { key: "ctrl+l", cmd: "screen.redraw" },
    { key: "ctrl+b", cmd: "turn.background" },
    // Chat tabs (opencode mod-t/w/shift-t parity, terminal-deliverable chords).
    { key: "ctrl+n", cmd: "tab.new" },
    { key: "ctrl+alt+w", cmd: "tab.close" },
    { key: "ctrl+shift+t", cmd: "tab.reopenClosed" },
    { key: "alt+right", cmd: "tab.next" },
    { key: "alt+left", cmd: "tab.prev" },
    // Free chord (2026-10-24): ctrl+alt+w is tab.close; the composer and the
    // OpenTUI textarea bind no meta+w (ctrl+w is delete-word-backward).
    { key: "alt+w", cmd: "workflow.graph" },
    // Bug fix: the Activity tray toggle keys were defined but never bound.
    ...ACTIVITY_TOGGLE_KEYS.map((key) => ({ key, cmd: "activity-console.toggle" })),
  ];
}

/** Detail-overlay and approval verbs: match only while their owner claims
 * them (`claim`), replacing the 2026-10-17 setData guard fields. */
function overlayBindings(): R4Binding[] {
  const team = { claim: "team-overlay" as const, idle: true };
  const exec = { claim: "exec-overlay" as const, idle: true };
  const approval = { claim: "approval" as const };
  return [
    // Arrows/letters only without a typed draft (idle-gated); a typed
    // draft keeps Enter for the composer's submit (round 2).
    { key: "down", cmd: "team-overlay.down", ...team },
    { key: "j", cmd: "team-overlay.down", ...team },
    { key: "up", cmd: "team-overlay.up", ...team },
    { key: "k", cmd: "team-overlay.up", ...team },
    { key: "return", cmd: "team-overlay.toggle", ...team },
    { key: "enter", cmd: "team-overlay.toggle", ...team },
    { key: "r", cmd: "team-overlay.run", ...team },
    { key: "p", cmd: "team-overlay.pause", ...team },
    // Shifted letters MUST be spelled shift+<lower>: the keymap parser
    // lowercases a bare "R" into plain r (round 2 — R/O/T were dead).
    { key: "shift+r", cmd: "team-overlay.resume", ...team },
    { key: "c", cmd: "team-overlay.cancel", ...team },
    { key: "l", cmd: "team-overlay.logs", ...team },
    { key: "o", cmd: "team-overlay.report", ...team },
    { key: "shift+o", cmd: "team-overlay.open-agent", ...team },
    { key: "m", cmd: "team-overlay.tailor", ...team },
    // Workflow-execution overlay verbs.
    { key: "shift+t", cmd: "exec-overlay.traces", ...exec },
    { key: "x", cmd: "exec-overlay.cancel", ...exec },
    { key: "y", cmd: "approval.yes", ...approval },
    { key: "a", cmd: "approval.always", ...approval },
    { key: "n", cmd: "approval.no", ...approval },
    { key: "return", cmd: "approval.yes", ...approval },
    { key: "enter", cmd: "approval.yes", ...approval },
  ];
}

function researchBindings(): R4Binding[] {
  const research = { space: "research", ...idleLetter };
  return [
    // Letter shortcuts: space + idle so they never steal keys from verify/chat
    { key: "r", cmd: "research.runDemo", ...research },
    { key: "c", cmd: "research.cancelJob", ...research },
    { key: "a", cmd: "research.startMenu", ...research },
    { key: "return", cmd: "research.graph.ask", ...research },
    { key: "enter", cmd: "research.graph.ask", ...research },
    { key: "k", cmd: "research.graph.knowledge", ...research },
    // Tab may leave verify (cycleResearchFocus handles that) — not idle-gated
    { key: "tab", cmd: "research.focus.next", space: "research" },
    { key: "shift+tab", cmd: "research.focus.prev", space: "research" },
    { key: "v", cmd: "research.verify.focus", ...research },
    { key: "up", cmd: "research.nav.up", ...research },
    { key: "down", cmd: "research.nav.down", ...research },
    { key: "e", cmd: "research.strategy.eval", ...research },
    { key: "w", cmd: "research.blackboard.write", ...research },
    { key: "i", cmd: "research.loop.start", ...research },
    { key: "x", cmd: "research.loop.stop", ...research },
    { key: "o", cmd: "research.loop.iter", ...research },
    { key: "y", cmd: "research.ops.approve", ...research },
    { key: "n", cmd: "research.letter.n", ...research },
    { key: "]", cmd: "research.ops.cycleNext", ...research },
    { key: "[", cmd: "research.ops.cyclePrev", ...research },
    { key: "j", cmd: "research.context.json", ...research },
  ];
}

function paneBindings(): R4Binding[] {
  const infra = { space: "infrastructure", ...idleLetter };
  const tasks = { space: "tasks", ...idleLetter };
  const knowledge = { space: "knowledge", ...idleLetter };
  const sessions = { space: "sessions", ...idleLetter };
  return [
    { key: "s", cmd: "infra.stop", ...infra },
    { key: "c", cmd: "infra.cancel", ...infra },
    { key: "r", cmd: "infra.retry", ...infra },
    { key: "l", cmd: "infra.logs", ...infra },
    { key: "a", cmd: "tasks.ask", ...tasks },
    { key: "r", cmd: "tasks.research", ...tasks },
    { key: "k", cmd: "tasks.knowledge", ...tasks },
    { key: "tab", cmd: "knowledge.tab", space: "knowledge" },
    { key: "v", cmd: "knowledge.verify.focus", ...knowledge },
    { key: "a", cmd: "knowledge.proposal.approve", ...knowledge },
    { key: "r", cmd: "knowledge.proposal.reject", ...knowledge },
    { key: "]", cmd: "knowledge.proposal.next", ...knowledge },
    { key: "[", cmd: "knowledge.proposal.prev", ...knowledge },
    // Wave D: sessions pane verbs (pane-focused grammar, same as research)
    { key: "up", cmd: "sessions.nav.up", ...sessions },
    { key: "down", cmd: "sessions.nav.down", ...sessions },
    { key: "enter", cmd: "sessions.resume", ...sessions },
    { key: "return", cmd: "sessions.resume", ...sessions },
    { key: "n", cmd: "sessions.new", ...sessions },
    { key: "r", cmd: "sessions.rename", ...sessions },
    { key: "p", cmd: "sessions.pin", ...sessions },
    { key: "d", cmd: "sessions.archive", ...sessions },
    { key: "x", cmd: "sessions.export", ...sessions },
    // Wave 2026-09-21: t tears the remote runtime of a Server session down.
    { key: "t", cmd: "sessions.stop", ...sessions },
    // P3: filter chips — s cycles state, v cycles venue, A toggles archives
    { key: "s", cmd: "sessions.filter.state", ...sessions },
    { key: "v", cmd: "sessions.filter.venue", ...sessions },
    { key: "shift+a", cmd: "sessions.filter.archived", ...sessions },
  ];
}

function transcriptAndCodeviewBindings(): R4Binding[] {
  const codeview = { space: "codeview", ...idleLetter };
  return [
    // Transcript copy (G): single modifier key, no chord prefix (the old
    // g,y/g,c/g,m comma chords swallowed a leading 'g' when typed with an
    // empty draft — "cannot type g" bug, 2026-09-15). ctrl+g = grab
    // transcript; ctrl+y = yank the active mouse-drag selection;
    // ctrl+alt+m = mouse-reporting toggle (native selection fallback).
    { key: "ctrl+g", cmd: "transcript.cursor.enter", idle: true },
    { key: "ctrl+y", cmd: "selection.copy", idle: true },
    { key: "ctrl+alt+m", cmd: "mouse.toggle", idle: true },
    // shift+tab cycles chat → plan → autopilot (idle so pane typing is
    // unaffected; research space keeps its own focus.prev binding above).
    { key: "shift+tab", cmd: "mode.rotate", idle: true },
    // Wave H: code view verbs (pane-focused)
    { key: "up", cmd: "codeview.nav.up", ...codeview },
    { key: "down", cmd: "codeview.nav.down", ...codeview },
    { key: "o", cmd: "codeview.open.editor", ...codeview },
    { key: "r", cmd: "codeview.revert.arm", ...codeview },
    { key: "shift+r", cmd: "codeview.revert.exec", ...codeview },
    { key: "t", cmd: "codeview.run.tests", ...codeview },
  ];
}

function tabBindings(): R4Binding[] {
  return [
    // Modifier-only: comma chords (g,N) retired — prefix keys swallowed typing.
    ...bindingEntries(["alt+shift+1", "ctrl+alt+shift+1", "alt+1"], "research.tab.ops", idleLetter),
    ...bindingEntries(["alt+shift+2", "ctrl+alt+shift+2", "alt+2"], "research.tab.strategy", idleLetter),
    ...bindingEntries(["alt+shift+3", "ctrl+alt+shift+3", "alt+3"], "research.tab.explain", idleLetter),
    ...bindingEntries([...ACTIVITY_TAB_CYCLE_KEYS.prev], "activity.tab.prev"),
    ...bindingEntries([...ACTIVITY_TAB_CYCLE_KEYS.next], "activity.tab.next"),
    // Digits only while the tray is open (and not typing).
    ...ACTIVITY_TABS.flatMap((tab, idx) =>
      bindingEntries([ACTIVITY_TAB_DIGIT_KEYS[idx]!], `activity.tab.${tab}`, idleLetter),
    ),
  ];
}

/**
 * The default binding table, in dispatch order. S2 keymap prune: no default
 * space bindings — the command surface (palette or `/research` etc.) is the
 * canonical navigation; power users re-add F-keys/chords via
 * ~/.config/r4/v3-keymap.json (D1 additive overrides).
 */
export function defaultBindings(): R4Binding[] {
  return [
    ...chromeBindings(),
    ...overlayBindings(),
    ...researchBindings(),
    ...paneBindings(),
    ...transcriptAndCodeviewBindings(),
    ...tabBindings(),
    // LAST: chat ↔ pane focus toggle. Registration order decides conflicts:
    // research.focus.next / knowledge.tab (earlier) keep Tab in their
    // space; every other pane view falls through to this. The handler
    // yields Tab to its claimants; a plain draft no longer keeps the keys
    // in the composer (r2 #67).
    { key: "tab", cmd: "pane.focus.toggle" },
  ];
}

/** Claim keys the bindings gate on (`claim` field) — fed by updateKeymapClaims. */
let bindingClaimKeys: ClaimKey[] = [];

export function createR4Keymap(renderer: CliRenderer): R4Keymap {
  const keymap = createDefaultOpenTuiKeymap(renderer);
  // No registerCommaBindings: comma chords died with the prefix-swallow bug.
  wireSelectionCopy(renderer);
  registerFields(keymap);

  const handlers = buildHandlers(renderer);
  const commands = Object.entries(handlers).map(([name, run]) => ({ name, run }));
  const bindings = mergeKeymapOverrides(
    defaultBindings(),
    loadKeymapOverrides(),
    new Set(commands.map((c) => c.name)),
  );

  keymap.registerLayer({ name: "global", commands, bindings });

  // D1: expose registered bindings for the which-key overlay; H-6: the
  // command table for the binding lint tests.
  registeredBindings = bindings;
  registeredHandlers = handlers;
  bindingClaimKeys = [...new Set(bindings.flatMap((b) => (b.claim ? [b.claim] : [])))];

  // Default: idle (letter shortcuts active) until app syncs from letterShortcutsBlocked.
  keymap.setData("app.idle", true);
  updateKeymapClaims(keymap);

  return keymap;
}

export function updateKeymapSpace(keymap: R4Keymap, space: string) {
  keymap.setData("app.space", space);
}

/** D1: current which-key/cheatsheet source (rebuilt on each layer registration). */
let registeredBindings: R4Binding[] = [];
let registeredHandlers: Handlers = {};

export function getRegisteredBindings(): R4Binding[] {
  return registeredBindings;
}

/** H-6 lint seam: the live command table (name → handler). */
export function getRegisteredHandlers(): Readonly<Handlers> {
  return registeredHandlers;
}

/** Keep letter/chord bindings out of the match set while the user is typing. */
export function updateKeymapIdle(keymap: R4Keymap, idle: boolean) {
  keymap.setData("app.idle", idle);
}

/** Mirror the claims the bindings gate on into keymap data (`claim.<key>`).
 * Reactive: call it from an effect; it reads the claims registry. */
export function updateKeymapClaims(keymap: R4Keymap) {
  for (const key of bindingClaimKeys) keymap.setData(`claim.${key}`, keyClaimed(key));
}
