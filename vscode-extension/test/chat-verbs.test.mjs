// The console's chat verbs over the bridge (ADR-103 VP-2c): the edit and
// /rewind (session.truncate before the resend), /btw, /bg, /explain, /palette.
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
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-chat-verbs-"));
const bundle = path.join(outDir, "host.cjs");
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  ["src/bridge/bridge-agent-host.ts", "--bundle", "--platform=node", "--format=cjs", `--outfile=${bundle}`],
  { stdio: "inherit", cwd: rootDir },
);
const { BridgeAgentHost } = createRequire(import.meta.url)(bundle);
process.on("exit", () => rmSync(outDir, { recursive: true, force: true }));

const LOCAL = { id: "s-1", name: "One", interaction_mode: "chat", venue: "local", web_access: { mode: "none", domains: [], source: "prompt" } };
const MESSAGES = [
  { role: "user", content: "first question", metadata: { turn_id: "t-1" } },
  { role: "assistant", content: "a1", metadata: { turn_id: "t-1" } },
  { role: "user", content: "/help" },
  { role: "user", content: "second question", metadata: { turn_id: "t-2" } },
  { role: "assistant", content: "a2", metadata: { turn_id: "t-2" } },
];

class FakeBridge extends EventEmitter {
  constructor(session, handlers) {
    super();
    this.initResult = { session, bridge_protocol: 1 };
    this.calls = [];
    this.inFlight = 0;
    this.handlers = {
      list_sessions: () => ({ sessions: [] }),
      get_session_changes: () => ({ changes: [] }),
      get_session_plan: () => ({ steps: [] }),
      get_state: () => ({ messages: MESSAGES }),
      send_message: () => ({ response: "ok", turn_id: "t-3" }),
      ...handlers,
    };
  }
  get turnInFlight() {
    return this.inFlight > 0;
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

function makeHost(session, handlers = {}, hooks = {}) {
  const ctx = { posts: [], picked: [], palettes: 0, t: 0 };
  ctx.bridge = new FakeBridge(session, handlers);
  const clock = { every: () => () => undefined, sleep: async () => undefined, now: () => ctx.t };
  ctx.host = new BridgeAgentHost(
    ctx.bridge,
    (m) => ctx.posts.push(m),
    {
      pickPrompt: async (rows) => {
        ctx.picked.push(rows);
        return rows.at(-1)?.turnId ?? null;
      },
      openPalette: () => {
        ctx.palettes += 1;
      },
      shell: { running: false, run: async (command) => ((ctx.t += 3_000), { command, exitCode: 1, stdout: "", stderr: "boom", durationMs: 3 }), stop() {} },
      ...hooks,
    },
    clock,
  );
  ctx.of = (type) => ctx.posts.filter((p) => p.type === type);
  ctx.methods = () => ctx.bridge.calls.map(([m]) => m);
  ctx.card = () => ctx.of("slashOutput").at(-1);
  return ctx;
}

test("✎ then send: the history is cut at that turn first, the rows go, then the new turn runs", async () => {
  const ctx = makeHost(LOCAL);
  await ctx.host.initialize();
  ctx.host.rewindTo("t-1", "first question");
  assert.deepEqual(ctx.of("restoreDraft").at(-1), { type: "restoreDraft", sessionId: "s-1", text: "first question", force: true });
  assert.deepEqual(ctx.of("rewindPending").at(-1), { type: "rewindPending", sessionId: "s-1", turnId: "t-1" });
  assert.ok(!ctx.methods().includes("session.truncate"), "nothing is cut until the send");
  await ctx.host.send("first question, better");
  const at = ctx.methods().indexOf("session.truncate");
  assert.deepEqual(ctx.bridge.calls[at][1], { turn_id: "t-1" });
  assert.ok(at < ctx.methods().indexOf("send_message"), "cut, then send");
  assert.deepEqual(ctx.of("truncateFrom").at(-1), { type: "truncateFrom", sessionId: "s-1", turnId: "t-1" });
  await ctx.host.send("next");
  assert.equal(ctx.methods().filter((m) => m === "session.truncate").length, 1, "one cut per rewind");
});

test("/rewind lists the chat prompts newest first (no slash lines); cancel keeps the history", async () => {
  const ctx = makeHost(LOCAL);
  await ctx.host.initialize();
  await ctx.host.send("/rewind");
  assert.deepEqual(ctx.picked[0], [
    { turnId: "t-2", text: "second question" },
    { turnId: "t-1", text: "first question" },
  ]);
  assert.equal(ctx.of("rewindPending").at(-1).turnId, "t-1");
  await ctx.host.send("/rewind cancel");
  assert.equal(ctx.card().lines[0].text, "rewind cancelled — the history stays as it is");
  assert.equal(ctx.of("rewindPending").at(-1).turnId, "");
  await ctx.host.send("go on");
  assert.ok(!ctx.methods().includes("session.truncate"));
});

test("a rewind is refused on a server session and for a slash line; a failed cut sends nothing", async () => {
  const remote = makeHost({ ...LOCAL, venue: "frontend", venue_runtime: { state: "running", phase: "idle" } });
  await remote.host.initialize();
  remote.host.rewindTo("t-1", "first question");
  assert.match(remote.of("notice").at(-1).text, /^cannot rewind a remote session/);
  assert.equal(remote.of("rewindPending").length, 0);

  const ctx = makeHost(LOCAL, {
    "session.truncate": () => {
      throw new Error("turn not found");
    },
  });
  await ctx.host.initialize();
  ctx.host.rewindTo("/help", "/help");
  assert.match(ctx.of("notice").at(-1).text, /^cannot rewind: this turn|^cannot rewind to a slash command/);
  ctx.host.rewindTo("t-2", "second question");
  await ctx.host.send("second, edited");
  assert.equal(ctx.of("error").at(-1).message, "rewind failed (nothing was cut or sent): turn not found");
  assert.equal(ctx.of("restoreDraft").at(-1).text, "second, edited");
  assert.ok(!ctx.methods().includes("send_message"));
});

test("/btw answers while a turn runs and is not a turn", async () => {
  const ctx = makeHost(LOCAL, { "session.side_question": (p) => ({ answer: `side: ${p.question}` }) });
  await ctx.host.initialize();
  ctx.bridge.inFlight = 1;
  await ctx.host.send("/btw what was the file name?");
  assert.deepEqual(ctx.card(), {
    type: "slashOutput",
    sessionId: "s-1",
    command: "/btw what was the file name?",
    lines: [{ kind: "info", text: "side: what was the file name?" }],
  });
  assert.ok(!ctx.methods().includes("send_message") && !ctx.methods().includes("session.queue_remote"));
  ctx.bridge.inFlight = 0;
  await ctx.host.send("/btw");
  assert.match(ctx.card().lines[0].text, /^usage: \/btw/);
});

test("/bg lists `!` commands with status and time; /explain and /palette", async () => {
  const ctx = makeHost(LOCAL, {
    explain: (p) => (p.error_id === "auth.forbidden" ? { found: true, cause: "no grant", remediation: "ask an owner", exit_code: 3, retryable: false } : { found: false }),
  });
  await ctx.host.initialize();
  await ctx.host.send("/bg");
  assert.match(ctx.card().lines[0].text, /^no background work/);
  ctx.posts.length = 0;
  await ctx.host.send("!make test");
  await ctx.host.send("/bg");
  assert.equal(ctx.card().lines[0].text, "background work (1):\nfailed    shell 3s  make test  — exit 1");
  await ctx.host.send("/explain auth.forbidden");
  assert.equal(ctx.card().lines[0].text, "explain auth.forbidden\n  cause: no grant\n  fix: ask an owner\n  exit 3 · retryable no");
  await ctx.host.send("/explain nope.nope");
  assert.equal(ctx.card().lines[0].text, "explain nope.nope: unknown error id — r4 explain --list");
  await ctx.host.send("/palette");
  assert.equal(ctx.palettes, 1);
});
