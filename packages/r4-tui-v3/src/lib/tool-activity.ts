/**
 * Human-readable tool activity labels and turn summaries for the chat transcript.
 * Ported from tui-js tool-activity.mjs (B1/B2 waves).
 */

import { iconizeToolLabel, isDeniedText } from "./tool-glyph";
import { statusIcon } from "./tool-icons";
import { chatContentWidth } from "./term-width";
import { DIFF_IN_CARD_MARKER, isWriteFamilyTool, stripEmbeddedDiff } from "./modified-files";
import {
  basename,
  formatPathLabel,
  parseToolArgs,
  patternHint,
  type ToolArgs,
} from "./tool-args";
import {
  formatWorkflowDone,
  formatWorkflowInProgress,
  isWorkflowError,
  isWorkflowTool,
  parseWorkflowPayload,
} from "./workflow-activity";

export interface ActivityLine {
  status: "ok" | "warn" | "error" | "info";
  blocked?: boolean;
  text: string;
  name?: string;
  args?: ToolArgs;
  payload?: unknown;
  batch?: number;
  /** Tool execution time (ms) from the bridge envelope, for detail rows. */
  durationMs?: number | null;
  /** Full tool result body (capped) — fuels click-expand full output view. */
  resultText?: string;
}

/** Max result body kept per tool call (chars). */
const RESULT_TEXT_CAP = 20_000;
/** Detail-body caps so a huge stdout cannot flood the transcript. */
export const DETAIL_MAX_OUTPUT_LINES = 60;
export const DETAIL_MAX_OUTPUT_CHARS = 3500;
/** Legacy fixed wrap column — kept for reference/tests; rendering uses
 * wrapWidth() which tracks the REAL chat column (no more 110-col breaklines
 * on 200-col terminals). */
export const WRAP_WIDTH = 110;
/** Default preview for the per-call expand (first N output lines). */
export const OUTPUT_PREVIEW_LINES = 5;

/** Live wrap column: the synced chat width minus row chrome. */
export function wrapWidth(): number {
  return chatContentWidth(4);
}

/** Hard-wrap a long line into <= `width` chunks (space-free commands). */
function wrapLong(line: string, width: number = wrapWidth()): string[] {
  if (line.length <= width) return [line];
  const out: string[] = [];
  for (let i = 0; i < line.length; i += width) out.push(line.slice(i, i + width));
  return out;
}

/** Preview prefix the render attaches — segments must honor it (truncate lung
 * prefers a healthy 4-char headroom on the render side). */
const OUTPUT_PREFIX_WIDTH = 4;

function wrapOutputLine(line: string): string[] {
  return wrapLong(line, wrapWidth() - OUTPUT_PREFIX_WIDTH);
}

/** Kind tags per typed detail row (T5 topic-12 — lets the renderer pick the
 * right colorizer per row; never fused). */
export type DetailRowKind = "command" | "code" | "output" | "marker";

export interface DetailRow {
  kind: DetailRowKind;
  text: string;
}

/** Raw output lines for a call (cr stripped), before any preview cap.
 * Default keeps DETAIL_MAX_OUTPUT_LINES; pass `Infinity` for the raw output
 * (used by "full output" expansion — always renders the whole content). */
export function toolOutputLines(line: ActivityLine, maxLines = DETAIL_MAX_OUTPUT_LINES): string[] {
  const body = (line.resultText ?? "").replace(/\r/g, "");
  if (!body.trim()) return [];
  if (maxLines === Infinity) return body.split("\n");
  return body.split("\n").slice(0, maxLines);
}

function outputRowsOne(
  bLine: string,
  isFull: boolean,
  budget: number,
): { rows: DetailRow[]; spent: number } {
  const clipped = isFull ? bLine : bLine.slice(0, budget);
  if (!isFull) return { rows: [{ kind: "output", text: `  | ${clipped}` }], spent: clipped.length + 3 };
  const rows: DetailRow[] = [];
  for (const seg of wrapOutputLine(clipped)) rows.push({ kind: "output", text: `  | ${seg}` });
  return { rows, spent: clipped.length + 3 };
}

