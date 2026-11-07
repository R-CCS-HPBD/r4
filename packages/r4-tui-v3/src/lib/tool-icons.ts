/**
 * Status icons for activity rows (ported from tui-js tool-icons.mjs).
 * Deterministic single-width glyphs only.
 */

export type ActivityStatus = "ok" | "error" | "warn" | "info" | "running";

const STATUS_ICONS: Record<ActivityStatus, string> = {
  ok: "✓",
  error: "✗",
  warn: "⚠",
  info: "ℹ",
  running: "◐",
};

export function statusIcon(status: string): string {
  return (STATUS_ICONS as Record<string, string>)[status] ?? STATUS_ICONS.info;
}
