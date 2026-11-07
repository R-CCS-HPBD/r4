/** Truncate a string to max length, appending an ellipsis when needed. */
export function truncate(text: string, max: number): string {
  if (max <= 0) return "";
  if (text.length <= max) return text;
  if (max === 1) return "…";
  return `${text.slice(0, max - 1)}…`;
}

/**
 * Word-boundary-aware truncation (V3 visual wave): never cuts mid-token;
 * over-budget text gets "…" appended at the last complete word.
 */
export function truncateSmart(text: string, max: number): string {
  if (max <= 0) return "";
  if (text.length <= max) return text;
  if (max === 1) return "…";
  const head = text.slice(0, max - 1);
  const lastSpace = head.lastIndexOf(" ");
  const stem = lastSpace > Math.floor(max / 2) ? head.slice(0, lastSpace) : head;
  return `${stem}…`;
}

/** Word-wrap into up to maxLines of width cols (no ellipsis mid-word when possible). */
export function wrapText(text: string, cols: number, maxLines = 8): string[] {
  const words = text.replace(/\s+/g, " ").trim().split(" ");
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    if (!w) continue;
    const next = cur ? `${cur} ${w}` : w;
    if (next.length <= cols) {
      cur = next;
      continue;
    }
    if (cur) lines.push(cur);
    if (lines.length >= maxLines) return lines;
    if (w.length <= cols) {
      cur = w;
    } else {
      // Hard-break long tokens
      let rest = w;
      while (rest.length > cols && lines.length < maxLines) {
        lines.push(rest.slice(0, cols));
        rest = rest.slice(cols);
      }
      cur = rest;
    }
  }
  if (cur && lines.length < maxLines) lines.push(cur);
  return lines.length > 0 ? lines : [""];
}

/** Compact connection/status words for tight status strips. */
export function shortStatus(state: string): string {
  switch (state) {
    case "disconnected":
      return "disc.";
    case "connected":
      return "ok";
    case "connecting":
      return "…";
    case "degraded":
      return "deg.";
    default:
      return truncate(state, 8);
  }
}
