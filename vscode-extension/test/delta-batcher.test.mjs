import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "delta-batcher.bundle.cjs");

execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  [
    "webview-ui/src/state/delta-batcher.ts",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    "--outfile=test/delta-batcher.bundle.cjs",
  ],
  { cwd: rootDir, stdio: ["ignore", "pipe", "inherit"] },
);

const require = createRequire(import.meta.url);
const { createStreamDeltaDispatcher } = require(bundlePath);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

test("coalesces same-session burst into one dispatch", async () => {
  const events = [];
  const send = createStreamDeltaDispatcher((e) => events.push(e), { intervalMs: 20 });
  send({ type: "assistantDelta", sessionId: "s1", text: "a" });
  send({ type: "assistantDelta", sessionId: "s1", text: "b" });
  send({ type: "assistantDelta", sessionId: "s1", text: "c" });
  await sleep(50);
  assert.deepEqual(events, [{ type: "assistantDelta", sessionId: "s1", text: "abc" }]);
});

test("multi-session buffers flush independently", async () => {
  const events = [];
  const send = createStreamDeltaDispatcher((e) => events.push(e), { intervalMs: 20 });
  send({ type: "assistantDelta", sessionId: "a", text: "A" });
  send({ type: "assistantDelta", sessionId: "b", text: "B" });
  await sleep(50);
  assert.equal(events.length, 2);
  const bySession = Object.fromEntries(events.map((e) => [e.sessionId, e.text]));
  assert.deepEqual(bySession, { a: "A", b: "B" });
});

test("non-delta event flushes pending deltas first (causal order)", () => {
  const events = [];
  const send = createStreamDeltaDispatcher((e) => events.push(e), { intervalMs: 10_000 });
  send({ type: "assistantDelta", sessionId: "s1", text: "chunk" });
  send({ type: "tool_call", name: "shell_exec" });
  assert.deepEqual(events, [
    { type: "assistantDelta", sessionId: "s1", text: "chunk" },
    { type: "tool_call", name: "shell_exec" },
  ]);
});

test("maxBytes forces immediate flush", () => {
  const events = [];
  const send = createStreamDeltaDispatcher((e) => events.push(e), { intervalMs: 10_000, maxBytes: 4 });
  send({ type: "assistantDelta", sessionId: "s", text: "12345" });
  assert.deepEqual(events, [{ type: "assistantDelta", sessionId: "s", text: "12345" }]);
});
