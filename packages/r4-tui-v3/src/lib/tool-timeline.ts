/**
 * Live tool timeline helpers (ported from tui-js live-tool-timeline.mjs).
 * Rendering stays in layout/activity-console.tsx; these are the pure pieces.
 */

export function truncate(text: unknown, maxLen = 40): string {
  const str = String(text || "");
  if (str.length <= maxLen) return str;
  return `${str.slice(0, maxLen - 1)}…`;
}

export type ToolTimelineStatus = "success" | "blocked" | "error" | "errored" | "running";

export function timelineStatusIcon(status: string): string {
  if (status === "success") return "✓";
  if (status === "blocked") return "⊘";
  if (status === "error" || status === "errored") return "✗";
  return "→";
}

export function formatDuration(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/**
 * Icon for a workspace tool event (action + detail), as shown in the
 * Tool Calls tab. ◐ in-flight · ✓ done · ⊘ blocked.
 */
export function toolActionIcon(action: string, detail?: string): string {
  if (/blocked|⛔/i.test(detail ?? "")) return "⊘";
  if (action === "tool_call") return "◐";
  if (action === "tool_result") return "✓";
  return "•";
}
