// /models and /mode as the console answers them (ADR-103 VP-2e).
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
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-model-mode-"));
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
    this.handlers = {
      list_sessions: () => ({ sessions: [] }),
      set_interaction_mode: (p) => ({ mode: p.mode }),
      dispatch_slash_command: () => ({ handled: true, output: [{ kind: "info", text: "bridge answered" }] }),
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

async function makeHost(handlers = {}, pick = (ask) => ask.models.at(-1)?.name ?? null) {
  const ctx = { posts: [], asks: [] };
  ctx.bridge = new FakeBridge(handlers);
  ctx.host = new BridgeAgentHost(ctx.bridge, (m) => ctx.posts.push(m), {
    pickModel: async (ask) => {
      ctx.asks.push(ask);
      return pick(ask);
    },
  });
  await ctx.host.initialize();
  ctx.of = (type) => ctx.posts.filter((p) => p.type === type);
  ctx.card = () => ctx.of("slashOutput").at(-1);
  ctx.calls = (method) => ctx.bridge.calls.filter(([m]) => m === method);
  return ctx;
}

test("/models: the picker over list_models; a new pick sets the model; the current one changes nothing", async () => {
  const ctx = await makeHost({
    list_models: () => ({ models: [{ name: "glm-5.3", label: "rikyu" }, { name: "qwen3" }], current: "glm-5.3", problems: [{ source: "gb10", error: "down" }] }),
  });
  await ctx.host.send("/models");
  assert.deepEqual(ctx.asks[0], {
    models: [
      { name: "glm-5.3", label: "rikyu" },
      { name: "qwen3", label: "" },
    ],
    current: "glm-5.3",
    problems: ["gb10: down"],
  });
  assert.deepEqual(ctx.calls("set_model")[0][1], { model: "qwen3" });
  assert.equal(ctx.of("modelChanged").at(-1).model, "qwen3");
  assert.equal(ctx.card().lines[0].text, "model → qwen3");
  const same = await makeHost({ list_models: () => ({ models: [{ name: "glm-5.3" }], current: "glm-5.3" }) });
  await same.host.send("/model list");
  assert.equal(same.calls("set_model").length, 0);
});

test("/models with nothing provisioned names why; /models default <name> goes to the bridge", async () => {
  const ctx = await makeHost({ list_models: () => ({ models: [], problems: [{ source: "rikyu", error: "API key rejected" }] }) });
  await ctx.host.send("/models");
  assert.deepEqual(ctx.card().lines[0], { kind: "error", text: "no models to pick — rikyu: API key rejected" });
  await ctx.host.send("/models default qwen3");
  assert.deepEqual(ctx.calls("dispatch_slash_command").at(-1)[1], { command: "/models default qwen3" });
});

test("/mode <name> switches the mode; write-plan on|off has its own verb; bare /mode goes to the bridge", async () => {
  const ctx = await makeHost({ set_write_requires_plan: (p) => ({ write_requires_plan: p.enabled }) });
  await ctx.host.send("/mode autopilot");
  assert.deepEqual(ctx.calls("set_interaction_mode").at(-1)[1], { mode: "autopilot" });
  assert.equal(ctx.of("modeChanged").at(-1).mode, "agent", "autopilot is the view's agent mode");
  await ctx.host.send("/mode write-plan on");
  assert.deepEqual(ctx.calls("set_write_requires_plan")[0][1], { enabled: true });
  assert.equal(ctx.card().lines[0].text, "write_requires_plan → on");
  await ctx.host.send("/mode");
  assert.deepEqual(ctx.calls("dispatch_slash_command").at(-1)[1], { command: "/mode" });
});
