// The agent view's chat over the bridge, against a scripted bridge (ADR-103 VP-2).
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
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-agent-host-"));
const bundle = path.join(outDir, "host.cjs");
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  ["src/bridge/bridge-agent-host.ts", "--bundle", "--platform=node", "--format=cjs", `--outfile=${bundle}`],
  { stdio: "inherit", cwd: rootDir },
);
const { BridgeAgentHost, approvalOptions, toSessionSummary } = createRequire(import.meta.url)(bundle);
const { BridgeRequestError } = createRequire(import.meta.url)(
  (() => {
    const wire = path.join(outDir, "wire.cjs");
    execFileSync(
      path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
      ["../packages/r4-tui-v3/src/bridge/wire.ts", "--bundle", "--platform=node", "--format=cjs", `--outfile=${wire}`],
      { stdio: "inherit", cwd: rootDir },
    );
    return wire;
  })(),
);
process.on("exit", () => rmSync(outDir, { recursive: true, force: true }));

const SESSION = { id: "s-1", name: "Session one", model: "m", interaction_mode: "chat" };

/** A bridge whose replies are scripted per method; `during` runs while a turn is pending. */
class FakeBridge extends EventEmitter {
  constructor(handlers = {}) {
    super();
    this.initResult = { session: SESSION, bridge_protocol: 1 };
    this.calls = [];
    this.approvals = [];
    this.answers = [];
    this.cancels = 0;
    this.pendingTurns = 0;
    this.handlers = {
      list_sessions: () => ({ sessions: [{ id: "s-1", name: "Session one", pinned: true }, { id: "old", archived_at: "2026-10-01" }] }),
      "session.queue_remote": (p) => ({ applied: true, queue: p.action === "append" ? [p.content] : [] }),
      get_session_changes: () => ({ changes: [] }),
      get_session_plan: () => ({ steps: [] }),
      ...handlers,
    };
  }
  get turnInFlight() {
    return this.pendingTurns > 0;
  }
  async ensureStarted() {}
  async reconnect() {
    this.calls.push(["reconnect", {}]);
    this.initResult = { session: { id: "s-3", name: "New" }, bridge_protocol: 1 };
  }
  async request(method, params = {}) {
    this.calls.push([method, params]);
    const turn = method === "send_message" || method === "confirm_plan" || method === "dispatch_slash_command";
    if (turn) this.pendingTurns++;
    try {
      const handler = this.handlers[method];
      return handler ? await handler(params, this) : {};
    } finally {
      if (turn) this.pendingTurns--;
    }
  }
  respondApproval(id, decision) {
    this.approvals.push([id, decision]);
  }
  respondAskUser(id, answer) {
    this.answers.push([id, answer]);
  }
  cancelTurn() {
    this.cancels++;
  }
}

/** A `!cmd` runner that answers with a scripted result. */
class FakeShell {
  constructor(result) {
    this.result = result;
    this.commands = [];
    this.stops = 0;
  }
  get running() {
    return false;
  }
  async run(command) {
    this.commands.push(command);
    return { command, exitCode: 0, stdout: "", stderr: "", durationMs: 3, ...this.result };
  }
  stop() {
    this.stops++;
    return 0;
  }
}

function makeHost(handlers, shellResult = {}) {
  const bridge = new FakeBridge(handlers);
  const posts = [];
  const prompts = [];
  const opened = [];
  const confirms = [];
  const confirmAnswer = { value: true };
  const webAsks = [];
  const webAnswer = { value: { mode: "none", domains: [] } };
  const shell = new FakeShell(shellResult);
  const host = new BridgeAgentHost(bridge, (m) => posts.push(m), {
    onPrompt: (t) => prompts.push(t),
    shell,
    openDocument: (title, content) => opened.push(["doc", title, content]),
    openDiff: (path, before, after) => opened.push(["diff", path, before, after]),
    confirm: async (message, action) => {
      confirms.push([message, action]);
      return confirmAnswer.value;
    },
    workspaceFiles: () => ["src/app.ts", "docs/guide.md", "src/bridge/host.ts"],
    pickWebAccess: async (ask) => {
      webAsks.push(ask);
      return webAnswer.value;
    },
  });
  return { bridge, host, posts, prompts, opened, shell, confirms, confirmAnswer, webAsks, webAnswer, types: () => posts.map((p) => p.type) };
}

const tick = () => new Promise((r) => setImmediate(r));

