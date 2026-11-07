/**
 * Chat panel — streaming replies, model attribution, interrupt, and history.
 */

import { For, Show, createEffect, createMemo, createSignal, on, onCleanup, onMount, untrack } from "solid-js";
import { workspace, actions, setScopedField, rowExpanded, activeTab } from "../stores";
import { getCompanionBridge } from "../bridge";
import { rpc } from "../bridge/rpc";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { formatCostChip, formatToolSchemaSuffix, formatTtftSuffix } from "../lib/cost-chip";
import { chipifyFileDisplayText } from "../lib/file-chip";
import { collapseChipsOnBackspace } from "../lib/mention-chips";
import { diffHeader, isDiffShaped } from "../lib/diff-highlight";
import {
  hasRenderableDiff,
  modifiedFilesFromToolCalls,
  type ModifiedFile,
} from "../lib/modified-files";
import { DiffBlock } from "../ui/DiffBlock";
import { ParallelDiffBlock, preferSideBySide } from "../ui/ParallelDiffBlock";
import { useTerminalDims } from "../lib/terminal-dims";
import { chatColumnWidth } from "../lib/term-width";
import { paneBounds } from "../lib/pane-measure";
import { thoughtBody, thoughtHeader } from "../lib/thought-widget";
import { PLACEHOLDER_BUCKET_MS, rotatingPlaceholder } from "../lib/placeholder-rotation";
import { type KeyEvent, type ScrollBoxRenderable } from "@opentui/core";
import type { ChatMessage } from "../types";
import { dispatchToolApprovalKey } from "../bridge/approval-bridge";
import { shouldDivertSlashToPalette } from "../lib/chat-input";
import { resolveWorkspace } from "../lib/resolve-python";
import { CodeFenceBlock, MarkdownBlock } from "../ui/MarkdownBlock";
import { localTimeHHMMSS } from "../lib/time";
import { tokenizeLine, langForPath, type CodeTokenKind } from "../lib/code-highlight";
import { classifyDiffLines } from "../lib/diff-highlight";
import { MgmtCardView } from "./mgmt-card";
import { Panel } from "../ui/Panel";
import { Floating } from "../ui/Floating";
import { ErrorCardView } from "./error-card-view";
import { PlanCard } from "./plan-card";
import { GreetingPanel } from "./greeting";
import { planVerbToAction, respondPlan } from "../bridge/plan-bridge";
import {
  isAtBottom,
  newContentCount,
  newContentPillLabel,
} from "../lib/scroll-follow";
import {
  extractMentionQuery,
  rankMentions,
  type MentionCandidate,
} from "../lib/file-mentions";
import { ensureWorkspaceIndex, workspaceFiles } from "../lib/mention-scan";
import {
  maskShellResult,
  maskShellText,
  runLocalShell,
  shellDisplayCommand,
  shellEcho,
} from "../lib/local-shell";
import { appendPromptHistory, promptHistory } from "../lib/prompt-history";
import { isDoubleEsc, reverseSearch, stashToggle } from "../lib/tui-parity";
import { escInterruptHint, escStopKeys, escSwallowed, escTapStops } from "../lib/esc-interrupt";
import { newlineHint, useKittyKeys } from "../lib/kitty-keys";
import { registerRedraw } from "../lib/redraw";
import { caretIndex, commonPrefixLength, createPasteTracker, PASTE_CHIP_MIN_CHARS } from "../lib/paste-chips";
import { exceedsRowCap } from "../lib/render-window";
import { approvalTight } from "../lib/approval-fit";
import { pastedText } from "./key-trap";
import type { PasteEvent } from "@opentui/core";
import {
  editorForEnv,
  openInExternalEditor,
} from "../lib/external-editor";
import { useRenderer } from "@opentui/solid";

function roleColor(role: string) {
  switch (role) {
    case "user":
      return palette.accent;
    case "assistant":
      return palette.knowledge;
    default:
      return palette.fgDim;
  }
}

function roleLabel(role: string) {
  switch (role) {
    case "user":
      return "You";
    case "assistant":
      return "R4";
    default:
      return "•";
  }
}

function attribution(msg: ChatMessage): string {
  if (msg.role !== "assistant") return "";
  const parts: string[] = [];
  // E: model-name chip gets its own accent slot so tool rows decode the engine.
  if (msg.model && msg.model !== "offline") parts.push(`@${msg.model}`);
  if (msg.fallback) {
    if (msg.content.includes("LLM/gateway network failed") || /handshake|_ssl/i.test(msg.content)) {
      parts.push("gateway down");
    } else if (msg.content.startsWith("[Chat unavailable]")) {
      parts.push("chat down");
    } else {
      parts.push("offline fallback");
    }
  }
  if (msg.streaming) parts.push("streaming…");
  return parts.join(" · ");
}

function citationPreview(cite: { artifactId: string; kind: string; title: string; contentPreview: string }) {
  const shortId = cite.artifactId.slice(0, 8);
  return `  [${cite.kind}/${shortId}] ${cite.title || cite.artifactId}`;
}

// chatInputFocused lives in lib/chat-input-focus (pure; shared with pins).
import { chatInputFocused } from "../lib/chat-input-focus";
import { claimKeys, keyClaimed } from "../keymap/key-claims";
import { citationBlockLabel, citationChip, openCitationPreview } from "../lib/citation-preview";
import { saveMessageAt, turnOfMessage } from "../commands/save-command";

/** A5: user bubbles show file-kind chips ([Image N]/[PDF N]/[SVG N]) while
 *  the model payload stays verbatim; system/assistant content untouched. */
function displayContentFor(msg: ChatMessage): string {
  if (msg.role !== "user") return msg.content;
  return chipifyFileDisplayText(msg.content);
}

function chatPlaceholder(draftShellMode: boolean, nowMs: number): string {
  if (draftShellMode) return "Shell command… (Esc exits `!` mode)";
  // D5a: hint rotation while idle.
  if (!activeTab().chatStreaming) return rotatingPlaceholder(nowMs);
  if (activeTab().askUser) return "Answer the question overlay · Esc cancels";
  // Live status lives ONLY in the bottom status bar (single indicator) — the
  // placeholder keeps just the Esc affordance, not a duplicated status text.
  // 2026-11-03: Esc stops a turn on a repeated tap (lib/esc-interrupt);
  // the first tap arms and the placeholder says so.
  const armed = escInterruptHint();
  if (armed) return `${armed} · the turn keeps running otherwise`;
  if (activeTab().chatTurnStatus) return `Working… ${escStopKeys()} to stop`;
  return `Streaming… press ${escStopKeys()} to stop`;
}

import { ToolApprovalOverlay } from "./tool-approval-overlay";
import { AskUserOverlay } from "./ask-user-overlay";
import { HighlightText } from "../ui/HighlightText";
import {
  buildToolDetailRows,
  formatDurationMs,
  toolOutputLines,
  OUTPUT_PREVIEW_LINES,
  type ActivityLine,
} from "../lib/tool-activity";

const TOOL_CODE_TOKEN_FG: Record<CodeTokenKind, string> = {
  keyword: palette.mdHeading,
  string: palette.statusOk,
  comment: palette.fgDim,
  number: palette.knowledge,
  default: palette.mdCode,
};

import { toolFamilyFgKey } from "../lib/tool-glyph";

/** Columns between the chat column edge and a cell's text: panel bar (1)
 * + panel padding (2) + scrollbar (1) + cell spine (1) + cell padding (1).
 * Visibility wave 2026-10-19 measured it; stabilization 2026-10-21 made it
 * the ONE source for every width inside a cell (prose wrap, tool rows,
 * diffs) and dropped the 40-column floor — a floor wider than the real
 * column made each wrapped row overlong and the engine dropped its words. */
const CELL_TEXT_CHROME = 6;

/** Text column inside a chat cell for a chat-column width. */
function cellTextWidth(chatCol: number): number {
  return Math.max(1, chatCol - CELL_TEXT_CHROME);
}

/** Wave-A: full-row severity coloring per row status (was label-only).
 * error/warn statuses keep their full-row hue through expansion; honest
 * healthy rows fall back to tool-category fg (decode by color alone). */
function toolRowLabelFg(status: ActivityLine["status"] | undefined, name?: string): string {
  if (status === "error") return palette.statusErr;
  if (status === "warn") return palette.statusWarn;
  if (name) {
    const key = toolFamilyFgKey(name);
    if (key) {
      const anyPalette = palette as unknown as Record<string, string | undefined>;
      if (anyPalette[key]) return anyPalette[key]!;
    }
  }
  return palette.fg;
}

/** Tool-activity code line (one per token span) with per-token coloring.
 * Tokens are flexShrink=0 + unwrapped: an overwide row clips at the right
 * edge instead of Yoga shrinking every token (stabilization 2026-10-21). */
function ToolCodeLine(props: { text: string; lang: string }) {
  return (
    <box height={1} width="100%" flexDirection="row" overflow="hidden">
      <For each={tokenizeLine(props.text, props.lang)}>
        {(token) => (
          <text fg={TOOL_CODE_TOKEN_FG[token.kind]} flexShrink={0} wrapMode="none">
            {token.text}
          </text>
        )}
      </For>
    </box>
  );
}

/** Per-line diff fg for tool-activity output rows (T5 topic-13 tail).
 * Colors a single row only when the parent block already carries hunk/meta
 * structure (never colors arbitrary +/- shell-bullets in prose). */
