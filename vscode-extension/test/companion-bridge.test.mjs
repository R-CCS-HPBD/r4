// The extension's companion bridge client against a fake process (ADR-103 VP-1).
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import { test } from "node:test";
import { setImmediate as tick } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-companion-bridge-"));
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  [
    "src/bridge/companion-bridge.ts",
    "src/bridge/resolve-python.ts",
    "src/console-launch.ts",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    "--out-extension:.js=.cjs",
    `--outdir=${outDir}`,
  ],
  { stdio: "inherit", cwd: rootDir },
);
const require = createRequire(import.meta.url);
const { CompanionBridge, BridgeRequestError, bridgeErrorHint } = require(path.join(outDir, "bridge", "companion-bridge.cjs"));
const { pythonCandidates, pythonFromLauncher, resolveBridgePython } = require(path.join(outDir, "bridge", "resolve-python.cjs"));
const { consoleTerminalSpec } = require(path.join(outDir, "console-launch.cjs"));
rmSync(outDir, { recursive: true, force: true });

/** A scripted bridge process: `reply(request)` answers each request line. */
class FakeProcess extends EventEmitter {
  constructor(reply) {
    super();
    this.stdin = new PassThrough();
    this.stdout = new PassThrough();
    this.stderr = new PassThrough();
    this.written = [];
    this.signals = [];
    let buf = "";
    this.stdin.setEncoding("utf8");
    this.stdin.on("data", (chunk) => {
      buf += chunk;
      let nl;
      while ((nl = buf.indexOf("\n")) !== -1) {
        const line = JSON.parse(buf.slice(0, nl));
        buf = buf.slice(nl + 1);
        this.written.push(line);
        const answer = reply(line, this);
        if (answer !== undefined) this.send(answer);
      }
    });
  }
  send(obj) {
    this.stdout.write(`${typeof obj === "string" ? obj : JSON.stringify(obj)}\n`);
  }
  kill(signal) {
    this.signals.push(signal);
    return true;
  }
  exit() {
    this.emit("exit", 1, null);
  }
}

const INIT = { session: { id: "s-1" }, bridge_protocol: 1 };

function okReply(line) {
  if (line.method === "initialize") return { id: line.id, ok: true, result: INIT };
  if (line.method) return { id: line.id, ok: true, result: { echo: line.method, params: line.params } };
  return undefined;
}

function makeBridge(reply = okReply, opts = {}) {
  const spawned = [];
  const bridge = new CompanionBridge({
    python: "/py",
    workspace: "/ws",
    killGraceMs: 5,
    spawner: (argv, options) => {
      const proc = new FakeProcess(reply);
      spawned.push({ argv, options, proc });
      return proc;
    },
    ...opts,
  });
  return { bridge, spawned };
}

test("spawns the console's argv in the workspace and initializes", async () => {
  const { bridge, spawned } = makeBridge(okReply, { webAccess: "public", sandbox: "on", env: { R4_PROJECT_ID: "p1" } });
  let init;
  bridge.on("init_state", (s) => (init = s));
  const result = await bridge.request("get_state");
  assert.deepEqual(spawned[0].argv, [
    "/py", "-m", "r4.companion.ui.bridge_server", "--fs-root", "/ws", "--web-access", "public", "--sandbox", "on",
  ]);
  assert.equal(spawned[0].options.cwd, "/ws");
  assert.equal(spawned[0].options.env.R4_PROJECT_ID, "p1");
  assert.deepEqual(init, INIT);
  assert.deepEqual(bridge.initResult, INIT);
  assert.deepEqual(result, { echo: "get_state", params: {} });
  assert.deepEqual(spawned[0].proc.written.map((l) => l.method), ["initialize", "get_state"]);
  assert.equal(spawned.length, 1, "one process for concurrent and later requests");
});

test("concurrent first requests share one start", async () => {
  const { bridge, spawned } = makeBridge();
  await Promise.all([bridge.request("a"), bridge.request("b"), bridge.request("c")]);
  assert.equal(spawned.length, 1);
});

