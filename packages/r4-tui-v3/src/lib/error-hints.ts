/**
 * Human next-action hints for error bubbles.
 * Ported from companion-tui-js error-hints.mjs.
 *
 * An error should tell you what to *do* next, not just what broke.
 * Rules are broad and ordered — first match wins.
 */
const RULES: Array<[RegExp, string]> = [
  [/timed?\s*out|timeout/i, "retry — or switch endpoint with /model if it persists"],
  [/econnrefused|connection refused|fetch failed/i, "gateway unreachable — check `r4 status`"],
  [/\b401\b|unauthorized|invalid api key|token.*expired/i, "auth expired — run `r4 auth login` again"],
  // Before the generic 403 (2026-11-03): the gateway does not know this
  // project for this account — most often a /project create never registered.
  [
    /project_not_member|not a member of project/i,
    "the server does not know this project for you — /project register <id> creates it (or ask its owner to add you)",
  ],
  [/\b403\b|forbidden|permission denied/i, "permission denied — check your role or /permissions"],
  [/\b404\b|not found/i, "resource vanished — start fresh with /session new"],
  [/\b429\b|rate.?limit/i, "rate limited — wait a few seconds and retry"],
  [/\b5\d\d\b|internal server error|bad gateway|service unavailable/i, "endpoint hiccup — retry shortly, or switch with /model"],
  [/context.?length|maximum context|too long|max.?tokens/i, "context full — /clear to start fresh"],
];

/** Map an error message to a next-action hint, or null when nothing matches. */
export function hintForError(message: unknown): string | null {
  const text = String(message || "");
  for (const [pattern, hint] of RULES) {
    if (pattern.test(text)) return hint;
  }
  return null;
}
