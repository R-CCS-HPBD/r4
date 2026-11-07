import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "store-stream-boundary.bundle.cjs");

// Bundle ONLY the store module surface — the test drives the real reducer
// chain (applyEventForTest over initialStateForTest), asserting what the
// transcript bubble would render at end of turn.
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
const { applyEventForTest, initialStateForTest } = require(bundlePath);

const SID = "sess-boundary";

function drive(events) {
  let state = initialStateForTest();
  for (const event of events) state = applyEventForTest(state, event);
  return state;
}

function transcriptText(state) {
  return (state.transcripts[SID] ?? [])
    .filter((entry) => entry.kind === "msg")
    .map((entry) => entry.text)
    .join("\n");
}

// The ACP server accumulates all streamed chunks of a turn and ships them as
// ONE session/text (assistantDelta) plus the identical chat/send result
// (assistantComplete). Round-boundary separators ("\n\n", inserted by
// acp_session.py) must survive both messages into the rendered bubble.
test("paragraph break survives a successful tool round", () => {
  const finalText = "…fits the existing patterns.\n\nNow let me look at the file.";
  const state = drive([
    { type: "userMessage", sessionId: SID, text: "find validate_key" },
    { type: "assistantDelta", sessionId: SID, text: finalText },
    { type: "assistantComplete", sessionId: SID, text: finalText },
  ]);
  const text = transcriptText(state);
  assert.match(text, /patterns\.\n\nNow let me look/);
  assert.ok(!/patterns\.Now let me look/.test(text), "rounds must not glue");
});

test("paragraph break survives a FAILED tool round (5xx/403 error card)", () => {
  const finalText =
    "…my file tools are mostly platform-storage-oriented.\n\ntool_search isn't available, so I'll try the catalog.";
  const state = drive([
    { type: "userMessage", sessionId: SID, text: "find validate_key" },
    { type: "assistantDelta", sessionId: SID, text: finalText },
    {
      type: "toolCall",
      sessionId: SID,
      toolCallId: "call_1",
      name: "search_agentic",
      status: "failed",
      resultPreview: "HTTP 503",
    },
    { type: "assistantComplete", sessionId: SID, text: finalText },
  ]);
  const text = transcriptText(state);
  assert.match(text, /storage-oriented\.\n\ntool_search isn't available/);
  assert.ok(!/storage-oriented\.tool_search/.test(text), "failed rounds must not glue either");
});

// Task 2: a 5 MB tool result stays BOUNDED in the store — envelope preview
// ≤ 16 KiB + marker (python-side cap); expand fetch loads the journal copy
// by tool_call_id and marks the card complete.
test("5 MB tool result: preview bounded, expand fetch loads full text", () => {
  const BOUND_PREVIEW = "x".repeat(16384) + "…[truncated — expand to load full result]";
  let state = drive([
    { type: "userMessage", sessionId: SID, text: "tail the job log" },
    {
      type: "toolCall",
      sessionId: SID,
      toolCallId: "call_big",
      name: "job_logs",
      status: "pending",
    },
    {
      type: "toolCall",
      sessionId: SID,
      toolCallId: "call_big",
      name: "job_logs",
      status: "completed",
      resultPreview: BOUND_PREVIEW,
      resultTruncated: true,
    },
  ]);
  const cards = state.toolCalls[SID];
  assert.equal(cards.length, 1);
  assert.ok(cards[0].truncated, "bounded card must mark truncated");
  assert.ok(!cards[0].fullResult);

  state = applyEventForTest(state, {
    type: "toolResult",
    sessionId: SID,
    toolCallId: "call_big",
    name: "job_logs",
    found: true,
    content: "FULL-5MB".repeat(700_000), // ≈ 5.6 MB journal copy
  });
  const [card] = state.toolCalls[SID];
  assert.equal(card.truncated, false);
  assert.ok(card.fullResult && card.fullResult.length > 5_000_000);
});

test("sourceStale renders exactly one warning row (task 7)", () => {
  // The dedupe-of-the-stale-warning contract keys off `state.currentId` —
  // the reducer only emits the visible row once a session is selected, and
  // `userMessage` alone never completes that session-start hook (verified
  // live 2026-09-15 via a node probe of the bundle reducer).
  let state = drive([{ type: "userMessage", sessionId: SID, text: "hi" }]);
  state = applyEventForTest(state, {
    type: "sessionSelected",
    session: { id: SID, title: "stale test", startedAt: "2026-09-15T00:00:00Z", messages: [] },
    fresh: true,
  });
  state = applyEventForTest(state, { type: "sourceStale", text: "stale child" });
  state = applyEventForTest(state, { type: "sourceStale", text: "stale child" });
  const rows = (state.transcripts[SID] ?? []).filter((e) => e.kind === "event");
  const stale = rows.filter((entry) => entry.text.includes("stale child"));
  assert.equal(stale.length, 1);
});

test("50 bounded cards keep the store footprint under the journal-independent bound", () => {
  const events = [{ type: "userMessage", sessionId: SID, text: "run" }];
  for (let i = 0; i < 50; i++) {
    events.push(
      {
        type: "toolCall",
        sessionId: SID,
        toolCallId: `call_${i}`,
        name: "job_logs",
        status: "pending",
      },
      {
        type: "toolCall",
        sessionId: SID,
        toolCallId: `call_${i}`,
        name: "job_logs",
        status: "completed",
        resultPreview: "x".repeat(16384) + "…",
        resultTruncated: true,
      },
    );
  }
  const state = drive(events);
  const cards = state.toolCalls[SID];
  assert.equal(cards.length, 50);
  const totalPreviewChars = cards.reduce((sum, c) => sum + (c.resultPreview?.length ?? 0) + (c.fullResult?.length ?? 0), 0);
  // Bound: 50 × (16 KiB + marker; no fullResult fetched) — never × 5 MB.
  assert.ok(totalPreviewChars < 50 * (16384 + 64), `preview total ${totalPreviewChars} exceeds bound`);
});
