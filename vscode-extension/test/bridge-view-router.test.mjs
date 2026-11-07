// Which webview messages the bridge answers (ADR-103 VP-2).
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-view-router-"));
const bundle = path.join(outDir, "router.cjs");
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  ["src/bridge/bridge-view-router.ts", "--bundle", "--platform=node", "--format=cjs", `--outfile=${bundle}`],
  { stdio: "inherit", cwd: rootDir },
);
const { routeBridgeViewMessage, BRIDGE_VIEW_TYPES } = createRequire(import.meta.url)(bundle);
rmSync(outDir, { recursive: true, force: true });

/** Records every host call as "method(args)". */
function recordingHost() {
  const calls = [];
  return new Proxy(
    { calls },
    {
      get(target, prop) {
        if (prop === "calls") return target.calls;
        return async (...args) => {
          calls.push(`${String(prop)}(${args.map((a) => JSON.stringify(a)).join(",")})`);
        };
      },
    },
  );
}

async function route(message) {
  const host = recordingHost();
  let afterReady = 0;
  const routed = await routeBridgeViewMessage(host, message, { afterReady: async () => void afterReady++ });
  return { routed, calls: host.calls, afterReady };
}

test("chat-core messages route to the host", async () => {
  assert.deepEqual(await route({ type: "ready" }), { routed: true, calls: ["initialize()"], afterReady: 1 });
  assert.deepEqual((await route({ type: "send", text: "hi", model: "m" })).calls, ['setModel("m")', 'send("hi")']);
  assert.deepEqual((await route({ type: "selectSession", sessionId: " s2 " })).calls, ['selectSession("s2")']);
  assert.deepEqual((await route({ type: "deleteSession", sessionId: "s2" })).calls, ['archiveSession("s2")']);
  assert.deepEqual((await route({ type: "unpinSession", sessionId: "s2" })).calls, ['pinSession("s2",false)']);
  assert.deepEqual((await route({ type: "approvalDecision", id: 7, decision: "always" })).calls, ['answerApproval(7,"always")']);
  assert.deepEqual((await route({ type: "askUserAnswer", id: "q", answer: 3 })).calls, ['answerQuestion("q",null)']);
  assert.deepEqual((await route({ type: "confirmPlan", action: "execute_ask" })).calls, ['confirmPlan("execute_ask")']);
});

test("VP-2b message verbs, citations and changed files route to the host", async () => {
  assert.deepEqual((await route({ type: "forkSession", turnId: "t-1" })).calls, ['forkFrom("t-1")']);
  assert.deepEqual((await route({ type: "forkSession" })).calls, ["forkFrom()"]);
  assert.deepEqual((await route({ type: "saveTurn", turnId: "t-1", part: "answer" })).calls, ['saveTurn("t-1","answer")']);
  assert.deepEqual((await route({ type: "saveTurn", turnId: "t-1", part: "everything" })).calls, []);
  assert.deepEqual((await route({ type: "openCitation", citation: { sid: "S1" } })).calls, ['openCitation({"sid":"S1"})']);
  assert.deepEqual((await route({ type: "openCitation", citation: "S1" })).calls, []);
  assert.deepEqual((await route({ type: "openChange", path: "a.py" })).calls, ['openChange("a.py")']);
  assert.deepEqual((await route({ type: "revertChange", path: "" })).calls, []);
  assert.deepEqual((await route({ type: "revertChange", path: "a.py" })).calls, ['revertChange("a.py")']);
  assert.deepEqual((await route({ type: "mentionQuery", query: "src/ap" })).calls, ['mentionCandidates("src/ap")']);
  assert.deepEqual((await route({ type: "toolResultRequest", sessionId: "s", toolCallId: "c1" })).calls, ['toolResult("c1")']);
  assert.deepEqual((await route({ type: "sessionFilter", state: "error", venue: "", includeArchived: true })).calls, [
    'setSessionFilter({"state":"error","includeArchived":true})',
  ]);
  assert.deepEqual((await route({ type: "exportSession", sessionId: "s-1" })).calls, ['exportSession("s-1")']);
  assert.deepEqual((await route({ type: "venueStop", sessionId: "s-1" })).calls, ['stopRemote("s-1")']);
  assert.deepEqual((await route({ type: "forkRow", sessionId: "s-1" })).calls, ['forkSession("s-1")']);
  assert.deepEqual((await route({ type: "rewindTo", turnId: "t-1", text: "q" })).calls, ['rewindTo("t-1","q")']);
  assert.deepEqual((await route({ type: "rewindTo", turnId: "", text: "q" })).calls, [], "no turn id, no rewind");
  assert.deepEqual((await route({ type: "sessionTurns", sessionId: "s-1" })).calls, ['sessionTurns("s-1")']);
  assert.deepEqual((await route({ type: "openInTab", sessionId: "s-2" })).calls, ['openInTab("s-2")']);
});

test("malformed asks do nothing; other panels fall through to the ACP path", async () => {
  assert.deepEqual(await route({ type: "selectSession" }), { routed: true, calls: [], afterReady: 0 });
  assert.deepEqual((await route({ type: "approvalDecision", decision: "once" })).calls, []);
  for (const type of ["refreshStudio", "catalogSearch", "mgmtCardQuery", "acceptEdit", "nope"]) {
    assert.equal((await route({ type })).routed, false, type);
    assert.equal(BRIDGE_VIEW_TYPES.has(type), false);
  }
});
