// The extension's bridge client against the REAL companion bridge of this
// checkout (ADR-103 VP-1): same process the console spawns, scratch HOME, no
// gateway. Skipped, with the reason, when no Python that can import r4 is found
// (set R4_TEST_PYTHON to the repository venv's python to run it).
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";

import { loadMatrix } from "../scripts/parity-matrix.mjs";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoSrc = path.join(rootDir, "..", "r4", "src");
const scratch = mkdtempSync(path.join(os.tmpdir(), "r4-bridge-live-"));
const outDir = path.join(scratch, "bundle");
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  [
    "src/bridge/companion-bridge.ts",
    "src/bridge/rpc.ts",
    "src/bridge/bridge-agent-host.ts",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    "--out-extension:.js=.cjs",
    `--outdir=${outDir}`,
  ],
  { stdio: "inherit", cwd: rootDir },
);
const require = createRequire(import.meta.url);
const { CompanionBridge, BridgeRequestError } = require(path.join(outDir, "companion-bridge.cjs"));
const { rpc } = require(path.join(outDir, "rpc.cjs"));
const { BridgeAgentHost } = require(path.join(outDir, "bridge-agent-host.cjs"));

const home = path.join(scratch, "home");
const workspace = path.join(scratch, "ws");
mkdirSync(path.join(home, ".config"), { recursive: true });
mkdirSync(workspace);
const bridgeEnv = {
  HOME: home,
  XDG_CONFIG_HOME: path.join(home, ".config"),
  R4_CONFIG_HOME: path.join(home, ".config", "r4"),
  R4_CONFIG_DIR: path.join(home, ".config", "r4"),
  PYTHONPATH: repoSrc,
};

function findPython() {
  const candidates = [process.env.R4_TEST_PYTHON, process.env.R4_TUI_PYTHON, "python3"].filter(Boolean);
  return candidates.find(
    (python) => spawnSync(python, ["-c", "import r4.companion.ui.bridge_server"], { env: { ...process.env, ...bridgeEnv }, stdio: "ignore", timeout: 30_000 }).status === 0,
  );
}

const python = existsSync(repoSrc) ? findPython() : undefined;
const skip = python ? false : "no Python that imports r4.companion.ui.bridge_server (set R4_TEST_PYTHON)";
const bridge = python ? new CompanionBridge({ python, workspace, env: bridgeEnv, killGraceMs: 500 }) : null;

after(async () => {
  await bridge?.shutdown();
  rmSync(scratch, { recursive: true, force: true });
});

test("initialize speaks protocol v1 and opens a session", { skip, timeout: 90_000 }, async () => {
  await bridge.ensureStarted();
  assert.equal(bridge.initResult.bridge_protocol, 1);
  assert.equal(typeof bridge.initResult.session.id, "string");
});

test("typed get_state answers the same session", { skip, timeout: 30_000 }, async () => {
  const state = await rpc(bridge, "get_state", {});
  assert.equal(state.session.id, bridge.initResult.session.id);
});

test("every slash command the bridge serves has a parity matrix row", { skip, timeout: 30_000 }, async () => {
  const { commands } = await bridge.request("list_slash_commands", {});
  assert.ok(commands.length > 20, "the registry is served");
  const rows = new Set(loadMatrix().rows.map((r) => r.id));
  const unclassified = commands.map((c) => `slash:/${c.name}`).filter((id) => !rows.has(id));
  assert.deepEqual(unclassified, [], "regenerate the slash fixture (scripts/regen-slash-fixture.py), then the matrix");
});

test("a failed verb rejects with the server's error type", { skip, timeout: 30_000 }, async () => {
  await assert.rejects(bridge.request("session.rename", { session_id: "no-such-session", name: "x" }), (err) => {
    assert.ok(err instanceof BridgeRequestError);
    assert.match(err.message, /session not found/);
    assert.equal(err.errorType, "ValueError");
    return true;
  });
  await assert.rejects(bridge.request("no_such_verb", {}), /unknown method: no_such_verb/);
});

