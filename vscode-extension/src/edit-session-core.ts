import { createHash } from "node:crypto";

/**
 * Pure edit-session core: pending-edit store, line stats, anchored edit
 * application. Zero `vscode` imports so the node:test harness can esbuild-bundle
 * and exercise this module directly; the vscode shell lives in
 * `edit-session.ts`.
 */

export type PendingEditStatus = "pending" | "accepted" | "rejected";

/** One staged file edit awaiting user review. */
export interface PendingEdit {
  /** Stable per-edit id (content hash + counter). */
  id: string;
  /** Chat/agent session that produced the edit. */
  sessionId: string;
  /** ACP tool_call_id when the edit came from a governed tool call. */
  agentCallId?: string;
  /** Canonical absolute path on disk. */
  filePath: string;
  /** Workspace-relative path for display. */
  relPath: string;
  /** Disk content before edit; undefined when the file does not exist yet. */
  originalText?: string;
  /** Proposed new content. */
  modifiedText: string;
  /** Line-level change counts vs. originalText. */
  stats: EditStats;
  status: PendingEditStatus;
  /** RFC3339 timestamp of staging. */
  stagedAt: string;
}

export interface EditStats {
  added: number;
  removed: number;
}

export interface StageEditInput {
  sessionId: string;
  agentCallId?: string;
  filePath: string;
  relPath: string;
  originalText?: string;
  modifiedText: string;
}

/** One anchored text replacement proposed by the agent. */
export interface AnchoredEdit {
  /** Exact substring to replace; empty string means append/insert whole file. */
  oldString: string;
  newString: string;
}

export type EditSessionEvent =
  | { kind: "staged"; edit: PendingEdit }
  | { kind: "resolved"; edit: PendingEdit }
  | { kind: "cleared"; sessionId: string; cleared: PendingEdit[] };

export type EditSessionListener = (event: EditSessionEvent) => void;

function splitLines(text: string): string[] {
  if (text.length === 0) return [];
  const lines = text.split("\n");
  // A trailing newline terminates the last line — it is not an extra empty
  // line, so drop the phantom element. "a\n" → ["a"], "a\n\n" → ["a", ""].
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/**
 * Line-level added/removed counts. Small inputs only — staged edits are
 * file-sized, not repo-sized. Symmetric: adding a line counts `added`,
 * removing counts `removed`, context lines are free.
 */
export function computeEditStats(originalText: string | undefined, modifiedText: string): EditStats {
  const oldLines = splitLines(originalText ?? "");
  const newLines = splitLines(modifiedText);
  const degenerate = degenerateStats(oldLines.length, newLines.length);
  if (degenerate) return degenerate;

  const { oldMid, newMid, commonAffix } = trimCommonAffixes(oldLines, newLines);
  const midDegenerate = degenerateStats(oldMid.length, newMid.length);
  if (midDegenerate) return midDegenerate;

  const common = commonAffix + longestRepeatedSubsequenceLength(oldMid, newMid);
  return { added: newLines.length - common, removed: oldLines.length - common };
}

function degenerateStats(oldCount: number, newCount: number): EditStats | undefined {
  if (oldCount === 0 && newCount === 0) return { added: 0, removed: 0 };
  if (oldCount === 0) return { added: newCount, removed: 0 };
  if (newCount === 0) return { added: 0, removed: oldCount };
  return undefined;
}

/** Strip equal prefix/suffix lines; returns the differing mid-sections. */
function trimCommonAffixes(
  oldLines: string[],
  newLines: string[],
): { oldMid: string[]; newMid: string[]; commonAffix: number } {
  let prefix = 0;
  while (prefix < oldLines.length && prefix < newLines.length && oldLines[prefix] === newLines[prefix]) {
    prefix += 1;
  }
  let oldEnd = oldLines.length;
  let newEnd = newLines.length;
  while (oldEnd > prefix && newEnd > prefix && oldLines[oldEnd - 1] === newLines[newEnd - 1]) {
    oldEnd -= 1;
    newEnd -= 1;
  }
  return {
    oldMid: oldLines.slice(prefix, oldEnd),
    newMid: newLines.slice(prefix, newEnd),
    commonAffix: prefix + (oldLines.length - oldEnd),
  };
}

/** Length of the longest common subsequence between the mid-sections. */
function longestRepeatedSubsequenceLength(oldMid: string[], newMid: string[]): number {
  // Hunt–Szymanski: map each old line to its positions (descending), compress
  // new lines to those position lists, then longest-increasing-subsequence
  // gives the LCS length in O(n log n). Descending positions are required for
  // correctness with duplicate lines.
  const positions = new Map<string, number[]>();
  oldMid.forEach((line, i) => {
    const list = positions.get(line) ?? [];
    list.unshift(i);
    positions.set(line, list);
  });
  const compressed = newMid.flatMap((line) => positions.get(line) ?? []);
  return longestIncreasingSubsequenceLength(compressed);
}

function longestIncreasingSubsequenceLength(values: number[]): number {
  const tails: number[] = [];
  for (const value of values) {
    let lo = 0;
    let hi = tails.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (tails[mid] < value) lo = mid + 1;
      else hi = mid;
    }
    tails[lo] = value;
  }
  return tails.length;
}

