/**
 * A3 — shared prompt history (~/.config/r4/prompt-history.jsonl, cap 50,
 * dedup). One file across all r4 surfaces so a prompt typed on the console is
 * still there on the TUI and back (opencode prompt/history.tsx paraphrase).
 */
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join as joinPath } from "node:path";

import { carriesSecret } from "./secret-redact";

const HISTORY_LIMIT = 50;

type State = { lines: string[] };

let cached: State | null = null;
let cachedPath: string | null = null;

function historyPath(): string {
  const home = process.env.R4_CONFIG_HOME || joinPath(homedir(), ".config", "r4");
  // Prompts can be sensitive: a directory this creates is owner-only
  // (c2 security F8, 2026-10-21 — the history was 0644 in a 0755 dir).
  mkdirSync(home, { recursive: true, mode: 0o700 });
  return joinPath(home, "prompt-history.jsonl");
}

/** One stored row → its prompt text ("" when empty or malformed). */
function rowText(line: string): string {
  try {
    const entry = JSON.parse(line) as { text?: unknown } | null;
    return typeof entry?.text === "string" ? entry.text.trim() : "";
  } catch {
    return line.trim(); // tolerate older plain-text rows
  }
}

/** Stored rows, minus any line holding a credential (written before S4):
 * it is never recalled, and the next append rewrites the file without it. */
function readRows(path: string): string[] {
  try {
    return readFileSync(path, "utf8")
      .split("\n")
      .filter((line) => line.trim())
      .map(rowText)
      .filter((text) => text && !carriesSecret(text));
  } catch {
    return []; // unreadable — start empty
  }
}

function load(): State {
  const path = historyPath();
  if (cached && cachedPath === path) return cached;
  const lines = existsSync(path) ? readRows(path) : [];
  cached = { lines: lines.slice(-HISTORY_LIMIT) };
  cachedPath = path;
  return cached;
}

/**
 * Append a new prompt line; dedups adjacent repeats; caps the file.
 * A line holding a credential (`/login <token>`, `--api-key <key>`) is never
 * written (S4, onboarding plan 2026-10-15): returns false, and the caller
 * keeps it out of its in-memory recall too.
 */
export function appendPromptHistory(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed || carriesSecret(trimmed)) return false;
  const state = load();
  const deduped = state.lines.filter((l) => l !== trimmed);
  deduped.push(trimmed);
  const capped = deduped.slice(-HISTORY_LIMIT);
  state.lines = capped;
  const tmp = `${historyPath()}.tmp`;
  const body = capped.map((l) => JSON.stringify({ text: l })).join("\n") + "\n";
  writeFileSync(tmp, body, { encoding: "utf8", mode: 0o600 });
  // `mode` applies only when the file is created: a stale, wider .tmp keeps
  // its bits, and the rename replaces an older 0644 history with this file.
  chmodSync(tmp, 0o600);
  renameSync(tmp, historyPath());
  return true;
}

/** Current history view, oldest → newest. */
export function promptHistory(): string[] {
  return load().lines.slice();
}
