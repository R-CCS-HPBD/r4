/**
 * Build the approval-card context rows (opencode parity: $ cmd / content /
 * patterns). Pure — keeps JSX out of the overlay logic so the rules are
 * unit-testable without a renderer.
 */

import { chatContentWidth } from "./term-width";

export type ApprovalPreviewRow = { label: string; text: string };

type PendingApprovalPayload = {
  tool: string;
  description?: string;
};

/** One-line form of a value — whitespace folded, never cut: the card wraps
 * (rows were truncated to the column width before 2026-10-21). */
function flat(value: string): string {
  return visible(value.replace(/\s+/g, " ").trim());
}

/** Control characters, bidi overrides and zero-width marks are shown as
 * escapes, so a command can never hide part of itself from the card (a bare
 * \r or an ANSI sequence could overwrite what the user reads). */
const INVISIBLE = /[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]/g;

function visible(line: string): string {
  return line
    .replace(/\t/g, "  ")
    .replace(INVISIBLE, (c) => `\\u{${c.charCodeAt(0).toString(16)}}`);
}

/** Cmd rows for shell_exec-style tools — EVERY line, never cut (the user
 * approves exactly what runs; stabilization sweep 2026-10-21, chat#1: the
 * card kept 2 lines of the first 300 chars, each cut to the column width,
 * so "y" ran code that was never on screen). The card wraps long rows. */
function shellPreview(payload: PendingApprovalPayload): ApprovalPreviewRow[] {
  const cmd = (payload.description ?? "").replace(/^Run shell command:\s*/i, "");
  const lines = cmd.replace(/\r\n/g, "\n").split("\n").map(visible).filter((l) => l.trim().length > 0);
  return lines.map((line, i) => ({ label: i === 0 ? "$" : " ", text: line }));
}

/** Command rows the card shows before it switches to a scroll box with an
 * explicit "N more rows" marker (the card sits outside the transcript
 * scroll, so an unbounded card would push its own Yes/No rows off screen). */
export const APPROVAL_PREVIEW_MAX_ROWS = 12;

/** Text columns of one card row: chat column − chat/card borders + padding
 * − the 8-column label gutter. */
export function approvalWrapWidth(): number {
  return chatContentWidth(18, 20);
}

/** Terminal cells of one glyph (Bun's table; 1 where Bun is absent). */
type BunLike = { Bun?: { stringWidth(s: string): number } };
const cellsOf = (ch: string): number => (globalThis as BunLike).Bun?.stringWidth(ch) ?? 1;

/** Split `text` into pieces of at most `width` terminal cells (wide CJK
 * glyphs count 2, so no piece is clipped by the row's overflow). */
function wrapCells(text: string, width: number): string[] {
  const out: string[] = [];
  let cur = "";
  let used = 0;
  for (const ch of text) {
    const w = cellsOf(ch);
    if (used + w > width && cur) {
      out.push(cur);
      cur = "";
      used = 0;
    }
    cur += ch;
    used += w;
  }
  out.push(cur);
  return out;
}

/** Preview rows → visual card rows; the label rides the first piece only. */
export function wrapApprovalRows(
  rows: ApprovalPreviewRow[],
  width = approvalWrapWidth(),
): ApprovalPreviewRow[] {
  return rows.flatMap((row) =>
    wrapCells(row.text, Math.max(1, width)).map((text, i) => ({ label: i === 0 ? row.label : "", text })),
  );
}

/** Path + content lines for editor-like tools. */
function filePreview(payload: PendingApprovalPayload): ApprovalPreviewRow[] {
  const desc = payload.description ?? "";
  const path = desc.replace(/^(Write file|Edit file|Run code|Compile or syntax-check code|Run local tests):\s*/i, "");
  if (!path && !desc) return [];
  return [
    { label: "path", text: flat(path || "(inline)") },
    ...(desc && desc !== path ? [{ label: "note", text: flat(desc) }] : []),
  ];
}

/** Workflow verbs — name + parameters k=v rows. */
function workflowPreview(payload: PendingApprovalPayload): ApprovalPreviewRow[] {
  const desc = payload.description ?? "";
  const sep = desc.indexOf(":");
  const head = desc.slice(0, sep >= 0 ? sep : 50);
  const kvs = desc.slice(sep + 1).trim().split(/\s*,\s*/).filter(Boolean);
  const rows: ApprovalPreviewRow[] = [{ label: "function", text: flat(head) }];
  kvs.forEach((kv, i) => rows.push({ label: i === 0 ? "params" : "", text: flat(kv) }));
  return rows;
}

