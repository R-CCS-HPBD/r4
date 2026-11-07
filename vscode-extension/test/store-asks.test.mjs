// Inline cards of the bridge backend through the real store (ADR-103 VP-2).
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-store-asks-"));
const bundlePath = path.join(outDir, "store.cjs");
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  ["webview-ui/src/state/store.tsx", "--bundle", "--platform=node", "--format=cjs", "--loader:.tsx=tsx", `--outfile=${bundlePath}`],
  { stdio: "inherit", cwd: rootDir },
);
const { applyEventForTest, initialStateForTest } = createRequire(import.meta.url)(bundlePath);
rmSync(outDir, { recursive: true, force: true });

const SID = "s-1";
const drive = (events) => events.reduce((state, event) => applyEventForTest(state, event), initialStateForTest());
const approval = (id, extra = {}) => ({
  type: "approvalRequest", sessionId: SID, id, tool: "file_edit", description: "edit", fenced: false, options: ["once", "reject"], ...extra,
});

test("approval cards queue per session and close one by one", () => {
  const state = drive([approval(1), approval(2, { diff: "-a\n+b" }), approval(1, { description: "again" })]);
  assert.deepEqual(state.asks.approvals[SID].map((a) => [a.id, a.description]), [[2, "edit"], [1, "again"]], "a repeated id replaces its card");
  assert.equal(state.asks.approvals[SID][0].diff, "-a\n+b");
  const after = applyEventForTest(state, { type: "approvalResolved", sessionId: SID, id: 2, decision: "once" });
  assert.deepEqual(after.asks.approvals[SID].map((a) => a.id), [1]);
});

test("question cards open and close", () => {
  const state = drive([{ type: "askUserRequest", sessionId: SID, id: "q", question: "Which?", options: ["a"] }]);
  assert.deepEqual(state.asks.questions[SID], [{ id: "q", question: "Which?", options: ["a"] }]);
  assert.deepEqual(applyEventForTest(state, { type: "askUserResolved", sessionId: SID, id: "q" }).asks.questions[SID], []);
});

test("the plan question closes on the next message even though the stream group claims that event", () => {
  const state = drive([{ type: "planConfirm", sessionId: SID, request: "refactor" }]);
  assert.equal(state.asks.planConfirm[SID], "refactor");
  const next = applyEventForTest(state, { type: "userMessage", sessionId: SID, text: "go" });
  assert.equal(SID in next.asks.planConfirm, false);
  assert.ok((next.transcripts[SID] ?? []).length > 0, "the stream group still recorded the message");
});

test("restoreDraft counts up so the composer sees each one", () => {
  const state = drive([
    { type: "restoreDraft", sessionId: SID, text: "hello" },
    { type: "restoreDraft", sessionId: SID, text: "hello" },
  ]);
  assert.deepEqual(state.asks.restoreDraft, { sessionId: SID, text: "hello", seq: 2, force: false });
});

test("events that are not asks leave the slice untouched", () => {
  const start = initialStateForTest();
  const next = applyEventForTest(start, { type: "status", status: "ready" });
  assert.equal(next.asks, start.asks);
});
