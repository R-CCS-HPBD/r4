// A session on the server over the bridge (ADR-103 VP-3b part 2): /venue,
// the remote: chip and its poller, the source picker, push / unpush, stop.
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
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-venue-host-"));
const bundle = path.join(outDir, "host.cjs");
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  ["src/bridge/bridge-agent-host.ts", "--bundle", "--platform=node", "--format=cjs", `--outfile=${bundle}`],
  { stdio: "inherit", cwd: rootDir },
);
const { BridgeAgentHost } = createRequire(import.meta.url)(bundle);
process.on("exit", () => rmSync(outDir, { recursive: true, force: true }));

const LOCAL = { id: "s-1", name: "One", interaction_mode: "chat", venue: "local", web_access: { mode: "" } };

class FakeBridge extends EventEmitter {
  constructor(session, handlers = {}) {
    super();
    this.initResult = { session, bridge_protocol: 1 };
    this.calls = [];
    this.handlers = { list_sessions: () => ({ sessions: [] }), ...handlers };
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

/** A clock the test drives: `every` records timers, `sleep` advances time. */
function fakeClock() {
  const clock = { t: 0, timers: [], stopped: 0 };
  clock.every = (ms, fn) => {
    clock.timers.push({ ms, fn });
    return () => {
      clock.stopped += 1;
    };
  };
  clock.sleep = async (ms) => {
    clock.t += ms;
  };
  clock.now = () => clock.t;
  return clock;
}

function makeHost(session, handlers, hooks = {}) {
  const bridge = new FakeBridge(session, handlers);
  const posts = [];
  const clock = fakeClock();
  const asks = { web: [], sources: [], confirms: [] };
  const host = new BridgeAgentHost(
    bridge,
    (m) => posts.push(m),
    {
      pickWebAccess: async (ask) => {
        asks.web.push(ask);
        return { mode: "none", domains: [] };
      },
      pickSource: async (ask) => {
        asks.sources.push(ask);
        return ask.rows[0]?.name ?? null;
      },
      confirm: async (message, action) => {
        asks.confirms.push([message, action]);
        return true;
      },
      ...hooks,
    },
    clock,
  );
  const last = (type) => posts.filter((p) => p.type === type).at(-1);
  return { bridge, host, posts, clock, asks, last };
}

const tick = () => new Promise((r) => setImmediate(r));
const inMinutes = (m) => new Date(Date.now() + m * 60_000 + 30_000).toISOString();

test("a server session's snapshot shows the remote: chip with its lease and arms the poller", async () => {
  const session = { ...LOCAL, venue: "frontend", venue_runtime: { state: "running", phase: "idle", lease_expires_at: inMinutes(47) } };
  const { host, last, clock, bridge } = makeHost(session, {
    "session.venue_status": () => ({ venue: { state: "running", runtime: { phase: "running" } }, lease_remaining_sec: 200 }),
  });
  await host.initialize();
  assert.deepEqual(
    { chip: last("venueState").chip, state: last("venueState").chipState },
    { chip: "remote:running/idle · lease 47m", state: "ok" },
  );
  assert.equal(clock.timers.length, 1);
  assert.equal(clock.timers[0].ms, 30_000);
  clock.timers[0].fn();
  await tick();
  assert.ok(bridge.calls.some(([m]) => m === "session.venue_status"));
  assert.equal(last("venueState").chip, "remote:running/running · lease 3m");
  assert.equal(last("venueState").chipState, "warn", "under five minutes of lease warns");
});

test("a local session has no chip and no poller; /venue says it runs locally", async () => {
  const { host, last, clock } = makeHost(LOCAL, {
    "session.venue_status": () => {
      throw new Error("venue.not_found: no runtime");
    },
  });
  await host.initialize();
  assert.equal(last("venueState").chip, "");
  assert.equal(clock.timers.length, 0);
  await host.send("/venue");
  assert.match(last("slashOutput").lines[0].text, /runs locally — \/venue start/);
  await host.send("/venue sideways");
  assert.match(last("slashOutput").lines[0].text, /^usage: \/venue/);
});

test("/venue start: the web question with the server's caps, the picker, a push on source_not_pushed, then provisioning → running", async () => {
  let starts = 0;
  let statusReads = 0;
  const SANDBOXED = { ...LOCAL, sandbox: { mode: "off", available: true, notice: "Sandbox off: tools run with your permissions." } };
  const { host, bridge, posts, asks, last, clock } = makeHost(SANDBOXED, {
    "session.venue_policy": () => ({ limits: { web: { allow: ["none", "allowlist", "public"], domains: ["arxiv.org"] } } }),
    "session.web_access_set": (p) => ({ web_access: { mode: p.mode, domains: p.domains, source: "prompt" } }),
    "source.list_remote": () => ({
      remote_available: true,
      rows: [
        { name: "rikyu", kind: "public", has_api_key: true, remote: "-", url: "https://rikyu.example/v1", provider: "openai-compat" },
        { name: "laptop", kind: "local", has_api_key: false, remote: "-", url: "http://localhost:8000" },
      ],
    }),
    "source.push": () => ({ allow_host: "rikyu.example", key_fingerprint: "ab12" }),
    "session.venue_start": () => {
      starts += 1;
      if (starts === 1) throw new Error("venue.source_not_pushed: sources not pushed: rikyu");
      return {
        venue: { state: "provisioning" },
        state: { session: { ...SANDBOXED, venue: "frontend", web_access: { mode: "none", domains: [], source: "prompt" }, venue_runtime: { state: "provisioning" } } },
      };
    },
    "session.venue_status": () => {
      statusReads += 1;
      return { venue: { state: statusReads < 2 ? "provisioning" : "running", runtime: { phase: "idle" } }, lease_remaining_sec: 3600 };
    },
  });
  await host.initialize();
  await host.send("/venue start");
  assert.deepEqual(asks.web[0].caps.allow, ["none", "allowlist"], "a domain cap removes public");
  assert.match(asks.web[0].note, /sandbox proxy/);
  assert.deepEqual(asks.sources[0].rows.map((r) => [r.name, r.badge]), [["rikyu", "not pushed"]], "only eligible rows");
  assert.equal(asks.confirms[0][1], "Push");
  assert.deepEqual(bridge.calls.find(([m]) => m === "source.push")[1], { name: "rikyu" });
  assert.deepEqual(bridge.calls.filter(([m]) => m === "session.venue_start").at(-1)[1], {
    source_refs: ["rikyu"],
    default_source: "rikyu",
    web_policy: "none",
    web_allow_domains: [],
  });
  const notices = () => posts.filter((p) => p.type === "notice").map((p) => p.text);
  assert.ok(notices().some((t) => /^pushed 'rikyu' → rikyu.example \(ab12\)/.test(t)));
  assert.ok(notices().some((t) => /provisioning on the server… · source rikyu · web /.test(t)));
  const sandboxNotices = posts.filter((p) => p.type === "policyState" && p.sandboxNotice);
  assert.equal(sandboxNotices.length, 1, "one sandbox notice: the session-open one (a server session announces none)");
  for (let i = 0; i < 6; i += 1) await tick();
  assert.equal(notices().at(-1), "remote session running on the server");
  assert.equal(clock.t, 4_000, "two fast polls");
  assert.equal(last("venueState").chip, "remote:running/idle · lease 1h0m");
});

test("/venue start refused in the server's words; no web choice made → nothing starts", async () => {
  const { host, bridge, last } = makeHost(
    { ...LOCAL, web_access: { mode: "public", domains: [], source: "prompt" } },
    {
      "source.list_remote": () => ({ remote_available: true, rows: [{ name: "rikyu", kind: "public", has_api_key: true, remote: "pushed (40m)" }] }),
      "session.venue_start": () => {
        throw new Error("venue.unavailable: the journal is down");
      },
    },
  );
  await host.initialize();
  await host.send("/venue start");
  assert.equal(last("slashOutput").lines[0].text, "remote session start failed: server refused (unavailable): the journal is down");
  const backedOut = makeHost(LOCAL, {}, { pickWebAccess: async () => null });
  await backedOut.host.initialize();
  await backedOut.host.send("/venue start");
  assert.ok(!backedOut.bridge.calls.some(([m]) => m === "source.list_remote" || m === "session.venue_start"));
  assert.ok(bridge.calls.some(([m]) => m === "session.venue_start"));
});

test("/venue stop: the chip dims and the poller stops; a Sessions row stops another session by id", async () => {
  const session = { ...LOCAL, venue: "frontend", venue_runtime: { state: "running", phase: "idle" } };
  const { host, bridge, last, clock } = makeHost(session, {
    "session.venue_stop": (p) => ({ venue: { state: "stopped" }, ...(p.session_id ? {} : { state: { session: { ...session, venue_runtime: { state: "stopped" } } } }) }),
  });
  await host.initialize();
  await host.send("/venue stop");
  assert.equal(last("venueState").chip, "remote:stopped");
  assert.equal(last("venueState").chipState, "dim");
  assert.equal(clock.stopped, 1);
  assert.equal(last("notice").text, "remote session stopped");
  const chips = host.venue.current;
  await host.venue.stop("s-9abcdef12");
  assert.deepEqual(bridge.calls.at(-1), ["session.venue_stop", { session_id: "s-9abcdef12" }]);
  assert.equal(last("notice").text, "remote session stopped (s-9abcde)");
  assert.equal(host.venue.current, chips, "another session's stop leaves this chip alone");
});

test("/venue sources: a pushed row unpushes after a confirm; a declined confirm sends nothing", async () => {
  const handlers = {
    "source.list_remote": () => ({ remote_available: true, rows: [{ name: "rikyu", kind: "public", has_api_key: true, remote: "pushed (40m)" }] }),
    "source.unpush": () => ({ leases_revoked: 2 }),
  };
  const { host, bridge, last, asks } = makeHost(LOCAL, handlers);
  await host.initialize();
  await host.send("/venue sources");
  assert.equal(asks.confirms[0][1], "Unpush");
  assert.deepEqual(bridge.calls.at(-1), ["source.unpush", { name: "rikyu" }]);
  assert.equal(last("notice").text, "unpushed 'rikyu' (leases revoked: 2)");
  const declined = makeHost(LOCAL, handlers, { confirm: async () => false });
  await declined.host.initialize();
  await declined.host.send("/venue sources");
  assert.ok(!declined.bridge.calls.some(([m]) => m === "source.unpush"));
});

test("dispose stops the poller", async () => {
  const session = { ...LOCAL, venue: "frontend", venue_runtime: { state: "provisioning" } };
  const { host, clock } = makeHost(session, {});
  await host.initialize();
  host.dispose();
  assert.equal(clock.stopped, 1);
});

test("a new session asks where it runs: Local restarts the bridge; a disabled Server row says why", async () => {
  const picks = [];
  const ctx = makeHost(LOCAL, { list_sessions: (p) => (p.limit === 1 ? { sessions: [], remote_available: false, remote_reason: "no gateway" } : { sessions: [] }) }, {
    pickVenue: async (options) => {
      picks.push(options);
      return picks.length === 1 ? "local" : "frontend";
    },
  });
  ctx.bridge.reconnect = async () => {
    ctx.bridge.calls.push(["reconnect", {}]);
    ctx.bridge.initResult = { session: { ...LOCAL, id: "s-2" } };
  };
  await ctx.host.initialize();
  await ctx.host.newSession();
  assert.deepEqual(picks[0].map((o) => [o.choice, o.disabled]), [["local", false], ["frontend", true], ["cluster:pending", true]]);
  assert.equal(picks[0][1].disabledReason, "unavailable — no gateway");
  assert.equal(ctx.host.currentSessionId, "s-2");
  await ctx.host.newSession();
  assert.equal(ctx.last("notice").text, "Server (frontend): unavailable — no gateway");
  assert.equal(ctx.bridge.calls.filter(([m]) => m === "reconnect").length, 1, "a disabled row creates nothing");
});

test("a new Server session: the web answer, a source, initialize {venue: frontend} once, then venue_start", async () => {
  const ctx = makeHost(LOCAL, {
    list_sessions: (p) => (p.limit === 1 ? { sessions: [], remote_available: true } : { sessions: [] }),
    "session.venue_policy": () => ({ limits: null }),
    "source.list_remote": () => ({ remote_available: true, rows: [{ name: "rikyu", kind: "public", has_api_key: true, remote: "pushed (40m)" }] }),
    initialize: (p) => ({ session: { ...LOCAL, id: "s-remote", venue: p.venue, web_access: { ...p.web_access, source: "prompt" } } }),
    "session.venue_start": () => ({ venue: { state: "running" } }),
  }, { pickVenue: async () => "frontend" });
  await ctx.host.initialize();
  await ctx.host.newSession();
  assert.equal(ctx.asks.web.at(-1).current, null, "the question is for a session not created yet");
  assert.deepEqual(ctx.bridge.calls.filter(([m]) => m === "initialize").map(([, p]) => p), [{ venue: "frontend", web_access: { mode: "none", domains: [] } }]);
  assert.equal(ctx.host.currentSessionId, "s-remote");
  assert.ok(ctx.posts.some((p) => p.type === "notice" && p.text === "new session started (frontend venue · web off)"));
  assert.deepEqual(ctx.bridge.calls.at(-1)[0], "session.venue_start");
  assert.equal(ctx.last("venueState").chip, "remote:running");
});

test("a Sessions row: turns as an output card; fork opens the copy here", async () => {
  const ctx = makeHost(LOCAL, {
    "session.turns": () => ({ rows: [{ turn_id: "t-1", index: 1, role: "user", committed_at: "2026-11-01T10:00Z" }] }),
    "session.fork": () => ({ session_id: "s-fork", name: "One (fork)", messages: 4 }),
    load_session: (p) => ({ session: { ...LOCAL, id: p.session_id }, messages: [] }),
  });
  await ctx.host.initialize();
  await ctx.host.sessionTurns("s-9abcdef12");
  assert.deepEqual(ctx.last("slashOutput"), {
    type: "slashOutput",
    sessionId: "s-1",
    command: "/sessions turns s-9abcde",
    lines: [{ kind: "info", text: "  #1 t-1 · user · 2026-11-01T10:00Z" }],
  });
  await ctx.host.forkSession("s-other");
  assert.deepEqual(ctx.bridge.calls.find(([m]) => m === "session.fork")[1], { session_id: "s-other" });
  assert.equal(ctx.host.currentSessionId, "s-fork");
});
