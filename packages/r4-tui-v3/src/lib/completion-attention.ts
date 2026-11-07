/**
 * B7 — Completion attention.
 * Long turns (≥20 s) deserve a delegated-work signal: terminal bell (opt-out
 * via /settings bell off) + window title `R4 ✓` until the next turn starts.
 */

export const COMPLETION_ATTENTION_MIN_MS = 20_000;

/** Pure gate used by the watcher — long, finished, and not opted out. */
export function shouldSignalCompletion(elapsedMs: number, enabled: boolean): boolean {
  return enabled && elapsedMs >= COMPLETION_ATTENTION_MIN_MS;
}

const TITLE_DONE = "R4 ✓";
const TITLE_RUNNING = "R4";
let titleDone = false;

/** Bell + DONE title (best-effort; any write failure is swallowed). */
export function signalCompletion(): void {
  try {
    // \x07 = BEL. OSC 0 = set window title.
    process.stdout.write(`\x07\x1b]0;${TITLE_DONE}\x07`);
    titleDone = true;
  } catch {
    // Never let an attention nicety break a turn.
  }
}

/** Restore the running title when the next turn starts (or when asked. */
export function restoreTitle(): void {
  if (!titleDone) return;
  try {
    process.stdout.write(`\x1b]0;${TITLE_RUNNING}\x07`);
  } catch {
    // best-effort
  }
  titleDone = false;
}

/** Completion-notification tracker: call on chat streaming transitions. */
export function makeCompletionWatcher(signal: (elapsedMs: number) => void) {
  let turnStart: number | null = null;
  return (streaming: boolean) => {
    if (streaming) {
      turnStart = Date.now();
      restoreTitle();
      return;
    }
    if (turnStart === null) return;
    const elapsed = Date.now() - turnStart;
    turnStart = null;
    signal(elapsed);
  };
}
