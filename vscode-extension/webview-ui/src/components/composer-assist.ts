/**
 * Composer helpers of the bridge backend (ADR-103 VP-2b part 2), as the
 * console's composer has them:
 *   - `@` file mentions: candidates ranked by the host over the workspace,
 *     ↑↓ to pick, Tab/Enter inserts `@path` (a `#L10-L20` range is kept);
 *   - large pastes (3+ lines or 150+ chars) become a `[Pasted text #N ~L lines]`
 *     chip, re-inflated when the message is sent;
 *   - one Backspace inside a chip removes the whole chip.
 * The rules are the console's own modules (`lib/console-shared.ts`).
 */

import { useEffect, useMemo, useRef, useState, type ClipboardEvent, type KeyboardEvent } from 'react';
import { post } from '@/lib/acp-bridge';
import { collapseChipsOnBackspace, createPasteTracker, extractMentionQuery } from '@/lib/console-shared';
import { useApp } from '@/state/store';

export interface MentionAssist {
  open: boolean;
  items: string[];
  index: number;
  accept(index: number): void;
  /** True when the key belonged to the popup. */
  onKeyDown(event: KeyboardEvent): boolean;
}

const MENTION_KEYS = new Set(['ArrowUp', 'ArrowDown', 'Tab', 'Enter', 'Escape']);

export function useMentions(text: string, setText: (text: string) => void, onChip: (token: string) => void): MentionAssist {
  const { extras } = useApp();
  const [index, setIndex] = useState(0);
  const [dismissedFor, setDismissedFor] = useState<string | null>(null);
  const query = useMemo(() => extractMentionQuery(text), [text]);
  useEffect(() => {
    if (query) post({ type: 'mentionQuery', query: query.query });
  }, [query?.query]);
  const items = query && extras.mentions.query === query.query ? extras.mentions.items : [];
  const open = Boolean(query) && items.length > 0 && dismissedFor !== query?.query;
  const safeIndex = Math.min(index, Math.max(0, items.length - 1));

  function accept(at: number): void {
    const path = items[at];
    if (!path || !query) return;
    const token = `@${path}${query.range}`;
    setText(`${text.slice(0, query.start)}${token} `);
    onChip(token);
    setIndex(0);
  }

  function onKeyDown(event: KeyboardEvent): boolean {
    if (!open || !MENTION_KEYS.has(event.key)) return false;
    event.preventDefault();
    if (event.key === 'ArrowUp') setIndex(Math.max(0, safeIndex - 1));
    else if (event.key === 'ArrowDown') setIndex(Math.min(items.length - 1, safeIndex + 1));
    else if (event.key === 'Escape') setDismissedFor(query?.query ?? null);
    else accept(safeIndex);
    return true;
  }

  return { open, items, index: safeIndex, accept, onKeyDown };
}

export interface ChipAssist {
  /** The draft after an edit: a deletion inside a chip removes the chip. */
  onEdit(prev: string, next: string): string;
  /** A large paste becomes a chip at the caret (returns true when handled). */
  onPaste(event: ClipboardEvent<HTMLTextAreaElement>, text: string, setText: (text: string) => void): boolean;
  /** Track an inserted `@path` token as an atomic chip. */
  trackMention(token: string): void;
  /** The draft as the agent should get it (paste chips re-inflated); forgets them. */
  expandForSend(text: string): string;
}

export function useChips(): ChipAssist {
  const tracker = useRef(createPasteTracker());
  const mentions = useRef<string[]>([]);

  return {
    onEdit(prev, next) {
      const chips = [...mentions.current, ...tracker.current.tokens()];
      const result = collapseChipsOnBackspace(prev, next, chips);
      mentions.current = mentions.current.filter((token) => result.text.includes(token));
      return result.text;
    },
    onPaste(event, text, setText) {
      const raw = event.clipboardData.getData('text');
      const { textToInsert, chipId } = tracker.current.ingest(raw);
      if (chipId === null) return false;
      event.preventDefault();
      const el = event.currentTarget;
      const start = el.selectionStart ?? text.length;
      const end = el.selectionEnd ?? text.length;
      setText(`${text.slice(0, start)}${textToInsert}${text.slice(end)}`);
      return true;
    },
    trackMention(token) {
      mentions.current = [...mentions.current, token];
    },
    expandForSend(text) {
      const full = tracker.current.expand(text);
      tracker.current.forget(text);
      mentions.current = [];
      return full;
    },
  };
}
