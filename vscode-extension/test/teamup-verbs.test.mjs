// /teamup (/team, /teams) as the console answers it (ADR-103 VP-4b part 1):
// the request verbs, the console's texts (lib/teamup-text.ts), as cards.
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
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-teamup-"));
const bundle = path.join(outDir, "host.cjs");
execFileSync(path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"), ["src/bridge/bridge-agent-host.ts", "--bundle", "--platform=node", "--format=cjs", `--outfile=${bundle}`], {
  stdio: "inherit",
  cwd: rootDir,
});
const { BridgeAgentHost } = createRequire(import.meta.url)(bundle);
process.on("exit", () => rmSync(outDir, { recursive: true, force: true }));

const SESSION = { id: "s-1", name: "One", interaction_mode: "chat", venue: "local", web_access: { mode: "none", domains: [], source: "prompt" } };

class FakeBridge extends EventEmitter {
  constructor(handlers) {
    super();
    this.initResult = { session: SESSION, bridge_protocol: 1 };
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

async function run(line, handlers = {}) {
  const posts = [];
  const bridge = new FakeBridge(handlers);
  const host = new BridgeAgentHost(bridge, (m) => posts.push(m), {});
  await host.initialize();
  await host.send(line);
  const card = posts.filter((p) => p.type === "slashOutput").at(-1);
  const calls = bridge.calls.filter(([m]) => m.startsWith("teamup.") || m.startsWith("team."));
  return { line: card?.lines[0], calls };
}

test("bare /teamup and /team list today's sessions as the Work pane's rows", async () => {
  const list = { sessions: [{ session_id: "ts-0123456789abcdef", name: "survey", status: "running", iteration: 2, max_iterations: 6, roles: ["pm", "se"] }] };
  const bare = await run("/teamup", { "teamup.list": () => list });
  assert.equal(bare.line.text, "teams (1):\n  ts-012345678 · survey · running · it 2/6 · pm,se");
  assert.equal((await run("/team list", { "teamup.list": () => ({ sessions: [] }) })).line.text.startsWith("teams: none today"), true);
});

test("session verbs: status card, logs with the console's limit, lifecycle, run, report, tailor; a missing id is usage", async () => {
  const status = await run("/teamup status ts-1", {
    "teamup.status": () => ({
      session_id: "ts-1",
      name: "survey",
      status: "running",
      iteration: 2,
      max_iterations: 6,
      crew_pack: "research",
      agents: { pm: { status: "thinking", model: "glm-5.3", current_task: "split the work" } },
    }),
  });
  assert.equal(status.line.text, "team ts-1 · survey · running · iteration 2/6 · crew research\n  pm: thinking · glm-5.3 — split the work");
  const logs = await run("/teamup logs ts-1", { "teamup.logs": () => ({ entries: ["a", "b"] }) });
  assert.deepEqual(logs.calls[0], ["teamup.logs", { session_id: "ts-1", limit: 12 }]);
  assert.equal(logs.line.text, "teamup logs ts-1 (latest 2):\na\nb");
  assert.equal((await run("/teamup pause ts-1", { "teamup.pause": () => ({ session_id: "ts-1", status: "paused" }) })).line.text, "teamup pause: ts-1 → paused");
  assert.equal((await run("/teamup run ts-1", { "teamup.run": () => ({ started: true, expected_iteration: 3 }) })).line.text, "teamup ts-1 — iteration 3 STARTED (follow live below)");
  assert.equal((await run("/teamup report ts-1", { "teamup.report": () => ({ report: "" }) })).line.text, "teamup report ts-1: (empty)");
  assert.equal(
    (await run("/teamup tailor ts-1", { "teamup.tailor": () => ({ root: "pm", tailored: ["se"], kept_default: ["pg"] }) })).line.text,
    "teamup tailor ts-1 — root pm wrote 1 prompt ({se}); kept template: {pg}",
  );
  assert.deepEqual((await run("/teamup cancel")).line, { kind: "error", text: "usage: /teamup cancel <session-id>" });
});

test("create parses the console's form; inventory verbs; plans; failures name the verb", async () => {
  const created = await run("/teamup create survey roles=pm,se find the bug fast", {
    "teamup.create": (p) => ({ session_id: "ts-0123456789abcdef", name: p.name, status: "created", roles: p.roles.split(","), started: true }),
  });
  assert.deepEqual(created.calls[0][1], { name: "survey", goal: "find the bug fast", roles: "pm,se", hardware: "local", max_iterations: 10, auto_approve: false, start: true });
  assert.equal(created.line.text, "teamup create survey → started (ts-012345678 roles:pm,se)");
  assert.match((await run("/teamup create")).line.text, /^teamup.create needs a name/);
  assert.equal(
    (await run("/teamup engines", { "teamup.engines": () => ({ engines: [{ name: "r4", version: "1", trust_tier: "core", available: true }] }) })).line.text,
    "teamup engines (1):\n  ✓ r4@v1 [core]",
  );
  assert.equal((await run("/teamup hardware", { "teamup.hardware": () => ({ profiles: [] }) })).line.text, "teamup hardware: no profiles registered");
  assert.equal(
    (await run("/teamup plans", { "team.plan_templates": () => ({ templates: [{ name: "review", description: "Review a PR", params: { pr: { required: true } } }] }) })).line.text,
    "team plan templates (1) — /teamup on plan:<name>:\n  ▸ review (needs pr) — Review a PR",
  );
  const failed = await run("/teamup crews", {
    "teamup.crews": () => {
      throw new Error("broker busy");
    },
  });
  assert.deepEqual(failed.line, { kind: "error", text: "teamup.crews failed: broker busy" });
});

test("help and an unknown verb say what to do; no Object.prototype key reaches a verb", async () => {
  assert.match((await run("/teamup help")).line.text, /^\/teamup verbs: on \[crew/);
  assert.match((await run("/teamup sideways")).line.text, /^teamup: unknown sub "sideways"/);
  assert.match((await run("/teamup constructor")).line.text, /^teamup: unknown sub "constructor"/);
  assert.equal((await run("/teamup off")).line.text, "teamup arm was not set");
});
