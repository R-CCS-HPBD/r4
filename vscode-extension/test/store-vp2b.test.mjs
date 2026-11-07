// VP-2b webview state through the real store (ADR-103): turn ids, !cmd
// cells, notices, queue / changed files / plan / citations, forced drafts.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-store-vp2b-"));
const esbuild = path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild");
for (const [entry, out] of [
  ["webview-ui/src/state/store.tsx", "store.cjs"],
  ["webview-ui/src/lib/turn-of-entry.ts", "turn.cjs"],
]) {
  execFileSync(esbuild, [entry, "--bundle", "--platform=node", "--format=cjs", "--loader:.tsx=tsx", `--outfile=${path.join(outDir, out)}`], {
    stdio: "inherit",
    cwd: rootDir,
  });
}
const require = createRequire(import.meta.url);
const { applyEventForTest, initialStateForTest } = require(path.join(outDir, "store.cjs"));
const { turnOfEntry, isFinalAnswer } = require(path.join(outDir, "turn.cjs"));
rmSync(outDir, { recursive: true, force: true });

const SID = "s-1";
const drive = (events, state = initialStateForTest()) => events.reduce((s, e) => applyEventForTest(s, e), state);
const entries = (state) => state.transcripts[SID] ?? [];

test("turnOpened tags the latest user message once", () => {
  const state = drive([
    { type: "userMessage", sessionId: SID, text: "one" },
    { type: "turnOpened", sessionId: SID, turnId: "t-1" },
    { type: "assistantComplete", sessionId: SID, text: "ok" },
    { type: "turnOpened", sessionId: SID, turnId: "t-other" },
  ]);
  assert.deepEqual(entries(state).filter((e) => e.role === "You").map((e) => e.turnId), ["t-1"]);
});

test("loaded messages keep their turn ids", () => {
  const state = drive([
    {
      type: "sessionSelected",
      session: { id: SID, name: "x" },
      fresh: false,
      messages: [{ role: "user", content: "q", turnId: "t-9" }, { role: "assistant", content: "a" }],
    },
  ]);
  assert.equal(entries(state)[0].turnId, "t-9");
});

test("a !cmd cell starts running and is replaced by its result", () => {
  const state = drive([
    { type: "shellStarted", sessionId: SID, id: "sh-1", echo: "$ ls" },
    { type: "shellResult", sessionId: SID, id: "sh-1", text: "$ ls (exit 2)\nnope", exitCode: 2 },
  ]);
  const shells = entries(state).filter((e) => e.kind === "shell");
  assert.equal(shells.length, 1);
  assert.equal(shells[0].text, "$ ls (exit 2)\nnope");
  assert.equal(shells[0].error, true);
});

test("notices land as event rows", () => {
  const state = drive([{ type: "notice", sessionId: SID, text: "a.py: reverted" }]);
  assert.deepEqual(entries(state).map((e) => [e.kind, e.text]), [["event", "a.py: reverted"]]);
});

test("queue, changed files, plan and citations are kept per session", () => {
  const state = drive([
    { type: "queueState", sessionId: SID, items: ["two"] },
    { type: "filesChanged", sessionId: SID, files: [{ path: "a.py", inserted: 1, deleted: 0, created: true, reverted: false }] },
    { type: "planState", sessionId: SID, steps: [{ id: "1", title: "Read", status: "completed", depth: 0 }] },
    { type: "citations", sessionId: SID, turnId: "t-1", citations: [{ sid: "S1", title: "Paper" }] },
    { type: "citations", sessionId: SID, turnId: "", citations: [{ sid: "lost" }] },
  ]);
  assert.deepEqual(state.extras.queue[SID], ["two"]);
  assert.equal(state.extras.files[SID][0].path, "a.py");
  assert.equal(state.extras.plan[SID][0].title, "Read");
  assert.deepEqual(Object.keys(state.extras.citations[SID]), ["t-1"], "citations without a turn are dropped");
});

test("Edit forces the draft; a refused turn's restore does not", () => {
  const forced = drive([{ type: "restoreDraft", sessionId: SID, text: "edit me", force: true }]);
  assert.equal(forced.asks.restoreDraft.force, true);
  const gentle = drive([{ type: "restoreDraft", sessionId: SID, text: "again" }]);
  assert.equal(gentle.asks.restoreDraft.force, false);
});

test("slash output becomes one output card; an error line marks it", () => {
  const state = drive([
    { type: "slashOutput", sessionId: SID, command: "/context", lines: [{ kind: "info", text: "a" }, { kind: "error", text: "b" }] },
    { type: "slashOutput", sessionId: SID, command: "/x", lines: [] },
  ]);
  assert.deepEqual(entries(state).map((e) => [e.kind, e.text, Boolean(e.error)]), [
    ["output", "a\nb", true],
    ["output", "(done)", false],
  ]);
});

