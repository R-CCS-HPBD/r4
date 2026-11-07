/**
 * B4 — external $EDITOR for the chat draft (Ctrl+E).
 *
 * Draft text goes to a temp file, $VISUAL (or $EDITOR) edits it synchronously
 * with the renderer suspended, and the result adopts into the active draft
 * via the pending-draft seq path (chat.tsx adoption effect — adopted not
 * consumed, so other surfaces' assertions keep working).
 */

import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export interface EditorChoice {
  editor: string;
  source: "VISUAL" | "EDITOR";
}

/** $VISUAL wins over $EDITOR; neither set → null (fall back to notice only). */
export function editorForEnv(env: { VISUAL?: string; EDITOR?: string }): EditorChoice | null {
  const visual = env.VISUAL?.trim();
  if (visual) return { editor: visual, source: "VISUAL" };
  const editor = env.EDITOR?.trim();
  if (editor) return { editor, source: "EDITOR" };
  return null;
}

export interface ExternalEditorResult {
  ok: boolean;
  /** Edited text (only when ok). */
  text?: string;
  /** Human-readable failure reason (only when !ok). */
  error?: string;
}

/**
 * Open `initialText` in an external editor. The renderer is suspended for the
 * editor's lifetime so it's not fighting the TUI for the terminal.
 * Synchronous by design — the chat input is the only thing waiting.
 */
export function openInExternalEditor(opts: {
  initialText: string;
  editor: string;
  suspend: () => void;
  resume: () => void;
}): ExternalEditorResult {
  const dir = mkdtempSync(join(tmpdir(), "r4-editor-"));
  const file = join(dir, "draft.md");
  try {
    writeFileSync(file, opts.initialText);
    opts.suspend();
    try {
      const res = spawnSync(opts.editor, [file], {
        stdio: "inherit",
        env: process.env as Record<string, string>,
      });
      if (res.error) return { ok: false, error: String(res.error) };
      if (res.status !== 0) return { ok: false, error: `${opts.editor} exited with status ${res.status}` };
      return { ok: true, text: readFileSync(file, "utf8").replace(/\s+$/, "") };
    } catch (error) {
      return { ok: false, error: error instanceof Error ? error.message : String(error) };
    } finally {
      opts.resume();
    }
  } finally {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      // best-effort cleanup
    }
  }
}
