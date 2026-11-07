// A turn on the server over the bridge (ADR-103 VP-3b part 2): the
// venue_sync watch, round breaks, tool summary, remote approvals and
// questions, stop, failures, and a question shown on attach.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-remote-turn-"));
const bundle = path.join(outDir, "host.cjs");
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  ["src/bridge/bridge-agent-host.ts", "--bundle", "--platform=node", "--format=cjs", `--outfile=${bundle}`],
  { stdio: "inherit", cwd: rootDir },
);
const { BridgeAgentHost } = createRequire(import.meta.url)(bundle);
process.on("exit", () => rmSync(outDir, { recursive: true, force: true }));

const REMOTE = {
  id: "s-1",
  name: "On the server",
  interaction_mode: "chat",
  venue: "frontend",
  web_access: { mode: "none", domains: [], source: "prompt" },
  venue_runtime: { state: "running", phase: "idle" },
};
const QUEUED = { remote: true, queued: true, state: { total_messages: 4 } };

class FakeBridge extends EventEmitter {
  constructor(session, handlers) {
    super();
    this.initResult = { session, bridge_protocol: 1 };
    this.calls = [];
    this.handlers = {
      list_sessions: () => ({ sessions: [] }),
      get_session_changes: () => ({ changes: [] }),
      get_session_plan: () => ({ steps: [] }),
      send_message: () => QUEUED,
      ...handlers,
    };
  }
  get turnInFlight() {
    return false;
  }
  async ensureStarted() {}
  async reconnect() {}
  async request(method, params = {}) {
    this.calls.push([method, params]);
    const handler = this.handlers[method];
    return handler ? await handler(params, this) : {};
  }
  respondApproval() {}
  respondAskUser() {}
  cancelTurn() {}
}

/** venue_sync answers in order; the last one repeats. `before[i]` runs before answer i. */
function syncScript(answers, before = {}) {
  let i = 0;
  return async () => {
    const at = Math.min(i, answers.length - 1);
    i += 1;
    await before[at]?.();
    const answer = answers[at];
    if (answer instanceof Error) throw answer;
    return answer;
  };
}

function makeHost(session, handlers) {
  const ctx = { posts: [] };
  ctx.bridge = new FakeBridge(session, handlers(ctx));
  ctx.clock = { t: 0, every: () => () => undefined, sleep: async (ms) => void (ctx.clock.t += ms), now: () => ctx.clock.t };
  ctx.host = new BridgeAgentHost(ctx.bridge, (m) => ctx.posts.push(m), {}, ctx.clock);
  ctx.of = (type) => ctx.posts.filter((p) => p.type === type);
  ctx.calls = (method) => ctx.bridge.calls.filter(([m]) => m === method);
  return ctx;
}

const running = (partial, extra = {}) => ({ venue_state: "running", runtime: { phase: "running", partial, ...extra }, state: { total_messages: 5 } });
const idle = (answer) => ({
  adopted: true,
  venue_state: "running",
  runtime: { phase: "idle" },
  state: { total_messages: 6, messages: [{ role: "user", content: "q" }, { role: "assistant", content: answer }] },
});

test("a queued turn streams the worker's partial, closes a round on reset, sums the tools, ends on the journal's answer", async () => {
  const ctx = makeHost(REMOTE, () => ({
    "session.venue_sync": syncScript([
      running("Hel", { tool_activity: ["read_file a.py"] }),
      running("Hello wor"),
      running("Second round"),
      idle("Final answer"),
    ]),
  }));
  await ctx.host.initialize();
  await ctx.host.send("go");
  const stream = ctx.posts
    .filter((p) => ["assistantDelta", "assistantComplete", "notice", "sessionState"].includes(p.type))
    .map((p) => [p.type, p.text ?? p.state]);
  assert.deepEqual(stream, [
    ["sessionState", "running"],
    ["assistantDelta", "Hel"],
    ["assistantDelta", "lo wor"],
    ["assistantComplete", "Hello wor"],
    ["assistantDelta", "Second round"],
    ["notice", "remote turn — 1 tool action\n  read_file a.py"],
    ["assistantComplete", "Final answer"],
    ["sessionState", "idle"],
  ]);
  assert.equal(ctx.clock.t, 4 * 1_500);
  assert.equal(ctx.of("venueState").at(-1).chip, "remote:running/idle");
  assert.equal(ctx.calls("session.venue_approvals").length, 1, "approvals are read every other poll");
});