function diffLineFgForKind(kind: string | null): string | null {
  if (kind === "add") return palette.diffAdd;
  if (kind === "del") return palette.diffDel;
  if (kind === "hunk") return palette.diffHunk;
  if (kind === "meta") return palette.diffMeta;
  return null;
}

/** Whole-block detection: diff-shape classifier requires at least one add AND
 * del AND hunk-or-meta marker — mirrors `isDiffShaped` exactly. */
export function blockIsDiffLike(text: string): boolean {
  const lines = classifyDiffLines(text);
  let hasAdd = false;
  let hasDel = false;
  let hasStructure = false;
  for (const line of lines) {
    if (line.kind === "add") hasAdd = true;
    else if (line.kind === "del") hasDel = true;
    else if (line.kind === "hunk" || line.kind === "meta") hasStructure = true;
  }
  return hasAdd && hasDel && hasStructure;
}

/** Language detection for one activity-row's code context (best-effort —
 * args.path for write-family tools, args.language as a verbatim override). */
function toolCodeLang(line: ActivityLine): string {
  const explicit = typeof line.args?.language === "string" ? line.args.language : "";
  if (explicit) return explicit.toLowerCase();
  const path = typeof line.args?.path === "string" ? line.args.path : "";
  return langForPath(path) ?? "js";
}

/** Diff-shape detection inside a tool-activity output row (T5 topic-12): if
 * the row's own text parses as a diff (---/+++ headers etc), prefer the
 * pre-existing DiffBlock-based colorizer. */
function rowIsDiffShaped(text: string): boolean {
  // treat as diff only when it carries both the marker header pair; never
  // a one-line `+`/`−` accidental prose appearance.
  return /^(---|\+\+\+) /.test(text);
}

/**
 * One tool call inside a message's work group.
 * Row shows the call label + duration; click expands full command + output
 * preview; when output overflows the preview, a nested row expands the rest.
 */
function ToolCallRow(props: { msgId: string; index: number; line: ActivityLine }) {
  const dims = useTerminalDims();
  /** Live chat column — caps track the real width (resize + pane dock). */
  const col = () =>
    chatColumnWidth(dims().width, workspace.paneView !== null, workspace.paneWidthOverride, paneBounds());
  // Inside the cell text column: label row paddingLeft 1, detail rows 2.
  const labelCap = () => cellTextWidth(col()) - 1;
  const detailCap = () => cellTextWidth(col()) - 2;
  const key = () => `tc:${props.msgId}:${props.index}`;
  const fullKey = () => `${key()}:full`;
  const expanded = () => rowExpanded(key());
  const fullExpanded = () => rowExpanded(fullKey());
  const dur = () => formatDurationMs(props.line.durationMs);
  const previewRows = () => buildToolDetailRows(props.line, OUTPUT_PREVIEW_LINES);
  // Full-output mode: EVERY retained output line renders (no source-side
  // line/char cap); each line re-segments through live chat-width chunks so
  // narrow panes still survive while long commands/results never disappear.
  const fullRows = () => buildToolDetailRows(props.line, Number.MAX_SAFE_INTEGER);
  const bodyTruncated = () => previewRows().some((r) => r.kind === "marker");
  // T5 topic-13 tail: diff-shape classifier at BLOCK level — memoized once
  // per fullRows evaluation; per-row tint fires only when the whole block
  // already carries hunk/meta/exists markers (never a prose accident).
  const blockIsDiff = createMemo(() => {
    const rows = previewRows();
    const texts = rows.map((r) => r.text);
    return blockIsDiffLike(texts.join("\n"));
  });

  /** Per-row render entries split by shape (CCN-friendly; generated rows
   * visible as plain-text JSX hoisted into the renderer). */
  const renderDiffRow = (text: string, kind: string, cap: number | "none") => {
    if (!blockIsDiff()) return null;
    const lineKind = classifyDiffLines(text)[0]?.kind ?? null;
    const fg = diffLineFgForKind(lineKind);
    if (!fg) return null;
    return (
      <box height={1} width="100%" overflow="hidden" paddingLeft={cap === "none" ? 3 : 2}>
        <text fg={fg}>{text || " "}</text>
      </box>
    );
  };

  const renderPlainRow = (text: string, cap: number | "none") => (
    <box height={1} width="100%" overflow="hidden" paddingLeft={cap === "none" ? 3 : 2}>
      <HighlightText
        text={text}
        baseFg={props.line.status === "error" ? palette.statusErr : palette.fgDim}
      />
    </box>
  );

  const renderMarkerRow = (text: string) => (
    <box height={1} width="100%" overflow="hidden" paddingLeft={2}>
      <text fg={palette.accent}>{text}</text>
    </box>
  );

  const renderDetailRow = (row: { kind: string; text: string }, cap: number | "none") => {
    if (row.kind === "code") {
      return <ToolCodeLine text={row.text} lang={toolCodeLang(props.line)} />;
    }
    if (row.kind === "marker") return renderMarkerRow(truncate(row.text, cap === "none" ? 400 : cap));
    const text = cap === "none" ? row.text : truncate(row.text, cap);
    return renderDiffRow(text, row.kind, cap) ?? renderPlainRow(text, cap);
  };
  return (
    <box flexDirection="column" width="100%" overflow="hidden">
      <box height={1} width="100%" overflow="hidden" paddingLeft={1} onMouseDown={() => actions.toggleToolCallExpanded(key())}>
        <text fg={toolRowLabelFg(props.line.status, props.line.name)}>
          {truncate(`${expanded() ? "▾" : "▸"} ${props.line.text}${dur() ? ` · ${dur()}` : ""}`, labelCap())}
        </text>
      </box>
      <Show when={expanded()}>
        {/* Full-output mode REPLACES the preview (the additive render printed
            every line twice — read as "collapse does nothing"). */}
        <Show
          when={!fullExpanded()}
          fallback={
            <>
              <box height={1} width="100%" overflow="hidden" paddingLeft={2} onMouseDown={() => actions.toggleToolCallExpanded(fullKey())}>
                <text fg={palette.accent}>▾ collapse full output</text>
              </box>
              <For each={fullRows()}>
                {(row) => renderDetailRow(row, "none")}
              </For>
            </>
          }
        >
          <For each={previewRows()}>
            {(row) => renderDetailRow(row, detailCap())}
          </For>
          <Show when={bodyTruncated()}>
            <box height={1} width="100%" overflow="hidden" paddingLeft={2} onMouseDown={() => actions.toggleToolCallExpanded(fullKey())}>
              <text fg={palette.accent}>↵/click — full output</text>
            </box>
          </Show>
        </Show>
      </Show>
    </box>
  );
}

/** Chronological work group pinned to the top of an assistant cell. */
function ToolCallsGroup(props: { msgId: string; calls: ActivityLine[] }) {
  const groupKey = () => `group:${props.msgId}`;
  const expanded = () => rowExpanded(groupKey());
  return (
    // marginTop: one blank row of air so the tool block reads as its own
    // section under the reply text (visibility wave 2026-10-19).
    <box flexDirection="column" width="100%" overflow="hidden" marginTop={1}>
      <box height={1} width="100%" overflow="hidden" onMouseDown={() => actions.toggleToolCallExpanded(groupKey())}>
        <text fg={palette.fgDim}>
          {`⚙ ${props.calls.length} agent action${props.calls.length === 1 ? "" : "s"} — ${expanded() ? "▾ collapse" : "↵/click expand"}${workspace.verboseTranscript ? " (verbose: everything starts expanded)" : ""}`}
        </text>
      </box>
      <Show when={expanded()}>
        <For each={props.calls}>
          {(line, i) => <ToolCallRow msgId={props.msgId} index={i()} line={line} />}
        </For>
      </Show>
    </box>
  );
}

/** Modified-code preview caps: keep one turn's edits from flooding the
 * transcript while staying visible by default. */
const MODFILE_PREVIEW_LINES = 30;

/** One file's inline diff, width-aware (unified below 110, side-by-side at/above). */
function ModifiedFileDiffView(props: { msgId: string; file: ModifiedFile; width: number }) {
  const diff = () => props.file.diffText!;
  const moreKey = () => `moddiff-more:${props.msgId}:${props.file.path}`;
  const showAll = () => rowExpanded(moreKey());
  const lines = createMemo(() => diff().split("\n"));
  const shown = () => (showAll() ? lines() : lines().slice(0, MODFILE_PREVIEW_LINES));
  const hiddenCount = () => Math.max(0, lines().length - MODFILE_PREVIEW_LINES);
  // props.width is the cell text column; the diff sits under paddingLeft 3.
  const inner = () => Math.max(1, props.width - 3);
  return (
    <box paddingLeft={3} flexDirection="column" width="100%" overflow="hidden">
      <Show
        when={preferSideBySide(inner())}
        fallback={<DiffBlock content={shown().join("\n")} width={inner()} filePath={props.file.path} />}
      >
        <ParallelDiffBlock content={shown().join("\n")} width={inner()} filePath={props.file.path} />
      </Show>
      <Show when={hiddenCount() > 0}>
        <box
          height={1}
          width="100%"
          overflow="hidden"
          onMouseDown={() => actions.toggleRowExpanded(moreKey())}
        >
          <text fg={palette.accent}>
            {showAll() ? "▾ collapse full diff" : `↵/click — ${hiddenCount()} more diff lines`}
          </text>
        </box>
      </Show>
    </box>
  );
}

