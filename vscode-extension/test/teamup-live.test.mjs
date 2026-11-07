// /teamup on|off, the armed fire, and the live team/plan rows (ADR-103
// VP-4b part 2) — the console's rules and texts (lib/teamup-text.ts).
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
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-teamup-live-"));
const esbuild = path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild");
const load = (entry, out, extra = []) => {
  execFileSync(esbuild, [entry, "--bundle", "--platform=node", "--format=cjs", ...extra, `--outfile=${path.join(outDir, out)}`], { stdio: "inherit", cwd: rootDir });
  return createRequire(import.meta.url)(path.join(outDir, out));
};
const { BridgeAgentHost } = load("src/bridge/bridge-agent-host.ts", "host.cjs");
const { applyEventForTest, initialStateForTest } = load("webview-ui/src/state/store.tsx", "store.cjs", ["--loader:.tsx=tsx"]);
process.on("exit", () => rmSync(outDir, { recursive: true, force: true }));

const SESSION = { id: "s-1", name: "One", interaction_mode: "chat", venue: "local", web_access: { mode: "none", domains: [], source: "prompt" } };
const TEMPLATES = { templates: [{ name: "review" }, { name: "survey" }] };
const CREWS = { crews: [{ name: "research", template: "survey", aliases: ["r"] }, { name: "hpc", template: null }] };

class FakeBridge extends EventEmitter {
  constructor(handlers) {
    super();
    this.initResult = { session: SESSION, bridge_protocol: 1 };
    this.calls = [];
    this.handlers = {
      list_sessions: () => ({ sessions: [] }),
      "team.plan_templates": () => TEMPLATES,
      "teamup.crews": () => CREWS,
      send_message: () => ({ response: "a normal turn" }),
      ...handlers,
    };
  }
  get turnInFlight() {
    return false;
  }
  async ensureStarted() {}
  async reconnect() {}
  async request(method, params = {}, timeoutMs) {
    this.calls.push([method, params, timeoutMs]);
    const handler = this.handlers[method];
    return handler ? await handler(params, this) : {};
  }
  respondApproval() {}
  respondAskUser() {}
  cancelTurn() {}
}

async function makeHost(handlers = {}) {
  const ctx = { posts: [] };
  ctx.bridge = new FakeBridge(handlers);
  ctx.host = new BridgeAgentHost(ctx.bridge, (m) => ctx.posts.push(m), {});
  await ctx.host.initialize();
  ctx.of = (type) => ctx.posts.filter((p) => p.type === type);
  ctx.card = () => ctx.of("slashOutput").at(-1)?.lines[0];
  ctx.calls = (method) => ctx.bridge.calls.filter(([m]) => m === method);
  return ctx;
}

const tick = () => new Promise((r) => setImmediate(r));

test("arming: a crew that is a plan (by name or alias), plan:<name> with args, hpc with a width; the chip follows", async () => {
  const ctx = await makeHost();
  await ctx.host.send("/teamup on");
  assert.match(ctx.card().text, /^team plan armed \(survey\) — your NEXT chat message is its goal/);
  assert.equal(ctx.of("teamArmed").at(-1).label, "team:on · plan:survey");
  await ctx.host.send("/teamup on plan:review pr=42");
  assert.match(ctx.card().text, /^team plan armed \(review; pr=42\)/);
  await ctx.host.send("/teamup on hpc 3");
  assert.equal(ctx.card().text, "teamup armed (hpc, 3 parallel roles) — your NEXT chat message starts a team on that text (then auto-disarm). /teamup off cancels.");
  assert.equal(ctx.of("teamArmed").at(-1).label, "team:on · hpc ×3");
  await ctx.host.send("/teamup off");
  assert.equal(ctx.card().text, "teamup disarmed — next message stays a normal turn");
  assert.equal(ctx.of("teamArmed").at(-1).label, "");
});

test("arming refusals: an unknown template or crew, a bad width, an arg that is not name=value", async () => {
  const ctx = await makeHost();
  await ctx.host.send("/teamup on plan:nope");
  assert.equal(ctx.card().text, 'teamup on plan: no template "nope" — available: review, survey');
  await ctx.host.send("/teamup on wizards");
  assert.equal(ctx.card().text, "teamup on: crew must be research|hpc, or plan:<template>");
  await ctx.host.send("/teamup on hpc 9");
  assert.equal(ctx.card().text, "teamup on: parallel width must be an integer 1-8");
  await ctx.host.send("/teamup on plan:review loose");
  assert.equal(ctx.card().text, 'teamup on plan:review: "loose" is not name=value — the goal is your NEXT message');
  assert.equal(ctx.of("teamArmed").length, 0, "nothing was armed");
});

