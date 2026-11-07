// The Work and Infrastructure panes as output cards (ADR-103 VP-4c).
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
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-work-"));
const bundle = path.join(outDir, "host.cjs");
execFileSync(path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"), ["src/bridge/bridge-agent-host.ts", "--bundle", "--platform=node", "--format=cjs", `--outfile=${bundle}`], {
  stdio: "inherit",
  cwd: rootDir,
});
const { BridgeAgentHost } = createRequire(import.meta.url)(bundle);
process.on("exit", () => rmSync(outDir, { recursive: true, force: true }));

const SESSION = { id: "s-1", name: "One", interaction_mode: "chat", venue: "local", web_access: { mode: "none", domains: [], source: "prompt" } };
const DASHBOARD = {
  dashboard: {
    project: "example-project-a",
    gateway: "https://gw",
    readiness: { ready: true },
    endpoints: [{ id: "ep-1", model: "glm-5.3", status: "ready", backend: "slurm", location: "node7:8000" }],
    jobs: [{ id: "job-9", type: "train", status: "running", attempts: "2" }],
    errors: { rag: "timeout" },
  },
};

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
  return { lines: card?.lines ?? [], posts, calls: bridge.calls };
}

test("/work: every section from its own read, the console's row texts; one failing read is its own line", async () => {
  const { lines } = await run("/work", {
    "workflow.list": () => ({ workflows: [{ workflow_name: "example-track", description: "Track objects\nmore", shared: true }] }),
    "team.runs": () => ({ runs: [{ kind: "plan", run_id: "run-7", name: "review", status: "running" }, { kind: "team", run_id: "x" }] }),
    "teamup.list": () => ({ sessions: [] }),
    "loops.list": () => {
      throw new Error("loops service down");
    },
    get_backend_dashboard: () => DASHBOARD,
  });
  const text = lines.map((l) => `${l.kind}: ${l.text}`);
  assert.equal(text[0], "info: workflows (1) — /work show <name> · /work run <name>:\n  △ example-track (shared) — Track objects");
  assert.match(text[1], /^info: plan runs \(1\):\n {2}plan review · running/);
  assert.match(text[2], /^info: teams: none today/);
  assert.equal(text[3], "error: loops: unavailable — loops service down");
  assert.equal(text[4], "info: jobs (1) — /job describe|cancel|retry <id>:\n  job-9 · train · running · ×2");
  assert.equal(text[5], "info: endpoints (1) — /endpoint down <id>:\n  ep-1 · glm-5.3 · ready · slurm · node7:8000");
});

test("/workflows is the same pane; /infra is readiness, endpoints, jobs and the failed probes", async () => {
  const pane = await run("/workflows", { get_backend_dashboard: () => DASHBOARD });
  assert.equal(pane.lines.length, 6);
  const infra = await run("/infra", { get_backend_dashboard: () => DASHBOARD });
  assert.match(infra.lines[0].text, /^backend ready · project example-project-a · gateway https:\/\/gw\n {2}⚠ rag: timeout\n\nendpoints \(1\)/);
  const down = await run("/infra", { get_backend_dashboard: () => ({ dashboard: { readiness: { ready: false, error: "401" } } }) });
  assert.match(down.lines[0].text, /^backend not ready — 401 · project default · gateway -\n\nendpoints: none running\n\njobs: none recent$/);
});

test("/work show: the console's definition card and the run hint", async () => {
  const { lines, calls } = await run("/work show example-track", {
    "workflow.package": () => ({ workflow_name: "example-track", description: "Track objects", steps: [{ name: "prep" }, { name: "train", depends_on: ["prep"] }] }),
  });
  assert.deepEqual(calls.find(([m]) => m === "workflow.package")[1], { workflow_name: "example-track" });
  assert.match(lines[0].text, /example-track/);
  assert.match(lines[0].text, /\nrun: \/work run example-track$/);
});

test("/work run: runs a workflow with no required values; drafts the trigger line when it needs some", async () => {
  const ran = await run("/work run example-track", {
    "workflow.package": () => ({ workflow_name: "example-track", steps: [{ name: "prep" }] }),
    "workflow.run": () => ({ execution_id: "ex-1", status: "queued", error: null }),
  });
  assert.deepEqual(ran.calls.find(([m]) => m === "workflow.run")[1], { workflow_name: "example-track", parameters: {}, wait: false });
  assert.equal(ran.lines[0].text, "▶ example-track → ex-1 · queued");
  const needs = await run("/work run example-track", {
    "workflow.package": () => ({ workflow_name: "example-track", parameter_schema: { type: "object", properties: { video: { type: "string" } }, required: ["video"] } }),
  });
  assert.equal(needs.calls.filter(([m]) => m === "workflow.run").length, 0);
  const draft = needs.posts.filter((p) => p.type === "restoreDraft").at(-1);
  assert.match(draft.text, /^\/workflow trigger example-track --param video=$/);
  assert.match(needs.lines[0].text, /needs 1 value\(s\): the trigger line is in the composer/);
});

test("/work usage", async () => {
  assert.deepEqual((await run("/work show")).lines[0], { kind: "error", text: "usage: /work show <workflow>" });
  assert.match((await run("/work sideways")).lines[0].text, /^usage: \/work \[show/);
});
