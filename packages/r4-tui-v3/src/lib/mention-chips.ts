/**
 * A6: extmark-style chip bookkeeping for @-mention chips.
 *
 * A chip is the exact mention token inserted by the A1 completion
 * (`@<path>`). A single-character deletion that lands *inside* a chip
 * removes the whole chip atomically (plus one adjacent trailing space),
 * instead of leaving a broken partial path. Deletions elsewhere pass
 * through untouched.
 *
 * The composer tracks inserted chip tokens; this module resolves their
 * ranges on each edit and decides whether to collapse.
 */

export interface ChipCollapseResult {
  /** Text to place in the draft (either `next`, or prev with the chip removed). */
  text: string;
  /** Remaining chip tokens (the collapsed chip is dropped). */
  chips: string[];
  /** True when a chip was collapsed by this edit. */
  collapsed: boolean;
}

interface ChipRange {
  token: string;
  start: number;
  end: number; // exclusive
}

/** Locate chip token ranges in `text`, matched in insertion order. */
function locateChips(text: string, chips: string[]): ChipRange[] {
  const ranges: ChipRange[] = [];
  let cursor = 0;
  for (const token of chips) {
    const idx = text.indexOf(token, cursor);
    if (idx === -1) continue; // chip already edited away — drop silently
    ranges.push({ token, start: idx, end: idx + token.length });
    cursor = idx + token.length;
  }
  return ranges;
}

/**
 * Apply chip-collapse bookkeeping to a single edit step.
 * `prev` is the previous draft, `next` the new draft, `chips` the tracked
 * mention tokens in insertion order.
 */
export function collapseChipsOnBackspace(
  prev: string,
  next: string,
  chips: string[],
): ChipCollapseResult {
  // Only handle exactly-one-character removals; anything larger (selection
  // deletes, pastes, replacements) passes through with chips re-synced.
  if (chips.length === 0 || next.length !== prev.length - 1) {
    return { text: next, chips, collapsed: false };
  }
  // Common prefix length → position of the removed character in `prev`.
  let removedAt = 0;
  while (removedAt < next.length && prev[removedAt] === next[removedAt]) {
    removedAt += 1;
  }
  const ranges = locateChips(prev, chips);
  const hit = ranges.find((r) => removedAt >= r.start && removedAt < r.end);
  if (!hit) {
    return { text: next, chips: pruneChips(next, chips), collapsed: false };
  }
  // Collapse: remove the whole chip, and one trailing space if present.
  let end = hit.end;
  if (prev[end] === " ") end += 1;
  const text = prev.slice(0, hit.start) + prev.slice(end);
  const remaining = chips.filter((token) => token !== hit.token);
  return { text, chips: pruneChips(text, remaining), collapsed: true };
}

/** Drop chips that no longer appear verbatim in the text. */
function pruneChips(text: string, chips: string[]): string[] {
  return chips.filter((token) => text.includes(token));
}
