/**
 * `/term share` — user-initiated relay of the terminal pane to the session
 * agent. Same contract as the `!cmd` relay (sendChatMessage with
 * `relay: true`, no tab naming, queues behind a running turn) because the
 * pane's PTY never passes through the tools pipeline.
 *
 * The pane is never auto-streamed: the shell is the user's raw environment
 * (secrets, huge output) — capture fires only when the user asks.
 */

import { termSession } from "./pty-session";

export const TERM_SHARE_DEFAULT_LINES = 40;
export const TERM_SHARE_MAX_LINES = 500;
export const TERM_SHARE_MAX_CHARS = 20000;

export type TermShareResult =
  | { kind: "ok"; text: string; lineCount: number; truncatedChars: boolean }
  | { kind: "no-session" }
  | { kind: "empty" };

/** Capture the last N lines of the pane buffer (bounds-clamped). */
export function captureTermShare(requestedLines?: number): TermShareResult {
  const n = Math.min(
    Math.max(1, Math.floor(requestedLines ?? TERM_SHARE_DEFAULT_LINES) || TERM_SHARE_DEFAULT_LINES),
    TERM_SHARE_MAX_LINES,
  );
  const raw = termSession.captureText(n);
  if (raw === null) return { kind: "no-session" };
  if (raw.trim() === "") return { kind: "empty" };
  const truncated = raw.length > TERM_SHARE_MAX_CHARS;
  const text = truncated ? raw.slice(-TERM_SHARE_MAX_CHARS) : raw;
  return { kind: "ok", text, lineCount: text.split("\n").length, truncatedChars: truncated };
}

/** Model-facing frame — same shape as local-shell's `formatShellForAgent`. */
export function formatTermShareForAgent(capture: {
  text: string;
  lineCount: number;
  truncatedChars: boolean;
}): string {
  return [
    "I am sharing the tail of my terminal pane (the `/term` PTY embedded in the TUI) — what you see below happened in MY shell, not through your tools, so it is not in your tool history.",
    `${capture.lineCount} lines${capture.truncatedChars ? " (truncated to the last 20000 chars)" : ""}. Respond to it if there is something to act on.`,
    "",
    "--- terminal ---",
    capture.text,
  ].join("\n");
}
