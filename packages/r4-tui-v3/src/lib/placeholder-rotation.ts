/**
 * D5a: dynamic placeholder rotation for the chat composer (opencode parity).
 * Rotation is time-bucketed — a light interval in the component bumps a
 * signal so the placeholder re-computes; pure logic lives here for tests.
 */

export const PLACEHOLDER_HINTS = [
  "Message R4…  (↑/↓ history, @ file, ! shell)",
  "Ask about your project, data, or models…",
  "@ to mention a file · / for commands · ! for shell",
  "/loop start <goal> to launch a research loop",
  "/knowledge show — what has the graph learned?",
] as const;

const BUCKET_MS = 6_000;

/** Rotate the hint list by time bucket; deterministic for tests.
 * R4_TUI_STILL=1 pins the first hint (P4 motion budget — decorative only). */
export function rotatingPlaceholder(
  nowMs: number,
  idleBase?: string,
  env: Record<string, string | undefined> = typeof process !== "undefined" ? process.env : {},
): string {
  const base = idleBase ?? "Message R4…";
  const hints: readonly string[] = PLACEHOLDER_HINTS;
  if (hints.length === 0) return base;
  if ((env.R4_TUI_STILL ?? "").trim() === "1") return PLACEHOLDER_HINTS[0] ?? base;
  const idx = Math.floor(nowMs / BUCKET_MS) % PLACEHOLDER_HINTS.length;
  return PLACEHOLDER_HINTS[idx] ?? base;
}

export const PLACEHOLDER_BUCKET_MS = BUCKET_MS;
