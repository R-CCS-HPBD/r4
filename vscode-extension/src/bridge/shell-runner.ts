/**
 * `!cmd` in the agent view (ADR-103 VP-2b): the user's own shell runs the
 * line in the workspace, as the console's `local-shell.ts` does with Bun.
 * The result is shown and relayed through the console's `lib/shell-relay.ts`.
 */

import { spawn, type ChildProcess } from "node:child_process";

import type { ShellRunResult } from "../../../packages/r4-tui-v3/src/lib/shell-relay";

export interface ShellRunner {
  readonly running: boolean;
  run(command: string): Promise<ShellRunResult>;
  /** SIGINT every running command's process group; returns how many. */
  stop(): number;
}

const KILL_GRACE_MS = 2_000;

function signalGroup(proc: ChildProcess, signal: NodeJS.Signals): void {
  try {
    if (proc.pid) process.kill(-proc.pid, signal);
  } catch {
    proc.kill(signal); // no group (already reaped): the shell itself
  }
}

export class NodeShellRunner implements ShellRunner {
  private readonly procs = new Map<ChildProcess, { interrupted: boolean }>();

  constructor(
    private readonly workspace: string,
    private readonly shell = process.env.SHELL || "/bin/sh",
  ) {}

  get running(): boolean {
    return this.procs.size > 0;
  }

  run(command: string): Promise<ShellRunResult> {
    const started = Date.now();
    // detached: its own process group, so a stop reaches the whole command.
    const proc = spawn(this.shell, ["-lc", command], { cwd: this.workspace, env: process.env, detached: true });
    const state = { interrupted: false };
    this.procs.set(proc, state);
    let stdout = "";
    let stderr = "";
    proc.stdout?.setEncoding("utf8").on("data", (chunk: string) => (stdout += chunk));
    proc.stderr?.setEncoding("utf8").on("data", (chunk: string) => (stderr += chunk));
    return new Promise((resolve) => {
      const finish = (exitCode: number) => {
        this.procs.delete(proc);
        resolve({ command, exitCode, stdout, stderr, durationMs: Date.now() - started, interrupted: state.interrupted || undefined });
      };
      proc.once("error", (err) => {
        stderr += String(err.message);
        finish(127);
      });
      proc.once("close", (code, signal) => finish(code ?? (signal ? 130 : 1)));
    });
  }

  stop(): number {
    for (const [proc, state] of this.procs) {
      state.interrupted = true;
      signalGroup(proc, "SIGINT");
      setTimeout(() => {
        if (this.procs.has(proc)) signalGroup(proc, "SIGKILL");
      }, KILL_GRACE_MS).unref?.();
    }
    return this.procs.size;
  }
}
