// VP-2d (ADR-103): the live slash catalog in the popup and /help,
// /search-server with its pushed progress, and the composer's Esc ladder.
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
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-console-tools-"));
const esbuild = path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild");
const load = (entry, out) => {
  execFileSync(esbuild, [entry, "--bundle", "--platform=node", "--format=cjs", `--outfile=${path.join(outDir, out)}`], { stdio: "inherit", cwd: rootDir });
  return createRequire(import.meta.url)(path.join(outDir, out));
};
const { BridgeAgentHost } = load("src/bridge/bridge-agent-host.ts", "host.cjs");
const { escStep, ESC_DOUBLE_MS } = load("webview-ui/src/lib/esc-ladder.ts", "esc.cjs");
const { filterSlashSuggestions } = load("webview-ui/src/lib/mgmt-slash.ts", "slash.cjs");
process.on("exit", () => rmSync(outDir, { recursive: true, force: true }));

const SESSION = { id: "s-1", name: "One", interaction_mode: "chat", venue: "local", web_access: { mode: "none", domains: [], source: "prompt" } };
const CATALOG = [
  { name: "models", help: "Pick a model", category: "model", takesArgs: true },
  { name: "workflow", help: "UMRS workflows", category: "hpc", subcommands: ["list", "run"] },
  { name: "help", help: "the bridge's own help row", category: "help" },
];

