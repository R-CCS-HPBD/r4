/**
 * Exit cleanup (OpenTUI issue #853 class, user report 2026-09-28): quitting
 * must destroy the renderer and stop the bridge BEFORE process.exit — the
 * terminal stayed in raw mode and every quit left garbage characters on the
 * shell. One registered cleanup, one safeExit.
 */

import { getCompanionBridge } from "../bridge";

type Cleanup = () => Promise<void> | void;

let _cleanup: Cleanup | null = null;

/** App-level: register the renderer + bridge teardown once. */
export function registerExitCleanup(fn: Cleanup): void {
  _cleanup = fn;
}

/** Run the registered cleanup then exit. Never arrives twice. */
export async function safeExit(code = 0): Promise<void> {
  const fn = _cleanup;
  _cleanup = null;
  if (fn) {
    try {
      await fn();
    } catch {
      // teardown must never block the exit itself
    }
  }
  process.exit(code);
}

/** Default cleanup: stop the bridge, then the renderer restores the shell. */
export async function defaultCleanup(bridgeStop: () => Promise<void>, destroy: () => void): Promise<void> {
  try {
    await bridgeStop();
  } catch {
    // best-effort bridge stop; renderer restore still runs
  }
  destroy();
}

/** Signals that end the console, with the shell's exit code for each.
 * SIGHUP is the terminal closing (window closed, ssh dropped): OpenTUI's
 * own handler only destroys the renderer, so with a `!` command or another
 * handle alive the console lived on headless (c2-tui 2026-10-21). */
const EXIT_SIGNALS: Array<[NodeJS.Signals, number]> = [
  ["SIGINT", 130],
  ["SIGTERM", 143],
  ["SIGHUP", 129],
];

/** Route every exit signal to `onSignal(code)`; returns the uninstaller. */
export function installExitSignals(onSignal: (code: number) => void): () => void {
  const handlers = EXIT_SIGNALS.map(([signal, code]) => [signal, () => onSignal(code)] as const);
  for (const [signal, handler] of handlers) process.on(signal, handler);
  return () => {
    for (const [signal, handler] of handlers) process.off(signal, handler);
  };
}

export { getCompanionBridge };