test("the agent host drives the real bridge: sessions, /new, a slash command, the mode", { skip, timeout: 120_000 }, async () => {
  const posts = [];
  const host = new BridgeAgentHost(bridge, (m) => posts.push(m));
  await host.initialize();
  const first = host.currentSessionId;
  assert.ok(first, "initialize selected the bridge's session");
  assert.ok(posts.some((p) => p.type === "sessions"));

  await host.newSession();
  assert.notEqual(host.currentSessionId, first, "/new opened another session");

  await host.send("/help");
  const card = posts.filter((p) => p.type === "slashOutput").at(-1);
  assert.ok(card && card.lines.map((l) => l.text).join("\n").length > 20, "the slash dispatcher answered (an output card)");

  await host.setMode("plan");
  assert.equal(posts.filter((p) => p.type === "modeChanged").at(-1).mode, "plan");
  await host.setMode("ask");
  assert.equal(posts.filter((p) => p.type === "modeChanged").at(-1).mode, "ask");

  await host.selectSession(first);
  assert.equal(host.currentSessionId, first, "load_session switched back");
  assert.deepEqual(posts.filter((p) => p.type === "error"), []);
});

test("VP-3a on the real bridge: sandbox notice and status, web access", { skip, timeout: 120_000 }, async () => {
  const posts = [];
  const host = new BridgeAgentHost(bridge, (m) => posts.push(m), {
    pickWebAccess: async () => ({ mode: "none", domains: [] }),
  });
  await host.initialize();
  const policy = posts.find((p) => p.type === "policyState");
  assert.ok(policy, "the snapshot's policy was posted");
  assert.match(policy.sandboxChip, /^sbx:(on|off|n\/a)$/);
  await host.send("/sandbox status");
  assert.match(posts.filter((p) => p.type === "slashOutput").at(-1).lines[0].text, /^sandbox: /);
  await host.send("/web");
  assert.equal(posts.filter((p) => p.type === "policyState").at(-1).web, "off");
  assert.deepEqual(posts.filter((p) => p.type === "error"), []);
});

test("VP-3b on the real bridge: a local session has no remote: chip and /venue says so", { skip, timeout: 120_000 }, async () => {
  const posts = [];
  const host = new BridgeAgentHost(bridge, (m) => posts.push(m));
  await host.initialize();
  assert.equal(posts.find((p) => p.type === "venueState").chip, "", "a local session shows no remote: chip");
  await host.send("/venue");
  assert.match(posts.filter((p) => p.type === "slashOutput").at(-1).lines[0].text, /runs locally — \/venue start|^remote runtime: /);
  host.dispose();
  assert.deepEqual(posts.filter((p) => p.type === "error"), []);
});

test("VP-2d on the real bridge: the live slash catalog reaches the popup and /help", { skip, timeout: 120_000 }, async () => {
  const posts = [];
  const host = new BridgeAgentHost(bridge, (m) => posts.push(m));
  await host.initialize();
  const names = posts.find((p) => p.type === "slashCatalog").commands.map((c) => c.name);
  assert.ok(names.length > 30, `the bridge's catalog arrived (${names.length})`);
  assert.ok(names.includes("venue") && names.includes("models"), "host verbs and bridge rows together");
  await host.send("/help models");
  assert.match(posts.filter((p) => p.type === "slashOutput").at(-1).lines[0].text, /^\/models — /);
  host.dispose();
});

test("VP-2b on the real bridge: changed files, the plan, a fork", { skip, timeout: 120_000 }, async () => {
  const posts = [];
  const host = new BridgeAgentHost(bridge, (m) => posts.push(m));
  await host.initialize();
  await host.refreshChanges();
  assert.deepEqual(posts.find((p) => p.type === "filesChanged").files, [], "a fresh session changed nothing");
  await host.refreshPlan();
  assert.deepEqual(posts.find((p) => p.type === "planState").steps, []);
  const before = host.currentSessionId;
  await host.forkFrom(undefined);
  assert.notEqual(host.currentSessionId, before, "session.fork opened the fork");
  assert.match(posts.find((p) => p.type === "notice").text, /^forked into/);
  assert.deepEqual(posts.filter((p) => p.type === "error"), []);
});