test("routes one-way lines to the console's channels and scrubs control characters", async () => {
  const { bridge, spawned } = makeBridge();
  await bridge.ensureStarted();
  const seen = [];
  for (const ch of ["event", "team_event", "session_title", "approval", "ask_user", "turn_stopped", "control_event", "project_job_event"]) {
    bridge.on(ch, (payload) => seen.push([ch, payload]));
  }
  const proc = spawned[0].proc;
  proc.send({ event: { type: "tool_call", message: "x\u001b]52;c;Zm9v\u0007y" } });
  proc.send({ team_event: { run_id: "r" } });
  proc.send({ session_title: { title: "t" } });
  proc.send({ approval_request: { id: 7, tool: "shell_exec" } });
  proc.send({ ask_user_request: { id: 8 } });
  proc.send({ turn_stopped: { reason: "cancelled" } });
  proc.send({ type: "cost", payload: { usd: 0.1 } });
  proc.send({ project_job_event: { job_id: "j" } });
  await tick();
  assert.deepEqual(seen.map(([ch]) => ch), [
    "event", "team_event", "session_title", "approval", "ask_user", "turn_stopped", "control_event", "project_job_event",
  ]);
  assert.equal(seen[0][1].message, "x␛]52;c;Zm9v␇y", "escape sequences arrive as visible pictures");
  assert.deepEqual(seen[6][1], { type: "cost", payload: { usd: 0.1 } });
});

test("an error reply rejects with the server's hint, type and data", async () => {
  const { bridge } = makeBridge((line) =>
    line.method === "initialize"
      ? { id: line.id, ok: true, result: INIT }
      : {
          id: line.id,
          ok: false,
          error: { type: "SessionConflictError", message: "conflict", hint: "/sessions resume", data: { code: "session_conflict", stage: "before_turn" } },
        },
  );
  await assert.rejects(bridge.request("send_message", { text: "hi" }), (err) => {
    assert.ok(err instanceof BridgeRequestError);
    assert.equal(err.message, "conflict");
    assert.equal(err.errorType, "SessionConflictError");
    assert.equal(err.data.stage, "before_turn");
    assert.equal(bridgeErrorHint(err), "/sessions resume");
    return true;
  });
});

test("out-of-band approval, ask-user and cancel lines", async () => {
  const { bridge, spawned } = makeBridge();
  await bridge.ensureStarted();
  bridge.respondApproval(7, "allow_once");
  bridge.respondAskUser(8, "yes");
  bridge.cancelTurn();
  await tick();
  assert.deepEqual(spawned[0].proc.written.slice(1), [
    { approval_response: { id: 7, decision: "allow_once" } },
    { ask_user_response: { id: 8, answer: "yes" } },
    { cancel_turn: true },
  ]);
});

test("turnInFlight follows pending turn verbs only", async () => {
  const held = [];
  const { bridge } = makeBridge((line) => {
    if (line.method === "initialize") return { id: line.id, ok: true, result: INIT };
    held.push(line);
    return undefined;
  });
  await bridge.ensureStarted();
  const read = bridge.request("get_state");
  await tick();
  assert.equal(bridge.turnInFlight, false);
  const turn = bridge.request("send_message", { text: "hi" });
  await tick();
  assert.equal(bridge.turnInFlight, true);
  bridge.failPending(new Error("done"));
  await assert.rejects(read);
  await assert.rejects(turn);
  assert.equal(bridge.turnInFlight, false);
});

test("a request times out and is forgotten", async () => {
  const { bridge } = makeBridge((line) => (line.method === "initialize" ? { id: line.id, ok: true, result: INIT } : undefined));
  await assert.rejects(bridge.request("slow", {}, 10), /bridge request timed out: slow/);
});

test("process exit fails pending requests with the stderr tail; the next request restarts", async () => {
  const { bridge, spawned } = makeBridge((line, proc) => {
    if (line.method === "initialize") return { id: line.id, ok: true, result: INIT };
    if (line.method !== "crash") return okReply(line);
    proc.stderr.write("Traceback: boom\n");
    setTimeout(() => proc.exit(), 5);
    return undefined;
  });
  let exited;
  bridge.on("exit", (err) => (exited = err));
  await assert.rejects(bridge.request("crash"), /bridge exited: Traceback: boom/);
  assert.match(exited.message, /boom/);
  assert.equal(bridge.alive, false);
  await bridge.request("get_state");
  assert.equal(spawned.length, 2);
});

test("a stale process's exit does not touch its successor", async () => {
  const { bridge, spawned } = makeBridge();
  await bridge.ensureStarted();
  await bridge.reconnect();
  assert.equal(spawned.length, 2);
  spawned[0].proc.exit();
  await tick();
  assert.equal(bridge.alive, true);
  assert.deepEqual(await bridge.request("get_state"), { echo: "get_state", params: {} });
});

