// `!cmd` runner of the agent view (ADR-103 VP-2b), with a real shell.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-shell-runner-"));
const bundle = path.join(outDir, "runner.cjs");
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  ["src/bridge/shell-runner.ts", "--bundle", "--platform=node", "--format=cjs", `--outfile=${bundle}`],
  { stdio: "inherit", cwd: rootDir },
);
const { NodeShellRunner } = createRequire(import.meta.url)(bundle);
const workspace = mkdtempSync(path.join(os.tmpdir(), "r4-shell-ws-"));
process.on("exit", () => {
  rmSync(outDir, { recursive: true, force: true });
  rmSync(workspace, { recursive: true, force: true });
});
const skip = process.platform === "win32" ? "POSIX shell only" : false;

test("runs in the workspace and reports output and exit code", { skip }, async () => {
  const runner = new NodeShellRunner(workspace, "/bin/sh");
  const result = await runner.run("pwd; echo oops >&2; exit 3");
  assert.equal(result.exitCode, 3);
  assert.equal(result.stdout.trim(), workspace);
  assert.equal(result.stderr.trim(), "oops");
  assert.equal(result.interrupted, undefined);
  assert.equal(runner.running, false);
});

test("stop interrupts a long command and marks it", { skip, timeout: 10_000 }, async () => {
  const runner = new NodeShellRunner(workspace, "/bin/sh");
  const pending = runner.run("sleep 30");
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(runner.running, true);
  assert.equal(runner.stop(), 1);
  const result = await pending;
  assert.equal(result.interrupted, true);
  assert.notEqual(result.exitCode, 0);
});
