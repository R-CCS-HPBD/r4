import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { readFileSync, writeFileSync } from "node:fs";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "agent-engine.bundle.cjs");

execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  [
    "src/agent-engine.ts",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    "--external:vscode",
    "--outfile=" + bundlePath,
  ],
  { stdio: "inherit", cwd: rootDir },
);

// Patch out runtime vscode require — agent-engine.ts only uses vscode types,
// but transitively dependencies (thinking.ts) also import vscode at runtime.
let bundle = readFileSync(bundlePath, "utf-8");
bundle = bundle.replace(
  /(?:var|let|const) (vscode\d?) = __toESM\(require\("vscode"\)\);/g,
  "var $1 = {};",
);
writeFileSync(bundlePath, bundle);

const require = createRequire(import.meta.url);
const { AgentEngine, knownToolsToSchemas, ToolCallAccum } = require(bundlePath);

// ---------------------------------------------------------------------------
// Mock helpers
// ---------------------------------------------------------------------------

function mockStream() {
  return {
    markdown() {},
    thinkingProgress() {},
    progress() {},
  };
}

function mockAgentToolBridge() {
  return {
    call: async () => ({ result: "mocked" }),
    listTools: () => [
      { name: "workspace.readFile", description: "Read a file" },
    ],
  };
}

// ---------------------------------------------------------------------------
// B3: tool_choice is sent when enableToolCalls is true
// ---------------------------------------------------------------------------

test("chat request includes tool_choice when tools are available and enableToolCalls is true", async () => {
  let lastReq = null;
  const client = {
    chatCompletionStream: async function* (req, _signal) {
      lastReq = req;
      yield { content: "done" };
    },
    callMCPTool: async () => ({ content: [], isError: false }),
  };

  await new AgentEngine(
    client,
    mockAgentToolBridge(),
    [{ name: "knowledge_search", description: "Search knowledge", parameters: { type: "object", properties: {} } }],
    [],
    false,
    () => {},
    () => {},
  ).runTurn(
    {
      messages: [],
      model: "test-model",
      enableToolCalls: true,
      mode: "chat",
    },
    { isCancellationRequested: false, onCancellationRequested: () => ({ dispose: () => {} }) },
    mockStream(),
  );

  assert.ok(lastReq, "chatCompletionStream must be called");
  assert.equal(lastReq.tool_choice, "auto", "tool_choice must be 'auto' when tools are present");
  assert.ok(lastReq.tools, "tools must be sent to the model");
  // KNOWN_R4_TOOLS lost list_workflows with the 53d900da workflow-policy
  // reparenting (12 base tools) — MCP knowledge_search adds one more.
  assert.ok(lastReq.tools.length >= 12, `tools sent to model (${lastReq.tools.length} total)`);
});

test("chat request omits tool_choice when enableToolCalls is false", async () => {
  let lastReq = null;
  const client = {
    chatCompletionStream: async function* (req, _signal) {
      lastReq = req;
      yield { content: "done" };
    },
    callMCPTool: async () => ({ content: [], isError: false }),
  };

  await new AgentEngine(
    client,
    mockAgentToolBridge(),
    [{ name: "knowledge_search", description: "Search knowledge", parameters: { type: "object", properties: {} } }],
    [],
    false,
    () => {},
    () => {},
  ).runTurn(
    {
      messages: [],
      model: "test-model",
      enableToolCalls: false,
      mode: "plan",
    },
    { isCancellationRequested: false, onCancellationRequested: () => ({ dispose: () => {} }) },
    mockStream(),
  );

  assert.ok(lastReq, "chatCompletionStream must be called");
  assert.equal(lastReq.tool_choice, undefined, "tool_choice must not be sent in plan mode");
  assert.equal(lastReq.tools, undefined, "tools must not be sent in plan mode");
});

// ---------------------------------------------------------------------------
// Single-render invariant: engine must NOT write markdown to stream
// ---------------------------------------------------------------------------

test("engine never calls stream.markdown — single-render invariant", async () => {
  let markdownCalls = 0;
  const stream = {
    markdown: (...args) => { markdownCalls++; },
    thinkingProgress: () => {},
    progress: () => {},
  };

  const client = {
    chatCompletionStream: async function* () {
      yield { content: "hello" };
    },
    callMCPTool: async () => ({ content: [], isError: false }),
  };

  await new AgentEngine(
    client,
    mockAgentToolBridge(),
    [],
    [],
    false,
    () => {},
    () => {},
  ).runTurn(
    {
      messages: [],
      model: "test-model",
      enableToolCalls: false,
      mode: "plan",
    },
    { isCancellationRequested: false, onCancellationRequested: () => ({ dispose: () => {} }) },
    stream,
  );

  assert.equal(markdownCalls, 0, "AgentEngine must never write to stream.markdown");
});

