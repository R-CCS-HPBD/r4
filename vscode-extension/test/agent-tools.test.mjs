import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { setImmediate as tick } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "agent-tools.bundle.cjs");
const esbuild = path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild");
execFileSync(
  esbuild,
  ["src/agent-tools.ts", "--bundle", "--platform=node", "--format=cjs", `--outfile=${bundlePath}`],
  { stdio: "inherit", cwd: rootDir },
);

const require = createRequire(import.meta.url);
const {
  AgentToolBridge,
  AgentToolRegistry,
  createCoreToolRegistry,
  createJsonlAuditWriter,
} = require(bundlePath);

const policy = {
  name: "test-policy",
  tools: {
    "workspace.readFile": { access: "allow", pathArgument: "path" },
    "workspace.writeFile": { access: "approval", pathArgument: "path" },
  },
};

async function workspaceTest(run) {
  const created = await mkdtemp(path.join(os.tmpdir(), "r4-agent-tools-"));
  // The bridge canonicalizes workspace paths (symlink-escape containment), so on
  // macOS it reports /private/var/... while mkdtemp hands back /var/... . Compare
  // against the canonical form or scope assertions fail on that platform only.
  const root = await realpath(created);
  try {
    await run(root);
  } finally {
    await rm(created, { recursive: true, force: true });
  }
}

function createBridge(root, approve, events, registry = createCoreToolRegistry()) {
  return new AgentToolBridge(root, policy, registry, approve, async (event) => events.push(event));
}

test("read tool is allowed only inside workspace and unknown tools are denied", async () => {
  await workspaceTest(async (root) => {
    await writeFile(path.join(root, "note.txt"), "research", "utf8");
    const events = [];
    const bridge = createBridge(root, async () => true, events);

    assert.deepEqual(
      await bridge.call({ sessionId: "s1", name: "workspace.readFile", arguments: { path: "note.txt" } }),
      { content: "research" },
    );
    await assert.rejects(
      bridge.call({ sessionId: "s1", name: "workspace.readFile", arguments: { path: "../secret" } }),
      /denied by policy/,
    );
    await assert.rejects(
      bridge.call({ sessionId: "s1", name: "shell", arguments: {} }),
      /denied by policy/,
    );
    assert.deepEqual(events.map((event) => event.decision), ["allow", "deny", "deny"]);
  });
});

test("workspace symlinks cannot escape containment", async () => {
  await workspaceTest(async (root) => {
    const outside = await mkdtemp(path.join(os.tmpdir(), "r4-agent-outside-"));
    try {
      await writeFile(path.join(outside, "secret.txt"), "secret", "utf8");
      await symlink(outside, path.join(root, "escape"), "dir");
      const bridge = createBridge(root, async () => true, []);

      await assert.rejects(
        bridge.call({
          sessionId: "s1",
          name: "workspace.readFile",
          arguments: { path: "escape/secret.txt" },
        }),
        /denied by policy/,
      );
    } finally {
      await rm(outside, { recursive: true, force: true });
    }
  });
});

test("write tool shows exact action and scope and fails closed", async () => {
  await workspaceTest(async (root) => {
    const approvals = [];
    const events = [];
    const bridge = createBridge(root, async (request) => {
      approvals.push(request);
      return false;
    }, events);

    await assert.rejects(
      bridge.call({
        sessionId: "s2",
        name: "workspace.writeFile",
        arguments: { path: "result.txt", content: "token=do-not-log" },
      }),
      /denied by policy/,
    );
    assert.deepEqual(approvals, [{
      tool: "workspace.writeFile",
      action: "Write workspace file: result.txt",
      scope: path.join(root, "result.txt"),
    }]);
    assert.equal(JSON.stringify(events).includes("do-not-log"), false);

    const brokenApproval = createBridge(root, async () => { throw new Error("UI unavailable"); }, events);
    await assert.rejects(
      brokenApproval.call({ sessionId: "s2", name: "workspace.writeFile", arguments: { path: "x", content: "x" } }),
      /denied by policy/,
    );
  });
});