test("initialize selects the bridge's session, its mode, and lists live sessions", async () => {
  const { host, posts, types } = makeHost();
  await host.initialize();
  assert.deepEqual(types(), ["status", "backendInfo", "sessionSelected", "modeChanged", "venueState", "policyState", "sessions", "status", "slashCatalog"]);
  assert.equal(posts[2].session.id, "s-1");
  assert.equal(posts[3].mode, "ask", "bridge chat mode is the webview's ask mode");
  assert.equal(posts[4].chip, "", "a local session has no remote: chip");
  assert.deepEqual(posts[6].sessions.map((s) => s.id), ["s-1"], "archived rows are dropped");
  assert.equal(host.currentSessionId, "s-1");
});

test("a turn streams the reply through the console's fold and closes with the final text", async () => {
  const { host, posts, types, prompts } = makeHost({
    send_message: (params, bridge) => {
      bridge.emit("event", { type: "response_start" });
      bridge.emit("event", { type: "response_chunk", message: "Hel" });
      bridge.emit("event", { type: "response_chunk", message: "lo" });
      bridge.emit("event", { type: "response_done", message: "Hello" });
      bridge.emit("control_event", { type: "tool_call_start", payload: { tool_call_id: "t1", name: "file_read", args_summary: "a.py" } });
      bridge.emit("control_event", { type: "tool_call_end", payload: { tool_call_id: "t1", name: "file_read", status: "done", duration_ms: 4 } });
      bridge.emit("control_event", { type: "thinking_delta", payload: { seq: 0, text: "hm" } });
      bridge.emit("control_event", { type: "cost", payload: { usd: 0.01 } });
      return { response: "Hello", state: { session: SESSION } };
    },
  });
  await host.initialize();
  posts.length = 0;
  await host.send("  hi  ");
  assert.deepEqual(prompts, ["hi"]);
  assert.deepEqual(types().slice(0, 9), [
    "userMessage", "sessionState", "assistantDelta", "assistantDelta", "toolCall", "toolCall", "thinkingDelta", "costState", "assistantComplete",
  ]);
  assert.deepEqual(posts.filter((p) => p.type === "assistantDelta").map((p) => p.text), ["Hel", "lo"], "a streamed done is not repeated");
  assert.equal(posts.find((p) => p.type === "toolCall" && p.status === "completed").durationMs, 4);
  assert.equal(posts.find((p) => p.type === "assistantComplete").text, "Hello");
  assert.deepEqual(posts.find((p) => p.type === "sessionState" && p.state === "idle"), { type: "sessionState", sessionId: "s-1", state: "idle" });
});

test("a slash line goes to the bridge's slash dispatcher and runs its follow-up turns", async () => {
  const { host, bridge, posts } = makeHost({
    dispatch_slash_command: () => ({ output: [{ kind: "info", text: "retrying" }], follow_up_messages: ["again please"], state: { session: SESSION } }),
    send_message: () => ({ response: "ok", state: { session: SESSION } }),
  });
  await host.initialize();
  await host.send("/retry");
  assert.deepEqual(bridge.calls.filter(([m]) => m === "dispatch_slash_command" || m === "send_message"), [
    ["dispatch_slash_command", { command: "/retry" }],
    ["send_message", { text: "again please" }],
  ]);
  assert.ok(posts.some((p) => p.type === "slashOutput" && p.lines[0].text === "retrying"));
});

test("a message typed while a turn runs is queued, then sent when the turn ends", async () => {
  const releases = [];
  const { host, bridge, posts } = makeHost({
    send_message: () => new Promise((resolve) => releases.push(() => resolve({ response: "done" }))),
  });
  await host.initialize();
  const first = host.send("one");
  await tick();
  await host.send("two");
  assert.deepEqual(posts.filter((p) => p.type === "queueState").at(-1).items, ["two"]);
  await host.send("/help");
  assert.match(posts.at(-1).message, /wait for it, or stop it/, "a slash line is not queued");
  await host.selectSession("other");
  assert.match(posts.at(-1).message, /A turn is running/);
  releases.shift()();
  while (releases.length === 0) await tick();
  releases.shift()();
  await first;
  const sends = bridge.calls.filter(([m]) => m === "send_message").map(([, p]) => p.text);
  assert.deepEqual(sends, ["one", "two"]);
  const queueOps = bridge.calls.filter(([m]) => m === "session.queue_remote").map(([, p]) => p.action);
  assert.deepEqual(queueOps, ["append", "pop"]);
  assert.deepEqual(posts.filter((p) => p.type === "queueState").at(-1).items, []);
  assert.equal(posts.filter((p) => p.type === "userMessage" && p.text === "two").length, 1, "echoed once, when it runs");
});

