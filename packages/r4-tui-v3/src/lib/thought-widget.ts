/**
 * B4: thinking-part collapse widget helpers.
 * Pure — header line construction kept out of JSX for unit tests.
 */

export interface ThoughtState {
  text: string;
  open: boolean;
  expanded: boolean;
  startedAtMs: number | null;
  elapsedMs: number;
}

/** `Thought: <first-line preview> · Ns` with a live marker while streaming. */
export function thoughtHeader(state: Pick<ThoughtState, "text" | "open" | "elapsedMs">): string {
  const firstLine =
    state.text
      .split("\n")
      .map((line) => line.trim())
      .find((line) => line.length > 0) ?? "";
  const title = firstLine.length > 42 ? `${firstLine.slice(0, 41)}…` : firstLine;
  const seconds = (state.elapsedMs / 1000).toFixed(1);
  const live = state.open ? " ⏳" : "";
  const body = title ? `Thought: ${title} · ${seconds}s` : `Thought: … · ${seconds}s`;
  return `${body}${live}`;
}

/** Expanded body, capped for transcript safety. */
export function thoughtBody(text: string, maxLines = 30): string[] {
  const lines = String(text).split("\n");
  if (lines.length <= maxLines) return lines;
  return [...lines.slice(0, maxLines), `… (${lines.length - maxLines} more lines)`];
}