test("shutdown asks the bridge first, then SIGTERM and SIGKILL after the grace", async () => {
  const { bridge, spawned } = makeBridge();
  await bridge.ensureStarted();
  await bridge.shutdown();
  const proc = spawned[0].proc;
  assert.equal(proc.written.at(-1).method, "shutdown");
  await new Promise((r) => setTimeout(r, 20));
  assert.deepEqual(proc.signals, ["SIGTERM", "SIGKILL"]);
  assert.equal(bridge.alive, false);
});

test("a protocol mismatch refuses to start and names the extension", async () => {
  const { bridge, spawned } = makeBridge((line) => ({ id: line.id, ok: true, result: { bridge_protocol: 2 } }));
  await assert.rejects(bridge.ensureStarted(), /VS Code expects v1, server speaks v2/);
  assert.deepEqual(spawned[0].proc.signals, ["SIGTERM"]);
});

test("a legacy bridge without a protocol version starts with a notice", async () => {
  const { bridge } = makeBridge((line) => ({ id: line.id, ok: true, result: { session: {} } }));
  const notes = [];
  bridge.on("stderr", (t) => notes.push(t));
  await bridge.ensureStarted();
  assert.match(notes.join(""), /legacy server without protocol version/);
});

test("initialize into a known session falls back to a new one when the bridge does not know it", async () => {
  const { bridge, spawned } = makeBridge(
    (line) =>
      line.params?.session_id
        ? { id: line.id, ok: false, error: { type: "R4Error", message: "Session not found: gone" } }
        : { id: line.id, ok: true, result: INIT },
    { initialSessionId: () => "gone-1234" },
  );
  await bridge.ensureStarted();
  assert.deepEqual(spawned[0].proc.written.map((l) => l.params), [{ session_id: "gone-1234" }, {}]);
});

test("invalid JSON is a protocol error, an unknown id is unmatched", async () => {
  const { bridge, spawned } = makeBridge();
  await bridge.ensureStarted();
  const errors = [];
  const unmatched = [];
  bridge.on("protocol_error", (e) => errors.push(e.message));
  bridge.on("unmatched", (m) => unmatched.push(m));
  spawned[0].proc.send("{not json");
  spawned[0].proc.send({ id: 999, ok: true, result: {} });
  await tick();
  assert.match(errors[0], /invalid bridge JSON/);
  assert.equal(unmatched[0].id, 999);
});

test("python candidates: setting, R4_TUI_PYTHON, launcher, workspace venv, then python3", () => {
  const files = { "/bin/r4": "#!/opt/venv/bin/python3\nimport r4\n", "/ws/.venv/bin/python": "" };
  const deps = {
    readFile: (f) => files[f],
    exists: (f) => f in files,
    canImportR4: (p) => p === "/opt/venv/bin/python3",
  };
  const input = { setting: " /set/python ", env: { R4_TUI_PYTHON: "/env/python" }, cliPath: "/bin/r4", workspace: "/ws", platform: "linux" };
  assert.deepEqual(pythonCandidates(input, deps), [
    "/set/python", "/env/python", "/opt/venv/bin/python3", "/ws/.venv/bin/python", "python3", "python",
  ]);
  assert.equal(resolveBridgePython(input, deps).python, "/opt/venv/bin/python3");
  assert.equal(resolveBridgePython({ ...input, cliPath: "r4" }, { ...deps, canImportR4: () => false }).python, null);
});

test("launcher parsing: bash exec line and shebang", () => {
  assert.equal(pythonFromLauncher('#!/bin/sh\nexec "/u/.venv/bin/python" -m r4 "$@"\n'), "/u/.venv/bin/python");
  assert.equal(pythonFromLauncher("#!/u/.venv/bin/python3.11 -I\n"), "/u/.venv/bin/python3.11");
  assert.equal(pythonFromLauncher("#!/bin/bash\necho hi\n"), null);
});

test("Open Console runs `r4 console` in the workspace folder", () => {
  assert.deepEqual(consoleTerminalSpec("/home/u/.local/bin/r4", "/ws"), {
    name: "R4 Console",
    shellPath: "/home/u/.local/bin/r4",
    shellArgs: ["console"],
    cwd: "/ws",
    env: { R4_TUI_WORKSPACE: "/ws" },
  });
  assert.deepEqual(consoleTerminalSpec("r4", undefined).env, {});
});
