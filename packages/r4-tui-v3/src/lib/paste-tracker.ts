/**
 * Large-paste summarization (pattern: opencode PasteEvent extmarks, MIT).
 * Ported from companion-tui-js paste-chips.mjs.
 *
 * Small pastes pass through; large pastes become an inline placeholder chip
 * `[Pasted text #N ~L lines]` tracked here and re-inflated at submit time.
 *
 * Semantics (matches opencode's threshold):
 *   - summarize when: 3+ lines OR > 150 chars
 *   - the chip text lives INSIDE the buffer at the caret position
 *   - re-inflation replaces each chip by the paste its `#N` names — by
 *     IDENTITY, never by position: a removed, cleared or damaged chip can no
 *     longer shift a later chip onto an earlier paste (composer#1: Enter
 *     sent a discarded paste instead of the one on screen). Ids are never
 *     reused, so a stale chip never maps onto a newer paste.
 *
 * Pure (ADR-103 D-3, 2026-11-01): `paste-chips.ts` re-exports it for the
 * console and the VS Code webview's composer imports it.
 */

export const PASTE_CHIP_MIN_LINES = 3;
export const PASTE_CHIP_MIN_CHARS = 150;

const CHIP_RE = /\[Pasted text #(\d+) ~\d+ lines?\]/g;

export interface PasteTracker {
  /** Summarize one paste; `chipId` is the chip's `#N` (null: no chip). */
  ingest(raw: string | null | undefined): { textToInsert: string; chipId: number | null };
  /** Re-inflate the tracked chips in a buffer. Unknown or damaged chips survive. */
  expand(buffer: string | null | undefined): string;
  /** The chip tokens currently tracked (atomic-backspace bookkeeping). */
  tokens(): string[];
  /** Drop the pastes whose chips appear in `buffer` (that draft was cleared or sent). */
  forget(buffer: string | null | undefined): void;
  /** Number of chips tracked (status display). */
  readonly count: number;
  clear(): void;
}

export function createPasteTracker(): PasteTracker {
  const tracked = new Map<number, { full: string; token: string }>();
  let nextId = 1;
  return {
    ingest(raw) {
      if (!raw) return { textToInsert: "", chipId: null };
      const cleaned = String(raw);
      const lineCount = cleaned.includes("\n") ? cleaned.split("\n").length : 1;
      const summarize =
        lineCount >= PASTE_CHIP_MIN_LINES || cleaned.length >= PASTE_CHIP_MIN_CHARS;
      if (!summarize) return { textToInsert: cleaned, chipId: null };
      const chipId = nextId;
      nextId += 1;
      const token = `[Pasted text #${chipId} ~${lineCount} lines]`;
      tracked.set(chipId, { full: cleaned, token });
      return { textToInsert: token, chipId };
    },

    expand(buffer) {
      if (!buffer || tracked.size === 0) return String(buffer ?? "");
      return String(buffer).replace(CHIP_RE, (match, id: string) => {
        const entry = tracked.get(Number(id));
        return entry && entry.token === match ? entry.full : match;
      });
    },

    tokens() {
      return [...tracked.values()].map((entry) => entry.token);
    },

    forget(buffer) {
      const text = String(buffer ?? "");
      for (const [id, entry] of tracked) {
        if (text.includes(entry.token)) tracked.delete(id);
      }
    },

    get count() {
      return tracked.size;
    },

    clear() {
      tracked.clear();
    },
  };
}
