/**
 * c2-tui (2026-10-21, PTY journey j9): quitting while a `!` command ran
 * (Ctrl+Q twice, SIGTERM) left the command running as an orphan — `!sleep 300`
 * outlived the console; the shell runs detached in its own process group, so
 * nothing else reaped it. And a closed terminal (SIGHUP) with a `!` command
 * running left a headless console alive forever: R4 handled only SIGINT and
 * SIGTERM, and OpenTUI's own SIGHUP handler only destroys the renderer.
 */
import { describe, expect, it } from "bun:test";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { installExitSignals } from "../src/lib/exit-cleanup";
import { localShellRunning, runLocalShell, stopLocalShellsForExit } from "../src/lib/local-shell";

const alive = (pid: number): boolean => {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
};

describe("exit stops running `!` commands", () => {
  it("kills the whole process group of a running command, even one ignoring SIGTERM", async () => {
    const dir = mkdtempSync(join(tmpdir(), "r4-exit-shell-"));
    const pidFile = join(dir, "child.pid");
    try {
      const run = runLocalShell(`trap '' TERM; sleep 30 & echo $! > '${pidFile}'; wait`);
      for (let i = 0; i < 100 && !existsSync(pidFile); i++) await Bun.sleep(30);
      await Bun.sleep(50);
      const child = Number(readFileSync(pidFile, "utf8").trim());
      expect(alive(child)).toBe(true);
      expect(localShellRunning()).toBe(true);
      const started = Date.now();
      await stopLocalShellsForExit(300);
      const result = await run;
      expect(Date.now() - started).toBeLessThan(3000);
      expect(result.interrupted).toBe(true);
      expect(localShellRunning()).toBe(false);
      await Bun.sleep(100);
      expect(alive(child)).toBe(false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("is a no-op with nothing running", async () => {
    await stopLocalShellsForExit(50);
    expect(localShellRunning()).toBe(false);
  });
});

describe("exit signals", () => {
  it("SIGHUP (terminal closed) exits like SIGINT / SIGTERM, with its own code", () => {
    const seen: number[] = [];
    const uninstall = installExitSignals((code) => seen.push(code));
    process.emit("SIGHUP" as never);
    process.emit("SIGTERM" as never);
    process.emit("SIGINT" as never);
    uninstall();
    process.emit("SIGHUP" as never);
    expect(seen).toEqual([129, 143, 130]);
  });
});