test("dropTurns removes a turn's user message and what followed it", () => {
  const state = drive([
    { type: "userMessage", sessionId: SID, text: "one" },
    { type: "turnOpened", sessionId: SID, turnId: "t-1" },
    { type: "assistantComplete", sessionId: SID, text: "first answer" },
    { type: "userMessage", sessionId: SID, text: "two" },
    { type: "turnOpened", sessionId: SID, turnId: "t-2" },
    { type: "assistantComplete", sessionId: SID, text: "second answer" },
    { type: "dropTurns", sessionId: SID, turnIds: ["t-2"] },
  ]);
  assert.deepEqual(entries(state).filter((e) => e.kind === "msg").map((e) => e.text), ["one", "first answer"]);
});

test("dock focus counts each /changes; candidates and the backend are kept", () => {
  const state = drive([
    { type: "dockFocus", sessionId: SID, panel: "files" },
    { type: "dockFocus", sessionId: SID, panel: "files" },
    { type: "mentionCandidates", sessionId: SID, query: "ap", items: ["src/app.ts"] },
    { type: "backendInfo", backend: "bridge" },
  ]);
  assert.deepEqual(state.extras.dockFocus[SID], { panel: "files", seq: 2 });
  assert.deepEqual(state.extras.mentions, { query: "ap", items: ["src/app.ts"] });
  assert.equal(state.extras.backend, "bridge");
});

test("policyState sets the chips and shows the sandbox notice once (VP-3a)", () => {
  const state = drive([
    { type: "policyState", sessionId: SID, sandboxNotice: "Sandbox off: …", sandboxChip: "sbx:off", web: "off" },
    { type: "policyState", sessionId: SID, sandboxNotice: "", sandboxChip: "sbx:on", web: "public" },
    { type: "sessionFilterState", filter: { state: "error", venue: "", includeArchived: true } },
  ]);
  assert.deepEqual(entries(state).map((e) => e.text), ["Sandbox off: …"]);
  assert.deepEqual(state.extras.policy[SID], { sandboxChip: "sbx:on", web: "public" });
  assert.deepEqual(state.extras.sessionFilter, { state: "error", venue: "", includeArchived: true });
});

test("venueState keeps the remote: chip per session; an empty chip is a local session (VP-3b)", () => {
  const state = drive([
    { type: "venueState", sessionId: SID, chip: "remote:running · lease 47m", chipState: "ok" },
    { type: "venueState", sessionId: "other", chip: "", chipState: "" },
  ]);
  assert.deepEqual(state.extras.venue[SID], { chip: "remote:running · lease 47m", state: "ok" });
  assert.deepEqual(state.extras.venue.other, { chip: "", state: "" });
  assert.deepEqual(entries(state), [], "no transcript rows");
});

test("an answer belongs to the user message above it; citations go on the final answer", () => {
  const list = [
    { kind: "msg", role: "You", text: "q", turnId: "t-1" },
    { kind: "event", text: "tool" },
    { kind: "msg", role: "R4 Agent", text: "partial" },
    { kind: "msg", role: "R4 Agent", text: "final" },
    { kind: "msg", role: "You", text: "q2" },
  ];
  assert.equal(turnOfEntry(list, 2), "t-1");
  assert.equal(turnOfEntry(list, 4), undefined);
  assert.equal(isFinalAnswer(list, 2), false);
  assert.equal(isFinalAnswer(list, 3), true);
});

test("truncateFrom drops the rewound prompt and everything after it; rewindPending marks it (VP-2c)", () => {
  const state = drive([
    { type: "userMessage", sessionId: SID, text: "one" },
    { type: "turnOpened", sessionId: SID, turnId: "t-1" },
    { type: "assistantComplete", sessionId: SID, text: "a1" },
    { type: "userMessage", sessionId: SID, text: "two" },
    { type: "turnOpened", sessionId: SID, turnId: "t-2" },
    { type: "assistantComplete", sessionId: SID, text: "a2" },
    { type: "rewindPending", sessionId: SID, turnId: "t-2" },
  ]);
  assert.equal(state.extras.rewind[SID], "t-2");
  const cut = drive([{ type: "truncateFrom", sessionId: SID, turnId: "t-2" }], state);
  assert.deepEqual(entries(cut).map((e) => e.text), ["one", "a1"]);
  assert.equal(cut.extras.rewind[SID], "", "the cut ends the edit");
  const unknown = drive([{ type: "truncateFrom", sessionId: SID, turnId: "t-9" }], state);
  assert.equal(entries(unknown).length, 4, "an unknown turn cuts nothing");
});
