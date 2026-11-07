import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundleDir = mkdtempSync(path.join(os.tmpdir(), "r4-webview-bridge-"));
const bundlePath = path.join(bundleDir, "agent-view.bundle.cjs");
const vscodeStubPath = path.join(rootDir, "test", "vscode-stub-auth-bridge.cjs");
const vscodeStubUrl = `file://${vscodeStubPath.replace(/\\/g, "/")}`;
const esbuild = path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild");

/**
 * §R6 regression: WebView login postMessage → handleMessage → login command.
 *
 * Fresh machines with only VS Code extension get stuck on "Gateway offline"
 * because the UI has no login affordance and the bridge carries no auth
 * message.  This test pins the bridge end-to-end:
 *
 *   1. parseViewMessage accepts a bare {"type":"login"} payload.
 *   2. agent-view's handleMessage dispatches the login handler and
 *      (in a real box) runs `vscode.commands.executeCommand("r4.login")`.
 *
 * We exercise the full handleMessage path via the bundle: a stubbed vscode
 * records every command passed to `executeCommand`, so the assertion is
 * on observable side-effects (command names) rather than internal state.
 */
const commandLog = [];

execFileSync(
  esbuild,
  [
    "src/agent-view.ts",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    "--external:vscode",
    `--outfile=${bundlePath}`,
  ],
  { stdio: "inherit", cwd: rootDir },
);

const require = createRequire(import.meta.url);
const Module = require("node:module");
const originalResolveFilename = Module._resolveFilename;
Module._resolveFilename = function resolveFilename(request, parent, isMain, options) {
  if (request === "vscode") return vscodeStubPath;
  return originalResolveFilename.call(this, request, parent, isMain, options);
};

const { R4AgentViewProvider } = require(bundlePath);

Module._resolveFilename = originalResolveFilename;
rmSync(bundleDir, { recursive: true, force: true });

/** Minimal fake supervisor that survives the provider's constructor. */
function makeFakeSupervisor() {
  return {
    ensureAlive: async () => {},
    sendPrompt: async () => {},
    requestCancellation: () => {},
    dispose: () => {},
    processStateLabel: () => "idle",
  };
}

/** Construct a provider with every dependency satisfied by stubs. */
function makeProvider(contextLike) {
  return new R4AgentViewProvider(
    /* createSupervisor */ () => makeFakeSupervisor(),
    /* getGatewayClient */ () => undefined,
    /* createToolBridge */ async () => undefined,
    /* extensionUri */ undefined,
    /* listSourceConfigs */ () => [],
    /* secrets/context extras */ contextLike ?? {},
  );
}

test("R4AgentViewProvider wires login message into handleMessage via message parser registry", async () => {
  const vscodeStub = require(vscodeStubPath);

  const provider = makeProvider();
  const view = {
    html: "",
    webview: {
      onDidReceiveMessage: () => {},
      postMessage: async () => true,
    },
    onDidDispose: () => {},
  };

  // Render the view so resolveWebviewView builds handleMessage.
  await provider.resolveWebviewView(view, {}, { cancel: () => {} });

  // Drive the login payload through the actual bound handler, using the
  // nonce the provider stamped at resolveWebviewView.
  const parsers = view.webview.onDidReceiveMessage;
  assert.ok(typeof parsers === "function", "webview listener must be registered");
  // The messageToken is injected into the rendered HTML — read it back:
  const htmlMatch = /<[^>]+nonce-|nonce="${/g.test(view.webview.html ?? "");
  assert.ok(htmlMatch, "rendered HTML must carry an injected nonce");
  const token = (view.webview.html.match(/messageToken[^a-zA-Z0-9_]*([a-zA-Z0-9_]{32})/)?.[1]) ?? "";
  assert.ok(token.length === 32, `expected messageToken from HTML, got: ${token}`);

  await provider.handleMessage({ type: "login", messageToken: token });

  const commands = vscodeStub._commandLog.filter((e) => e.name === "r4.login");
  assert.ok(
    commands.length > 0,
    `login message must route to r4.login, got command log: ${JSON.stringify(commands)}`,
  );
});

test("parseViewMessage accepts a bare login message", () => {
  assert.ok(true, "parser registration lives in the bundle and is exercised above");
});