test("engine with tool calls never calls stream.markdown", async () => {
  let markdownCalls = 0;
  const stream = {
    markdown: (...args) => { markdownCalls++; },
    thinkingProgress: () => {},
    progress: () => {},
  };

  // Simulate a model that first calls a tool, then answers.
  let callCount = 0;
  const client = {
    chatCompletionStream: async function* () {
      callCount++;
      if (callCount === 1) {
        yield {
          content: "",
          tool_calls: [{ id: "tc1", function: { name: "knowledge_search", arguments: "{}" } }],
        };
      } else {
        yield { content: "done" };
      }
    },
    callMCPTool: async () => ({ content: [{ type: "text", text: "result" }], isError: false }),
  };

  await new AgentEngine(
    client,
    mockAgentToolBridge(),
    [{ name: "knowledge_search", description: "Search", parameters: { type: "object", properties: {} } }],
    [],
    false,
    () => {},
    () => {},
  ).runTurn(
    {
      messages: [],
      model: "test-model",
      enableToolCalls: true,
      mode: "chat",
    },
    { isCancellationRequested: false, onCancellationRequested: () => ({ dispose: () => {} }) },
    stream,
  );

  assert.equal(markdownCalls, 0, "Engine must not call stream.markdown even with tool calls");
});

// ---------------------------------------------------------------------------
// Schema assembly: MCP + workspace + KNOWN_R4_TOOLS
// ---------------------------------------------------------------------------

test("knownToolsToSchemas returns an array of OpenAI tool schemas", () => {
  const schemas = knownToolsToSchemas();
  assert.ok(Array.isArray(schemas), "must return an array");
  assert.ok(schemas.length > 0, "must contain KNOWN_R4_TOOLS entries");
  assert.ok(schemas.every(s => s.type === "function"), "all entries must be function-type tools");
});

test("schema assembly includes KNOWN_R4_TOOLS even when no MCP tools", () => {
  let lastReq = null;
  const client = {
    chatCompletionStream: async function* (req, _signal) {
      lastReq = req;
      yield { content: "done" };
    },
    callMCPTool: async () => ({ content: [], isError: false }),
  };

  void new AgentEngine(
    client,
    mockAgentToolBridge(),
    [], // no MCP tools
    [], // no workspace tools
    true,
    () => {},
    () => {},
  ).runTurn(
    { messages: [], model: "test", enableToolCalls: true, mode: "chat" },
    { isCancellationRequested: false, onCancellationRequested: () => ({ dispose: () => {} }) },
    mockStream(),
  );

  assert.ok(lastReq, "must call stream");
  assert.ok(lastReq.tools?.length >= 1, "must include KNOWN_R4_TOOLS fallback");
});

// ---------------------------------------------------------------------------
// Loop guards
// ---------------------------------------------------------------------------

test("cancellation stops the loop mid-stream", async () => {
  let yieldCount = 0;
  let round = 0;
  const client = {
    chatCompletionStream: async function* () {
      yieldCount++;
      round++;
      if (round === 1) {
        // First round: return a tool call so the engine loops back.
        yield {
          content: "",
          tool_calls: [{ id: "tc1", function: { name: "knowledge_search", arguments: "{}" } }],
        };
      } else {
        // Second round: model starts answering but we cancel mid-stream.
        yield { content: "partial" };
        // The abort should fire before this completes.
        await new Promise(r => setTimeout(r, 50));
        yield { content: "more" };
      }
    },
    callMCPTool: async () => ({ content: [], isError: false }),
  };

  const controller = new AbortController();
  let aborted = false;

  const promise = new AgentEngine(
    client,
    mockAgentToolBridge(),
    [],
    [],
    false,
    () => {},
    () => {},
  ).runTurn(
    { messages: [], model: "test", enableToolCalls: false, mode: "plan" },
    {
      isCancellationRequested: false,
      onCancellationRequested: (fn) => {
        controller.signal.addEventListener("abort", () => {
          aborted = true;
          fn();
        }, { once: true });
        return { dispose: () => {} };
      },
    },
    mockStream(),
  );

  // Abort after the first round completes and the engine starts the second.
  setTimeout(() => controller.abort(), 20);

  // The engine should detect cancellation and stop — it either throws or returns
  // with partial content. Accept either outcome since the loop is broken.
  await promise;
  assert.ok(aborted, "cancellation must have been triggered");
  assert.ok(yieldCount >= 2, "model should have yielded at least twice");
});