function markerRowFor(line: ActivityLine, shownLen: number): DetailRow | null {
  const body = (line.resultText ?? "").replace(/\r/g, "");
  const totalLines = body.split("\n").length;
  if (
    totalLines > DETAIL_MAX_OUTPUT_LINES ||
    body.length > DETAIL_MAX_OUTPUT_CHARS ||
    shownLen > 0 && totalLines > shownLen
  ) {
    return { kind: "marker", text: `  | … (${Math.max(totalLines - shownLen, 0)} more lines)` };
  }
  return null;
}

function outputRowsFor(line: ActivityLine, previewLines: number, isFull: boolean): DetailRow[] {
  const out: DetailRow[] = [];
  const bodyLines = toolOutputLines(line, isFull ? Infinity : DETAIL_MAX_OUTPUT_LINES);
  if (bodyLines.length === 0) return out;
  let budget = isFull ? Number.MAX_SAFE_INTEGER : DETAIL_MAX_OUTPUT_CHARS;
  const shown = bodyLines.slice(0, previewLines);
  for (const bLine of shown) {
    if (budget <= 0) break;
    const one = outputRowsOne(bLine, isFull, budget);
    out.push(...one.rows);
    budget -= one.spent;
  }
  if (!isFull) {
    const marker = markerRowFor(line, shown.length);
    if (marker) out.push(marker);
  }
  return out;
}

/** Kind-tagged typed rows — same construction as `buildToolDetailBody` minus
 * the plain-string loss step; the renderer picks per-kind colorizer. */
export function buildToolDetailRows(
  line: ActivityLine,
  previewLines = DETAIL_MAX_OUTPUT_LINES,
): DetailRow[] {
  const out: DetailRow[] = [];
  const command = typeof line.args?.command === "string" ? line.args.command : "";
  if (command) {
    for (const chunk of wrapLong(`$ ${command}`)) out.push({ kind: "command", text: chunk });
  }
  const code = typeof line.args?.code === "string" ? line.args.code : "";
  if (code && !command) {
    for (const [i, cLine] of code.split("\n").slice(0, 40).entries()) {
      out.push({ kind: "code", text: `${i === 0 ? "» " : "  "}${cLine}` });
    }
  }
  // Write-family results embed the same unified diff the ✎ files-modified
  // card renders styled — printing it again here is duplicated information.
  // Keep the summary head + a pointer marker instead.
  let bodyLine = line;
  let stripped = false;
  if (isWriteFamilyTool(line.name)) {
    const summary = stripEmbeddedDiff(line.resultText ?? "");
    if (summary !== null) {
      bodyLine = { ...line, resultText: summary };
      stripped = summary.length > 0;
    }
  }
  const isFull = previewLines >= Number.MAX_SAFE_INTEGER;
  out.push(...outputRowsFor(bodyLine, previewLines, isFull));
  if (stripped) out.push({ kind: "marker", text: DIFF_IN_CARD_MARKER });
  return out;
}

/**
 * Detail body for a tool call (legacy string row variant — identical
 * construction as `buildToolDetailRows`, retained for callers that still
 * consume row text only).
 */
export function buildToolDetailBody(
  line: ActivityLine,
  previewLines = DETAIL_MAX_OUTPUT_LINES,
): string[] {
  return buildToolDetailRows(line, previewLines).map((row) => row.text);
}

/** Compact duration label: <1s → "123ms", else "1.2s"."" */
export function formatDurationMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const TODO_STATUS_GLYPH: Record<string, string> = {
  completed: "✓",
  done: "✓",
  finished: "✓",
  in_progress: "⏳",
  running: "⏳",
  pending: "□",
  todo: "□",
};