test("a stop gives the queued text back and clears the queue", async () => {
  let release;
  const { host, bridge, posts, shell } = makeHost({
    send_message: () => new Promise((resolve) => (release = () => resolve({ response: "", cancelled: true }))),
  });
  await host.initialize();
  const first = host.send("one");
  await tick();
  await host.send("two");
  await host.send("three");
  host.cancel();
  await tick();
  assert.equal(bridge.cancels, 1);
  assert.equal(shell.stops, 1);
  assert.deepEqual(posts.find((p) => p.type === "restoreDraft"), { type: "restoreDraft", sessionId: "s-1", text: "two\nthree" });
  assert.equal(bridge.calls.at(-1)[1].action, "clear");
  release();
  await first;
  assert.deepEqual(bridge.calls.filter(([m]) => m === "send_message").map(([, p]) => p.text), ["one"]);
});

test("a refused queue append gives the text back; an unanswered one stays queued and warns", async () => {
  let release;
  const refused = makeHost({
    send_message: () => new Promise((resolve) => (release = () => resolve({ response: "ok" }))),
    "session.queue_remote": () => {
      throw new BridgeRequestError("down", "", "ConnectionError");
    },
    "session.queue_append": () => {
      throw new BridgeRequestError("conflict", "", "SessionConflictError", { code: "session_conflict" });
    },
  });
  await refused.host.initialize();
  const first = refused.host.send("one");
  await tick();
  await refused.host.send("two");
  assert.match(refused.posts.find((p) => p.type === "error").message, /queue append failed: conflict/);
  assert.deepEqual(refused.posts.find((p) => p.type === "restoreDraft").text, "two");
  release();
  await first;

  const unanswered = makeHost({
    send_message: () => new Promise((resolve) => (release = () => resolve({ response: "ok" }))),
    "session.queue_remote": () => {
      throw new Error("bridge request timed out: session.queue_remote");
    },
    "session.queue_append": () => {
      throw new Error("bridge request timed out: session.queue_append");
    },
  });
  await unanswered.host.initialize();
  const again = unanswered.host.send("one");
  await tick();
  await unanswered.host.send("two");
  assert.match(unanswered.posts.find((p) => p.type === "error").message, /unconfirmed/);
  assert.equal(unanswered.posts.some((p) => p.type === "restoreDraft"), false);
  assert.deepEqual(unanswered.posts.filter((p) => p.type === "queueState").at(-1).items, ["two"]);
  release();
  await tick();
  release();
  await again;
});

test("an approval becomes an inline card; only a pending ask is answered, with a known decision", async () => {
  const { host, bridge, posts } = makeHost();
  await host.initialize();
  bridge.emit("approval", { id: 7, tool: "file_edit", description: "edit a.py", path: "a.py", diff: "-a\n+b", session_approval_allowed: true });
  const card = posts.at(-1);
  assert.equal(card.type, "approvalRequest");
  assert.deepEqual(card.options, ["once", "always", "reject"], "an ask without options derives all three (ADR-104 amendment 1)");
  assert.equal(card.diff, "-a\n+b");
  host.answerApproval(7, "always");
  host.answerApproval(7, "once");
  host.answerApproval(99, "once");
  assert.deepEqual(bridge.approvals, [[7, "always"]]);
  bridge.emit("approval", { id: 8, tool: "shell_exec" });
  host.answerApproval(8, "yes-please");
  assert.deepEqual(bridge.approvals.at(-1), [8, "reject"], "an unknown decision is a rejection");
});

test("approval options follow the console's lib/approval-options.ts (ADR-104 D-3)", () => {
  assert.deepEqual(approvalOptions({ options: ["always", "bogus"] }), ["once", "always", "reject"], "once and reject always stay");
  assert.deepEqual(approvalOptions({ options: ["always_project"] }), ["once", "reject"], "an older bridge's project answer is not shown");
  assert.deepEqual(approvalOptions({ options: ["once", "reject"], fenced: true }), ["once", "reject"]);
  assert.deepEqual(approvalOptions({ fenced: true }), ["once", "reject"]);
  assert.deepEqual(approvalOptions({ risk_tier: "never_auto" }), ["once", "reject"]);
  assert.deepEqual(approvalOptions({ session_approval_allowed: false }), ["once", "reject"]);
  assert.deepEqual(approvalOptions({}), ["once", "always", "reject"]);
});

