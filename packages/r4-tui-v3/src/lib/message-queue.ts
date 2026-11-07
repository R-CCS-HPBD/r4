/**
 * Busy-queue labels and queued-message badge helpers (pure).
 * Ported from companion-tui-js message-queue.mjs + queued-marker.mjs.
 */

export const QUEUED_BADGE = "QUEUED";

/** Format the live activity label when chat messages are waiting. */
export function formatBusyLabel(base: string, queuedCount: number): string {
  const label = String(base || "thinking").trim() || "thinking";
  const count = Math.max(0, Number(queuedCount) || 0);
  if (count === 0) return label;
  const noun = count === 1 ? "message" : "messages";
  return `${label} · ${count} ${noun} queued`;
}

/** True for normal chat turns (not slash commands). */
export function isChatMessage(text: string): boolean {
  const trimmed = String(text || "").trim();
  return Boolean(trimmed) && !trimmed.startsWith("/");
}

export const BUSY_WHILE_SLASH_MESSAGE =
  "busy — wait for the current turn; chat messages queue automatically";

export const STOPPED_MESSAGE = "(Generation stopped.)";

export interface QueuedItem {
  kind?: string;
  queued?: boolean;
}

/** Badge text for one static transcript item, or null when nothing to mark. */
export function queuedMarkerFor(item: QueuedItem | null | undefined): string | null {
  if (!item || item.kind !== "user") return null;
  return item.queued === true ? QUEUED_BADGE : null;
}
