/** storeTest — W5c shellStreams reducer slice. Bundles reducer surface only
 * (same pattern as store-catalog-scoping.test.mjs). */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "shell-stream-store.bundle.cjs");

execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  [
    "webview-ui/src/state/store.tsx",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    "--loader:.tsx=tsx",
    `--outfile=${bundlePath}`,
  ],
  { stdio: "inherit", cwd: rootDir },
);

const require = createRequire(import.meta.url);
const { applyShellStreamState, STREAM_PANEL_MAX_BYTES, MAX_TERMINAL_STREAMS } = require(bundlePath);
const reducer = applyShellStreamState;

function emptyState() {
  return {
    sessions: [],
    runStates: {},
    endpoints: [],
    shellStreams: {},
    connected: false,
    status: "connecting",
    transcripts: {},
    streaming: {},
    toolCalls: {},
    usage: {},
    budgets: {},
    thinking: {},
    modes: {},
    tab: "chat",
    studioPanel: "overview",
    studio: {},
    system: {},
    edits: {},
  };
}

test("shellStream creates a running stream entry and appends chunks (W5c)", () => {
  let s = reducer(emptyState(), {
    type: "shellStream",
    sessionId: "s1",
    streamId: "shell-aa01",
    chunk: "alpha\nbeta\n",
  });
  const e = s.shellStreams["shell-aa01"];
  assert.equal(e.status, "running");
  assert.equal(e.body, "alpha\nbeta\n");
  assert.equal(e.lines, 2);

  s = reducer(s, {
    type: "shellStream",
    sessionId: "s1",
    streamId: "shell-aa01",
    chunk: "gamma\n",
  });
  assert.equal(s.shellStreams["shell-aa01"].body, "alpha\nbeta\ngamma\n");
  assert.equal(s.shellStreams["shell-aa01"].lines, 3);
});

test("shellStreamStatus moves stream into terminal state (W5c)", () => {
  let s = reducer(emptyState(), {
    type: "shellStream",
    sessionId: "s1",
    streamId: "shell-bb02",
    chunk: "x\n",
  });
  s = reducer(s, {
    type: "shellStreamStatus",
    streamId: "shell-bb02",
    status: "cancelled",
  });
  assert.equal(s.shellStreams["shell-bb02"].status, "cancelled");

  // Late chunk does NOT reopen a terminal stream.
  s = reducer(s, {
    type: "shellStream",
    sessionId: "s1",
    streamId: "shell-bb02",
    chunk: "late\n",
  });
  assert.equal(s.shellStreams["shell-bb02"].status, "cancelled");
  assert.equal(s.shellStreams["shell-bb02"].body, "x\nlate\n");
});

test("shellStreamStatus on missing stream creates terminal placeholder", () => {
  const s = reducer(emptyState(), {
    type: "shellStreamStatus",
    streamId: "shell-cc03",
    status: "not-found",
  });
  const e = s.shellStreams["shell-cc03"];
  assert.equal(e.status, "not-found");
  assert.equal(e.body, "");
});

test("body truncation keeps the tail within STREAM_PANEL_MAX_BYTES (W5c)", () => {
  const big = "x".repeat(STREAM_PANEL_MAX_BYTES + 4096);
  const s = reducer(emptyState(), {
    type: "shellStream",
    sessionId: "s1",
    streamId: "shell-dd04",
    chunk: big,
  });
  const e = s.shellStreams["shell-dd04"];
  assert.ok(e.body.length <= STREAM_PANEL_MAX_BYTES, "body is capped");
  assert.ok(e.body.endsWith("x".repeat(4096)), "tail kept");
});

test("ui/dismissShellStream removes the chip (terminal chips are dismissible)", () => {
  let s = reducer(emptyState(), {
    type: "shellStream",
    sessionId: "s1",
    streamId: "shell-ee05",
    chunk: "done!\n",
  });
  s = reducer(s, { type: "shellStreamStatus", streamId: "shell-ee05", status: "done" });
  s = reducer(s, { type: "ui/dismissShellStream", streamId: "shell-ee05" });
  assert.equal(s.shellStreams["shell-ee05"], undefined);
  // panel is empty → ShellStreamsPanel renders nothing again
  assert.deepEqual(Object.keys(s.shellStreams), []);
});

test("ui/dismissShellStream on unknown id is a no-op", () => {
  const before = reducer(emptyState(), {
    type: "shellStream",
    sessionId: "s1",
    streamId: "shell-ff06",
    chunk: "x\n",
  });
  const after = reducer(before, { type: "ui/dismissShellStream", streamId: "shell-gone" });
  assert.equal(after, before, "unknown dismiss does not create entries");
  assert.equal(after.shellStreams["shell-ff06"].status, "running");
});

test("terminal streams are pruned past MAX_TERMINAL_STREAMS, running kept", () => {
  let s = emptyState();
  // Fill beyond the terminal bound; each new status event triggers one prune.
  for (let i = 0; i < MAX_TERMINAL_STREAMS + 4; i += 1) {
    s = reducer(s, {
      type: "shellStreamStatus",
      streamId: `shell-term-${i}`,
      status: "done",
    });
  }
  const terminalIds = Object.values(s.shellStreams)
    .filter((e) => e.status !== "running")
    .map((e) => e.streamId);
  assert.equal(terminalIds.length, MAX_TERMINAL_STREAMS);
  // Oldest evicted, newest kept.
  assert.equal(s.shellStreams["shell-term-0"], undefined);
  assert.ok(s.shellStreams[`shell-term-${MAX_TERMINAL_STREAMS + 3}`]);
  // Running stream survives pruning.
  s = reducer(s, {
    type: "shellStream",
    sessionId: "s1",
    streamId: "shell-live",
    chunk: "still running\n",
  });
  s = reducer(s, { type: "shellStreamStatus", streamId: "shell-term-extra", status: "done" });
  assert.equal(s.shellStreams["shell-live"].status, "running");
});
