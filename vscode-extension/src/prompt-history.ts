/**
 * A3 — shared prompt history (~/.config/r4/prompt-history.jsonl, cap 50,
 * dedup, tmp+rename atomic write). One file across all r4 surfaces: console
 * (v3), companion TUI (tui-js), VS Code webview (this module). Semantics
 * mirrored verbatim from packages/r4-tui-v3/src/lib/prompt-history.ts, so a
 * prompt typed anywhere appears in every surface. Pure node:fs — no vscode
 * imports so the unit test can load it directly.
 */
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join as joinPath } from "node:path";

export const PROMPT_HISTORY_LIMIT = 50;

type State = { lines: string[] };

let cached: State | null = null;
let cachedPath: string | null = null;

export function promptHistoryPath(): string {
  const home = process.env.R4_CONFIG_HOME || joinPath(homedir(), ".config", "r4");
  mkdirSync(home, { recursive: true });
  return joinPath(home, "prompt-history.jsonl");
}

function load(): State {
  const path = promptHistoryPath();
  if (cached && cachedPath === path) return cached;
  const lines: string[] = [];
  if (existsSync(path)) {
    try {
      const raw = readFileSync(path, "utf8");
      for (const line of raw.split("\n")) {
        if (!line.trim()) continue;
        try {
          const entry = JSON.parse(line) as { text?: string };
          if (typeof entry.text === "string" && entry.text.trim()) lines.push(entry.text.trim());
        } catch {
          // tolerate older plain-text rows
          if (line.trim()) lines.push(line.trim());
        }
      }
    } catch {
      // unreadable — start empty
    }
  }
  cached = { lines: lines.slice(-PROMPT_HISTORY_LIMIT) };
  cachedPath = path;
  return cached;
}

/** Append a submitted prompt: dedups prior occurrences, pushes to the tail,
 *  caps at 50, rewrites the file atomically. */
export function appendPromptHistory(text: string): void {
  const trimmed = text.trim();
  if (!trimmed) return;
  const state = load();
  const deduped = state.lines.filter((line) => line !== trimmed);
  deduped.push(trimmed);
  const capped = deduped.slice(-PROMPT_HISTORY_LIMIT);
  state.lines = capped;
  const tmp = `${promptHistoryPath()}.tmp`;
  const body = capped.map((line) => JSON.stringify({ text: line })).join("\n") + "\n";
  writeFileSync(tmp, body, "utf8");
  renameSync(tmp, promptHistoryPath());
  cached = { lines: capped };
}

/** Current history view, oldest → newest. */
export function promptHistory(): string[] {
  return load().lines.slice();
}

/** Test hook: drop the module-level cache. */
export function _resetPromptHistoryCache(): void {
  cached = null;
  cachedPath = null;
}