test("the worker idles with more queued: the watch goes on until the queue is empty", async () => {
  const ctx = makeHost(REMOTE, () => ({
    "session.venue_sync": syncScript([
      { ...idle("first"), state: { ...idle("first").state, session: { chat_queue: ["next"] } } },
      idle("second"),
    ]),
  }));
  await ctx.host.initialize();
  await ctx.host.send("go");
  assert.deepEqual(ctx.of("assistantComplete").map((p) => p.text), ["second"]);
});

test("a remote approval opens as a remote: card; the decision goes to session.venue_decide and is not reopened", async () => {
  const ctx = makeHost(REMOTE, (c) => ({
    // The server still lists it pending on the next poll (it lags the decision).
    "session.venue_approvals": () => ({
      approvals: [{ approval_id: "a1", status: "pending", action: { tool: "bash", description: "rm -rf build" } }],
    }),
    "session.venue_sync": syncScript(
      [{ venue_state: "running", runtime: { phase: "awaiting_approval" }, state: { total_messages: 5 } }, { venue_state: "running", runtime: { phase: "running" } }, idle("done")],
      { 1: () => c.host.answerApproval("remote:a1", "always") },
    ),
  }));
  await ctx.host.initialize();
  await ctx.host.send("clean up");
  const card = ctx.of("approvalRequest")[0];
  assert.deepEqual([card.id, card.tool, card.description, card.options], ["remote:a1", "bash", "rm -rf build", ["once", "always", "reject"]]);
  assert.deepEqual(ctx.calls("session.venue_decide")[0][1], { approval_id: "a1", decision: "always" });
  assert.deepEqual(ctx.of("approvalResolved").map((p) => [p.id, p.decision]), [["remote:a1", "always"]]);
});

test("a remote question: the one the worker names; an answer goes to venue_answer; a closed one gives the text back", async () => {
  const questions = {
    questions: [
      { question_id: "q1", session_id: "s-1", question: "old?", status: "pending" },
      { question_id: "q2", session_id: "s-1", question: "Which dataset?", options: ["A", "B"], status: "pending" },
    ],
  };
  const ctx = makeHost(REMOTE, (c) => ({
    "session.venue_questions": () => questions,
    "session.venue_answer": () => {
      throw new Error("venue.question_closed: answered elsewhere");
    },
    "session.venue_sync": syncScript(
      [{ venue_state: "running", runtime: { phase: "awaiting_user", detail: "question q2" } }, { venue_state: "running", runtime: { phase: "running" } }, idle("ok")],
      { 1: () => c.host.answerQuestion("remote:q2", "B") },
    ),
  }));
  await ctx.host.initialize();
  await ctx.host.send("analyse");
  const ask = ctx.of("askUserRequest")[0];
  assert.deepEqual([ask.id, ask.question, ask.options], ["remote:q2", "Which dataset?", ["A", "B"]]);
  assert.deepEqual(ctx.calls("session.venue_answer")[0][1], { question_id: "q2", answer: "B", session_id: "s-1" });
  assert.equal(ctx.of("restoreDraft").at(-1).text, "B");
  assert.ok(ctx.of("notice").some((n) => n.text === "remote question already closed (answered elsewhere or expired)"));
});

