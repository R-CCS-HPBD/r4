/**
 * User status line (Claude-Code parity `/statusline`): a shell command in
 * ${R4_CONFIG_HOME or ~/.config/r4}/statusline. The status bar runs it every
 * few seconds (2 s timeout) with R4_* context variables and shows the first
 * line of its stdout as a chip. Missing/empty file = no chip.
 */

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export const STATUSLINE_MAX_CHARS = 60;

export function statuslinePath(home: string = homedir()): string {
  const base = process.env.R4_CONFIG_HOME ?? join(home, ".config", "r4");
  return join(base, "statusline");
}

export function readStatuslineCommand(path: string = statuslinePath()): string {
  try {
    if (!existsSync(path)) return "";
    return (readFileSync(path, "utf8").split("\n")[0] ?? "").trim();
  } catch {
    return "";
  }
}

export function writeStatuslineCommand(command: string, path: string = statuslinePath()): void {
  const trimmed = command.trim();
  if (!trimmed || trimmed === "off") {
    rmSync(path, { force: true });
    return;
  }
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${trimmed}\n`, "utf8");
}

export interface StatuslineContext {
  model: string;
  project: string;
  session: string;
  permission: string;
}

function statuslineEnv(ctx: StatuslineContext): Record<string, string | undefined> {
  return {
    ...process.env,
    R4_MODEL: ctx.model,
    R4_PROJECT: ctx.project,
    R4_SESSION: ctx.session,
    R4_PERMISSION: ctx.permission,
  };
}

function firstLineClipped(stdout: string): string {
  const line = stdout.split("\n")[0]?.trim() ?? "";
  return line.length > STATUSLINE_MAX_CHARS ? `${line.slice(0, STATUSLINE_MAX_CHARS - 1)}…` : line;
}

/** First stdout line (clipped), or "" on no command / failure / timeout.
 * BLOCKS the event loop for the whole run — the status bar uses
 * refreshStatusline (async) instead. */
export function runStatusline(command: string, ctx: StatuslineContext): string {
  if (!command) return "";
  const result = spawnSync("sh", ["-c", command], {
    timeout: 2000,
    encoding: "utf8",
    env: statuslineEnv(ctx),
  });
  if (result.error || result.status !== 0) return "";
  return firstLineClipped(result.stdout ?? "");
}

/** Async twin of runStatusline: the command runs off the render thread
 * (TUI audit r2 #70 — spawnSync froze input and repaint for up to 2 s every
 * 5 s). Killed and "" past the timeout, even when a grandchild keeps the
 * stdout pipe open. */
export async function runStatuslineAsync(
  command: string,
  ctx: StatuslineContext,
  timeoutMs = 2000,
): Promise<string> {
  if (!command) return "";
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const proc = Bun.spawn(["sh", "-c", command], { stdout: "pipe", stderr: "ignore", env: statuslineEnv(ctx) });
    const deadline = new Promise<null>((resolve) => {
      timer = setTimeout(() => resolve(null), timeoutMs);
    });
    // The deadline bounds the stdout read AND the exit: a command that
    // closes stdout and keeps running is killed too (audit r2 review).
    const outAndCode = Promise.all([new Response(proc.stdout).text(), proc.exited]);
    const done = await Promise.race([outAndCode, deadline]);
    if (done === null) {
      proc.kill();
      return "";
    }
    return done[1] === 0 ? firstLineClipped(done[0]) : "";
  } catch {
    return "";
  } finally {
    clearTimeout(timer);
  }
}

let statuslineInFlight: Promise<string> | null = null;

/** One shared, single-flight statusline run for every StatusBar instance
 * (the sheet layout mounts a second one): a tick that lands while a run is
 * pending gets that run's result instead of starting another process. */
export function refreshStatusline(ctx: StatuslineContext): Promise<string> {
  if (statuslineInFlight) return statuslineInFlight;
  const command = readStatuslineCommand();
  statuslineInFlight = runStatuslineAsync(command, ctx).finally(() => {
    statuslineInFlight = null;
  });
  return statuslineInFlight;
}
