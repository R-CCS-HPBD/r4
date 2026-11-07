import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { PassThrough } from "node:stream";
import { test } from "node:test";
import { setImmediate } from "node:timers";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const clientBundlePath = path.join(rootDir, "test", "acp-client.bundle.cjs");
const processBundlePath = path.join(rootDir, "test", "acp-process.bundle.cjs");
const esbuild = path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild");

execFileSync(
  esbuild,
  [
    "src/acp-client.ts",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    `--outfile=${clientBundlePath}`,
  ],
  { stdio: "inherit", cwd: rootDir },
);
execFileSync(
  esbuild,
  [
    "src/acp-process.ts",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    `--outfile=${processBundlePath}`,
  ],
  { stdio: "inherit", cwd: rootDir },
);

const require = createRequire(import.meta.url);
const { AcpClient, AcpRequestError } = require(clientBundlePath);
const { AcpProcessSupervisor } = require(processBundlePath);

function createTransport(timeoutMs = 100) {
  const input = new PassThrough();
  const output = new PassThrough();
  const sent = [];
  output.setEncoding("utf8");
  output.on("data", (chunk) => {
    for (const line of chunk.trim().split("\n")) {
      if (line) sent.push(JSON.parse(line));
    }
  });
  return { input, output, sent, client: new AcpClient(input, output, timeoutMs) };
}

function tick() {
  return new Promise((resolve) => setImmediate(resolve));
}

test("AcpClient correlates responses and forwards notifications", async () => {
  const { client, input, sent } = createTransport();
  const notifications = [];
  client.onNotification((event) => notifications.push(event));

  const pending = client.request("sessions/create", { name: "Research" });
  await tick();
  assert.equal(sent[0].method, "sessions/create");
  input.write(`${JSON.stringify({ jsonrpc: "2.0", method: "session/event", params: { step: 1 } })}\n`);
  input.write(`${JSON.stringify({ jsonrpc: "2.0", id: sent[0].id, result: { session: { id: "s1" } } })}\n`);

  assert.deepEqual(await pending, { session: { id: "s1" } });
  assert.equal(notifications[0].method, "session/event");
  client.dispose();
});

test("AcpClient maps JSON-RPC errors", async () => {
  const { client, input, sent } = createTransport();
  const pending = client.request("chat/send", { message: "hello" });
  await tick();
  input.write(`${JSON.stringify({ jsonrpc: "2.0", id: sent[0].id, error: { code: -32602, message: "invalid" } })}\n`);

  await assert.rejects(pending, (error) => error instanceof AcpRequestError && error.code === -32602);
  client.dispose();
});

test("AcpClient rejects timed out and aborted requests", async () => {
  const timed = createTransport(5);
  await assert.rejects(timed.client.request("health/check"), /timed out/);
  assert.deepEqual(timed.sent.at(-1), {
    jsonrpc: "2.0",
    method: "$/cancelRequest",
    params: { id: timed.sent[0].id },
  });
  timed.client.dispose();

  const aborted = createTransport();
  const controller = new globalThis.AbortController();
  const pending = aborted.client.request("chat/send", {}, { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, (error) => error.name === "AbortError");
  assert.deepEqual(aborted.sent.at(-1), {
    jsonrpc: "2.0",
    method: "$/cancelRequest",
    params: { id: aborted.sent.at(-2).id },
  });
  aborted.client.dispose();
});

test("AcpClient rejects pending requests when transport closes", async () => {
  const { client, input } = createTransport();
  const pending = client.request("chat/send");
  input.end();
  await assert.rejects(pending, /output closed/);
});

test("AcpClient resets the request timeout on matching keepalive notifications", async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  // Wide margins: parallel test-file processes can stall the event loop, and
  // the keepalive MUST be processed before the original timer would fire.
  const { client, input, sent } = createTransport(150);
  const pending = client.request("chat/send", { message: "hi", request_id: "req-1" });
  await tick();
  await wait(40);
  input.write(
    `${JSON.stringify({ jsonrpc: "2.0", method: "session/progress", params: { request_id: "req-1", state: "running" } })}\n`,
  );
  await wait(50);
  // Without the keepalive reset the request would already have hit the 150ms
  // cap at ~40ms; the reset pushes the deadline to ~190ms, past this reply.
  input.write(
    `${JSON.stringify({ jsonrpc: "2.0", id: sent[0].id, result: { status: "completed" } })}\n`,
  );
  assert.deepEqual(await pending, { status: "completed" });
  client.dispose();
});

test("AcpClient ignores keepalive notifications for other request ids", async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  // Attach the rejection handler BEFORE any timer can fire: parallel test
  // bundles can stall the event loop past the cap while the wait below is
  // sleeping, and a rejection that lands unattached fails the whole run
  // even though the text matches (observed 2026-08-23, publish script).
  const { client, input } = createTransport(150);
  const pending = client.request("chat/send", { message: "hi", request_id: "req-1" });
  const assertion = assert.rejects(pending, /timed out/);
  await tick();
  await wait(50);
  input.write(
    `${JSON.stringify({ jsonrpc: "2.0", method: "session/progress", params: { request_id: "req-other", state: "running" } })}\n`,
  );
  await assertion;
  client.dispose();
});