test("ask_user becomes a card and its answer goes back once", async () => {
  const { host, bridge, posts } = makeHost();
  await host.initialize();
  bridge.emit("ask_user", { id: "q1", question: "Which file?", options: ["a", "", "b"] });
  assert.deepEqual(posts.at(-1), { type: "askUserRequest", sessionId: "s-1", id: "q1", question: "Which file?", options: ["a", "b"] });
  host.answerQuestion("q1", "a");
  host.answerQuestion("q1", "b");
  assert.deepEqual(bridge.answers, [["q1", "a"]]);
});

test("cards still open when the turn ends are closed", async () => {
  const { host, posts } = makeHost({
    send_message: (_p, bridge) => {
      bridge.emit("approval", { id: 3, tool: "shell_exec" });
      return { response: "", cancelled: true };
    },
  });
  await host.initialize();
  await host.send("go");
  assert.ok(posts.some((p) => p.type === "cancelled"));
  assert.deepEqual(posts.find((p) => p.type === "approvalResolved"), { type: "approvalResolved", sessionId: "s-1", id: 3, decision: "expired" });
});

test("a plan awaiting confirmation asks; confirm sends the action", async () => {
  const { host, bridge, posts } = makeHost({
    send_message: () => ({ response: "1. do it", state: { session: { ...SESSION, plan_awaiting_confirmation: true, plan_pending_request: "refactor" } } }),
    confirm_plan: () => ({ response: "done" }),
  });
  await host.initialize();
  await host.send("plan it");
  assert.deepEqual(posts.find((p) => p.type === "planConfirm"), { type: "planConfirm", sessionId: "s-1", request: "refactor" });
  await host.confirmPlan("bogus");
  await host.confirmPlan("execute_auto");
  assert.deepEqual(bridge.calls.filter(([m]) => m === "confirm_plan"), [["confirm_plan", { action: "execute_auto" }]]);
});

test("a before_turn conflict gives the text back; an after_turn one warns against resending", async () => {
  const conflict = (stage) => () => {
    throw new BridgeRequestError("conflict", "/sessions resume", "SessionConflictError", { code: "session_conflict", stage });
  };
  const before = makeHost({ send_message: conflict("before_turn") });
  await before.host.initialize();
  await before.host.send("hello");
  assert.deepEqual(before.posts.find((p) => p.type === "restoreDraft"), { type: "restoreDraft", sessionId: "s-1", text: "hello" });
  assert.match(before.posts.find((p) => p.type === "error").message, /conflict\n→ \/sessions resume/);

  const after = makeHost({ send_message: conflict("after_turn") });
  await after.host.initialize();
  await after.host.send("hello");
  assert.equal(after.posts.some((p) => p.type === "restoreDraft"), false);
  assert.match(after.posts.find((p) => p.type === "error").message, /do not send it again/);
});

test("session verbs: load, new, rename, pin, archive, compact, mode, model", async () => {
  const { host, bridge, posts } = makeHost({
    load_session: () => ({ session: { id: "s-2", name: "Two", interaction_mode: "autopilot" }, messages: [{ role: "user", content: "x", timestamp: "" }] }),
    set_interaction_mode: (p) => ({ mode: p.mode }),
    "session.compact": () => ({ changed: true, from: 20, to: 6, keep_recent: 6 }),
  });
  await host.initialize();
  await host.selectSession("s-2");
  assert.deepEqual(posts.find((p) => p.type === "sessionSelected" && p.session.id === "s-2").messages, [{ role: "user", content: "x" }]);
  assert.equal(posts.filter((p) => p.type === "modeChanged").at(-1).mode, "agent");
  await host.newSession();
  assert.equal(host.currentSessionId, "s-3");
  await host.renameSession("s-3", "Renamed");
  await host.pinSession("s-3", false);
  await host.archiveSession("s-3");
  await host.compactSession("s-3");
  await host.setMode("plan");
  await host.setModel("qwen");
  const verbs = bridge.calls
    .map(([m, p]) => `${m} ${JSON.stringify(p)}`)
    .filter((c) => !c.startsWith("list_sessions") && !c.startsWith("list_slash_commands"));
  assert.deepEqual(verbs, [
    'load_session {"session_id":"s-2"}',
    "reconnect {}",
    'session.rename {"session_id":"s-3","name":"Renamed"}',
    'session.unpin {"session_id":"s-3"}',
    'session.archive {"session_id":"s-3"}',
    'session.compact {"keep_recent":6}',
    'set_interaction_mode {"mode":"plan"}',
    'set_model {"model":"qwen"}',
  ]);
  assert.ok(posts.some((p) => p.type === "sessionDeleted" && p.sessionId === "s-3"));
  assert.ok(posts.some((p) => p.type === "compactResult" && p.changed && p.to === 6));
});