test("the armed message runs the plan (its card's long wait) instead of a turn, once; a refusal says why", async () => {
  const ctx = await makeHost({ "team.plan_run": () => ({ ok: true, run_id: "run-7", plan: "review", status: "running" }) });
  await ctx.host.send("/teamup on plan:review pr=42");
  await ctx.host.send("review the parser change");
  await tick();
  const [, params, timeout] = ctx.calls("team.plan_run")[0];
  assert.deepEqual(params, { template: "review", goal: "review the parser change", args: { pr: "42" } });
  assert.equal(timeout, 15 * 60_000);
  assert.equal(ctx.calls("send_message").length, 0, "not a chat turn");
  assert.deepEqual(ctx.of("notice").at(-1), {
    type: "notice",
    sessionId: "s-1",
    text: "⚙ plan review started (run-7) — agents stream below; Work pane → teams (Enter result, c cancel)",
    key: "plan:run-7",
  });
  await ctx.host.send("and now a normal message");
  assert.equal(ctx.calls("send_message").length, 1, "auto-disarmed");

  const rejected = await makeHost({ "team.plan_run": () => ({ ok: false, status: "rejected" }) });
  await rejected.host.send("/teamup on plan:review");
  await rejected.host.send("go");
  await tick();
  assert.equal(rejected.of("notice").at(-1).text, "plan review not started — the card was not approved (nothing ran)");
});

test("the armed hpc crew starts through teamup.start with its width", async () => {
  const ctx = await makeHost({ "teamup.start": () => ({ accepted: true, crew: "hpc" }) });
  await ctx.host.send("/teamup on hpc 2");
  await ctx.host.send("tune the solver");
  await tick();
  assert.deepEqual(ctx.calls("teamup.start")[0][1], { goal: "tune the solver", crew: "hpc", max_iterations: 6, run: true, parallel_roles: 2 });
  assert.equal(ctx.of("notice").at(-1).text, "⚙ team bootstrap accepted (hpc) — the session opens when ready, follow live in the Work pane");
});

test("team_event lines: one keyed row per team, the start, progress and finish; a start error and a degraded tailor stand alone", async () => {
  const ctx = await makeHost();
  ctx.bridge.emit("team_event", { kind: "team_start_error", goal: "g", error_text: "no crew" });
  assert.equal(ctx.of("notice").at(-1).text, "team start failed (g) — no crew");
  ctx.bridge.emit("team_event", { kind: "team_started", session_id: "ts-1", session_name: "survey", crew: "hpc", roles: ["pm", "se"], iteration: 0, tailor: { degraded: "endpoint down" } });
  const started = ctx.of("notice").filter((n) => n.key === "team:ts-1").at(-1);
  assert.equal(started.text, "⚙ survey [hpc] — roles {pm,se} — follow live (Work pane, Enter)");
  assert.match(ctx.of("notice").at(-1).text, /^⚠ prompt tailoring skipped — .*endpoint down/);
  ctx.bridge.emit("team_event", { kind: "agent_status", session_id: "ts-1", iteration: 1, max_iterations: 6, role: "pm", status: "thinking" });
  assert.equal(ctx.of("notice").at(-1).text, "team survey — iter 1/6 · i1 pm → thinking");
  ctx.bridge.emit("team_event", { kind: "run_finished", session_id: "ts-1", iteration: 1, continued: true });
  assert.equal(ctx.of("notice").at(-1).text, "team survey — iteration 1 done (continues)");
  assert.equal(ctx.of("notice").at(-1).key, "team:ts-1");
});

test("plan events: agent results update the run's row, a gate asks on its own row, the finish closes it", async () => {
  const ctx = await makeHost();
  ctx.bridge.emit("team_event", { run_id: "run-7", plan: "review", kind: "plan_started" });
  ctx.bridge.emit("team_event", { run_id: "run-7", plan: "review", kind: "gate_requested", gate: "g1", class: "write", prompt: "edit files?" });
  assert.match(ctx.of("notice").at(-1).text, /^⏸ plan review: gate g1 \[write\] asks you — edit files\? \(answer the approval card\)$/);
  assert.equal(ctx.of("notice").at(-1).key, undefined);
  ctx.bridge.emit("team_event", { run_id: "run-7", plan: "review", kind: "agent_result", agent: "reader", answer: "looks fine" });
  const row = ctx.of("notice").at(-1);
  assert.equal(row.key, "plan:run-7");
  assert.match(row.text, /^plan review — /);
  ctx.bridge.emit("team_event", { run_id: "run-7", plan: "review", kind: "plan_finished", status: "succeeded" });
  assert.match(ctx.of("notice").at(-1).text, /^⏺ plan review — finished succeeded · 1 agent result \(Work pane → Enter for the result\)$/);
});

test("the webview keeps one row per key and replaces it in place", () => {
  const SID = "s-1";
  const drive = (events) => events.reduce((s, e) => applyEventForTest(s, e), initialStateForTest());
  const state = drive([
    { type: "notice", sessionId: SID, text: "team survey — iter 1 · started", key: "team:ts-1" },
    { type: "notice", sessionId: SID, text: "an ordinary notice" },
    { type: "notice", sessionId: SID, text: "team survey — iter 2 · i2 pm → idle", key: "team:ts-1" },
    { type: "teamArmed", sessionId: SID, label: "team:on · hpc" },
  ]);
  assert.deepEqual(state.transcripts[SID].map((e) => e.text), ["team survey — iter 2 · i2 pm → idle", "an ordinary notice"]);
  assert.equal(state.extras.teamArmed[SID], "team:on · hpc");
});
