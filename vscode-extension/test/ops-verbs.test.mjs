// The console's ops verbs over the bridge (ADR-103 VP-4a): /loops, /traces,
// /provenance, /sweep — same RPCs, the console's texts (lib/ops-text.ts).
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
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-ops-verbs-"));
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

async function run(line, handlers) {
  const posts = [];
  const bridge = new FakeBridge(handlers);
  const host = new BridgeAgentHost(bridge, (m) => posts.push(m), {});
  await host.initialize();
  await host.send(line);
  const card = posts.filter((p) => p.type === "slashOutput").at(-1);
  return { line: card?.lines[0], calls: bridge.calls.filter(([m]) => !["list_sessions", "list_slash_commands", "explain.match"].includes(m)) };
}

test("/loops: list, a control verb, detail, usage", async () => {
  const loops = { loops: [{ loop_id: "L1", status: "running", phase: "explore", current_iteration: 3, research_goal: "find the bug" }] };
  assert.equal((await run("/loops", { "loops.list": () => loops })).line.text, "loops (1):\n  L1 · running · explore · #3 · find the bug");
  assert.equal((await run("/loops", { "loops.list": () => ({ loops: [] }) })).line.text, "loops — none visible to this caller");
  const paused = await run("/loops pause L1", { "loops.pause": () => ({ status: "paused" }) });
  assert.deepEqual(paused.calls[0], ["loops.pause", { loop_id: "L1" }]);
  assert.equal(paused.line.text, "loop L1 → pause (paused)");
  assert.equal((await run("/loops detail L1", { "research_loop.status": () => ({ status: "running", iteration: 3 }) })).line.text, "loop L1:\n  status running\n  iteration 3");
  assert.deepEqual((await run("/loops stop", {})).line, { kind: "error", text: "usage: /loops stop <loop-id>" });
  assert.match((await run("/loops sideways", {})).line.text, /^loops: unknown sub/);
});

test("/traces: get, a job's traces, the companion's verbs go to /trace, usage", async () => {
  assert.equal((await run("/traces get T9", { "traces.get": () => ({ status: "ok", attempts: 2 }) })).line.text, "trace T9:\n  status ok\n  attempts 2");
  const job = await run("/traces J1", { "traces.job": () => ({ traces: [{ trace_id: "T1", status: "done", step_name: "train", started_at: "2026-11-02T10:00:00.123Z" }] }) });
  assert.equal(job.line.text, "traces for job J1 (1):\n  T1 · done · train · 2026-11-02T10:00:00");
  const listed = await run("/traces list J1", { dispatch_slash_command: () => ({ handled: true, output: [{ kind: "info", text: "trace rows" }] }) });
  assert.deepEqual(listed.calls[0], ["dispatch_slash_command", { command: "/trace list J1" }]);
  assert.equal(listed.line.text, "trace rows");
  assert.deepEqual((await run("/traces", {})).line, { kind: "error", text: "usage: /traces [get <trace-id>|<job-id>]" });
});

test("/provenance: a job, its lineage, a bad mode, a failure in the console's form", async () => {
  assert.equal((await run("/provenance J1", { "provenance.job": () => ({ owner: "mn", inputs: { a: 1 } }) })).line.text, 'provenance — job J1:\n  owner mn\n  inputs {"a":1}');
  const lineage = await run("/provenance J1 lineage", {
    "provenance.lineage": () => ({ nodes: [{ step_name: "prep", step_type: "task", status: "done", duration_seconds: 4 }], edges: [] }),
  });
  assert.equal(lineage.line.text, "provenance lineage — job J1 (1 steps, 0 edges):\n  prep · task · done · 4s");
  assert.match((await run("/provenance J1 sideways", {})).line.text, /unknown mode 'sideways'/);
  const failed = await run("/provenance J1", {
    "provenance.job": () => {
      throw new Error("job not found");
    },
  });
  assert.deepEqual(failed.line, { kind: "error", text: "provenance: job not found" });
});

test("/sweep: list, preview, help", async () => {
  const list = await run("/sweep", { "sweep.templates": () => ({ templates: [{ name: "grid", description: "a grid", required_params: ["lr", "bs"] }] }) });
  assert.equal(list.line.text, "sweep templates (1):\n  △ grid — a grid [requires: lr,bs]");
  const preview = await run("/sweep preview grid", { "sweep.preview": () => ({ name: "grid", required_params: ["lr"], job_name: "grid-0", script: "#!/bin/sh" }) });
  assert.equal(preview.line.text, "sweep preview grid (dry render of the default combo)\n  requires: lr\n  job: grid-0\n#!/bin/sh");
  assert.match((await run("/sweep what", {})).line.text, /^sweep — HPC sweep templates:/);
});