/** Summarize a TodoWrite payload (array of {content,status}) as one actionable line. */
export function formatTodoActivity(text: string): string | null {
  const raw = String(text ?? "").trim();
  if (!raw) return null;
  let items: Array<Record<string, unknown>> | null = null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    const list = Array.isArray(parsed)
      ? parsed
      : (parsed as Record<string, unknown>).todos;
    if (Array.isArray(list)) items = list as Array<Record<string, unknown>>;
  } catch {
    return null;
  }
  if (!items?.length) return null;
  const normalized = items.map((item) => ({
    content: String(item.content ?? item.title ?? item.task ?? "").slice(0, 48),
    status: String(item.status ?? "pending").toLowerCase(),
  }));
  const doneCount = normalized.filter((i) => TODO_STATUS_GLYPH[i.status] === "✓").length;
  const running = normalized.find((i) => TODO_STATUS_GLYPH[i.status] === "⏳");
  const nextPending = normalized.find((i) => TODO_STATUS_GLYPH[i.status] === "□");
  const parts = [`${doneCount}/${normalized.length} done`];
  if (running) parts.push(`▶ ${running.content}`);
  else if (nextPending) parts.push(`next: ${nextPending.content}`);
  if (normalized.length > 3) parts.push(`+${normalized.length - 3} more`);
  return `Todos ${parts.join(" · ")}`;
}

/** Compact result summary: line counts, match counts, or a short single line. */
export function summarizeToolResult(message: unknown, toolName = ""): string {
  const text = String(message || "").trim();
  if (!text) return "done";
  if (/^\(no matches\)$/i.test(text)) return "no matches";
  const countUnit = ["glob_search", "grep_search", "grep"].includes(toolName) ? "matches" : "lines";
  if (text.includes("\n")) {
    const lines = text.split("\n").filter((line) => line.length > 0).length;
    if (lines > 1) return `${lines} ${countUnit}`;
  }
  const single = text.replace(/\s+/g, " ").trim();
  if (single.length <= 56) return single;
  return `${text.split("\n").filter(Boolean).length} ${countUnit}`;
}

/** Live status while a tool is running. */
export function humanizeToolInProgress(name: string, rawArgs: unknown): string {
  const args: ToolArgs =
    typeof rawArgs === "string" ? parseToolArgs(rawArgs) : ((rawArgs as ToolArgs) || {});
  switch (name) {
    case "file_read":
      return args.path ? `Reading ${basename(args.path)}…` : "Reading file…";
    case "list_dir":
      return `Listing ${formatPathLabel(args.path)}…`;
    case "detect_code_context":
      return `Scanning ${formatPathLabel(args.path)}…`;
    case "glob_search":
      return patternHint(args) ? `Searching for ${patternHint(args)}…` : "Searching files…";
    case "grep_search":
    case "grep":
      return patternHint(args) ? `Searching code for ${patternHint(args)}…` : "Searching codebase…";
    case "shell_exec":
      // include enough of the actual command for the user to spot which root
      // it's crawling — 96 chars is still narrow (live chip truncates at ~66,
      // but the hover/tool-activity pane holds the full phrase).
      return args.command
        ? `Running ${String(args.command).slice(0, 96)}…`
        : "Running command…";
    case "python_exec": {
      const code = String(args.code || "").trim();
      if (!code) return "Running Python…";
      const hint = code.split("\n").find((line) => line.trim()) || code;
      return `Running Python: ${hint.slice(0, 48)}…`;
    }
    case "web_search":
      return args.query
        ? `Searching web for "${String(args.query).slice(0, 40)}"…`
        : "Searching web…";
    default:
      if (isWorkflowTool(name)) {
        return formatWorkflowInProgress(name, args);
      }
      return args.path ? `${name} ${basename(args.path)}…` : `${name || "tool"}…`;
  }
}