test("Esc sets a remote question aside: not reopened by the watch; /venue questions shows it again", async () => {
  const ctx = makeHost(REMOTE, (c) => ({
    "session.venue_questions": () => ({ questions: [{ question_id: "q2", session_id: "s-1", question: "Which?", status: "pending" }] }),
    "session.venue_sync": syncScript(
      [
        { venue_state: "running", runtime: { phase: "awaiting_user", detail: "question q2" } },
        { venue_state: "running", runtime: { phase: "awaiting_user", detail: "question q2" } },
        { venue_state: "running", runtime: { phase: "error", detail: "the worker gave up waiting" } },
      ],
      { 1: () => c.host.answerQuestion("remote:q2", null) },
    ),
  }));
  await ctx.host.initialize();
  await ctx.host.send("analyse");
  assert.equal(ctx.of("askUserRequest").length, 1, "set aside, not reopened");
  assert.match(ctx.of("notice").find((n) => /left pending/.test(n.text)).text, /r4 session answer <session> q2/);
  assert.equal(ctx.of("error").at(-1).message, "remote turn failed: the worker gave up waiting");
  assert.equal(ctx.of("sessionState").at(-1).state, "error");
  await ctx.host.send("/venue questions");
  assert.equal(ctx.of("askUserRequest").length, 2);
  assert.equal(ctx.of("slashOutput").at(-1).lines[0].text, "1 pending remote question — shown");
});

test("stop ends the watch, not the turn; a stopped runtime mid-turn and a failed sync end it as errors", async () => {
  const ctx = makeHost(REMOTE, (c) => ({
    "session.venue_sync": syncScript([running("par")], { 0: () => c.host.cancel() }),
  }));
  await ctx.host.initialize();
  await ctx.host.send("long job");
  assert.match(ctx.of("notice").at(-1).text, /^stopped watching the remote turn continues on the server/);
  assert.equal(ctx.of("cancelled").length, 1);
  assert.equal(ctx.of("sessionState").at(-1).state, "idle");
  assert.equal(ctx.calls("session.venue_sync").length, 1);

  const ended = makeHost(REMOTE, () => ({
    "session.venue_sync": syncScript([running("x"), { venue_state: "stopped", runtime: { phase: "running" } }]),
  }));
  await ended.host.initialize();
  await ended.host.send("go");
  assert.equal(ended.of("error").at(-1).message, "remote turn failed: remote runtime stopped before the turn finished");

  const broken = makeHost(REMOTE, () => ({ "session.venue_sync": syncScript([new Error("journal down")]) }));
  await broken.host.initialize();
  await broken.host.send("go");
  assert.match(broken.of("error").at(-1).message, /^remote sync failed: journal down/);
  assert.equal(broken.of("sessionState").at(-1).state, "error");
});

test("opening a server session that waits on the user shows its question; it closes once the worker stops waiting", async () => {
  const waiting = { ...REMOTE, venue_runtime: { state: "running", phase: "awaiting_user" } };
  const ctx = makeHost(waiting, () => ({
    "session.venue_questions": () => ({ questions: [{ question_id: "q7", session_id: "s-1", question: "Continue?", status: "pending" }] }),
    "session.venue_status": () => ({ venue: { state: "running", runtime: { phase: "running" } } }),
    "session.venue_approvals": () => ({ approvals: [] }),
  }));
  await ctx.host.initialize();
  await new Promise((r) => setImmediate(r));
  assert.equal(ctx.of("askUserRequest")[0].id, "remote:q7");
  await ctx.host.send("/venue");
  assert.deepEqual(ctx.of("askUserResolved").map((p) => p.id), ["remote:q7"]);
  assert.ok(ctx.of("notice").some((n) => /remote question closed/.test(n.text)));
  await ctx.host.send("/venue approvals");
  assert.equal(ctx.of("slashOutput").at(-1).lines[0].text, "no pending remote approvals");
});

test("a local session never polls the remote asks", async () => {
  const ctx = makeHost({ ...REMOTE, venue: "local", venue_runtime: { state: "", phase: "awaiting_user" } }, () => ({}));
  await ctx.host.initialize();
  await new Promise((r) => setImmediate(r));
  assert.equal(ctx.calls("session.venue_questions").length, 0);
});