/** Styled full-code view for file_write content (lang from the file path). */
function ModifiedFileCodeView(props: { msgId: string; file: ModifiedFile }) {
  const moreKey = () => `modcode-more:${props.msgId}:${props.file.path}`;
  const showAll = () => rowExpanded(moreKey());
  const lines = createMemo(() => (props.file.content ?? "").replace(/\n$/, "").split("\n"));
  const shown = () => (showAll() ? lines() : lines().slice(0, MODFILE_PREVIEW_LINES));
  const hiddenCount = () => Math.max(0, lines().length - MODFILE_PREVIEW_LINES);
  return (
    <box paddingLeft={3} flexDirection="column" width="100%" overflow="hidden">
      <CodeFenceBlock lang={langForPath(props.file.path)} lines={shown()} />
      <Show when={hiddenCount() > 0}>
        <box
          height={1}
          width="100%"
          overflow="hidden"
          onMouseDown={() => actions.toggleRowExpanded(moreKey())}
        >
          <text fg={palette.accent}>
            {showAll() ? "▾ collapse full content" : `↵/click — ${hiddenCount()} more lines`}
          </text>
        </box>
      </Show>
    </box>
  );
}

/** One modified file row: path header + ALWAYS-VISIBLE styled view —
 * diff when the result embeds one, else the styled write content, else
 * the one-line summary. Clicking the path collapses (opt-out). */
function ModifiedFileRow(props: { msgId: string; file: ModifiedFile; width: number }) {
  const key = () => `modfile-collapse:${props.msgId}:${props.file.path}`;
  const expanded = () => rowExpanded(key(), true);
  const hasDiff = () => hasRenderableDiff(props.file);
  const header = createMemo(() => (hasDiff() ? diffHeader(props.file.diffText!) : null));
  const headerCounts = () => {
    const h = header();
    return h ? ` +${h.added} −${h.removed}` : "";
  };
  return (
    <box flexDirection="column" width="100%" overflow="hidden">
      <box
        height={1}
        width="100%"
        overflow="hidden"
        paddingLeft={2}
        onMouseDown={() => actions.toggleRowExpanded(key(), true)}
      >
        <text fg={palette.accent}>
          {`${expanded() ? "▾" : "▸"} ✎~ ${props.file.path}${headerCounts()}`}
        </text>
      </box>
      <Show when={expanded()}>
        <Show
          when={hasDiff()}
          fallback={
            <Show
              when={props.file.content}
              fallback={
                <box height={1} width="100%" overflow="hidden" paddingLeft={3}>
                  <text fg={palette.fgDim}>{props.file.summary || "modified"}</text>
                </box>
              }
            >
              <ModifiedFileCodeView msgId={props.msgId} file={props.file} />
            </Show>
          }
        >
          <ModifiedFileDiffView msgId={props.msgId} file={props.file} width={props.width} />
        </Show>
      </Show>
    </box>
  );
}

/**
 * Per-message "files modified" card (visible code-change indication).
 * Expanded by default: the turn's edits and their diffs are always on
 * screen without an expand click; each file row collapses on click.
 */
export function ModifiedFilesCard(props: { msgId: string; files: ModifiedFile[]; width: number }) {
  const groupKey = () => `modfiles-collapse:${props.msgId}`;
  const expanded = () => rowExpanded(groupKey(), true);
  return (
    // marginTop: one blank row of air between the reply text and the edits
    // card (visibility wave 2026-10-19).
    <box flexDirection="column" width="100%" overflow="hidden" marginTop={1}>
      <box
        height={1}
        width="100%"
        overflow="hidden"
        onMouseDown={() => actions.toggleRowExpanded(groupKey(), true)}
      >
        <text fg={palette.fgDim}>
          {`✎ ${props.files.length} file${props.files.length === 1 ? "" : "s"} modified — ${expanded() ? "▾ click collapse" : "▸ click expand"}`}
        </text>
      </box>
      <Show when={expanded()}>
        <For each={props.files}>
          {(file) => <ModifiedFileRow msgId={props.msgId} file={file} width={props.width} />}
        </For>
      </Show>
    </box>
  );
}

/** A finished diff-shaped message paints as a colored diff. Past
 * RENDER_ROW_CAP lines it takes the windowed markdown path instead: the
 * diff views render every line (c3-tui D-1). */
function rendersAsDiff(msg: ChatMessage): boolean {
  return !msg.streaming && !exceedsRowCap(msg.content) && isDiffShaped(msg.content);
}

/** Renderable id of a transcript cell — the transcript cursor scrolls it
 * into view (c2-tui 2026-10-21). */
function messageCellId(messageId: string): string {
  return `chat-msg-${messageId}`;
}

/** One chat transcript cell (extracted from ChatPanel — keeps the panel under
 * the lizard line; the footer verb grammar stays one visible row (B3)). */
/** B3 focused-row footer — one interaction grammar (verbs). K1.6 review
 * (2026-10-24): `s save tN` names the key and the turn number /save takes. */
function focusedRowFooter(msg: ChatMessage, turn: string | undefined): string {
  const save = turn && (msg.role === "user" || msg.role === "assistant") ? `s save ${turn} · ` : "";
  if (msg.role === "user") return `c copy · ${save}r resend · e edit & resend · f fork (Wave D) · Esc back`;
  if ((msg.citations?.length ?? 0) > 0) return `c copy · ${save}o open source · k knowledge · Esc back`;
  return `c copy · ${save}Esc back`;
}

function ChatMessageCell(props: { msg: ChatMessage; idx: number }) {
  const msg = props.msg;
  // Read the index reactively: <For> reuses a cell after a mid-list removal
  // (queued take-back), so a captured copy went stale (round 2).
  const isCursor = () => activeTab().messageCursor === props.idx;
  const dims = useTerminalDims();
  // Live chat column (terminal minus a docked detail pane) — every row cap
  // in the cell derives from this instead of a fixed column count.
  const width = () =>
    chatColumnWidth(dims().width, workspace.paneView !== null, workspace.paneWidthOverride, paneBounds());
  // Text column INSIDE the cell chrome (CELL_TEXT_CHROME). Visibility wave
  // 2026-10-19 — the wrap/cap column previously ignored the cell chrome, so
  // text painted to (and clipped at) the right edge; an overlong row also
  // corrupts mid-span in the engine, so the margin is load-bearing, not
  // cosmetic. Diffs and the files card size from it too (wip-eval#3).
  const bodyWidth = () => cellTextWidth(width());
  return (
    <box
      flexDirection="column"
      marginTop={1}
      marginBottom={msg.role === "user" ? 2 : 1}
      border={["left"]}
      borderStyle="single"
      borderColor={roleColor(msg.role)}
      paddingLeft={1}
      overflow="hidden"
      width="100%"
      backgroundColor={
        msg.role === "user"
          ? palette.msgUserBg
          : msg.role === "assistant"
            ? palette.msgAssistantBg
            : undefined
      }
    >
      <box height={1} width="100%" overflow="hidden">
        <text fg={isCursor() ? palette.accent : roleColor(msg.role)}>
          {truncate(
            [
              isCursor() ? "▸" : "",
              roleLabel(msg.role),
              localTimeHHMMSS(msg.timestamp),
              attribution(msg),
              msg.queued ? "Queued" : "",
              citationChip(msg.citations),
            ]
              .filter(Boolean)
              .join(" "),
            bodyWidth(),
          )}
        </text>
      </box>
      {/* B3: focused row footer — one interaction grammar (verbs) */}
      <Show when={isCursor()}>
        <box height={1} width="100%" overflow="hidden" backgroundColor={palette.bgActive}>
          <text fg={palette.accent}>
            {focusedRowFooter(msg, turnOfMessage(activeTab().chatMessages, props.idx))}
          </text>
        </box>
      </Show>
      {/* Management card (if present) takes over the message body. */}
      <Show when={msg.mgmtCard}>
        <MgmtCardView card={msg.mgmtCard!} />
      </Show>
      {/* Streaming-markdown hybrid: engine renders while tokens arrive;
          finalized messages keep the per-line path so nothing is lost. */}
      <Show when={!msg.mgmtCard}>
        {/* B3: Write/Edit-style results render as colored diffs. */}
        <Show
          when={rendersAsDiff(msg)}
          fallback={
            <MarkdownBlock
              content={displayContentFor(msg)}
              streaming={!!msg.streaming}
              dimFg={palette.fgDim}
              baseFg={palette.fg}
              wrapWidth={bodyWidth()}
            />
          }
        >
          <Show
            when={preferSideBySide(bodyWidth())}
            fallback={<DiffBlock content={msg.content} width={bodyWidth()} />}
          >
            <ParallelDiffBlock content={msg.content} width={bodyWidth()} />
          </Show>
        </Show>
      </Show>
      {/* Modified code indication: per-turn file edits with inline diffs. */}
      <Show when={msg.role === "assistant" && modifiedFilesFromToolCalls(msg.toolCalls).length > 0}>
        <ModifiedFilesCard
          msgId={msg.id}
          files={modifiedFilesFromToolCalls(msg.toolCalls)}
          width={bodyWidth()}
        />
      </Show>
      {/* Agent work group: the tool calls this reply's text round triggered,
          pinned UNDER it so transcript order matches invocation time
          (text₁ → tools₁ → text₂ → tools₂ …). */}
      <Show when={msg.toolCalls && msg.toolCalls.length > 0}>
        <ToolCallsGroup msgId={msg.id} calls={msg.toolCalls!} />
      </Show>
      {/* B5: structured error card — what / action / verb. */}
      <Show when={msg.errorCard}>
        <box flexDirection="column" width="100%" overflow="hidden" marginTop={1}>
          <ErrorCardView msgId={msg.id} card={msg.errorCard!} />
        </box>
      </Show>
      {/* B1: expandable tool roll-up — ↵ (empty draft) or click. */}
      <Show when={msg.noticeKey && activeTab().noticeDetails[msg.noticeKey]}>
        {(() => {
          const key = msg.noticeKey!;
          const detail = () => activeTab().noticeDetails[key] ?? [];
          const expanded = () => rowExpanded(key);
          return (
            <box flexDirection="column">
              <box height={1} width="100%" overflow="hidden" onMouseDown={() => actions.toggleNoticeExpanded(key)}>
                <text fg={palette.fgDim}>
                  {expanded() ? "↵ collapse details" : "↵ expand details"}
                </text>
              </box>
              <Show when={expanded()}>
                <For each={detail()}>
                  {(line) => (
                    <box height={1} width="100%" overflow="hidden" paddingLeft={1}>
                      <HighlightText text={truncate(line, bodyWidth())} baseFg={palette.fgDim} />
                    </box>
                  )}
                </For>
              </Show>
            </box>
          );
        })()}
      </Show>
      <Show when={msg.citations && msg.citations.length > 0}>
        <box flexDirection="column" marginTop={1} paddingLeft={1} overflow="hidden">
          <box height={1} overflow="hidden">
            <text fg={palette.fgDim}>{citationBlockLabel(msg.citations ?? [])}</text>
          </box>
          <For each={msg.citations}>
            {(cite) => (
              <box height={1} overflow="hidden">
                <text fg={palette.fgDim}>{citationPreview(cite)}</text>
              </box>
            )}
          </For>
        </box>
      </Show>
    </box>
  );
}

