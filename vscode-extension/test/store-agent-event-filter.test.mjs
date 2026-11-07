// Generic agent events that other channels already render must not become
// "event:" rows (maintainer report 2026-11-01: reasoning tokens, one row each).
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-store-events-"));
const bundlePath = path.join(outDir, "store.cjs");
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  ["webview-ui/src/state/store.tsx", "--bundle", "--platform=node", "--format=cjs", "--loader:.tsx=tsx", `--outfile=${bundlePath}`],
  { stdio: "inherit", cwd: rootDir },
);
const { applyEventForTest, initialStateForTest } = createRequire(import.meta.url)(bundlePath);
rmSync(outDir, { recursive: true, force: true });

const SID = "s-1";
const agentEvent = (event_type, message) => ({ type: "agentEvent", sessionId: SID, event: { event_type, message, data: {} } });
const rows = (state) => (state.transcripts[SID] ?? []).filter((e) => e.kind === "event");

test("reasoning tokens from an older r4 core leave no event rows", () => {
  let state = initialStateForTest();
  for (const token of ["The", " user", " just", " typed"]) state = applyEventForTest(state, agentEvent("reasoning_delta", token));
  state = applyEventForTest(state, agentEvent("reasoning_done", ""));
  assert.deepEqual(rows(state), []);
});

test("an event no other channel renders still shows as a row", () => {
  const state = applyEventForTest(initialStateForTest(), agentEvent("skill_activated", "code"));
  assert.equal(rows(state).length, 1);
});
