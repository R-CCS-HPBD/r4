/**
 * Which turn a transcript message belongs to (VP-2b), as the console's
 * `turnOfMessage` reads it: a user message carries the turn it opened; an
 * answer belongs to the nearest user message above it.
 */

import type { TranscriptEntry } from '@/state/store';

export function turnOfEntry(entries: readonly TranscriptEntry[], index: number): string | undefined {
  for (let i = index; i >= 0; i -= 1) {
    const entry = entries[i];
    if (entry?.kind !== 'msg') continue;
    if (entry.role === 'You') return entry.turnId;
  }
  return undefined;
}

/** True when no other answer of the same turn follows `index` (citations
 * and save belong to a turn's final answer). */
export function isFinalAnswer(entries: readonly TranscriptEntry[], index: number): boolean {
  for (let i = index + 1; i < entries.length; i += 1) {
    const entry = entries[i];
    if (entry?.kind !== 'msg') continue;
    return entry.role === 'You';
  }
  return true;
}