test("AcpClient resolves matching request despite cross-request keepalive", async () => {
  // Deterministic crosstalk pin: a keepalive carrying an unknown request id
  // must not touch this request's entry — resolution arrives on time.
  const { client, input, sent } = createTransport(150);
  const pending = client.request("chat/send", { message: "hi", request_id: "req-1" });
  await tick();
  input.write(
    `${JSON.stringify({ jsonrpc: "2.0", method: "session/progress", params: { request_id: "req-other", state: "running" } })}\n`,
  );
  input.write(
    `${JSON.stringify({ jsonrpc: "2.0", id: sent[0].id, result: { status: "completed" } })}\n`,
  );
  assert.deepEqual(await pending, { status: "completed" });
  client.dispose();
});

test("AcpClient reports malformed protocol messages without crashing", async () => {
  const { client, input } = createTransport();
  const errors = [];
  client.onProtocolError((error) => errors.push(error));
  input.write("not-json\n");
  input.write(`${JSON.stringify({ jsonrpc: "1.0" })}\n`);
  await tick();
  assert.equal(errors.length, 2);
  client.dispose();
});

test("AcpClient dispatches registered editor methods and denies unknown methods", async () => {
  const { client, input, sent } = createTransport();
  client.registerMethod("editor/toolCall", async (params) => ({ echoed: params.value }));

  input.write(`${JSON.stringify({ jsonrpc: "2.0", id: 41, method: "editor/toolCall", params: { value: "ok" } })}\n`);
  input.write(`${JSON.stringify({ jsonrpc: "2.0", id: 42, method: "editor/unknown", params: {} })}\n`);
  await tick();

  assert.deepEqual(sent.find((message) => message.id === 41), {
    jsonrpc: "2.0", id: 41, result: { echoed: "ok" },
  });
  assert.deepEqual(sent.find((message) => message.id === 42), {
    jsonrpc: "2.0", id: 42, error: { code: -32601, message: "Method not found" },
  });
  client.dispose();
});

test("AcpClient hides editor handler errors", async () => {
  const { client, input, sent } = createTransport();
  client.registerMethod("editor/toolCall", async () => { throw new Error("secret-value"); });
  input.write(`${JSON.stringify({ jsonrpc: "2.0", id: 43, method: "editor/toolCall", params: {} })}\n`);
  await tick();

  const response = sent.find((message) => message.id === 43);
  assert.equal(JSON.stringify(response).includes("secret-value"), false);
  assert.equal(response.error.code, -32603);
  client.dispose();
});

class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.stdin = new PassThrough();
    this.stdout = new PassThrough();
    this.stderr = new PassThrough();
    this.exitCode = null;
    this.signalCode = null;
    this.killedWith = undefined;
    let buffer = "";
    this.stdin.setEncoding("utf8");
    this.stdin.on("data", (chunk) => {
      buffer += chunk;
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const request = JSON.parse(line);
        this.stdout.write(`${JSON.stringify({ jsonrpc: "2.0", id: request.id, result: { protocol_version: 1 } })}\n`);
      }
    });
  }

  kill(signal) {
    this.killedWith = signal;
    this.killSignals ??= [];
    this.killSignals.push(signal);
    return true;
  }
}

test("AcpProcessSupervisor spawns without a shell and initializes once", async () => {
  const child = new FakeChild();
  const calls = [];
  const spawnProcess = (...args) => {
    calls.push(args);
    return child;
  };
  const supervisor = new AcpProcessSupervisor({ command: "/opt/r4", spawnProcess });

  const first = await supervisor.start();
  const second = await supervisor.start();
  assert.equal(first, second);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].slice(0, 2), ["/opt/r4", ["acp"]]);
  assert.equal(calls[0][2].shell, false);

  supervisor.stop();
  assert.equal(child.killedWith, "SIGTERM");
});

test("AcpProcessSupervisor terminates a child when initialization fails", async () => {
  const child = new FakeChild();
  child.stdin.removeAllListeners("data");
  const supervisor = new AcpProcessSupervisor({
    command: "r4", spawnProcess: () => child, requestTimeoutMs: 1, killTimeoutMs: 1,
  });

  await assert.rejects(supervisor.start(), /timed out/);
  assert.equal(child.killSignals[0], "SIGTERM");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.deepEqual(child.killSignals, ["SIGTERM", "SIGKILL"]);
});

test("AcpProcessSupervisor rejects pending work after process exit", async () => {
  const child = new FakeChild();
  const supervisor = new AcpProcessSupervisor({
    command: "r4",
    spawnProcess: () => child,
  });
  await supervisor.start();
  child.stdin.removeAllListeners("data");
  const pending = supervisor.request("chat/send", { message: "long task" });
  await tick();
  child.stderr.write("runtime failed");
  child.emit("exit", 1, null);

  await assert.rejects(pending, /exited with code 1/);
  supervisor.stop();
});

test("AcpProcessSupervisor stop cancels pending work and terminates child", async () => {
  const child = new FakeChild();
  const supervisor = new AcpProcessSupervisor({ command: "r4", spawnProcess: () => child });
  await supervisor.start();
  child.stdin.removeAllListeners("data");
  const pending = supervisor.request("chat/send", { message: "long task" });
  await tick();

  supervisor.stop();

  await assert.rejects(pending, /process stopped/);
  assert.equal(child.killedWith, "SIGTERM");
});