/** Completed tool line for activity feed / transcript. */
export function humanizeToolDone(
  name: string,
  rawArgs: unknown,
  resultMessage: unknown,
  fullResult?: unknown,
): string {
  const args: ToolArgs =
    typeof rawArgs === "string" ? parseToolArgs(rawArgs) : ((rawArgs as ToolArgs) || {});
  if (isWorkflowTool(name)) {
    return formatWorkflowDone(name, args, resultMessage, fullResult);
  }
  const result = summarizeToolResult(resultMessage, name);
  const pattern = patternHint(args);
  switch (name) {
    case "file_read":
      return args.path
        ? `Read ${basename(args.path)}${result !== "done" ? ` (${result})` : ""}`
        : `Read file${result !== "done" ? ` (${result})` : ""}`;
    case "list_dir":
      return `Listed ${formatPathLabel(args.path)}${result !== "done" ? ` (${result})` : ""}`;
    case "detect_code_context":
      return `Scanned ${formatPathLabel(args.path)}${result !== "done" ? ` (${result})` : ""}`;
    case "glob_search":
      if (result === "no matches") {
        return pattern ? `No matches for ${pattern}` : "No file matches";
      }
      return pattern ? `Found ${result} for ${pattern}` : `Found ${result}`;
    case "grep_search":
    case "grep":
      if (result === "no matches") {
        return pattern ? `No code matches for ${pattern}` : "No code matches";
      }
      return pattern ? `Found ${result} for ${pattern}` : `Found ${result}`;
    case "shell_exec":
      return args.command
        ? `Ran ${String(args.command).slice(0, 48)}${result !== "done" ? ` → ${result}` : ""}`
        : `Ran command${result !== "done" ? ` → ${result}` : ""}`;
    case "python_exec":
      return result !== "done" ? `Ran Python (${result})` : "Ran Python";
    default:
      return result !== "done" ? `${name} → ${result}` : name;
  }
}

interface BridgeToolEvent {
  type?: string;
  message?: string;
  data?: {
    name?: string;
    arguments?: unknown;
    result?: unknown;
  };
}

interface PendingCall {
  name?: string;
  args?: ToolArgs;
}

/** One compact ActivityLine per completed tool event. */
export function formatToolActivityEntry(
  event: BridgeToolEvent,
  pendingCall?: PendingCall,
): ActivityLine | null {
  const { type, message, data } = event;
  const name = data?.name || pendingCall?.name || "tool";
  const args = pendingCall?.args || parseToolArgs(data?.arguments);

  if (type === "tool_result") {
    const fullResult = data?.result;
    const rawDur = (data as Record<string, unknown> | undefined)?.duration_ms;
    const durationMs = typeof rawDur === "number" && Number.isFinite(rawDur) ? rawDur : null;
    const resultText = String(fullResult ?? message ?? "").slice(0, RESULT_TEXT_CAP);
    // TodoWrite-class results — actionable inline checklist summary instead of raw JSON.
    if (/todo/i.test(name)) {
      const body =
        formatTodoActivity(String(message ?? "")) ||
        formatTodoActivity(String(fullResult ?? ""));
      if (body) {
        return {
          status: "ok",
          text: `${statusIcon("ok")} ${iconizeToolLabel(name, body)}`,
          name,
          args,
          payload: body,
          durationMs,
        };
      }
    }
    const payload = isWorkflowTool(name) ? parseWorkflowPayload(message, fullResult) : null;
    const denied = isDeniedText(String(message ?? "")) || isDeniedText(String(fullResult ?? ""));
    const failed =
      (payload && isWorkflowError(payload)) ||
      /^(error|blocked)/i.test(String(message || "").trim());
    const status = denied ? "warn" : failed ? "error" : "ok";
    const label = humanizeToolDone(name, args, message, fullResult);
    return {
      status,
      blocked: denied,
      text: `${statusIcon(status)} ${denied ? "⛔ " : ""}${iconizeToolLabel(name, label)}`,
      name,
      args,
      payload,
      durationMs,
      resultText,
    };
  }
  if (type === "tool_blocked") {
    const label = humanizeToolInProgress(name, args).replace(/…$/, "");
    return {
      status: "warn",
      blocked: true,
      text: `${statusIcon("warn")} ⛔ ${iconizeToolLabel(name, label)} blocked`,
      name,
    };
  }
  if (type === "tools_disabled") {
    return { status: "warn", text: `${statusIcon("warn")} ${message || "tools disabled"}`, name };
  }
  if (type === "error") {
    return { status: "error", text: `${statusIcon("error")} ${message || "tool error"}`, name };
  }
  if (type === "agent_spawn" || type === "agent_result") {
    return { status: "info", text: `${statusIcon("info")} ${message || type}`, name };
  }
  return null;
}