/** Permissions-like (egress patterns, mounts) — every pattern, verbatim
 * (was the first 4: a 5th egress host was approved unseen). */
function patternPreview(payload: PendingApprovalPayload): ApprovalPreviewRow[] {
  const desc = payload.description ?? "";
  const parts = desc.split(/,\s*/).filter(Boolean);
  return parts.map((part, i) => ({ label: i === 0 ? "pattern" : " ", text: flat(part) }));
}

/** The description's lines in full (a plan gate's prompt is multi-line). */
function detailPreview(payload: PendingApprovalPayload): ApprovalPreviewRow[] {
  const lines = (payload.description ?? "").split("\n").map(flat).filter(Boolean);
  return lines.map((text, i) => ({ label: i === 0 ? "detail" : " ", text }));
}

/** The header line python_exec's card puts above its code. */
const CODE_HEADER = /Run Python code:\s*$/;

/** python_exec rows — EVERY line of the code with its indentation (Python's
 * meaning rides on it), never cut (w3-py#1: the server sent the first 80
 * characters on one line). Lines above the header (an agent or fence prefix)
 * stay detail rows; a card without the header is shown as detail rows. */
function codePreview(payload: PendingApprovalPayload): ApprovalPreviewRow[] {
  const lines = (payload.description ?? "").replace(/\r\n/g, "\n").split("\n");
  const header = lines.findIndex((line) => CODE_HEADER.test(line));
  if (header < 0) return detailPreview(payload);
  const above = detailPreview({ tool: payload.tool, description: lines.slice(0, header + 1).join("\n") });
  const code = lines.slice(header + 1).map(visible).filter((l) => l.trim().length > 0);
  return [...above, ...code.map((text, i) => ({ label: i === 0 ? "python" : " ", text }))];
}

/** Rows of the colored diff block in the card; a longer diff is shown as
 * scrolled rows instead (the block has no scroll of its own). */
export const APPROVAL_DIFF_BLOCK_MAX_ROWS = 30;

const HAS_INVISIBLE = new RegExp(INVISIBLE.source);

/** Terminal cells of one diff line (a tab counted at its widest). */
function lineCells(line: string): number {
  let cells = 0;
  for (const ch of line) cells += ch === "\t" ? 8 : cellsOf(ch);
  return cells;
}

/** Whether the colored diff block shows ALL of `diff`: within its row budget,
 * every line within `budget` cells (the block cuts a longer line with "…")
 * and no control characters (the block prints them raw). */
export function diffFitsBlock(diff: string, budget: number): boolean {
  const lines = diff.split("\n");
  if (lines.length > APPROVAL_DIFF_BLOCK_MAX_ROWS) return false;
  return lines.every((line) => !HAS_INVISIBLE.test(line) && lineCells(line) <= budget);
}

/** The diff as plain card rows — every line verbatim, sign included. */
export function diffPreviewRows(diff: string): ApprovalPreviewRow[] {
  const lines = diff.replace(/\r\n/g, "\n").split("\n");
  return lines.map((line, i) => ({ label: i === 0 ? "diff" : "", text: visible(line) }));
}

/** Tool-name match → preview builder; first match wins, else the detail rows. */
const PREVIEW_ROUTES: ReadonlyArray<[RegExp, (p: PendingApprovalPayload) => ApprovalPreviewRow[]]> = [
  [/shell/, shellPreview],
  [/python/, codePreview],
  [/file|write|edit/, filePreview],
  [/egress|knowledge_ingest|mount/, patternPreview],
  [/workflow|project/, workflowPreview],
];

/** The server's card for a tool it has no describer for (agent prefix optional). */
const GENERIC_CARD = /^(\[[^\]\n]*\]\s*)?Call tool:/;

export function buildApprovalPreview(payload: PendingApprovalPayload): ApprovalPreviewRow[] {
  const tool = payload.tool ?? "";
  // A tool without a server describer lists every argument on its own line
  // (w3-py#1); the name-based routes would fold or split them.
  if (GENERIC_CARD.test(payload.description ?? "")) return detailPreview(payload);
  const route = PREVIEW_ROUTES.find(([pattern]) => pattern.test(tool));
  return route ? route[1](payload) : detailPreview(payload);
}