/**
 * Apply anchored replacements to `text`. Fails loudly when an anchor is
 * absent or ambiguous — the agent must retry with more context rather than
 * silently corrupting the file.
 */
export function applyAnchoredEdits(text: string, edits: AnchoredEdit[]): string {
  let result = text;
  for (const edit of edits) {
    if (typeof edit.newString !== "string") throw new Error("newString must be a string");
    if (typeof edit.oldString !== "string") throw new Error("oldString must be a string");
    if (edit.oldString === "") {
      if (result !== "") throw new Error("Empty oldString is only valid when creating a file");
      result = edit.newString;
      continue;
    }
    const first = result.indexOf(edit.oldString);
    if (first === -1) {
      throw new Error("Edit anchor not found; re-read the file and retry with exact context");
    }
    if (result.indexOf(edit.oldString, first + 1) !== -1) {
      throw new Error("Edit anchor is ambiguous; include more surrounding context");
    }
    result = result.slice(0, first) + edit.newString + result.slice(first + edit.oldString.length);
  }
  return result;
}

export class EditSessionStore {
  private readonly pending = new Map<string, PendingEdit>();
  private readonly listeners = new Set<EditSessionListener>();
  private sequence = 0;

  /** One pending edit per file at a time; re-staging supersedes. */
  private readonly pendingFileIndex = new Map<string, string>();

  onDidChange(listener: EditSessionListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emit(event: EditSessionEvent): void {
    for (const listener of this.listeners) listener(event);
  }

  stage(input: StageEditInput): PendingEdit {
    if (!input.filePath) throw new Error("stage: filePath is required");
    if (input.originalText === input.modifiedText) throw new Error("stage: edit produces no changes");
    const supersededId = this.pendingFileIndex.get(input.filePath);
    if (supersededId) {
      const superseded = this.pending.get(supersededId);
      if (superseded) {
        superseded.status = "rejected";
        this.emit({ kind: "resolved", edit: { ...superseded } });
      }
      this.pending.delete(supersededId);
    }
    this.sequence += 1;
    const digest = createHash("sha256")
      .update(`${input.filePath}${input.modifiedText}${this.sequence}`)
      .digest("hex")
      .slice(0, 12);
    const edit: PendingEdit = {
      id: `edit-${digest}`,
      sessionId: input.sessionId,
      agentCallId: input.agentCallId,
      filePath: input.filePath,
      relPath: input.relPath,
      originalText: input.originalText,
      modifiedText: input.modifiedText,
      stats: computeEditStats(input.originalText, input.modifiedText),
      status: "pending",
      stagedAt: new Date().toISOString(),
    };
    this.pending.set(edit.id, edit);
    this.pendingFileIndex.set(input.filePath, edit.id);
    this.emit({ kind: "staged", edit: { ...edit } });
    return edit;
  }

  get(id: string): PendingEdit | undefined {
    return this.pending.get(id);
  }

  getForFile(filePath: string): PendingEdit | undefined {
    const id = this.pendingFileIndex.get(filePath);
    return id ? this.pending.get(id) : undefined;
  }

  listPending(sessionId?: string): PendingEdit[] {
    return [...this.pending.values()]
      .filter((edit) => edit.status === "pending")
      .filter((edit) => sessionId === undefined || edit.sessionId === sessionId)
      .map((edit) => ({ ...edit }));
  }

  /**
   * Base for a follow-up edit to the same file: prefer the already-staged
   * modified text so compound agent edits diff against the newest proposal.
   */
  baseTextFor(filePath: string, diskText: string | undefined): string | undefined {
    const pending = this.getForFile(filePath);
    return pending ? pending.modifiedText : diskText;
  }

  resolve(id: string, status: Exclude<PendingEditStatus, "pending">): PendingEdit {
    const edit = this.pending.get(id);
    if (!edit) throw new Error(`Unknown pending edit: ${id}`);
    if (edit.status !== "pending") throw new Error(`Edit ${id} already ${edit.status}`);
    edit.status = status;
    this.pending.delete(id);
    if (this.pendingFileIndex.get(edit.filePath) === id) this.pendingFileIndex.delete(edit.filePath);
    this.emit({ kind: "resolved", edit: { ...edit } });
    return { ...edit };
  }

  /** Discard every pending edit for a session (abort / cancel / shutdown). */
  clearSession(sessionId: string): PendingEdit[] {
    const cleared: PendingEdit[] = [];
    for (const [id, edit] of [...this.pending]) {
      if (edit.sessionId !== sessionId) continue;
      edit.status = "rejected";
      cleared.push({ ...edit });
      this.pending.delete(id);
      if (this.pendingFileIndex.get(edit.filePath) === id) this.pendingFileIndex.delete(edit.filePath);
    }
    if (cleared.length > 0) this.emit({ kind: "cleared", sessionId, cleared });
    return cleared;
  }
}
