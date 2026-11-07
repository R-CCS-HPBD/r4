import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "reasoning-roundtrip.bundle.cjs");
const vscodeStubPath = path.join(rootDir, "test", "vscode-stub.cjs");

const require = createRequire(import.meta.url);
require.cache[vscodeStubPath] = {
  id: vscodeStubPath,
  filename: vscodeStubPath,
  loaded: true,
  exports: require(vscodeStubPath),
};

execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  [
    "test/reasoning-roundtrip-entry.ts",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    `--outfile=${bundlePath}`,
    "--external:vscode",
  ],
  { stdio: "inherit", cwd: rootDir },
);

const Module = require("node:module");
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, isMain, options) {
  if (request === "vscode") {
    return vscodeStubPath;
  }
  return originalResolve.call(this, request, parent, isMain, options);
};

const {
  createToolReasoningKey,
  createPostToolReasoningKey,
  cacheStreamedReasoning,
  encodeReplayMarkerBytes,
  parseReplayMarkerData,
  collectTrailingToolResultIds,
  convertMessages,
} = require(bundlePath);

const vscode = require(vscodeStubPath);

test("reasoning cache keys are stable", () => {
  assert.equal(createToolReasoningKey("call_abc"), "tool:call_abc");
  assert.equal(createPostToolReasoningKey(["a", "b"]), 'post-tool:["a","b"]');
});

test("replay marker round-trips reasoning text", () => {
  const bytes = encodeReplayMarkerBytes({ reasoningText: "plan step one" });
  assert.ok(bytes);
  const parsed = parseReplayMarkerData(bytes);
  assert.equal(parsed.valid, true);
  assert.equal(parsed.reasoningText, "plan step one");
});

test("replay marker rejects invalid prefix", () => {
  const parsed = parseReplayMarkerData(new TextEncoder().encode("bad\\j:xxx"));
  assert.equal(parsed.valid, false);
});

test("collectTrailingToolResultIds reads trailing tool messages", () => {
  const ids = collectTrailingToolResultIds([
    { role: "user", content: "hi" },
    {
      role: "assistant",
      content: "",
      tool_calls: [{ id: "c1", type: "function", function: { name: "x", arguments: "{}" } }],
    },
    { role: "tool", content: "ok", tool_call_id: "c1" },
  ]);
  assert.deepEqual(ids, ["c1"]);
});

test("convertMessages injects cached reasoning for assistant tool-call turn", () => {
  const cache = new Map();
  cache.set(createToolReasoningKey("call_1"), { text: "cached plan", timestamp: 1 });

  const messages = [
    {
      role: vscode.LanguageModelChatMessageRole.Assistant,
      content: [
        new vscode.LanguageModelToolCallPart("call_1", "read_file", { path: "a.ts" }),
      ],
    },
  ];

  const out = convertMessages(messages, true, cache);
  assert.equal(out.length, 1);
  assert.equal(out[0].role, "assistant");
  assert.equal(out[0].reasoning_content, "cached plan");
  assert.equal(out[0].tool_calls?.[0].id, "call_1");
});

test("convertMessages prefers replay marker over cache", () => {
  const cache = new Map();
  cache.set(createToolReasoningKey("call_1"), { text: "cached plan", timestamp: 1 });

  const markerBytes = encodeReplayMarkerBytes({ reasoningText: "marker plan" });
  const messages = [
    {
      role: vscode.LanguageModelChatMessageRole.Assistant,
      content: [
        new vscode.LanguageModelDataPart(markerBytes, "application/vnd.r4.replay-marker"),
        new vscode.LanguageModelToolCallPart("call_1", "read_file", {}),
      ],
    },
  ];

  const out = convertMessages(messages, true, cache);
  assert.equal(out[0].reasoning_content, "marker plan");
});

test("convertMessages injects post-tool reasoning from cache", () => {
  const cache = new Map();
  cache.set(createPostToolReasoningKey(["call_9"]), { text: "final thought", timestamp: 1 });

  const out = convertMessages(
    [
      {
        role: vscode.LanguageModelChatMessageRole.Assistant,
        content: [new vscode.LanguageModelToolCallPart("call_9", "t", {})],
      },
      {
        role: vscode.LanguageModelChatMessageRole.User,
        content: [
          new vscode.LanguageModelToolResultPart("call_9", [
            new vscode.LanguageModelTextPart("done"),
          ]),
        ],
      },
      {
        role: vscode.LanguageModelChatMessageRole.Assistant,
        content: [new vscode.LanguageModelTextPart("answer")],
      },
    ],
    true,
    cache,
  );

  const finalAssistant = out.find((m) => m.role === "assistant" && m.content === "answer");
  assert.equal(finalAssistant?.reasoning_content, "final thought");
});

test("cacheStreamedReasoning stores per tool call id", () => {
  const cache = new Map();
  cacheStreamedReasoning(true, "streamed", ["c1", "c2"], [], cache);
  assert.equal(cache.get(createToolReasoningKey("c1"))?.text, "streamed");
  assert.equal(cache.get(createToolReasoningKey("c2"))?.text, "streamed");
});
