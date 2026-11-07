// The reasoning card's text (thinkingDelta / thinkingDone): token deltas join
// as one stream, as the console appends them — never one paragraph per token;
// a block closed by thinkingDone and reopened keeps both; a new turn starts a
// new trace.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-store-thinking-"));
const esbuild = path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild");
execFileSync(esbuild, ["webview-ui/src/state/store.tsx", "--bundle", "--platform=node", "--format=cjs", "--loader:.tsx=tsx", `--outfile=${path.join(outDir, "store.cjs")}`], {
  stdio: "inherit",
  cwd: rootDir,
});
const { applyEventForTest, initialStateForTest, thinkingText } = createRequire(import.meta.url)(path.join(outDir, "store.cjs"));
rmSync(outDir, { recursive: true, force: true });

const SID = "s-1";
const drive = (events, state = initialStateForTest()) => events.reduce((s, e) => applyEventForTest(s, e), state);
const delta = (seq, text) => ({ type: "thinkingDelta", sessionId: SID, payload: { seq, text, partial: true } });
const done = { type: "thinkingDone", sessionId: SID, payload: {} };

test("token deltas read as one stream, not one paragraph per token", () => {
  const tokens = ["The", " user", " asked", " for", " /", "workflow", " list", "."];
  const state = drive(tokens.map((t, i) => delta(i, t)));
  assert.equal(thinkingText(state.thinking[SID]), "The user asked for /workflow list.");
});

test("a replayed delta changes nothing; an out-of-order one lands in place", () => {
  const state = drive([delta(0, "a"), delta(2, "c"), delta(1, "b"), delta(1, "b")]);
  assert.equal(thinkingText(state.thinking[SID]), "abc");
});

test("seq restarts per block: a second block after thinkingDone keeps the first", () => {
  const state = drive([delta(0, "First"), delta(1, " block."), done, delta(0, "Second"), delta(1, " block."), done]);
  assert.equal(thinkingText(state.thinking[SID]), "First block.\n\nSecond block.");
  assert.equal(state.thinking[SID].done, true);
});

test("a new turn starts a new trace", () => {
  const first = drive([delta(0, "old"), done]);
  const next = drive([{ type: "userMessage", sessionId: SID, text: "next" }, delta(0, "new")], first);
  assert.equal(thinkingText(next.thinking[SID]), "new");
});