function pushBatch(
  out: ActivityLine[],
  opts: { name: string; count: number; label: string },
): void {
  if (opts.count <= 0) return;
  out.push({
    status: "ok",
    text: iconizeToolLabel(opts.name, opts.label),
    name: opts.name,
    batch: opts.count,
  });
}

/** Collapse repetitive tool rows into natural summaries ("3 files read"). */
export function collapseToolActivity(lines: ActivityLine[]): ActivityLine[] {
  const out: ActivityLine[] = [];
  let fileReadBatch = 0;
  let listDirBatch = 0;
  let shellBatch = 0;
  let pythonBatch = 0;
  let globBatch = 0;
  let grepBatch = 0;
  let contextBatch = 0;

  const flushFileReads = () => {
    const label = fileReadBatch === 1 ? "1 file read" : `${fileReadBatch} files read`;
    pushBatch(out, { name: "file_read", count: fileReadBatch, label });
    fileReadBatch = 0;
  };
  const flushListDirs = () => {
    const label =
      listDirBatch === 1 ? "1 directory listed" : `${listDirBatch} directories listed`;
    pushBatch(out, { name: "list_dir", count: listDirBatch, label });
    listDirBatch = 0;
  };
  const flushShell = () => {
    const label = shellBatch === 1 ? "1 command run" : `${shellBatch} commands run`;
    pushBatch(out, { name: "shell_exec", count: shellBatch, label });
    shellBatch = 0;
  };
  const flushPython = () => {
    const label = pythonBatch === 1 ? "1 python run" : `${pythonBatch} python runs`;
    pushBatch(out, { name: "python_exec", count: pythonBatch, label });
    pythonBatch = 0;
  };
  const flushGlob = () => {
    const label = globBatch === 1 ? "1 file search" : `${globBatch} file searches`;
    pushBatch(out, { name: "glob_search", count: globBatch, label });
    globBatch = 0;
  };
  const flushGrep = () => {
    const label = grepBatch === 1 ? "1 code search" : `${grepBatch} code searches`;
    pushBatch(out, { name: "grep_search", count: grepBatch, label });
    grepBatch = 0;
  };
  const flushContext = () => {
    const label = contextBatch === 1 ? "1 context scan" : `${contextBatch} context scans`;
    pushBatch(out, { name: "detect_code_context", count: contextBatch, label });
    contextBatch = 0;
  };

  const flushBatches = () => {
    flushListDirs();
    flushContext();
    flushGlob();
    flushGrep();
    flushShell();
    flushPython();
    flushFileReads();
  };

  for (const line of lines) {
    if (line.status === "error" || line.status === "warn") {
      flushBatches();
      out.push({ ...line, text: line.text.replace(/^✓\s*/, "").replace(/^✗\s*/, "") });
      continue;
    }
    if (line.name === "file_read") {
      fileReadBatch += line.batch || 1;
      continue;
    }
    if (line.name === "list_dir") {
      listDirBatch += line.batch || 1;
      continue;
    }
    if (line.name === "detect_code_context") {
      contextBatch += line.batch || 1;
      continue;
    }
    if (line.name === "shell_exec") {
      shellBatch += line.batch || 1;
      continue;
    }
    if (line.name === "python_exec") {
      pythonBatch += line.batch || 1;
      continue;
    }
    if (line.name === "glob_search") {
      globBatch += line.batch || 1;
      continue;
    }
    if (line.name === "grep_search" || line.name === "grep") {
      grepBatch += line.batch || 1;
      continue;
    }
    flushBatches();
    out.push({ ...line, text: line.text.replace(/^✓\s*/, "") });
  }
  flushBatches();
  return out;
}

/** One-line turn summary for the transcript ("✓ 3 files read · 2 code searches"). */
export function summarizeToolTurn(lines: ActivityLine[]): string {
  const collapsed = collapseToolActivity(lines);
  if (!collapsed.length) return "";
  return collapsed.map((line) => line.text).join(" · ");
}