/** Terminal rows from which the composer's hint and receipt rows show. */
const COMPOSER_ROOMY_ROWS = 12;

/** Plain Enter submits. Modified Enter (Shift/Alt/Ctrl) is a COMPOSER key,
 *  handled earlier in the chain. Alt is option OR meta: legacy terminals
 *  deliver Alt+Enter as ESC+CR, which the parser reports with meta=true only
 *  (2026-10-19 probe) — without the meta check that keystroke SUBMITTED. */
function isSubmitKey(event: KeyEvent): boolean {
  if (event.name !== "return" && event.name !== "enter") return false;
  return !(event.shift || event.option || event.ctrl || event.meta);
}

/** Alt/Ctrl+Enter, or Ctrl+J — which arrives as the key named "linefeed"
 *  (it IS \n), never as j+ctrl on most terminals: a draft newline. */
function isNewlineKey(event: KeyEvent): boolean {
  if (event.name === "return") return Boolean(event.option || event.meta || event.ctrl);
  return event.name === "linefeed" || (event.name === "j" && Boolean(event.ctrl));
}

export function ChatPanel() {
  const renderer = useRenderer();
  // Kitty-negotiated truth for the composer hint (debug wave 2026-10-19):
  // Shift+Enter only exists as a distinct key when kitty is on; otherwise
  // the footer degrades to the byte-honest Alt+Enter/Ctrl+J fallback.
  const kittyKeys = useKittyKeys(renderer);
  const dims = useTerminalDims();
  /** Height budget (w3-tui, 2026-10-21): below COMPOSER_ROOMY_ROWS the
   * newline hint and the cost/TTFT receipt yield so the composer stays on
   * screen — at 30x10 they pushed the input below the status bar. */
  const roomy = () => dims().height >= COMPOSER_ROOMY_ROWS && !cardTight();
  /** A pending approval card too tall for the terminal takes the panel
   * body: the transcript collapses and the composer yields (item 5; the
   * card is a modal input layer, the composer has no keys meanwhile). */
  const cardTight = () => activeTab().toolApproval != null && approvalTight();
  /** Live chat column for panel-level row caps (mention popup, thinking…). */
  const panelCap = (chrome = 2) =>
    Math.max(
      40,
      chatColumnWidth(dims().width, workspace.paneView !== null, workspace.paneWidthOverride, paneBounds()) - chrome,
    );
  const [draft, setDraft] = createSignal("");
  // B4: multiline drafts — the input shows only the LAST line segment; the
  // full draft (with \n) submits as one message. Alt+Enter / Ctrl+J appends
  // a line; Ctrl+E opens $EDITOR on the whole draft.
  const displayValue = () => {
    const text = draft();
    const idx = text.lastIndexOf("\n");
    return idx >= 0 ? text.slice(idx + 1) : text;
  };
  const multiline = () => draft().includes("\n");
  const draftLineCount = () => (multiline() ? draft().split("\n").length : 1);
  const setDraftFull = (text: string) => {
    setDraft(text);
    actions.setChatDraft(text);
  };
  const appendDraftLine = () => {
    const next = `${draft()}\n`;
    setDraftFull(next);
  };
  const openDraftInEditor = () => {
    const choice = editorForEnv(process.env as { VISUAL?: string; EDITOR?: string });
    if (!choice) {
      actions.sendSystemNotice("Ctrl+E needs $VISUAL or $EDITOR set (e.g. VISUAL=vim).");
      return;
    }
    const result = openInExternalEditor({
      // The editor gets the pasted text, never a bare chip (composer#5).
      initialText: pastes.expand(draft()),
      editor: choice.editor,
      suspend: () => renderer.suspend(),
      resume: () => renderer.resume(),
    });
    if (result.ok) {
      // Adopt via the seq-tagged pending-draft path (adopted not consumed).
      actions.setPendingChatDraft(result.text ?? "");
    } else {
      actions.sendErrorCard({
        what: `external editor failed`,
        action: "dropped",
        detail: result.error ?? "unknown error",
      });
    }
  };
  onMount(() => {
    registerRedraw(() => {
      renderer.resize(renderer.width, renderer.height);
      renderer.requestRender();
    });
    onCleanup(() => registerRedraw(null));
  });
  // D5a: placeholder rotation tick (6s buckets).
  const [rotationTick, setRotationTick] = createSignal(0);
  onMount(() => {
    const id = setInterval(() => setRotationTick((tick) => tick + 1), PLACEHOLDER_BUCKET_MS);
    onCleanup(() => clearInterval(id));
  });
  // Realtime elapsed clock: thinking/waiting seconds tick visibly instead of
  // freezing between model events (400ms cadence; no-ops once closed).
  onMount(() => {
    const id = setInterval(() => {
      const started = activeTab().thinking.startedAtMs;
      if (started != null && activeTab().thinking.open) {
        setScopedField("thinking", "elapsedMs", Date.now() - started);
      }
    }, 400);
    onCleanup(() => clearInterval(id));
  });
  // Command history seeded from ~/.config/r4/prompt-history.jsonl (A3: one file
  // across all r4 surfaces); in-memory tail then continues per session.
  const [history, setHistory] = createSignal<string[]>(promptHistory());
  const [histIndex, setHistIndex] = createSignal(promptHistory().length);
  // The in-progress draft, kept while ↑/↓ browse history and given back on
  // ↓ past the newest entry — shell behaviour (composer#3: ↑ discarded it).
  const [histDraft, setHistDraft] = createSignal("");
  /** Back on the live draft slot (after a send, an Esc, an adopted draft). */
  const resetHistoryNav = () => {
    setHistIndex(history().length);
    setHistDraft("");
  };
  // A prompt sent from the splash is written to the shared history file
  // after this panel mounted: reload on the splash's close edge so ↑
  // recalls it (composer#9). Navigation already under way keeps its place.
  let splashWasOpen = workspace.splashOpen;
  createEffect(() => {
    const open = workspace.splashOpen;
    const closed = splashWasOpen && !open;
    splashWasOpen = open;
    if (!closed) return;
    untrack(() => {
      const atLive = histIndex() >= history().length;
      setHistory(promptHistory());
      if (atLive) setHistIndex(history().length);
    });
  });
  // A1: @-mention completion state (popup above the input).
  const [mentionItems, setMentionItems] = createSignal<MentionCandidate[]>([]);
  const [mentionIndex, setMentionIndex] = createSignal(0);
  // A6: extmark-style tracking of inserted @-mention chips; a backspace
  // inside a chip deletes the whole chip atomically.
  const [mentionChips, setMentionChips] = createSignal<string[]>([]);
  const mentionOpen = () => mentionItems().length > 0;
  const shellMode = () => draft().startsWith("!");
  // Claude-Code parity: Ctrl+R reverse-i-search, Ctrl+S stash, Esc Esc.
  const [search, setSearch] = createSignal<{
    query: string;
    match: { index: number; text: string } | null;
    saved: string;
  } | null>(null);
  const [stash, setStash] = createSignal<string[]>([]);
  // Large/multi-line pastes become `[Pasted text #N ~L lines]` chips, expanded at
  // submit — the single-line input would otherwise strip every newline.
  const pastes = createPasteTracker();
  let lastEscAt: number | null = null;

  /** Round 2 (2026-10-17): composer-local Esc state the global keymap must
   * yield to — without the claim a docked pane closed instead, and with no
   * pane the sweep swallowed the key. Only while the composer owns keys.
   * Plan H-6: a `local`-rank escape claim, so every overlay above it on
   * the ladder still closes first. */
  const composerOwnsEsc = (): boolean => {
    // Every keyboard-owning layer (the venue / remote-source pickers too)
    // already makes chatInputFocused() false — store-claims derives it.
    if (!chatInputFocused()) return false;
    return [
      search() !== null,
      mentionOpen(),
      activeTab().chatStreaming,
      draft().trim().length > 0,
      activeTab().messageCursor !== null,
      activeTab().planAwaitingConfirmation,
      isDoubleEsc(lastEscAt, Date.now()),
      escSwallowed(),
    ].some(Boolean);
  };
  onCleanup(claimKeys("composer", ["escape"], composerOwnsEsc));
  // Ctrl+R reverse-i-search owns the letters (its query never lands in
  // chatDraft), Tab and Ctrl+G (which cancels it).
  onCleanup(claimKeys("composer-search", ["letters", "tab", "ctrl+g"], () => search() !== null));
  // Tab completes an @-mention; the global pane toggle yields it then.
  onCleanup(claimKeys("composer-mention", ["tab"], mentionOpen));

  // Drops only the pastes this draft's chips name: a stashed draft, the ↑
  // saved draft or another tab's draft keep theirs (composer#1/#5).
  const clearDraftAndChips = () => {
    pastes.forget(draft());
    setDraft("");
    actions.setChatDraft("");
    setMentionChips([]);
  };

  // B2: scroll anchoring — stickyScroll follows only while the user is at the
  // bottom; while scrolled up, a `↓ N new` pill offers an End/click jump.
  let scrollRef: ScrollBoxRenderable | undefined;
  const [atBottom, setAtBottom] = createSignal(true);
  const [anchoredCount, setAnchoredCount] = createSignal(activeTab().chatMessages.length);
  const newCount = () => newContentCount(activeTab().chatMessages.length, anchoredCount());
  onMount(() => {
    const id = setInterval(() => {
      if (!scrollRef) return;
      const bottom = isAtBottom(scrollRef.scrollTop, scrollRef.height, scrollRef.scrollHeight);
      setAtBottom(bottom);
      if (bottom) setAnchoredCount(activeTab().chatMessages.length);
    }, 200);
    onCleanup(() => clearInterval(id));
  });
  // The transcript cursor (B3) selects a message; keep it on screen. ↑ past
  // the viewport used to move the ▸ out of sight while c/r/e/f acted on a
  // message the user could not see (c2-tui 2026-10-21).
  createEffect(() => {
    const cursor = activeTab().messageCursor;
    const msg = cursor === null ? undefined : activeTab().chatMessages[cursor];
    if (msg) scrollRef?.scrollChildIntoView(messageCellId(msg.id));
  });
  const jumpToBottom = () => {
    if (scrollRef) {
      scrollRef.scrollTo({ x: 0, y: scrollRef.scrollHeight });
    }
    setAtBottom(true);
    setAnchoredCount(activeTab().chatMessages.length);
  };

  /** Replace the trailing @query with the chosen candidate's @path. */
  const applyMention = (candidate: MentionCandidate) => {
    const text = draft();
    const found = extractMentionQuery(text);
    if (!found) return;
    // A1: preserve a `#L10-L20` range suffix through completion.
    const next = `${text.slice(0, found.start)} @${candidate.path}${found.range} `;
    setDraft(next);
    actions.setChatDraft(next);
    setMentionChips((chips) => [...chips, `@${candidate.path}${found.range}`]);
    setMentionItems([]);
    return;
  };

  const noteHistory = (text: string) => {
    // S4: a line holding a credential is neither written nor recalled.
    if (appendPromptHistory(text)) setHistory((h) => [...h, text]);
    // The next ↑ recalls the newest entry (was: index one past the end, so
    // the first ↑ after a send was a dead press that blanked the composer).
    resetHistoryNav();
  };

  // OpenTUI's <input> keeps its internal buffer and does NOT mirror post-mount
  // value-prop changes (found via the "›/" stuck draft bug-check): remount it
  // when the palette opens so the buffer starts empty.
  const [inputEpoch, setInputEpoch] = createSignal(0);
  /** Ref to the single-line composer renderable (explicit re-focus on the
   *  terminal click-release edge — see the paneFocus watch below). */
  let composerInput: { focus(): void; cursorOffset: number; isDestroyed?: boolean } | undefined;
  /** Caret column for the NEXT input mount (a remount puts it at the end). */
  let pendingCaret: number | null = null;
  const bindComposerInput = (el: NonNullable<typeof composerInput>) => {
    composerInput = el;
    const caret = pendingCaret;
    pendingCaret = null;
    if (caret === null) return;
    setTimeout(() => {
      if (!el.isDestroyed) el.cursorOffset = caret;
    }, 0);
  };
  /** Remount the input with its caret after `beforeCaret` (last-line text). */
  const remountWithCaret = (beforeCaret: string) => {
    pendingCaret = Bun.stringWidth(beforeCaret.slice(beforeCaret.lastIndexOf("\n") + 1));
    setInputEpoch((e) => e + 1);
  };
  createEffect(() => {
    if (workspace.commandPaletteOpen) {
      setInputEpoch((e) => e + 1);
    }
  });
  // Tab switch (chat tabs, 2026-09-29): the input keeps its own buffer, so
  // remount on every active-tab edge — the restored per-tab draft must paint.
  createEffect(
    on(
      () => workspace.activeChatTabId,
      () => {
        setDraft(activeTab().chatDraft);
        resetHistoryNav();
        setAnchoredCount(activeTab().chatMessages.length);
        setInputEpoch((e) => e + 1);
      },
      { defer: true },
    ),
  );

  // Overlay-close focus return: when any keyboard-grabbing overlay (slash picker,
  // approvals, ask-user, list overlay…) closes, the focused prop flips false→true
  // but OpenTUI does not reliably re-grab focus on a pre-existing node (dead keys
  // until the user clicks — the /models symptom). Remounting the input on the
  // false→true edge guarantees mount-time focus; the draft survives via the
  // value prop.
  let wasChatFocused = chatInputFocused();
  createEffect(() => {
    const now = chatInputFocused();
    if (now && !wasChatFocused) setInputEpoch((e) => e + 1);
    wasChatFocused = now;
  });

  // Terminal click-release (2026-10-03): OpenTUI grants keyboard focus to a
  // clicked renderable — clicking the chat column to leave the terminal
  // hands it to the transcript ScrollBox, and typing dies on scrollbox keys.
  // Watch the paneFocus FALLING edge on the terminal view and re-grab focus
  // EXPLICITLY onto the composer input (mount-time focused=true alone loses
  // to a renderable the mouse focused after mount).
  let wasTermPaneFocus = workspace.paneView === "term" && workspace.paneFocus;
  createEffect(() => {
    const now = workspace.paneView === "term" && workspace.paneFocus;
    if (!now && wasTermPaneFocus) {
      setTimeout(() => composerInput?.focus(), 0);
    }
    wasTermPaneFocus = now;
  });

  const runShellMode = async (command: string) => {
    noteHistory(`!${command}`);
    clearDraftAndChips();
    // The result belongs to THIS tab even if another is active when the
    // shell finishes (static-sweep#4) — captured before the await.
    const tabId = workspace.activeChatTabId;
    // S4: the shell gets the line as typed; every echo, the task label and
    // the model relay get it with its keys masked (lib/local-shell.ts).
    const shown = shellDisplayCommand(command);
    actions.sendSystemNotice(shellEcho(command));
    const taskId = actions.addBackgroundTask("shell", `$ ${shown}`.slice(0, 60));
    try {
      const result = maskShellResult(await runLocalShell(command));
      const verdict = result.interrupted ? "stopped" : `exit ${result.exitCode}`;
      actions.finishBackgroundTask(taskId, result.exitCode === 0 ? "done" : "failed", verdict);
      // Preview + the FULL result relayed into the model as a turn
      // (2026-10-01), unless stopped with Ctrl+C (composer#11).
      actions.finishLocalShell(tabId, command, result);
    } catch (error) {
      const message = maskShellText(command, error instanceof Error ? error.message : String(error));
      actions.finishBackgroundTask(taskId, "failed", message.slice(0, 80));
      actions.finishLocalShell(tabId, command, { error: message });
    }
  };

  const handlePaste = (event: PasteEvent) => {
    const text = pastedText(event);
    if (!text.includes("\n") && text.length < PASTE_CHIP_MIN_CHARS) return; // native paste
    event.preventDefault();
    insertAtCaret(pastes.ingest(text).textToInsert);
  };

  /** Splice `text` in at the caret — the input edits the draft's LAST line,
   * and its cursor is in display columns (composer#7: was appended). */
  const insertAtCaret = (text: string) => {
    const full = draft();
    const cut = full.lastIndexOf("\n") + 1;
    const line = full.slice(cut);
    const at = caretIndex(line, composerInput?.cursorOffset ?? Number.MAX_SAFE_INTEGER);
    const head = line.slice(0, at) + text;
    setDraftFull(full.slice(0, cut) + head + line.slice(at));
    remountWithCaret(head);
  };

  /** A paste chip is atomic (composer#5): one Backspace inside it removes the
   * whole chip — a half chip was sent as literal text and its paste lost. */
  const collapsePasteChip = (prev: string, next: string): string =>
    collapseChipsOnBackspace(prev, next, pastes.tokens()).text;

  const submit = () => {
    const text = pastes.expand(draft()).trim();
    if (!text) {
      // Empty-draft verbs in one grammar: latest error card verb wins (B5),
      // else the latest tool roll-up expands/collapses (B1).
      if (liveErrorCard() && actions.resolveLastErrorKey()) return;
      actions.toggleNoticeExpanded();
      return;
    }
    // Mention popup owns Enter while open — completes instead of dispatch.
    if (mentionOpen()) {
      applyMention(mentionItems()[mentionIndex()]);
      return;
    }
    // A4: `!cmd` — local shell mode; full output is shown AND relayed into
    // the model as a turn (2026-10-01: previously never entered context).
    if (text.startsWith("!")) {
      void runShellMode(text.slice(1));
      return;
    }
    // SlashBar owns slash dispatch while open — prevent dual dispatch.
    if (text.startsWith("/")) {
      if (workspace.slashBarOpen) {
        clearDraftAndChips();
        return;
      }
      void actions.dispatchSlashFromChat(text);
      clearDraftAndChips();
      return;
    }
    // B3: edit-and-resend — cut the companion history and the transcript
    // BEFORE the new turn lands; a refused cut keeps the draft (r2 #17).
    if (activeTab().editResendFromIndex !== null) {
      void resendEdited(text);
      return;
    }
    sendTyped(text);
  };

  const sendTyped = (text: string) => {
    noteHistory(text);
    // ADR-099 D-2: sendChatMessage asks first on a session without a
    // web-access choice; the message waits and comes back here on cancel.
    actions.sendChatMessage(text);
    clearDraftAndChips();
  };

  // The rewind awaits session.truncate: a second Enter meanwhile is ignored
  // in the store, and text typed meanwhile stays in the composer (only the
  // submitted draft is cleared; r2 #17 review).
  const resendEdited = async (text: string) => {
    if (!(await actions.rewindForResend())) return;
    if (pastes.expand(draft()).trim() === text) {
      sendTyped(text);
      return;
    }
    noteHistory(text);
    actions.sendChatMessage(text);
  };

  // Adopt seeded drafts by seq tag. The store value stays intact — a
  // permanently-mounted ChatPanel (or a stale instance from an earlier App in
  // the same process) must not consume data other surfaces assert against.
  const [lastAdoptedSeq, setLastAdoptedSeq] = createSignal(-1);
  createEffect(() => {
    const pending = workspace.pendingChatDraft;
    const seq = workspace.pendingChatDraftSeq;
    if (pending !== null && seq !== lastAdoptedSeq()) {
      setLastAdoptedSeq(seq);
      // The replaced draft's pastes go with it (Ctrl+C clear, Ctrl+E…):
      // a stale tracker entry used to expand the NEXT chip (composer#1).
      pastes.forget(untrack(draft));
      setDraft(pending);
      actions.setChatDraft(pending);
      // The OpenTUI input keeps its own buffer: remount so the adopted text
      // (or a Ctrl+C clear) is what the user sees.
      setInputEpoch((e) => e + 1);
      setMentionChips([]); // foreign draft source — no chip bookkeeping
      resetHistoryNav();
    }
  });

  /** Ctrl+R mode: typed text is the search query, not the draft. */
  const searchInput = (value: string): boolean => {
    const active = search();
    if (!active) return false;
    setSearch({ ...active, query: value, match: reverseSearch(history(), value, history().length) });
    return true;
  };

  /** B4: while the draft is multiline, the input edits only its LAST segment;
   * prefix lines stay untouched. Slash intercept is suspended in that mode. */
  const multilineInput = (value: string): boolean => {
    if (!multiline()) return false;
    const prefix = draft().slice(0, draft().lastIndexOf("\n") + 1);
    const prevLine = displayValue();
    const line = collapsePasteChip(prevLine, value);
    setDraftFull(prefix + line);
    if (line !== value) remountWithCaret(line.slice(0, commonPrefixLength(prevLine, line)));
    return true;
  };

  /** Typed "/" → the command palette (true = consumed). */
  const slashInput = (value: string): boolean => {
    if (!value.startsWith("/") || shellMode()) return false;
    // Palette owns slash text while open — do NOT let a stale onInput write
    // the raw "/" back into the draft (was: "›/" stuck after palette close).
    if (workspace.commandPaletteOpen) return true;
    if (shouldDivertSlashToPalette(value)) {
      // V4 S2: typed "/" opens the unified command surface preseeded. Also
      // cover "//" — a stale "/" left in the draft must never deadlock.
      actions.openCommandPalette(value.startsWith("//") ? value.slice(1) : value);
      clearDraftAndChips();
      return true;
    }
    // Full slash line with args (pasted/seeded, e.g. "/login <JWT>"): keep it
    // as a composer draft so Enter dispatches it verbatim.
    return false;
  };

  const handleInput = (value: string) => {
    // Stale mounts emit blur-time input events with their old draft — ignore
    // unless this panel is the focused input target (found via test pollution:
    // zombie mounts from previous renders injected stale text into chatDraft).
    if (!chatInputFocused()) return;
    if (activeTab().toolApproval || activeTab().askUser) return;
    if (searchInput(value) || multilineInput(value)) return;
    const prev = draft();
    if (slashInput(value)) return;
    // A6: backspace inside a tracked @-mention chip collapses the whole chip
    // (a paste chip likewise, composer#5).
    const collapsed = collapseChipsOnBackspace(prev, collapsePasteChip(prev, value), mentionChips());
    setDraft(collapsed.text);
    actions.setChatDraft(collapsed.text);
    setMentionChips(collapsed.chips);
    // The input still shows the half chip: repaint it, caret at the cut.
    if (collapsed.text !== value) remountWithCaret(collapsed.text.slice(0, commonPrefixLength(prev, collapsed.text)));
  };

  // A1: re-rank @-mention candidates whenever the draft changes.
  createEffect(() => {
    const text = draft();
    if (!text.includes("@") || shellMode()) {
      setMentionItems([]);
      return;
    }
    const found = extractMentionQuery(text);
    if (!found) {
      setMentionItems([]);
      return;
    }
    const cwd = resolveWorkspace();
    ensureWorkspaceIndex(cwd);
    setMentionItems(rankMentions(workspaceFiles(), found.query) ?? []);
    setMentionIndex(0);
  });

  /** The latest error card while its B5 verbs still apply: in the
   * transcript (/clear removes it) with no user turn after it. */
  const liveErrorCard = (): ChatMessage | null => {
    const id = activeTab().lastErrorCardId;
    const rows = activeTab().chatMessages;
    const at = id ? rows.findIndex((m) => m.id === id) : -1;
    if (at < 0 || rows.slice(at + 1).some((m) => m.role === "user")) return null;
    return rows[at] ?? null;
  };

  const handleApprovalKey = (event: KeyEvent): boolean => {
    if (!activeTab().toolApproval) return false;
    // Feedback stage: the card's input claims typed text; a stray key here
    // must never approve (round 2).
    if (keyClaimed("text")) {
      event.preventDefault();
      return true;
    }
    if (!dispatchToolApprovalKey(event.name || event.sequence || "")) return false;
    event.preventDefault();
    return true;
  };

  const handleEscapeKey = (event: KeyEvent) => {
    // A1: dismiss a live mention popup first (standard editor behavior).
    if (mentionOpen()) {
      event.preventDefault();
      setMentionItems([]);
      return;
    }
    // A stray Esc must not kill a long turn: only the Nth tap stops it, and
    // the taps right after a stop are swallowed (never clear the draft or
    // open the rewind menu by a mashed Esc) — maintainer 2026-11-03.
    if (activeTab().chatStreaming || escSwallowed()) {
      event.preventDefault();
      if (activeTab().chatStreaming && escTapStops()) actions.interruptChat();
      return;
    }
    const now = Date.now();
    if (draft().trim().length > 0) {
      // Claude parity: a cleared draft is kept in history (↑ recalls it).
      // History keeps the pasted text, never a bare chip (composer#5), and
      // the cleared draft's pastes are dropped (composer#1).
      event.preventDefault();
      noteHistory(pastes.expand(draft()));
      clearDraftAndChips();
      // The seeded edit dies with its draft — "Esc cancels the edit" (the
      // rewind notices' promise) used to be unkept: the marker survived and
      // refused every later Enter (2026-10-19).
      if (activeTab().editResendFromIndex !== null) actions.cancelEditResend();
      lastEscAt = null;
      return;
    }
    // Esc Esc on an empty prompt opens the rewind menu.
    if (isDoubleEsc(lastEscAt, now)) {
      event.preventDefault();
      lastEscAt = null;
      actions.openRewindMenu();
      return;
    }
    lastEscAt = now;
  };

  /** Claude parity: ↑ on an empty prompt takes back queued prompts first. */
  const takesBackQueued = (direction: -1 | 1) =>
    direction === -1 && !draft().trim() && activeTab().pendingChatQueue.length > 0;

  const handleHistoryKey = (event: KeyEvent, direction: -1 | 1) => {
    if (takesBackQueued(direction)) {
      event.preventDefault();
      actions.takeBackQueued();
      return;
    }
    const h = history();
    if (h.length === 0) return;
    event.preventDefault();
    const at = Math.min(histIndex(), h.length);
    const next = Math.max(0, Math.min(h.length, at + direction));
    if (next === at) return; // oldest entry, or ↓ on the live draft: keep it
    if (at === h.length) setHistDraft(draft()); // leaving the live draft
    setHistIndex(next);
    setDraftFull(next === h.length ? histDraft() : (h[next] ?? ""));
    remountInput();
  };

  /** Fork the live session over the bridge (G-P9-2; 'f' in message-cursor mode). */
  const forkActiveSession = () => {
    const currentSid = activeTab().sessionId;
    if (!currentSid) {
      actions.sendSystemNotice("fork: no active session to fork.");
      return;
    }
    void rpc(getCompanionBridge(), "session.fork", { session_id: currentSid }, 30_000)
      .then((forked) => {
        actions.sendSystemNotice(
          `forked session → ${forked.session_id ?? "?"} ("${forked.name ?? ""}")`,
        );
      })
      .catch((err: unknown) => {
        const detail = err instanceof Error ? err.message : String(err);
        actions.sendSystemNotice(`fork failed: ${detail}`);
      });
  };

  /** Wave C: plan card owns a/m/e/n and Esc while awaiting confirmation. It
   * outranks the message cursor — an awaiting plan must be answered first.
   * (Tool approvals still outrank BOTH: they gate forward progress.) */
  const handlePlanAwaitingKey = (event: KeyEvent): boolean => {
    if (!(activeTab().planAwaitingConfirmation && !activeTab().toolApproval)) return false;
    if (event.name === "escape") {
      event.preventDefault();
      void respondPlan("keep_planning");
      return true;
    }
    if (event.name === "e") {
      // The card advertises e = edit request: seed the draft and leave the
      // awaiting state, so the edited text is typed (not read as a/m/n verbs).
      event.preventDefault();
      actions.setPendingChatDraft(activeTab().planPendingRequest);
      void respondPlan("keep_planning");
      return true;
    }
    const planAction = planVerbToAction(event.name);
    if (planAction) {
      event.preventDefault();
      void respondPlan(planAction);
      return true;
    }
    return false;
  };

  /** K1.6: `s` on a focused message keeps it in project knowledge (/save):
   * yours lands established, an answer proposed (`/save tN --vouch` vouches). */
  const handleMessageSaveKey = (event: KeyEvent): boolean => {
    const cursor = activeTab().messageCursor;
    if (cursor === null || event.name !== "s" || event.ctrl || event.meta) return false;
    if (!saveMessageAt(activeTab().chatMessages, cursor)) return false;
    event.preventDefault();
    actions.exitMessageCursor();
    return true;
  };

  /** B3: transcript-cursor mode keys. Returns false onwards when a key exits
   * cursor mode so the key proceeds to the input path normally. */
  const handleMessageCursorKey = (event: KeyEvent): boolean => {
    const cursor = activeTab().messageCursor;
    if (cursor === null) return false;
    switch (event.name) {
      case "escape":
        event.preventDefault();
        actions.exitMessageCursor();
        return true;
      case "up":
        event.preventDefault();
        actions.moveMessageCursor(-1);
        return true;
      case "down":
        event.preventDefault();
        actions.moveMessageCursor(1);
        return true;
      case "c":
        event.preventDefault();
        void actions.copyMessageContent(cursor);
        return true;
      case "r":
        event.preventDefault();
        actions.retryMessage(cursor);
        return true;
      case "e":
        event.preventDefault();
        actions.beginEditResend(cursor);
        return true;
      case "f":
        event.preventDefault();
        forkActiveSession();
        return true;
      case "o": {
        // K1.4: the citation chip opens its passage (first source; ←/→ step).
        const cites = activeTab().chatMessages[cursor]?.citations;
        if (!cites || cites.length === 0) {
          actions.exitMessageCursor();
          return false;
        }
        event.preventDefault();
        void openCitationPreview(cites, 0);
        return true;
      }
      case "k": {
        // P3: drill into the first citation of the focused assistant reply.
        const msg = activeTab().chatMessages[cursor];
        const cite = msg?.citations?.[0];
        if (!msg || !cite) {
          actions.exitMessageCursor();
          return false;
        }
        event.preventDefault();
        actions.exitMessageCursor();
        actions.openCitedInKnowledge(cite.artifactId);
        return true;
      }
      default:
        // Any other key: exit cursor mode and process the key normally.
        actions.exitMessageCursor();
        return false;
    }
  };

  /** Mention overlay navigation/apply keys. */
  const handleMentionKeys = (event: KeyEvent): boolean => {
    if (!mentionOpen()) return false;
    if (event.name === "up" || event.name === "down") {
      event.preventDefault();
      const count = mentionItems().length;
      const delta = event.name === "up" ? -1 : 1;
      setMentionIndex((idx) => (idx + delta + count) % count);
      return true;
    }
    if (event.name === "tab") {
      event.preventDefault();
      applyMention(mentionItems()[mentionIndex()]);
      return true;
    }
    return false;
  };

  /** B5: empty-draft `l` opens /logs for the latest error card — only a
   * live RETRYABLE one, the only card that advertises `l` (static-sweep#1:
   * any old card ate the first letter of every later 'l…' message). */
  const logsKeyForErrorCard = (event: KeyEvent) =>
    event.name === "l" && !draft().trim() && Boolean(liveErrorCard()?.errorCard?.retryable);

  /** PgUp / PgDn page the transcript (c2-tui 2026-10-21): before, only the
   * mouse wheel scrolled it — a keyboard-only reader (or mouse reporting
   * off for native selection) could not read back a long session. */
  const handleTranscriptPageKeys = (event: KeyEvent): boolean => {
    if (event.name !== "pageup" && event.name !== "pagedown") return false;
    if (!scrollRef) return false;
    event.preventDefault();
    const page = Math.max(1, scrollRef.height - 1);
    scrollRef.scrollBy({ x: 0, y: event.name === "pageup" ? -page : page });
    return true;
  };

  /** Enter-submit, End-jump (B2), empty-draft `l` → /logs (B5). */
  const handleSubmitAndJumpKeys = (event: KeyEvent): boolean => {
    if (isSubmitKey(event)) {
      event.preventDefault();
      submit();
      return true;
    }
    // B2: End jumps to the latest row while the pill is visible (re-engages follow).
    if (event.name === "end" && !atBottom()) {
      event.preventDefault();
      jumpToBottom();
      return true;
    }
    if (logsKeyForErrorCard(event)) {
      event.preventDefault();
      actions.openPane("logs");
      return true;
    }
    return false;
  };

  /** Multiline draft keys (B4): Alt+Enter / Ctrl+Enter / Ctrl+J append a line;
   * Ctrl+E opens $EDITOR. (Shift+Enter is claimed earlier by parity keys;
   * Ctrl+Enter sends the queue first — parity again — when one is pending.)
   * Decoded 2026-10-19: Alt is option(kitty) OR meta(legacy ESC+CR — the only
   * Alt+Enter encoding most terminals produce), and Ctrl+J arrives as the key
   * named "linefeed" (it IS \n), never as j+ctrl. */
  const handleMultilineKeys = (event: KeyEvent): boolean => {
    if (isNewlineKey(event)) {
      event.preventDefault();
      appendDraftLine();
      return true;
    }
    if (event.name === "e" && event.ctrl) {
      event.preventDefault();
      openDraftInEditor();
      return true;
    }
    return false;
  };

  /** Backspace at the start of a continuation line joins it to the line
   * above — the input holds only the last line, so the key used to do
   * nothing and a stray newline could only be cleared with Esc (composer#12). */
  const handleLineJoinKey = (event: KeyEvent): boolean => {
    if (event.name !== "backspace" || !multiline() || (composerInput?.cursorOffset ?? 0) > 0) return false;
    event.preventDefault();
    const full = draft();
    const cut = full.lastIndexOf("\n");
    setDraftFull(full.slice(0, cut) + full.slice(cut + 1));
    remountWithCaret(full.slice(0, cut));
    return true;
  };

  /** Cursor-mode entry: Shift+Tab or Ctrl+↑ (B3). */
  const handleCursorEntryKey = (event: KeyEvent): boolean => {
    if ((event.name === "tab" && event.shift) || (event.name === "up" && event.ctrl)) {
      event.preventDefault();
      actions.enterMessageCursor();
      return true;
    }
    return false;
  };

  /** Escape + history fallback keys. */
  const handleFallbackKeys = (event: KeyEvent): void => {
    if (event.name === "escape") {
      handleEscapeKey(event);
      return;
    }
    if (event.name === "up") {
      handleHistoryKey(event, -1);
      return;
    }
    if (event.name === "down") {
      handleHistoryKey(event, 1);
    }
  };

  const remountInput = () => setInputEpoch((e) => e + 1);

  const olderSearchMatch = () => {
    const active = search();
    if (!active) return;
    const before = active.match ? active.match.index : history().length;
    const older = reverseSearch(history(), active.query, before);
    if (older) setSearch({ ...active, match: older });
  };

  const endSearch = (accept: boolean) => {
    const active = search();
    if (!active) return;
    setSearch(null);
    setDraftFull(accept ? (active.match?.text ?? active.saved) : active.saved);
    remountInput();
  };

  /** Ctrl+R search mode owns Enter (accept), Esc/Ctrl+G (cancel), Ctrl+R (older). */
  const handleSearchKey = (event: KeyEvent): boolean => {
    if (!search()) return false;
    const action = searchKeyAction(event);
    if (!action) return false;
    event.preventDefault();
    action();
    return true;
  };

  const searchKeyAction = (event: KeyEvent): (() => void) | null => {
    if (event.name === "r" && event.ctrl) return olderSearchMatch;
    if (event.name === "return" || event.name === "enter") return () => endSearch(true);
    if (event.name === "escape" || (event.name === "g" && event.ctrl)) return () => endSearch(false);
    return null;
  };

  const startSearch = () => {
    setSearch({ query: "", match: reverseSearch(history(), "", history().length), saved: draft() });
    setDraft("");
    remountInput();
  };

  const toggleStash = () => {
    const next = stashToggle(draft(), stash());
    setStash(next.stash);
    setDraftFull(next.draft);
    remountInput();
    actions.sendSystemNotice(next.notice);
  };

  /** Ctrl+R / Ctrl+S. */
  const ctrlChordAction = (event: KeyEvent): (() => void) | null => {
    if (!event.ctrl) return null;
    if (event.name === "r") return startSearch;
    if (event.name === "s") return toggleStash;
    return null;
  };

  /** Shift+Enter newline · Ctrl+Enter send queued now (when something is queued). */
  const returnVariantAction = (event: KeyEvent): (() => void) | null => {
    if (event.name !== "return" && event.name !== "enter") return null;
    if (event.shift) return appendDraftLine;
    if (event.ctrl && activeTab().pendingChatQueue.length > 0) return () => actions.sendQueuedNow();
    return null;
  };

  /** Claude-parity composer keys: Ctrl+R, Ctrl+S, Shift+Enter, Ctrl+Enter. */
  const handleParityKeys = (event: KeyEvent): boolean => {
    const action = ctrlChordAction(event) ?? returnVariantAction(event);
    if (!action) return false;
    event.preventDefault();
    action();
    return true;
  };

  // Ordered key chain: the first handler that claims a key wins.
  const keyChain: Array<(event: KeyEvent) => boolean> = [
    handleSearchKey,
    handlePlanAwaitingKey,
    handleMessageSaveKey,
    handleMessageCursorKey,
    handleCursorEntryKey,
    handleApprovalKey,
    handleMentionKeys,
    handleLineJoinKey,
    handleParityKeys,
    // Multiline BEFORE submit: Alt+Enter (return+option) must append a draft
    // line, not dispatch — the submit handler treats bare Enter only (2026-10-02).
    handleMultilineKeys,
    handleSubmitAndJumpKeys,
    handleTranscriptPageKeys,
  ];

  const handleKey = (event: KeyEvent) => {
    if (keyChain.some((handler) => handler(event))) return;
    handleFallbackKeys(event);
  };

  return (
    <Panel
      flexDirection="column"
      flexGrow={1}
      flexShrink={1}
      minHeight={8}
      accent={palette.accent}
      title="Chat with R4"
      titleColor={workspace.chatFirst ? palette.accent2 : palette.accent}
      tone={workspace.chatFirst ? "raised" : "base"}
      padding={1}
      overflow="hidden"
      gap={1}
    >
      <scrollbox
        ref={(el: ScrollBoxRenderable) => {
          scrollRef = el;
        }}
        flexGrow={cardTight() ? 0 : 1}
        flexBasis={cardTight() ? 0 : "auto"}
        stickyScroll
        stickyStart="bottom"
        scrollY
        overflow="hidden"
        // A click on the transcript (row expand, select) must leave the
        // composer the keyboard target: OpenTUI focuses the first focusable
        // ancestor of a clicked node — this ScrollBox — and every typed key
        // then died on it (panes-overlays#1). Wheel scrolling needs no focus.
        focusable={false}
      >
        <For each={activeTab().chatMessages}>
          {(msg, idx) => (
            // The id lets the transcript cursor scroll this cell into view.
            <box id={messageCellId(msg.id)} flexDirection="column" flexShrink={0} width="100%">
              <ChatMessageCell msg={msg} idx={idx()} />
            </box>
          )}
        </For>
        {/* V2/G2: boot greeting — the honest idling surface once startup
            seed lands; never new chrome. */}
        <GreetingPanel />
        {/* Live tool timeline: calls appear AS they complete (before the
            assistant message finalizes and pins its ToolCallsGroup). The slice
            clears at finalize, so this swap hands off seamlessly. */}
        <Show when={activeTab().turnToolActivity.length > 0}>
          <ToolCallsGroup msgId="live" calls={activeTab().turnToolActivity} />
        </Show>
      </scrollbox>
      {/* B2: new-content pill — visible only while scrolled away from bottom */}
      <Show when={!atBottom() && newCount() > 0}>
        <box
          height={1}
          width="100%"
          backgroundColor={palette.bgActive}
          onMouseDown={jumpToBottom}
          overflow="hidden"
        >
          <text fg={palette.accent}>{newContentPillLabel(newCount())}</text>
        </box>
      </Show>
      {/* A1: @-mention completion popup (above the input row) */}
      <Show when={mentionOpen()}>
        <Floating
          flexDirection="column"
          title={`@ files — ${mentionItems().length} matches`}
          height={Math.min(8, mentionItems().length + 2)}
          width="100%"
        >
          <For each={mentionItems()}>
            {(item, idx) => (
              <box height={1} width="100%" backgroundColor={idx() === mentionIndex() ? palette.bgHover : undefined}>
                <text fg={idx() === mentionIndex() ? palette.accent : palette.fg}>
                  {truncate(
                    `${idx() === mentionIndex() ? "→" : " "} @${item.path}${item.parentDir ? ` (${item.parentDir}/)` : ""}`,
                    panelCap(4),
                  )}
                </text>
              </box>
            )}
          </For>
        </Floating>
      </Show>
      {/* B4: live thinking-part collapse widget — click header to expand. */}
      <Show when={activeTab().thinking.open || activeTab().thinking.text}>
        <box flexDirection="column" width="100%" overflow="hidden">
          <box height={1} overflow="hidden" onMouseDown={() => actions.toggleThinkingExpanded()}>
            <text fg={palette.fgDim}>{thoughtHeader(activeTab().thinking)}</text>
          </box>
          <Show when={activeTab().thinking.expanded || workspace.verboseTranscript}>
            <For each={thoughtBody(activeTab().thinking.text)}>
              {(line) => (
                <box height={1} overflow="hidden">
                  <text fg={palette.fgDim}>{truncate(line || " ", panelCap(2))}</text>
                </box>
              )}
            </For>
          </Show>
        </box>
      </Show>
      {/* Wave C: plan-mode confirmation card (higher priority than approval —
          the plan decides whether approvals run at all). */}
      <Show when={activeTab().planAwaitingConfirmation}>
        <PlanCard />
      </Show>
      {/* V4: approvals + ask-user render inline as transcript cards (were
          absolute overlays) — conversation context stays visible while deciding. */}
      <Show when={activeTab().toolApproval}>
        <ToolApprovalOverlay />
      </Show>
      <Show when={activeTab().askUser}>
        <AskUserOverlay />
      </Show>
      {/* C3: token/cost/context chip — refreshed at every turn end. */}
      {/* ADR-037: tool-schema after-estimate suffixes the chip (hurts the
          honesty rule to invent one when no selection record exists, so
          the suffix is absent until a record arrives). */}
      <Show when={roomy() && activeTab().cost}>
        {(cost) => (
          <box height={1} width="100%" overflow="hidden">
            <text fg={palette.fgDim}>
              {formatCostChip(cost()) +
                formatToolSchemaSuffix(activeTab().toolSelection?.schema_tokens_estimate?.after) +
                formatTtftSuffix(activeTab().lastTurnTtftMs)}
            </text>
          </box>
        )}
      </Show>
      {/* TTFT stands alone when no cost record exists for the turn. */}
      <Show when={roomy() && !activeTab().cost && activeTab().lastTurnTtftMs != null}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgDim}>{formatTtftSuffix(activeTab().lastTurnTtftMs).trim()}</text>
        </box>
      </Show>
      {/* Persistent composer affordances: multiline hint is always visible
          (before, users could not tell that Alt+Enter adds lines); the box
          stays tall (3 rows) with a border so the click target is obvious
          (report: single-row composer was hard to hit, 2026-09-03). */}
      <Show when={roomy() && multiline()}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgDim}>
            {`≡ ${draftLineCount()}-line draft — Enter submits all · Alt+Enter/Ctrl+J adds a line · Ctrl+E opens $EDITOR`}
          </text>
        </box>
      </Show>
      <Show when={search()}>
        {(active) => (
          <box height={1} width="100%" overflow="hidden">
            <text fg={palette.accent}>
              {`(reverse-i-search)\`${active().query}': ${active().match?.text ?? "— no match"}  · Ctrl+R older · Enter use · Esc cancel`}
            </text>
          </box>
        )}
      </Show>
      <Show when={roomy() && !multiline() && !search()}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgDim}>
            {`${newlineHint(kittyKeys())} newline · Ctrl+R search · Ctrl+S stash · Esc Esc rewind · Ctrl+O verbose · F12 help`}
          </text>
        </box>
      </Show>
      <Show when={!cardTight()}>
      <Panel
        flexDirection="row"
        gap={1}
        height={3}
        accent={chatInputFocused() ? palette.accent : palette.border}
        tone="user"
        paddingLeft={1}
        paddingRight={1}
      >
        <text fg={shellMode() ? palette.statusWarn : palette.accent}>{shellMode() ? "!" : "›"}</text>
        <For each={[inputEpoch()]}>
          {() => (
            <input
              ref={bindComposerInput}
              placeholder={chatPlaceholder(shellMode(), rotationTick() * PLACEHOLDER_BUCKET_MS)}
              value={displayValue()}
              width="100%"
              focused={chatInputFocused()}
              onInput={handleInput}
              onKeyDown={handleKey}
              onPaste={handlePaste}
              backgroundColor={palette.msgUserBg}
              textColor={palette.fgBright}
              placeholderColor={palette.fg}
              cursorColor={palette.accent}
            />
          )}
        </For>
      </Panel>
      </Show>
    </Panel>
  );
}

