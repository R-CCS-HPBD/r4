/**
 * The console's Esc ladder in the composer (ADR-103 VP-2d): Esc while a reply
 * streams stops it; on an empty composer a second Esc within the window opens
 * `/rewind` (bridge backend). Popups close first (the composer handles those
 * before asking here). Pure, so it is tested without React.
 */

export const ESC_DOUBLE_MS = 600;

export interface EscState {
  running: boolean;
  empty: boolean;
  canRewind: boolean;
  /** When the last lone Esc armed the rewind (0 = not armed). */
  armedAt: number;
  now: number;
}

export type EscAction = 'stop' | 'rewind' | 'arm' | 'none';

export function escStep(s: EscState): { action: EscAction; armedAt: number } {
  if (s.running) return { action: 'stop', armedAt: 0 };
  if (!s.empty || !s.canRewind) return { action: 'none', armedAt: 0 };
  if (s.armedAt && s.now - s.armedAt <= ESC_DOUBLE_MS) return { action: 'rewind', armedAt: 0 };
  return { action: 'arm', armedAt: s.now };
}