test("session rows map to the webview summary", () => {
  assert.deepEqual(toSessionSummary({ id: "abcdef123", name: "", pinned: 1, message_count: 3 }), {
    venue: undefined, state: undefined, sync: undefined, archived: false,
    id: "abcdef123", name: "abcdef12", model: undefined, updated_at: undefined, message_count: 3, project_id: undefined, pinned: true,
  });
  assert.equal(toSessionSummary({ id: "" }), null);
});

test("a reloaded webview (ready again) repaints the session on screen, not the first one", async () => {
  const { host, bridge, posts } = makeHost({
    load_session: (p) => ({ session: { id: p.session_id, name: "Two" }, messages: [{ role: "assistant", content: "hi" }] }),
  });
  await host.initialize();
  await host.selectSession("s-2");
  posts.length = 0;
  await host.initialize();
  const selected = posts.find((p) => p.type === "sessionSelected");
  assert.equal(selected.session.id, "s-2");
  assert.deepEqual(selected.messages, [{ role: "assistant", content: "hi" }]);
  assert.equal(bridge.calls.filter(([m]) => m === "load_session").length, 2);
});

test("cancel stops the turn out of band", async () => {
  const { host, bridge } = makeHost();
  host.cancel();
  assert.equal(bridge.cancels, 1);
});

