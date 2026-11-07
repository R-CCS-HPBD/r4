/**
 * Esc stops a running turn only on a repeated tap (maintainer 2026-11-03:
 * "esc can be miss tapped very easily" — one stray Esc killed a long
 * thinking turn). The first tap arms and says how many more stop it; the
 * Nth tap within the window of the previous one stops. Ctrl+C still stops
 * on one press (the deliberate key).
 *
 * `R4_ESC_INTERRUPT_TAPS` = 1 · 2 · 3 (default 2; 1 = the old single tap).
 * The chord math is pure (`tapEsc`); the hint is one module signal that the
 * composer placeholder and the status bar's work line both read.
 */

import { createSignal } from "solid-js";

export const ESC_INTERRUPT_TAPS_ENV = "R4_ESC_INTERRUPT_TAPS";
export const ESC_INTERRUPT_DEFAULT_TAPS = 2;
/** Most time between two taps of one chord. */
export const ESC_INTERRUPT_WINDOW_MS = 1000;

/** Taps that stop a turn: the env value when it is 1..3, else the default. */
export function escInterruptTaps(env: Record<string, string | undefined> = process.env): number {
  const raw = (env[ESC_INTERRUPT_TAPS_ENV] ?? "").trim();
  return /^[1-3]$/.test(raw) ? Number(raw) : ESC_INTERRUPT_DEFAULT_TAPS;
}

export interface EscChord {
  count: number;
  lastAt: number | null;
}

export const NO_CHORD: EscChord = { count: 0, lastAt: null };

/** The chord after one tap at `now`; `fire` = this tap stops the turn. */
export function tapEsc(
  chord: EscChord,
  now: number,
  needed: number,
  windowMs: number = ESC_INTERRUPT_WINDOW_MS,
): { fire: boolean; chord: EscChord; remaining: number } {
  const fresh = chord.lastAt === null || now - chord.lastAt > windowMs;
  const count = (fresh ? 0 : chord.count) + 1;
  if (count >= needed) return { fire: true, chord: NO_CHORD, remaining: 0 };
  return { fire: false, chord: { count, lastAt: now }, remaining: needed - count };
}

/** "Esc" · "Esc Esc" · "Esc ×3": the key the placeholders name. */
export function escStopKeys(taps: number = escInterruptTaps()): string {
  if (taps <= 1) return "Esc";
  if (taps === 2) return "Esc Esc";
  return `Esc ×${taps}`;
}

/** The armed hint: how many more taps stop the turn. */
export function escArmedHint(remaining: number): string {
  return remaining === 1 ? "press Esc again to stop" : `press Esc ${remaining} more times to stop`;
}

const [hint, setHint] = createSignal("");
let chord: EscChord = NO_CHORD;
let clearTimer: ReturnType<typeof setTimeout> | null = null;
/** Taps right after a stop are swallowed (a mashed Esc must not fall
 * through to clear-draft / the rewind menu once the turn is idle). */
let swallowUntil = 0;

/** The armed hint ("" when not armed) — reactive. */
export const escInterruptHint = hint;

function disarm(): void {
  chord = NO_CHORD;
  setHint("");
  if (clearTimer) clearTimeout(clearTimer);
  clearTimer = null;
}

/** One Esc during a live turn: true when it stops the turn now. */
export function escTapStops(now: number = Date.now(), needed: number = escInterruptTaps()): boolean {
  const step = tapEsc(chord, now, needed);
  if (step.fire) {
    disarm();
    swallowUntil = now + ESC_INTERRUPT_WINDOW_MS;
    return true;
  }
  chord = step.chord;
  setHint(escArmedHint(step.remaining));
  if (clearTimer) clearTimeout(clearTimer);
  clearTimer = setTimeout(disarm, ESC_INTERRUPT_WINDOW_MS);
  return false;
}

/** True while a tap belongs to the chord that just stopped the turn. */
export function escSwallowed(now: number = Date.now()): boolean {
  return now < swallowUntil;
}

/** Tests: forget every armed or swallowed tap. */
export function resetEscInterrupt(): void {
  disarm();
  swallowUntil = 0;
}