class FakeBridge extends EventEmitter {
  constructor(handlers) {
    super();
    this.initResult = { session: SESSION, bridge_protocol: 1 };
    this.calls = [];
    this.handlers = { list_sessions: () => ({ sessions: [] }), list_slash_commands: () => ({ commands: CATALOG }), ...handlers };
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

async function makeHost(handlers = {}) {
  const ctx = { posts: [] };
  ctx.bridge = new FakeBridge(handlers);
  ctx.host = new BridgeAgentHost(ctx.bridge, (m) => ctx.posts.push(m), {});
  await ctx.host.initialize();
  ctx.of = (type) => ctx.posts.filter((p) => p.type === type);
  ctx.card = () => ctx.of("slashOutput").at(-1);
  return ctx;
}

test("the popup gets the bridge's catalog plus the host's verbs (the host's entry wins a shared name)", async () => {
  const ctx = await makeHost();
  const names = ctx.of("slashCatalog")[0].commands.map((c) => c.name);
  for (const name of ["models", "workflow", "venue", "rewind", "btw", "search-server", "tab"]) assert.ok(names.includes(name), name);
  assert.deepEqual(names, [...names].sort());
  const help = ctx.of("slashCatalog")[0].commands.find((c) => c.name === "help");
  assert.match(help.hint, /^Commands by category/);
});

test("a bridge without the catalog verb still offers the host's verbs", async () => {
  const ctx = await makeHost({
    list_slash_commands: () => {
      throw new Error("unknown method");
    },
  });
  assert.ok(ctx.of("slashCatalog")[0].commands.some((c) => c.name === "venue"));
});

test("/help lists commands by category with the keys; /help <command> shows its subcommands", async () => {
  const ctx = await makeHost();
  await ctx.host.send("/help");
  const text = ctx.card().lines[0].text;
  assert.match(text, /^companion:\n(?: {2}\/.*\n)* {2}\/sandbox — /m);
  assert.match(text, /hpc:\n {2}\/workflow — UMRS workflows/);
  assert.match(text, /Esc Esc on an empty composer rewinds/);
  await ctx.host.send("/help /workflow");
  assert.equal(ctx.card().lines[0].text, "/workflow — UMRS workflows\n  subcommands: list, run");
  await ctx.host.send("/help nope");
  assert.deepEqual(ctx.card().lines[0], { kind: "error", text: "no command /nope — /help lists them" });
});

test("/search-server: status, up (accepted or already starting), down; a start's events become notices", async () => {
  let accepted = true;
  const ctx = await makeHost({
    "search_server.status": () => ({ state: "none", engine: "podman", starting: false }),
    "search_server.up": () => ({ accepted }),
    "search_server.down": () => ({ state: "running", reason: "container busy" }),
  });
  await ctx.host.send("/search-server");
  assert.match(ctx.card().lines[0].text, /^search server: none — \/search-server up starts a local SearXNG with podman/);
  await ctx.host.send("/search-server up");
  assert.equal(ctx.card().lines[0].text, "search server: starting a local SearXNG for web_search…");
  accepted = false;
  await ctx.host.send("/search-server start");
  assert.equal(ctx.card().lines[0].text, "search server: a start is already running");
  ctx.bridge.emit("search_server_event", { phase: "progress", message: "pulling image" });
  ctx.bridge.emit("search_server_event", { phase: "done", url: "http://127.0.0.1:8888" });
  assert.deepEqual(ctx.of("notice").slice(-2).map((n) => n.text), [
    "search server: pulling image",
    "✓ search server ready at http://127.0.0.1:8888 — web_search works in sessions with Public web access (/web)",
  ]);
  await ctx.host.send("/search-server down");
  assert.deepEqual(ctx.card().lines[0], { kind: "error", text: "search server: not stopped — container busy" });
  await ctx.host.send("/search-server sideways");
  assert.equal(ctx.card().lines[0].text, "usage: /search-server [status|up|down]");
});

test("/worktree (ADR-108): list with the mode, enter, a refusal, mode; the agent's moves become notices", async () => {
  const row = { id: "feat", branch: "r4/feat", path: "/r/.r4/worktrees/feat", base: "b", parent: "", owner: "", level: "session", snapshot_ref: "", created_at: 0, changed: false };
  const ctx = await makeHost({
    "worktree.list": () => ({ worktrees: [row], current: "feat", workspace_root: "/r", mode: "auto", mode_source: "profile" }),
    "worktree.enter": (p) => (p.name === "taken" ? { ok: false, message: "already in worktree feat: leave it first" } : { ok: true, message: `worktree ${p.name} created and entered` }),
    "worktree.mode": (p) => ({ ok: true, message: "", mode: p.mode || "off", mode_source: p.mode ? "session" : "default" }),
  });
  await ctx.host.send("/worktree");
  assert.match(ctx.card().lines[0].text, /▸ feat  r4\/feat  clean[\s\S]*worktree mode: auto \(profile\)/);
  await ctx.host.send("/worktree new-one");
  assert.equal(ctx.card().lines[0].text, "worktree new-one created and entered");
  await ctx.host.send("/worktree taken");
  assert.deepEqual(ctx.card().lines[0], { kind: "error", text: "already in worktree feat: leave it first" });
  await ctx.host.send("/worktree mode auto");
  assert.match(ctx.card().lines[0].text, /^worktree mode: auto \(session\)/);
  await ctx.host.send("/worktree help");
  assert.match(ctx.card().lines[0].text, /^usage: \/worktree/);
  ctx.bridge.emit("worktree_event", { action: "enter", source: "auto", ok: true, message: "worktree auto-s1 created and entered" });
  ctx.bridge.emit("worktree_event", { action: "enter", source: "user", ok: true, message: "not repeated" });
  const notices = ctx.of("notice").map((n) => n.text);
  assert.ok(notices.includes("auto worktree auto-s1 created and entered"));
  assert.ok(!notices.some((t) => t.includes("not repeated")));
});

test("the Esc ladder: stop while running; Esc Esc within the window on an empty composer rewinds", () => {
  const base = { running: false, empty: true, canRewind: true, armedAt: 0, now: 10_000 };
  assert.deepEqual(escStep({ ...base, running: true, empty: false }), { action: "stop", armedAt: 0 });
  const first = escStep(base);
  assert.deepEqual(first, { action: "arm", armedAt: 10_000 });
  assert.deepEqual(escStep({ ...base, armedAt: first.armedAt, now: 10_000 + ESC_DOUBLE_MS }), { action: "rewind", armedAt: 0 });
  assert.equal(escStep({ ...base, armedAt: first.armedAt, now: 10_001 + ESC_DOUBLE_MS }).action, "arm", "too slow re-arms");
  assert.equal(escStep({ ...base, empty: false }).action, "none", "a draft is never rewound over");
  assert.equal(escStep({ ...base, canRewind: false }).action, "none", "the ACP backend has no rewind");
});

test("the popup filter: the live catalog joins this surface's verbs; a shared name keeps the surface's entry", () => {
  const live = [
    { name: "venue", hint: "server" },
    { name: "workflow", hint: "the bridge's" },
  ];
  const all = filterSlashSuggestions("/", live).map((s) => s.name);
  assert.ok(all.includes("venue") && all.includes("undo"));
  assert.equal(all.filter((n) => n === "workflow").length, 1);
  assert.equal(filterSlashSuggestions("/work", live)[0].runs, "mgmt");
  assert.deepEqual(filterSlashSuggestions("/ve", live).map((s) => s.name), ["venue"]);
  assert.deepEqual(filterSlashSuggestions("/venue st", live), [], "no popup once arguments start");
});

test("an error gets the registry's hint as the console's error card does; a miss or a failed lookup adds nothing", async () => {
  const ctx = await makeHost({
    "explain.match": (p) =>
      /forbidden/.test(p.message) ? { found: true, error_id: "auth.forbidden", remediation: "ask a project owner for a grant" } : { found: false },
    export_session: (p) => {
      throw new Error(p.session_id === "s-x" ? "403 forbidden for this project" : "disk full");
    },
  });
  await ctx.host.exportSession("s-x");
  await new Promise((r) => setImmediate(r));
  assert.equal(ctx.of("notice").at(-1).text, "? auth.forbidden: ask a project owner for a grant (/explain auth.forbidden)");
  const notices = ctx.of("notice").length;
  await ctx.host.exportSession("s-y");
  await new Promise((r) => setImmediate(r));
  assert.equal(ctx.of("notice").length, notices, "no match, no hint");
  ctx.bridge.handlers["explain.match"] = () => {
    throw new Error("unknown method");
  };
  await ctx.host.exportSession("s-x");
  await new Promise((r) => setImmediate(r));
  assert.equal(ctx.of("notice").length, notices, "a failed lookup adds nothing");
  assert.equal(ctx.of("error").length, 3, "and never a second error");
});