test("!cmd runs in the user's shell, shows its output, and relays it masked to the agent", async () => {
  const { host, bridge, posts, shell } = makeHost(
    { send_message: () => ({ response: "I see the listing" }) },
    { stdout: "a.py\nb.py\n", exitCode: 0 },
  );
  await host.initialize();
  await host.send("!ls --api-key sk-secret123456");
  assert.deepEqual(shell.commands, ["ls --api-key sk-secret123456"], "the shell gets the line as typed");
  const started = posts.find((p) => p.type === "shellStarted");
  assert.doesNotMatch(started.echo, /sk-secret123456/);
  const shown = posts.find((p) => p.type === "shellResult");
  assert.match(shown.text, /a\.py/);
  const relay = bridge.calls.find(([m]) => m === "send_message")[1].text;
  assert.match(relay, /the VS Code R4 view's local shell/);
  assert.match(relay, /--- stdout ---\na\.py/);
  assert.doesNotMatch(relay, /sk-secret123456/);
  assert.equal(posts.some((p) => p.type === "userMessage"), false, "the framed relay is not echoed as your message");
});

test("a stopped !cmd is not relayed", async () => {
  const { host, bridge } = makeHost({}, { interrupted: true, exitCode: 130 });
  await host.initialize();
  await host.send("!sleep 100");
  assert.equal(bridge.calls.some(([m]) => m === "send_message"), false);
});

test("turn ids, citations, changed files and the plan follow a turn", async () => {
  const { host, bridge, posts, opened } = makeHost({
    send_message: (_p, b) => {
      b.emit("event", { type: "turn_opened", data: { turn_id: "t-7" } });
      return { response: "ok", turn_id: "t-7", citations: [{ sid: "S1", title: "Paper", source_id: "src", project_id: "p" }] };
    },
    get_session_changes: () => ({
      changes: [{ path: "a.py", before: "x\n", after: "y\n", before_existed: true, inserted: 1, deleted: 1 }],
    }),
    get_session_plan: () => ({ steps: [{ id: "1", title: "Read", status: "completed", parent_id: null }, { id: "2", title: "Edit", status: "pending", parent_id: "1" }] }),
    "citation.open": () => ({ title: "Paper", text: "the passage" }),
    revert_session_change: () => ({ path: "a.py", reverted: true }),
  });
  await host.initialize();
  await host.send("go");
  assert.deepEqual(posts.find((p) => p.type === "turnOpened"), { type: "turnOpened", sessionId: "s-1", turnId: "t-7" });
  const cites = posts.find((p) => p.type === "citations");
  assert.equal(cites.turnId, "t-7");
  assert.equal(cites.citations[0].sid, "S1");
  assert.deepEqual(posts.find((p) => p.type === "filesChanged").files, [
    { path: "a.py", inserted: 1, deleted: 1, created: false, reverted: false },
  ]);
  assert.deepEqual(posts.find((p) => p.type === "planState").steps.map((s) => [s.title, s.depth]), [["Read", 0], ["Edit", 1]]);

  await host.openChange("a.py");
  assert.deepEqual(opened.at(-1), ["diff", "a.py", "x\n", "y\n"]);
  await host.openCitation(cites.citations[0]);
  assert.deepEqual(bridge.calls.find(([m]) => m === "citation.open")[1], { sid: "S1", source_id: "src", project_id: "p" });
  assert.match(opened.at(-1)[2], /the passage/);
  await host.revertChange("a.py");
  assert.match(posts.find((p) => p.type === "notice").text, /a\.py: reverted/);
});

test("fork from a turn opens the fork; save keeps a turn's side in knowledge", async () => {
  const { host, bridge, posts } = makeHost({
    "session.fork": () => ({ session_id: "s-9", name: "Fork", messages: 4 }),
    load_session: (p) => ({ session: { id: p.session_id, name: "Fork" }, messages: [{ role: "user", content: "x", metadata: { turn_id: "t-1" } }] }),
    "knowledge.save": () => ({ line: "saved t-1 as proposed" }),
  });
  await host.initialize();
  await host.forkFrom("t-1");
  assert.deepEqual(bridge.calls.find(([m]) => m === "session.fork")[1], { session_id: "s-1", from_turn: "t-1" });
  assert.equal(host.currentSessionId, "s-9");
  const selected = posts.filter((p) => p.type === "sessionSelected").at(-1);
  assert.deepEqual(selected.messages, [{ role: "user", content: "x", turnId: "t-1" }], "loaded messages carry their turn id");
  await host.saveTurn("t-1", "answer");
  assert.deepEqual(bridge.calls.find(([m]) => m === "knowledge.save")[1], { turn: "t-1", part: "answer" });
  assert.equal(posts.at(-1).text, "saved t-1 as proposed");
});

test("typed /fork, /changes and /save are answered here, as the console intercepts them", async () => {
  const { host, bridge, posts, confirms } = makeHost({
    "session.fork": () => ({ session_id: "s-9", name: "Fork", messages: 1 }),
    load_session: (p) => ({ session: { id: p.session_id, name: "Fork" }, messages: [] }),
    get_session_changes: () => ({ changes: [{ path: "a.py", before: "", after: "x", before_existed: false, inserted: 1, deleted: 0 }] }),
    "knowledge.save": (p) => ({ line: `saved (${p.args})` }),
    dispatch_slash_command: () => ({ output: [{ kind: "info", text: "wrote notes.md" }] }),
  });
  await host.initialize();
  await host.send("/changes");
  assert.deepEqual(posts.find((p) => p.type === "dockFocus"), { type: "dockFocus", sessionId: "s-1", panel: "files" });
  assert.equal(posts.filter((p) => p.type === "filesChanged").at(-1).files[0].path, "a.py");
  await host.send("/save 2 --vouch");
  assert.deepEqual(bridge.calls.find(([m]) => m === "knowledge.save")[1], { args: "2 --vouch" });
  assert.equal(confirms.length, 1, "the first save asks (L3)");
  await host.send("/save");
  assert.equal(confirms.length, 1, "later saves do not ask");
  await host.send("/save notes.md");
  assert.deepEqual(bridge.calls.find(([m]) => m === "dispatch_slash_command")[1], { command: "/save notes.md" }, "a transcript path is the bridge's /save");
  await host.send("/fork");
  assert.deepEqual(bridge.calls.find(([m]) => m === "session.fork")[1], { session_id: "s-1" });
  assert.equal(host.currentSessionId, "s-9");
  assert.equal(posts.filter((p) => p.type === "userMessage" && p.text.startsWith("/")).length, 5, "each line is echoed");
});

test("a slash line that cut turns drops their rows; an unhandled one says so", async () => {
  const { host, posts } = makeHost({
    dispatch_slash_command: (p) =>
      p.command === "/retry"
        ? { handled: true, output: [], dropped_turn_ids: ["t-1"], follow_up_messages: [] }
        : { handled: false, output: [] },
  });
  await host.initialize();
  await host.send("/retry");
  assert.deepEqual(posts.find((p) => p.type === "dropTurns"), { type: "dropTurns", sessionId: "s-1", turnIds: ["t-1"] });
  await host.send("/nonsense");
  assert.equal(posts.filter((p) => p.type === "error").at(-1).message, "Command not handled: /nonsense");
});

test("a declined publish confirmation saves nothing, and asks again next time", async () => {
  const { host, bridge, confirms, confirmAnswer } = makeHost({ "knowledge.save": () => ({ line: "saved" }) });
  await host.initialize();
  confirmAnswer.value = false;
  await host.saveTurn("t-1", "answer");
  assert.equal(bridge.calls.some(([m]) => m === "knowledge.save"), false);
  confirmAnswer.value = true;
  await host.saveTurn("t-1", "answer");
  assert.equal(confirms.length, 2);
  assert.equal(bridge.calls.filter(([m]) => m === "knowledge.save").length, 1);
});

test("@ candidates are ranked by the console's rules over the workspace files", async () => {
  const { host, posts } = makeHost();
  await host.initialize();
  host.mentionCandidates("host");
  assert.deepEqual(posts.at(-1), { type: "mentionCandidates", sessionId: "s-1", query: "host", items: ["src/bridge/host.ts"] });
});

test("a tool card's expand gets the full result the turn's tool_result line carried", async () => {
  const { host, posts } = makeHost({
    send_message: (_p, b) => {
      b.emit("event", { type: "tool_result", message: "short", data: { tool_call_id: "c1", name: "file_read", result: "the whole file" } });
      return { response: "ok" };
    },
  });
  await host.initialize();
  await host.send("read it");
  host.toolResult("c1");
  assert.deepEqual(posts.at(-1), { type: "toolResult", sessionId: "s-1", toolCallId: "c1", found: true, name: "file_read", content: "the whole file" });
  host.toolResult("unknown");
  assert.equal(posts.at(-1).found, false);
});

test("a session's sandbox and web access give the notice, the chip and the label (ADR-101, ADR-099)", async () => {
  const bridge = new FakeBridge();
  bridge.initResult = {
    session: {
      ...SESSION,
      venue: "local",
      sandbox: { mode: "off", available: true, notice: "Sandbox off: shell, Python and MCP servers run with your full permissions." },
      web_access: { mode: "allowlist", domains: ["a.org"] },
    },
    bridge_protocol: 1,
  };
  const posts = [];
  const host = new BridgeAgentHost(bridge, (m) => posts.push(m));
  await host.initialize();
  const policy = posts.find((p) => p.type === "policyState");
  assert.match(policy.sandboxNotice, /Sandbox off/);
  assert.equal(policy.sandboxChip, "sbx:off");
  assert.equal(policy.web, "allowlist (a.org)");
});

test("/sandbox: status, on, a typed refusal, usage", async () => {
  const { host, bridge, posts } = makeHost({
    "session.sandbox_get": () => ({ sandbox: { mode: "off", backend: "bwrap", available: true } }),
    "session.sandbox_set": (p) => {
      if (p.mode === "off") throw Object.assign(new Error("refused"), { data: { code: "sandbox.remote", reason: "a frontend session" } });
      return { sandbox: { mode: "on", backend: "bwrap", available: true }, restarted_mcp: [], still_running: [] };
    },
  });
  await host.initialize();
  const card = () => posts.filter((p) => p.type === "slashOutput").at(-1).lines[0];
  await host.send("/sandbox");
  assert.match(card().text, /^sandbox: off/);
  await host.send("/sandbox on");
  assert.deepEqual(bridge.calls.find(([m]) => m === "session.sandbox_set")[1], { mode: "on" });
  assert.equal(posts.filter((p) => p.type === "policyState").at(-1).sandboxChip, "sbx:on");
  await host.send("/sandbox default on");
  assert.deepEqual(bridge.calls.filter(([m]) => m === "session.sandbox_set").at(-1)[1], { mode: "on", default: true });
  await host.send("/sandbox off");
  assert.deepEqual(card(), { kind: "error", text: "sandbox.remote: a frontend session" });
  await host.send("/sandbox sideways");
  assert.match(card().text, /^usage: \/sandbox/);
});

test("/tab goes to the chat tabs, never the bridge; silent verbs post no card", async () => {
  const bridge = new FakeBridge({});
  const posts = [];
  const asked = [];
  const host = new BridgeAgentHost(bridge, (m) => posts.push(m), {
    tabCommand: async (args) => {
      asked.push(args);
      return args === "next" ? [] : [{ kind: "info", text: "▸ 1. Refactor" }];
    },
  });
  await host.initialize();
  await host.send("/tab");
  await host.send("/tab next");
  assert.deepEqual(asked, ["", "next"]);
  const cards = posts.filter((p) => p.type === "slashOutput");
  assert.equal(cards.length, 1);
  assert.deepEqual(cards[0].lines, [{ kind: "info", text: "▸ 1. Refactor" }]);
  assert.ok(!bridge.calls.some(([m]) => m === "dispatch_slash_command"), "the bridge never sees /tab");
  const bare = new BridgeAgentHost(new FakeBridge({}), (m) => posts.push(m), {});
  await bare.initialize();
  await bare.send("/tab list");
  assert.match(posts.filter((p) => p.type === "slashOutput").at(-1).lines[0].text, /not available/);
});

test("a session with no web choice asks before its first turn; backing out keeps the text", async () => {
  const bridge = new FakeBridge({
    send_message: () => ({ response: "ok" }),
    "session.web_access_set": (p) => ({ web_access: { mode: p.mode, domains: p.domains }, notice: p.mode === "public" ? "no search server yet" : "" }),
  });
  bridge.initResult = { session: { ...SESSION, web_access: { mode: "" } }, bridge_protocol: 1 };
  const posts = [];
  const asks = [];
  const answer = { value: null };
  const host = new BridgeAgentHost(bridge, (m) => posts.push(m), {
    pickWebAccess: async (ask) => {
      asks.push(ask);
      return answer.value;
    },
  });
  await host.initialize();
  await host.send("hello");
  assert.equal(bridge.calls.some(([m]) => m === "send_message"), false, "held while unanswered");
  assert.deepEqual(posts.find((p) => p.type === "restoreDraft"), { type: "restoreDraft", sessionId: "s-1", text: "hello" });
  answer.value = { mode: "public", domains: [] };
  await host.send("hello");
  assert.deepEqual(bridge.calls.find(([m]) => m === "session.web_access_set")[1], { mode: "public", domains: [], source: "prompt" });
  assert.equal(posts.find((p) => p.type === "notice").text, "no search server yet");
  assert.equal(bridge.calls.filter(([m]) => m === "send_message").length, 1);
  await host.send("again");
  assert.equal(asks.length, 2, "asked once per missing choice, not every turn");
  assert.equal(posts.filter((p) => p.type === "policyState").at(-1).web, "public");
});

test("Sessions filters ride the wire; archived rows come back only when asked; export opens the transcript", async () => {
  const { host, bridge, posts, opened } = makeHost({
    list_sessions: () => ({
      sessions: [
        { id: "s-1", name: "One", venue: "local", state: "ongoing", sync: "mirrored" },
        { id: "s-2", name: "Old", venue: "frontend", archived_at: "2026-10-01" },
      ],
    }),
    export_session: () => ({ markdown: "# One\n\nhello" }),
  });
  await host.initialize();
  assert.deepEqual(posts.filter((p) => p.type === "sessions").at(-1).sessions.map((s) => s.id), ["s-1"]);
  await host.setSessionFilter({ state: "stopped", venue: "frontend", includeArchived: true });
  assert.deepEqual(bridge.calls.filter(([m]) => m === "list_sessions").at(-1)[1], { state: "stopped", venue: "frontend", include_archived: true });
  const rows = posts.filter((p) => p.type === "sessions").at(-1).sessions;
  assert.deepEqual(rows.map((s) => [s.id, s.venue, s.archived]), [["s-1", "local", false], ["s-2", "frontend", true]]);
  assert.equal(rows[0].sync, "mirrored");
  await host.exportSession("s-1");
  assert.deepEqual(opened.at(-1), ["doc", "session s-1", "# One\n\nhello"]);
});

test("/web shows the choice and changes it", async () => {
  const { host, bridge, webAsks, webAnswer } = makeHost({ "session.web_access_set": (p) => ({ web_access: { mode: p.mode, domains: p.domains } }) });
  await host.initialize();
  webAnswer.value = { mode: "allowlist", domains: ["b.org"] };
  await host.send("/web");
  assert.equal(webAsks.length, 1);
  assert.deepEqual(bridge.calls.find(([m]) => m === "session.web_access_set")[1], { mode: "allowlist", domains: ["b.org"], source: "prompt" });
});
