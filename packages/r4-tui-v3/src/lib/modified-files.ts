/**
 * Per-message modified-files extraction (Leg: visible code-modification
 * indication). The backend's file_write / file_edit tool results embed a
 * unified diff in the result body; the session-changes overlay already
 * shows the cumulative view, but per-TURN edits were invisible unless the
 * user manually expanded the collapsed ⚙ tool group. This module pulls the
 * edits out of a message's tool calls so the transcript can pin a visible
 * "files modified" card under every assistant reply that changed code.
 *
 * Pure parsing — rendering lives in chat.tsx.
 */

import type { ActivityLine } from "./tool-activity";

/** Tool names whose result bodies carry an embedded file diff. */
const WRITE_FAMILY = new Set(["file_write", "file_edit", "apply_patch"]);

/** Marker the tool-detail rows show where a stripped diff used to sit. */
export const DIFF_IN_CARD_MARKER = "  | (diff — see the ✎ files-modified card above)";

export interface ModifiedFile {
  path: string;
  tool: string;
  diffText: string | null;
  /** First result line ("Edited x (replaced 1 occurrence)" / "Written N bytes to x"). */
  summary: string;
  /** Full written content (file_write only) — fuels the styled code view. */
  content?: string;
}

function isWriteTool(name: string | undefined): boolean {
  if (!name) return false;
  return WRITE_FAMILY.has(name.trim().toLowerCase());
}

/** First embedded-diff offset: the earlier of the first `---` header or `@@` hunk. */
function diffStart(resultText: string): number {
  const lines = resultText.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i]!;
    if (l.startsWith("---") || l.startsWith("@@") || l.startsWith("diff ")) return i;
  }
  return -1;
}

/** Tools whose result tail is an embedded unified diff (card renders it). */
export function isWriteFamilyTool(name: string | undefined): boolean {
  if (!name) return false;
  return WRITE_FAMILY.has(name.trim().toLowerCase());
}

/**
 * Strip the embedded diff tail from a write-family tool result, keeping the
 * summary head ("Written 71 bytes to x" / "Edited x …"). Returns null when
 * no diff is embedded (caller renders the body as-is). Used to stop the raw
 * diff from duplicating inside the tool-activity detail rows — the styled
 * ✎ files-modified card is the single surface for the diff.
 */
export function stripEmbeddedDiff(resultText: string): string | null {
  const start = diffStart(resultText);
  if (start < 0) return null;
  return resultText.split("\n").slice(0, start).join("\n");
}

/**
 * True when a diff is a NEW-FILE write (zero deletions beyond the header).
 * All-add diffs are green noise — opencode renders Write as the content
 * code block; we route such files to the styled code view instead.
 */
export function isNewFileDiff(diffText: string): boolean {
  for (const line of diffText.split("\n")) {
    if (line.startsWith("-") && !line.startsWith("---")) return false;
  }
  return true;
}

/** One call → ModifiedFile, or null when the call changed no file content. */
function fromCall(call: ActivityLine): ModifiedFile | null {
  if (!isWriteTool(call.name)) return null;
  const summary = (call.resultText ?? "").split("\n")[0]?.trim() ?? "";
  // Error results ("Error writing …", "Error: old_text not found…") carry no diff.
  if (/^error\b/i.test(summary)) return null;
  const path = typeof call.args?.path === "string" ? call.args.path : "";
  if (!path) return null;
  const body = call.resultText ?? "";
  const start = diffStart(body);
  let diffText = start >= 0 ? body.split("\n").slice(start).join("\n") : null;
  const content = call.args?.content;
  // New-file write: show the styled full content, not an all-add diff.
  if (diffText !== null && typeof content === "string" && content.length > 0 && isNewFileDiff(diffText)) {
    diffText = null;
  }
  const file: ModifiedFile = { path, tool: call.name!, diffText, summary };
  if (typeof content === "string" && content.length > 0) file.content = content;
  return file;
}

/**
 * Extract the files a message's tool calls modified. Same path edited
 * twice in one turn collapses to its LAST state (turn view, not history).
 */
export function modifiedFilesFromToolCalls(calls: ActivityLine[] | undefined): ModifiedFile[] {
  if (!calls || calls.length === 0) return [];
  const byPath = new Map<string, ModifiedFile>();
  for (const call of calls) {
    const file = fromCall(call);
    if (file) byPath.set(file.path, file);
  }
  return [...byPath.values()];
}

/**
 * Diff availability for the expand affordance. Tool-result diffs are
 * KNOWN to be diffs (the backend embedded them), so the strict add+del+hunk
 * prose guard does not apply — a header pair suffices. This keeps pure
 * additions (new-file writes, `before=""`) renderable.
 */
export function hasRenderableDiff(file: ModifiedFile): boolean {
  if (file.diffText === null) return false;
  return /^--- |^\+\+\+ |^diff /m.test(file.diffText);
}