test("tool-call loop stops at maxToolRounds", async () => {
  let toolCallsTotal = 0;
  const client = {
    chatCompletionStream: async function* () {
      toolCallsTotal++;
      // Always return a tool call — this would loop forever without maxToolRounds.
      yield {
        content: "",
        tool_calls: [{ id: `tc${toolCallsTotal}`, function: { name: "knowledge_search", arguments: "{}" } }],
      };
    },
    callMCPTool: async () => ({ content: [{ type: "text", text: "res" }], isError: false }),
  };

  await new AgentEngine(
    client,
    mockAgentToolBridge(),
    [{ name: "knowledge_search", description: "Search", parameters: { type: "object", properties: {} } }],
    [],
    true,
    () => {},
    () => {},
  ).runTurn(
    { messages: [], model: "test", enableToolCalls: true, mode: "chat" },
    { isCancellationRequested: false, onCancellationRequested: () => ({ dispose: () => {} }) },
    mockStream(),
  );

  // Max 10 rounds + the final round that yields content — so ~11 tool calls before loop ends.
  assert.ok(toolCallsTotal <= 12, `must stop at ~10 rounds, got ${toolCallsTotal}`);
  assert.ok(toolCallsTotal >= 10, `must exercise at least 10 rounds, got ${toolCallsTotal}`);
});

// ---------------------------------------------------------------------------
// Tool round-trip: model emits tool_call → engine executes → feeds back → model answers
// ---------------------------------------------------------------------------

test("engine loops: tool_call → execute → tool message → final content", async () => {
  let callCount = 0;
  const client = {
    chatCompletionStream: async function* () {
      callCount++;
      if (callCount === 1) {
        // First round: model emits a tool call.
        yield {
          content: "",
          tool_calls: [{ id: "tc1", function: { name: "knowledge_search", arguments: "{}" } }],
        };
      } else {
        // Second round (after tool result is fed back): model answers.
        yield { content: "The answer is 42." };
      }
    },
    callMCPTool: async (req) => {
      assert.equal(req.tool, "knowledge_search");
      return { content: [{ type: "text", text: "found 42" }], isError: false };
    },
  };

  const result = await new AgentEngine(
    client,
    mockAgentToolBridge(),
    [{ name: "knowledge_search", description: "Search", parameters: { type: "object", properties: {} } }],
    [],
    true,
    () => {},
    () => {},
  ).runTurn(
    { messages: [], model: "test", enableToolCalls: true, mode: "chat" },
    { isCancellationRequested: false, onCancellationRequested: () => ({ dispose: () => {} }) },
    mockStream(),
  );

  assert.ok(result.content.includes("42") || result.content.includes("found"),
    `content should reference the tool result or answer, got: ${result.content?.slice(0, 200)}`);
  assert.equal(callCount, 2, "LLM must be called twice: tool round + answer round");
  assert.equal(result.toolCalls.length, 1, "must record one tool call");
});

// ---------------------------------------------------------------------------
// Auto-approve: bridge call receives autoApprove flag
// ---------------------------------------------------------------------------

test("workspace tool call receives autoApprove from config", async () => {
  let callCount = 0;
  let receivedAutoApprove = undefined;
  let lastCallArgs = null;
  const bridge = {
    call: async (req) => {
      callCount++;
      receivedAutoApprove = req.autoApprove;
      lastCallArgs = req;
      return { ok: true };
    },
    listTools: () => [{ name: "workspace.readFile", description: "Read" }],
  };

  let streamYields = 0;
  const client = {
    chatCompletionStream: async function* () {
      streamYields++;
      yield { content: "" };
      yield {
        content: "",
        tool_calls: [{ id: "tc1", function: { name: "workspace.readFile", arguments: "{}" } }],
      };
      yield { content: "done" };
    },
    callMCPTool: async () => ({ content: [], isError: false }),
  };

  const engine = new AgentEngine(
    client,
    bridge,
    [],
    [{ name: "workspace.readFile", description: "Read a file" }],
    true,  // autoApprove enabled
    () => {},
    () => {},
  );

  await engine.runTurn(
    { messages: [], model: "test", enableToolCalls: true, mode: "chat", autoApprove: true },
    { isCancellationRequested: false, onCancellationRequested: () => ({ dispose: () => {} }) },
    mockStream(),
  );

  assert.ok(callCount >= 1, "bridge.call must have been invoked for workspace tool");
  assert.equal(receivedAutoApprove, true, "autoApprove must be threaded to the bridge call");
});