test("approved writes execute and JSONL audit contains metadata only", async () => {
  await workspaceTest(async (root) => {
    const auditPath = path.join(root, "audit.jsonl");
    const bridge = new AgentToolBridge(
      root,
      policy,
      createCoreToolRegistry(),
      async () => true,
      createJsonlAuditWriter(auditPath),
    );
    const states = [];
    bridge.setStateListener((_request, status) => states.push(status));
    await bridge.call({
      sessionId: "s3",
      name: "workspace.writeFile",
      arguments: { path: "output.txt", content: "credential-value" },
    });

    assert.equal(await readFile(path.join(root, "output.txt"), "utf8"), "credential-value");
    const event = JSON.parse((await readFile(auditPath, "utf8")).trim());
    assert.deepEqual(Object.keys(event).sort(), [
      "decision", "evidence_hash", "policy", "session_id", "timestamp", "tool",
    ]);
    assert.match(event.evidence_hash, /^sha256:[a-f0-9]{64}$/);
    assert.equal(JSON.stringify(event).includes("credential-value"), false);
    assert.deepEqual(states, ["approved", "running", "completed"]);
  });
});

test("registration rejects duplicate and unnamed tools", () => {
  const registry = new AgentToolRegistry();
  registry.register("workspace.diagnostics", async () => []);
  assert.throws(() => registry.register("workspace.diagnostics", async () => []), /registration rejected/);
  assert.throws(() => registry.register("", async () => []), /registration rejected/);
});

test("terminal policy rejects unlisted commands and workspace escape arguments", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "r4-agent-tools-"));
  const registry = new AgentToolRegistry();
  registry.register("terminal.run", async () => ({ ok: true }));
  const bridge = new AgentToolBridge(
    root,
    {
      name: "terminal-policy",
      tools: {
        "terminal.run": {
          access: "approval",
          pathArgument: "cwd",
          commands: { git: ["status", "diff"], rg: [] },
        },
      },
    },
    registry,
    async () => true,
    async () => undefined,
  );

  await assert.rejects(
    bridge.call({ sessionId: "s4", name: "terminal.run", arguments: { command: "bash", args: [], cwd: "." } }),
    /denied/,
  );
  await assert.rejects(
    bridge.call({ sessionId: "s4", name: "terminal.run", arguments: { command: "rg", args: ["../secret"], cwd: "." } }),
    /denied/,
  );
  await assert.rejects(
    bridge.call({
      sessionId: "s4", name: "terminal.run",
      arguments: { command: "git", args: ["diff", "--output=/tmp/result"], cwd: "." },
    }),
    /denied/,
  );
});

test("cancellation aborts only active tools for the matching request", async () => {
  await workspaceTest(async (root) => {
    const registry = new AgentToolRegistry();
    const signals = new Map();
    // Key by the caller's path, never by invocation order: the two calls race
    // through async scope resolution and may reach the handler out of order.
    registry.register("workspace.readFile", async (args, context) => {
      signals.set(args.path, context.signal);
      await new Promise((resolve) => context.signal.addEventListener("abort", resolve, { once: true }));
      context.signal.throwIfAborted();
    });
    const bridge = createBridge(root, async () => true, [], registry);
    const first = bridge.call({
      sessionId: "s5", requestId: "r1", toolCallId: "t1",
      name: "workspace.readFile", arguments: { path: "one.txt" },
    });
    const second = bridge.call({
      sessionId: "s6", requestId: "r2", toolCallId: "t2",
      name: "workspace.readFile", arguments: { path: "two.txt" },
    });

    while (signals.size < 2) await tick();
    bridge.cancel("r1");
    await assert.rejects(first, { name: "AbortError" });
    assert.equal(signals.get("two.txt").aborted, false);
    bridge.cancel("r2");
    await assert.rejects(second, { name: "AbortError" });
  });
});

test("already-cancelled tool call never invokes its handler", async () => {
  await workspaceTest(async (root) => {
    const registry = new AgentToolRegistry();
    let invoked = false;
    registry.register("workspace.readFile", async () => {
      invoked = true;
    });
    const bridge = createBridge(root, async () => true, [], registry);
    const pending = bridge.call({
      sessionId: "s7", requestId: "r7", toolCallId: "t7",
      name: "workspace.readFile", arguments: { path: "one.txt" },
    });

    bridge.cancel("r7");

    await assert.rejects(pending, { name: "AbortError" });
    assert.equal(invoked, false);
  });
});
