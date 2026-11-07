/**
 * PTY driver for the live r4-tui-v3 process (bun dev spawn from source).
 * Spawns the REAL app (not testRender) attached to a pseudo-terminal, so
 * focus claiming, paint order, and modal z-order are the production ones.
 *
 * Tier is opt-in (tests/pty-live/README.md): R4_PTY_LIVE=1 required; default
 * run is the live backend — set R4_TUI_BACKEND=offline for fixture runs.
 */

import { VtScreen } from "./screen";
import { createFixtureHome, type FixtureHome } from "./fixture-bridge/fixture";

export interface TuiProcess {
  screen: VtScreen;
  send(keys: string): void;
  pump(ms: number): Promise<void>;
  /** Poll the frame until `text` appears (or timeoutMs) — async UI races mean
   * fixed sleeps flake (catalog loads ~seconds on live); always waitFor. */
  waitFor(text: string, timeoutMs?: number): Promise<string>;
  snap(name: string): Promise<string>;
  /** Resolves once a fixture-mode process exited and its scratch home is gone. */
  kill(): Promise<void>;
}

export const KEYS = {
  enter: "\r",
  esc: "\x1b",
  backspace: "\x7f",
  down: "\x1b[B",
  up: "\x1b[A",
  home: "\x1b[H",
  end: "\x1b[F",
} as const;

export interface SpawnOptions {
  rows?: number;
  cols?: number;
  live?: boolean;
  extraEnv?: Record<string, string>;
  /** Spawn the compiled release binary instead of the dev source. */
  binPath?: string;
  /**
   * Offline fixture mode (plan H-9): CLI-mode TUI whose bridge is
   * fixture-bridge/server.ts, in a scratch HOME — no backend, no r4, no
   * user config. Overrides `live`.
   */
  fixture?: boolean;
}

/** The TUI's environment: live → no backend override; default → offline
 * fixtures (or the caller's R4_TUI_BACKEND); fixture mode wins over both. */
function tuiEnv(opts: SpawnOptions, fixture: FixtureHome | null): Record<string, string> {
  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    TERM: "xterm-256color",
    ...opts.extraEnv,
  };
  if (opts.live) delete env.R4_TUI_BACKEND;
  else env.R4_TUI_BACKEND = env.R4_TUI_BACKEND || "offline";
  if (fixture) Object.assign(env, fixture.env, opts.extraEnv);
  return env;
}

const pump = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Poll the frame until `text` appears; returns the last frame either way. */
async function waitForText(screen: VtScreen, text: string, timeoutMs: number): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  let frame = screen.dump();
  while (!frame.includes(text) && Date.now() <= deadline) {
    await pump(120);
    frame = screen.dump();
  }
  return frame;
}

async function snapFrame(screen: VtScreen, name: string): Promise<string> {
  const text = screen.dump();
  if (process.env.R4_PTY_GOLDEN) {
    const { mkdirSync, writeFileSync } = await import("node:fs");
    mkdirSync(new URL("./golden/", import.meta.url).pathname, { recursive: true });
    writeFileSync(new URL(`./golden/${name}.txt`, import.meta.url).pathname, text);
  }
  return text;
}

/** Stop the TUI; the fixture home goes only after the process exited (its
 * exit handler still writes tui-session-state.json — removing the dir
 * earlier left it behind in /tmp). */
function stopTui(proc: Bun.Subprocess, fixture: FixtureHome | null): Promise<void> {
  try {
    proc.terminal?.close();
    proc.kill();
  } catch {
    /* already gone */
  }
  if (!fixture) return Promise.resolve();
  const exited = Promise.race([proc.exited, pump(5_000)]);
  return exited.then(() => fixture.cleanup());
}

export function spawnTui(opts: SpawnOptions = {}): Promise<TuiProcess> {
  const rows = opts.rows ?? 44;
  const cols = opts.cols ?? 140;
  const screen = new VtScreen(rows, cols);
  const repoRoot = new URL("../../", import.meta.url).pathname;
  const bun = process.env.BUN_BIN ?? `${process.env.HOME}/.bun/bin/bun`;
  const fixture: FixtureHome | null = opts.fixture ? createFixtureHome(bun) : null;
  // NOTE: with `terminal` attached, stdin/stdout/stderr are null on the
  // subprocess — I/O flows through terminal callbacks (bun-types 7145+).
  const cmd = opts.binPath ? [opts.binPath] : [bun, "--preload", "@opentui/solid/preload", "src/cli.tsx"];
  const proc = Bun.spawn(cmd, {
    cwd: repoRoot,
    env: tuiEnv(opts, fixture),
    terminal: {
      cols,
      rows,
      data: (_terminal, data) => screen.feed(data),
      exit: () => {
        /* observed via kill/asserts */
      },
    },
  });

  return Promise.resolve({
    screen,
    send: (keys: string) => proc.terminal?.write(keys),
    pump,
    waitFor: (text: string, timeoutMs = 10_000) => waitForText(screen, text, timeoutMs),
    snap: (name: string) => snapFrame(screen, name),
    kill: () => stopTui(proc, fixture),
  });
}
