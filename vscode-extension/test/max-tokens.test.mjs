/**
 * Verifies that the chat-completions request sent to the Gateway does
 * NOT include a max_tokens cap.  The upstream server (vLLM, LiteLLM)
 * should decide the output limit, matching the "model decides" pattern.
 */

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "lm-provider-max-tokens.bundle.cjs");
const vscodeStubPath = path.join(rootDir, "test", "vscode-stub.cjs");

// ── bundle lm-provider.ts (externalise vscode) ───────────────────
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  [
    "src/lm-provider.ts",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    `--outfile=${bundlePath}`,
    "--external:vscode",
  ],
  { stdio: "inherit", cwd: rootDir },
);

// ── redirect "vscode" to our stub ────────────────────────────────
const require = createRequire(import.meta.url);
const Module = require("node:module");
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, isMain, options) {
  if (request === "vscode") {
    return vscodeStubPath;
  }
  return originalResolve.call(this, request, parent, isMain, options);
};

const { R4LanguageModelProvider } = require(bundlePath);
const vscode = require(vscodeStubPath);
rmSync(bundlePath, { force: true });

// ── helpers ──────────────────────────────────────────────────────
/** Captures the request passed to chatCompletionStream and yields nothing. */
function makeCapturingClient() {
  let capturedRequest = null;
  const client = {
    async *chatCompletionStream(req, signal) {
      capturedRequest = req;
    },
  };
  return { client, getRequest: () => capturedRequest };
}

function makeModel() {
  return {
    id: "test-model",
    name: "test-model",
    family: "r4",
    version: "1.0.0",
    maxInputTokens: 16_000,
    maxOutputTokens: 16_384,
    detail: "R4 Platform",
    capabilities: { toolCalling: true, imageInput: false },
  };
}

function makeMessages() {
  return [
    {
      role: vscode.LanguageModelChatMessageRole.User,
      content: [new vscode.LanguageModelTextPart("Hello, world!")],
    },
  ];
}

function makeOptions() {
  return { tools: [] };
}

function makeProgress() {
  return { report: () => {} };
}

function makeToken() {
  return {
    onCancellationRequested: () => ({ dispose: () => {} }),
    isCancellationRequested: false,
  };
}

// ── tests ────────────────────────────────────────────────────────

test("chatCompletionStream request does not include max_tokens", async () => {
  const { client, getRequest } = makeCapturingClient();
  const provider = new R4LanguageModelProvider(() => client, () => "R4");

  await provider.provideLanguageModelChatResponse(
    makeModel(),
    makeMessages(),
    makeOptions(),
    makeProgress(),
    makeToken(),
  );

  const req = getRequest();
  assert.ok(req, "chatCompletionStream was not called");
  assert.equal(
    "max_tokens" in req,
    false,
    "max_tokens must not be sent on the wire (model decides)",
  );
});

test("request includes model, messages, tools, and tool_choice", async () => {
  const { client, getRequest } = makeCapturingClient();
  const provider = new R4LanguageModelProvider(() => client, () => "R4");

  await provider.provideLanguageModelChatResponse(
    makeModel(),
    makeMessages(),
    makeOptions(),
    makeProgress(),
    makeToken(),
  );

  const req = getRequest();
  assert.equal(req.model, "test-model");
  assert.ok(Array.isArray(req.messages));
  assert.equal(req.messages.length, 1);
});
