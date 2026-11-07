/**
 * Local shell mode (`!` prefix) — opencode parity: run the command in the
 * user's shell from the TUI process, show the FULL output in the transcript,
 * and relay the full result into the chat as a turn so the agent sees the
 * output and responds to it (2026-10-01: preview cap removed, model ingest
 * added — the earlier 12-line preview with no model visibility made `!cmd`
 * output impossible to reason over in-session).
 */
import { resolveWorkspace } from "./resolve-python";
import type { ShellRunResult } from "./shell-relay";

// The pure formatting moved to ./shell-relay (ADR-103 D-3, 2026-10-31);
// re-exported so every existing import keeps working.
export {
  formatShellForAgent,
  formatShellPreview,
  maskShellResult,
  maskShellText,
  shellDisplayCommand,
  shellEcho,
  type ShellRunResult,
} from "./shell-relay";


type ShellProc = ReturnType<typeof Bun.spawn>;
/** `!` commands in flight; Ctrl+C stops them (composer#11: it could only
 *  arm the app exit, so a hung `!tail -f` was stoppable only by quitting). */
const running = new Map<ShellProc, { interrupted: boolean }>();
const KILL_GRACE_MS = 2000;

export function localShellRunning(): boolean {
  return running.size > 0;
}

/** SIGINT every running `!` command's process group (the shell AND its
 *  children, like a terminal's Ctrl+C); SIGKILL whatever ignores it. */
export function stopLocalShells(): number {
  for (const [proc, state] of running) {
    state.interrupted = true;
    signalGroup(proc, "SIGINT");
    setTimeout(() => {
      if (running.has(proc)) signalGroup(proc, "SIGKILL");
    }, KILL_GRACE_MS).unref?.();
  }
  return running.size;
}

/** Quitting with a `!` command still running (c2-tui 2026-10-21): it runs
 *  detached in its own process group, so nothing else reaps it — `!tail -f`
 *  or a long job outlived the console as an orphan. SIGTERM the group, give
 *  it `graceMs` to finish, then SIGKILL whatever is left. */
export async function stopLocalShellsForExit(graceMs = 500): Promise<void> {
  const procs = [...running.keys()];
  if (procs.length === 0) return;
  for (const proc of procs) {
    running.get(proc)!.interrupted = true;
    signalGroup(proc, "SIGTERM");
  }
  await Promise.race([Promise.all(procs.map((p) => p.exited)), Bun.sleep(graceMs)]);
  for (const proc of procs) signalGroup(proc, "SIGKILL");
}

function signalGroup(proc: ShellProc, signal: NodeJS.Signals): void {
  try {
    process.kill(-proc.pid, signal);
  } catch {
    proc.kill(signal); // no group (already reaped): the shell itself
  }
}

export async function runLocalShell(command: string): Promise<ShellRunResult> {
  const started = Date.now();
  const shell = process.env.SHELL || "/bin/zsh";
  // detached: its own process group, so Ctrl+C can stop the whole command.
  const proc = Bun.spawn([shell, "-lc", command], {
    stdout: "pipe",
    stderr: "pipe",
    cwd: resolveWorkspace(),
    env: process.env,
    detached: true,
  });
  const state = { interrupted: false };
  running.set(proc, state);
  try {
    const [stdout, stderr, exitCode] = await Promise.all([
      new Response(proc.stdout as ReadableStream).text(),
      new Response(proc.stderr as ReadableStream).text(),
      proc.exited,
    ]);
    const durationMs = Date.now() - started;
    return { command, exitCode, stdout, stderr, durationMs, interrupted: state.interrupted || undefined };
  } finally {
    running.delete(proc);
  }
}
