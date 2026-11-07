import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { setImmediate as tick } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundleDir = mkdtempSync(path.join(os.tmpdir(), "r4-agent-view-"));
const bundlePath = path.join(bundleDir, "agent-view.bundle.cjs");
const vscodeStubPath = path.join(rootDir, "test", "vscode-stub.cjs");
const esbuild = path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild");

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

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function abortError() {
  const error = new Error("Request aborted");
  error.name = "AbortError";
  return error;
}

function createClient(overrides = {}) {
  const requests = [];
  const methodHandlers = new Map();
  const removals = { notification: 0, method: 0 };
  let notificationListener;
  const client = {
    requests,
    methodHandlers,
    removals,
    async request(method, params = {}, options = {}) {
      requests.push({ method, params, options });
      if (overrides.request) return overrides.request(method, params, options, requests);
      if (method === "sessions/list") return { sessions: [] };
      if (method === "editor/tools/register") return {};
      throw new Error(`Unexpected request: ${method}`);
    },
    onNotification(listener) {
      notificationListener = listener;
      return () => {
        removals.notification += 1;
        notificationListener = undefined;
      };
    },
    registerMethod(method, handler) {
      methodHandlers.set(method, handler);
      return () => {
        removals.method += 1;
        methodHandlers.delete(method);
      };
    },
    notify(method, params) {
      notificationListener?.({ jsonrpc: "2.0", method, params });
    },
  };
  return client;
}

function createHarness({ clients, toolBridge, gatewayClient, sourceConfigs } = {}) {
  const availableClients = clients ?? [createClient()];
  const supervisors = [];
  let clientIndex = 0;
  const provider = new R4AgentViewProvider(
    () => {
      const client = availableClients[clientIndex++];
      const supervisor = {
        disposed: 0,
        async start() {
          return client;
        },
        dispose() {
          this.disposed += 1;
        },
      };
      supervisors.push(supervisor);
      return supervisor;
    },
    () => gatewayClient,
    async () => toolBridge,
    undefined,
    () => sourceConfigs ?? [],
  );
  const posted = [];
  let receiveMessage;
  let disposeView;
  const view = {
    webview: {
      cspSource: "test-csp",
      html: "",
      options: {},
      postMessage(message) {
        posted.push(message);
        return Promise.resolve(true);
      },
      onDidReceiveMessage(listener) {
        receiveMessage = listener;
        return { dispose() {} };
      },
    },
    onDidDispose(listener) {
      disposeView = listener;
      return { dispose() {} };
    },
  };
  provider.resolveWebviewView(view);
  const messageToken = view.webview.html.match(/const messageToken = "([^"]+)";/)?.[1];
  assert.ok(messageToken, "Webview must contain a message token");
  return {
    posted,
    provider,
    supervisors,
    send(message) {
      receiveMessage({ ...message, messageToken });
    },
    rawSend(message) {
      receiveMessage(message);
    },
    disposeView() {
      disposeView();
    },
  };
}

test("rejects Webview commands without the per-view message token", async () => {
  const harness = createHarness();

  harness.rawSend({ type: "ready" });
  await waitFor(() => messagesOfType(harness.posted, "error").length === 1, "token rejection");

  assert.equal(harness.supervisors.length, 0);
  assert.match(messagesOfType(harness.posted, "error")[0].message, /unauthenticated/);
});

test("inventory offers only ready endpoints as endpoint options", async () => {
  const client = createClient();
  const gatewayClient = {
    async listEndpoints() {
      return [
        { job_id: "j1", model: "source-model", served_model_name: "ready-model", status: "ready", backend: "slurm" },
        { job_id: "j2", model: "idle-model", status: "stopped", backend: "slurm" },
      ];
    },
  };
  const harness = createHarness({ clients: [client], gatewayClient });

  harness.send({ type: "ready" });
  await waitFor(() => messagesOfType(harness.posted, "inventory").length === 1, "inventory");

  const inventory = messagesOfType(harness.posted, "inventory")[0];
  assert.equal(inventory.connected, true);
  assert.deepEqual(inventory.endpoints, [
    { id: "ready-model", label: "ready-model", status: "ready" },
  ]);
});

test("single source inventory uses the @source routing suffix, not the gateway fallback", async () => {
  // Regression: with exactly one source, refreshInventory used the extension's
  // gateway client and emitted options WITHOUT the @source suffix. The r4
  // agent then routed a lone public source to the platform gateway (auth
  // failed / non-JSON). It must instead aggregate via collectSourceEndpoints
  // so every option carries model@source for explicit per-source routing.
  let gatewayCalled = false;
  const gatewayClient = {
    async listEndpoints() {
      gatewayCalled = true;
      return [];
    },
  };
  // Unreachable URL so collectSourceEndpoints fails fast (ECONNREFUSED) —
  // we only assert the gateway fallback is NOT taken.
  const sourceConfigs = [
    { name: "ollama", url: "http://127.0.0.1:1", token: "", kind: "public" },
  ];
  const harness = createHarness({ sourceConfigs, gatewayClient });

  harness.send({ type: "ready" });
  await waitFor(() => messagesOfType(harness.posted, "inventory").length === 1, "inventory");

  assert.equal(gatewayCalled, false, "single source must use collectSourceEndpoints, not the gateway client");
});

test("sessions event preserves project_id for chat-list project scoping", async () => {
  // Chat↔Studio project scoping: the companion store tags each chat with
  // the project active when it was saved. normalizeSessions must forward
  // the tag or the webview can't scope the chat list by project.
  const client = createClient({
    request: async (method) => {
      if (method === "sessions/list") {
        return {
          sessions: [
            { id: "s1", name: "Alpha", project_id: "proj-9", message_count: 3 },
            { id: "s2", name: "Beta" },
          ],
        };
      }
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "ready" });
  await waitFor(() => messagesOfType(harness.posted, "sessions").length >= 1, "sessions");
  const [alpha, beta] = messagesOfType(harness.posted, "sessions")[0].sessions;
  assert.equal(alpha.project_id, "proj-9");
  assert.equal(beta.project_id, undefined);
});

async function waitFor(predicate, description = "condition") {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await tick();
  }
  assert.fail(`Timed out waiting for ${description}`);
}

function messagesOfType(posted, type) {
  return posted.filter((message) => message.type === type);
}

test("sessions/load restores only valid transcript messages", async () => {
  const client = createClient({
    request: async (method) => {
      if (method === "sessions/load") {
        return {
          session: {
            id: "session-1",
            name: "Research",
            messages: [
              { role: "user", content: "Question" },
              { role: "assistant", content: "Answer" },
              { role: "assistant", content: 42 },
              null,
            ],
          },
        };
      }
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "selectSession", sessionId: " session-1 " });
  await waitFor(() => messagesOfType(harness.posted, "sessionSelected").length === 1, "session load");

  assert.deepEqual(messagesOfType(harness.posted, "sessionSelected")[0], {
    type: "sessionSelected",
    session: {
      id: "session-1",
      name: "Research",
      messages: [
        { role: "user", content: "Question" },
        { role: "assistant", content: "Answer" },
        { role: "assistant", content: 42 },
        null,
      ],
    },
    fresh: false,
    messages: [
      { role: "user", content: "Question" },
      { role: "assistant", content: "Answer" },
    ],
  });
});

test("notifications reject stale requests and cross-session events while forwarding structured lifecycle", async () => {
  const chat = deferred();
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/load") return { session: { id: params.session_id, name: "Active" } };
      if (method === "chat/send") return chat.promise;
      if (method === "sessions/list") return { sessions: [] };
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "selectSession", sessionId: "session-1" });
  await waitFor(() => messagesOfType(harness.posted, "sessionSelected").length === 1, "active session");
  harness.send({ type: "send", text: "Investigate" });
  await waitFor(() => client.requests.some((request) => request.method === "chat/send"), "chat request");
  const chatRequest = client.requests.find((request) => request.method === "chat/send");
  const requestId = chatRequest.params.request_id;

  client.notify("session/text", { session_id: "session-1", request_id: "stale", text: "stale" });
  client.notify("session/text", { session_id: "session-2", request_id: requestId, text: "wrong session" });
  client.notify("session/text", { session_id: "session-1", request_id: requestId, text: "live" });
  client.notify("session/toolState", {
    session_id: "session-1", request_id: requestId, tool: "workspace.readFile", status: "started",
  });
  client.notify("session/event", {
    session_id: "session-1", request_id: requestId,
    event: { event_type: "planning_started", data: { step: 1 } },
  });
  client.notify("session/toolState", {
    session_id: "session-1", request_id: requestId, tool: "workspace.readFile", status: "completed",
  });

  assert.deepEqual(messagesOfType(harness.posted, "assistantDelta"), [
    { type: "assistantDelta", sessionId: "session-1", text: "live" },
  ]);
  assert.deepEqual(messagesOfType(harness.posted, "toolState").map(({ status }) => status), ["started", "completed"]);
  assert.deepEqual(messagesOfType(harness.posted, "agentEvent")[0].event, {
    event_type: "planning_started",
    data: { step: 1 },
  });

  chat.resolve({ status: "complete", session_id: "session-1", response: "Done" });
  await waitFor(() => messagesOfType(harness.posted, "assistantComplete").length === 1, "chat completion");
});

test("tool_selection events route to the roll-up channel, not raw agentEvent rows", async () => {
  const chat = deferred();
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/load") return { session: { id: params.session_id, name: "Active" } };
      if (method === "chat/send") return chat.promise;
      if (method === "sessions/list") return { sessions: [] };
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "selectSession", sessionId: "session-1" });
  await waitFor(() => messagesOfType(harness.posted, "sessionSelected").length === 1, "active session");
  harness.send({ type: "send", text: "Investigate" });
  await waitFor(() => client.requests.some((request) => request.method === "chat/send"), "chat request");
  const chatRequest = client.requests.find((request) => request.method === "chat/send");
  const requestId = chatRequest.params.request_id;

  client.notify("session/event", {
    session_id: "session-1",
    request_id: requestId,
    event: {
      event_type: "tool_selection",
      message: "all 65 tools offered (no selection)",
      data: { available_tools: 65, candidate_tools: 65, strategy: "passthrough", selected_skills: [] },
    },
  });

  const selections = messagesOfType(harness.posted, "toolSelection");
  assert.equal(selections.length, 1);
  assert.equal(selections[0].sessionId, "session-1");
  assert.deepEqual(selections[0].payload, {
    available_tools: 65,
    candidate_tools: 65,
    strategy: "passthrough",
    selected_skills: [],
  });
  // The generic fallback must NOT also fire — that path leaked "event: …"
  // rows into the transcript (one per model call).
  assert.equal(messagesOfType(harness.posted, "agentEvent").length, 0);

  chat.resolve({ status: "complete", session_id: "session-1", response: "Done" });
  await waitFor(() => messagesOfType(harness.posted, "assistantComplete").length === 1, "chat completion");
});

test("canonical thinking notifications forward typed messages to the webview", async () => {
  const chat = deferred();
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/load") return { session: { id: params.session_id, name: "Active" } };
      if (method === "chat/send") return chat.promise;
      if (method === "sessions/list") return { sessions: [] };
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "selectSession", sessionId: "session-1" });
  await waitFor(() => messagesOfType(harness.posted, "sessionSelected").length === 1, "active session");
  harness.send({ type: "send", text: "Investigate" });
  await waitFor(() => client.requests.some((request) => request.method === "chat/send"), "chat request");
  const chatRequest = client.requests.find((request) => request.method === "chat/send");
  const requestId = chatRequest.params.request_id;

  client.notify("session/thinking_delta", {
    session_id: "session-1",
    request_id: requestId,
    payload: { session_id: "session-1", seq: 1, text: "partial reason", partial: true },
  });
  client.notify("session/thinking_done", {
    session_id: "session-1",
    request_id: requestId,
    payload: { session_id: "session-1", summary_hint: "" },
  });

  assert.equal(messagesOfType(harness.posted, "thinkingDelta").length, 1);
  assert.equal(messagesOfType(harness.posted, "thinkingDelta")[0].payload.seq, 1);
  assert.equal(messagesOfType(harness.posted, "thinkingDone").length, 1);

  chat.resolve({ status: "complete", session_id: "session-1", response: "ok" });
  await waitFor(() => messagesOfType(harness.posted, "assistantComplete").length === 1, "turn completion");
});

test("cancel aborts the active request and permits a subsequent request", async () => {
  const chats = [];
  const client = createClient({
    request: async (method, params, options) => {
      if (method === "sessions/create") {
        return { session: { id: "session-new", name: "New", model: null, messages: [] } };
      }
      if (method === "chat/send") {
        const chat = deferred();
        chats.push({ chat, params, options });
        options.signal.addEventListener("abort", () => chat.reject(abortError()), { once: true });
        return chat.promise;
      }
      if (method === "sessions/list") return { sessions: [] };
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "send", text: "First" });
  await waitFor(() => chats.length === 1, "first chat");
  harness.send({ type: "cancel", sessionId: "session-new" });
  await waitFor(() => messagesOfType(harness.posted, "cancelled").length === 1, "cancellation");
  assert.equal(chats[0].options.signal.aborted, true);

  harness.send({ type: "send", text: "Second" });
  await waitFor(() => chats.length === 2, "second chat");
  chats[1].chat.resolve({ status: "complete", session_id: "session-new", response: "Recovered" });
  await waitFor(() => messagesOfType(harness.posted, "assistantComplete").length === 1, "second completion");
  assert.equal(chats[0].params.request_id === chats[1].params.request_id, false);
});

test("successful chat leaves the session idle so a model switch + retry works", async () => {
  // Regression: refreshSessions was called while the just-finished request
  // was still in activeRequests, so the sessions snapshot re-marked the
  // session "running" and the webview kept showing Stop instead of Send —
  // blocking a retry in the same session after an error-as-text response.
  const chat = deferred();
  const client = createClient({
    request: async (method) => {
      if (method === "sessions/create") {
        return { session: { id: "s1", name: "New", model: null, messages: [] } };
      }
      if (method === "chat/send") return chat.promise;
      if (method === "sessions/list") return { sessions: [{ id: "s1", name: "New" }] };
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "send", text: "Hello" });
  await waitFor(() => client.requests.some((r) => r.method === "chat/send"), "chat request");
  chat.resolve({ status: "complete", session_id: "s1", response: "Hi" });
  await waitFor(() => messagesOfType(harness.posted, "assistantComplete").length === 1, "completion");

  const sessionsMsgs = messagesOfType(harness.posted, "sessions");
  const lastSessions = sessionsMsgs[sessionsMsgs.length - 1];
  assert.equal(lastSessions.running.includes("s1"), false, "finished session must not be reported running");

  const stateMsgs = messagesOfType(harness.posted, "sessionState");
  assert.equal(stateMsgs[stateMsgs.length - 1].state, "idle", "final session state must be idle");
});

test("agent error returned as text still leaves the session idle for retry", async () => {
  // The r4 agent returns upstream failures (auth, non-JSON) as the response
  // string rather than throwing, so the extension treats them as a success.
  // The session must still end idle so the user can switch models and retry.
  const chat = deferred();
  const client = createClient({
    request: async (method) => {
      if (method === "sessions/create") {
        return { session: { id: "s1", name: "New", model: null, messages: [] } };
      }
      if (method === "chat/send") return chat.promise;
      if (method === "sessions/list") return { sessions: [{ id: "s1", name: "New" }] };
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "send", text: "Hello" });
  await waitFor(() => client.requests.some((r) => r.method === "chat/send"), "chat request");
  chat.resolve({
    status: "complete",
    session_id: "s1",
    response: "Failed to get a response: Authentication failed",
  });
  await waitFor(() => messagesOfType(harness.posted, "assistantComplete").length === 1, "completion");

  const sessionsMsgs = messagesOfType(harness.posted, "sessions");
  assert.equal(
    sessionsMsgs[sessionsMsgs.length - 1].running.includes("s1"),
    false,
    "session must not be stuck running after an error response",
  );
  const stateMsgs = messagesOfType(harness.posted, "sessionState");
  assert.equal(stateMsgs[stateMsgs.length - 1].state, "idle");
});

test("concurrent send in the same session is rejected without forwarding another chat request", async () => {
  const chat = deferred();
  const client = createClient({
    request: async (method) => {
      if (method === "sessions/create") {
        return { session: { id: "session-new", name: "New", model: null, messages: [] } };
      }
      if (method === "chat/send") return chat.promise;
      if (method === "sessions/list") return { sessions: [] };
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "send", text: "First" });
  await waitFor(() => client.requests.some((request) => request.method === "chat/send"), "first chat");
  harness.send({ type: "send", sessionId: "session-new", text: "Second" });
  await waitFor(() => messagesOfType(harness.posted, "error").length === 1, "concurrent rejection");

  assert.equal(client.requests.filter((request) => request.method === "chat/send").length, 1);
  assert.equal(messagesOfType(harness.posted, "error")[0].message, "This chat is already responding.");
  assert.equal(harness.supervisors[0].disposed, 0);
  harness.send({ type: "cancel", sessionId: "session-new" });
  chat.reject(abortError());
});

test("application-level agent errors do not tear down the transport", async () => {
  // A JSON-RPC error reply (AcpRequestError) is an application failure — the
  // pipe is alive. Only transport failures should dispose the supervisor.
  const chat = deferred();
  const client = createClient({
    request: async (method) => {
      if (method === "sessions/create") {
        return { session: { id: "session-new", name: "New", model: null, messages: [] } };
      }
      if (method === "chat/send") return chat.promise;
      if (method === "sessions/list") return { sessions: [{ id: "session-new", name: "New" }] };
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "send", text: "Hello" });
  await waitFor(() => client.requests.some((r) => r.method === "chat/send"), "chat request");
  const appError = new Error("Session already has an active request");
  appError.name = "AcpRequestError";
  chat.reject(appError);
  await waitFor(() => messagesOfType(harness.posted, "error").length === 1, "error posted");

  assert.equal(harness.supervisors[0].disposed, 0, "supervisor must NOT be disposed for app errors");
  assert.equal(
    messagesOfType(harness.posted, "status").some(({ status }) => status === "disconnected"),
    false,
    "status must stay connected for app errors",
  );
  // Session marked error, but the webview can keep working.
  const stateMsgs = messagesOfType(harness.posted, "sessionState");
  assert.equal(stateMsgs[stateMsgs.length - 1].state, "error");
});

test("editor tool calls are structured and toolCancel routes to AgentToolBridge.cancel", async () => {
  const calls = [];
  const cancellations = [];
  const toolBridge = {
    listTools: () => [{ name: "workspace.readFile", description: "Read workspace file" }],
    setStateListener() {},
    async call(request) {
      calls.push(request);
      return { content: "result" };
    },
    cancel(requestId) {
      cancellations.push(requestId);
    },
  };
  const client = createClient();
  const harness = createHarness({ clients: [client], toolBridge });

  harness.send({ type: "ready" });
  await waitFor(() => client.methodHandlers.has("editor/toolCall"), "tool registration");
  const result = await client.methodHandlers.get("editor/toolCall")({
    session_id: " session-1 ",
    request_id: " request-1 ",
    tool_call_id: " tool-1 ",
    name: " workspace.readFile ",
    arguments: { path: "README.md" },
  });
  client.notify("editor/toolCancel", { request_id: "request-1" });

  assert.deepEqual(result, { content: "result" });
  assert.deepEqual(calls, [{
    sessionId: "session-1",
    requestId: "request-1",
    toolCallId: "tool-1",
    name: "workspace.readFile",
    arguments: { path: "README.md" },
  }]);
  assert.deepEqual(cancellations, ["request-1"]);
  assert.deepEqual(client.requests.find((request) => request.method === "editor/tools/register").params, {
    tools: [{ name: "workspace.readFile", description: "Read workspace file" }],
  });
});

test("companion tool approval handler returns editor decision", async () => {
  const toolBridge = {
    listTools: () => [{ name: "workspace.readFile", description: "Read workspace file" }],
    setStateListener() {},
    async call() {
      return { content: "result" };
    },
    cancel() {},
  };
  const client = createClient();
  const harness = createHarness({ clients: [client], toolBridge });

  harness.send({ type: "ready" });
  await waitFor(() => client.methodHandlers.has("session/toolApproval"), "approval registration");
  const result = await client.methodHandlers.get("session/toolApproval")({
    session_id: "session-1",
    request_id: "request-1",
    approval_id: "approval-1",
    tool: "shell_exec",
    description: "Run command: ls",
    arguments: { command: "ls" },
  });

  assert.deepEqual(result, { decision: "once" });
});

test("crash resets transport and restart creates a clean supervisor", async () => {
  const crashedClient = createClient({
    request: async (method) => {
      if (method === "sessions/create") {
        return { session: { id: "session-new", name: "New", model: null, messages: [] } };
      }
      if (method === "chat/send") throw new Error("ACP crashed\ntransport closed");
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const restartedClient = createClient();
  const harness = createHarness({ clients: [crashedClient, restartedClient] });

  harness.send({ type: "send", text: "Crash" });
  await waitFor(() => messagesOfType(harness.posted, "error").length === 1, "crash handling");
  assert.equal(messagesOfType(harness.posted, "error")[0].message, "ACP crashed transport closed");
  assert.equal(harness.supervisors[0].disposed, 1);
  assert.equal(crashedClient.removals.notification, 1);

  harness.send({ type: "restart" });
  await waitFor(
    () => restartedClient.requests.some((request) => request.method === "sessions/list"),
    "restarted client initialization",
  );
  assert.equal(harness.supervisors.length, 2);
  assert.equal(restartedClient.requests.some((request) => request.method === "sessions/list"), true);
  assert.deepEqual(messagesOfType(harness.posted, "status").slice(-3).map(({ status }) => status), [
    "disconnected", "connecting", "ready",
  ]);
});
// ── B2b-full: typed plan/diff/citation routing from session/event ──────────

async function runTypedEventCase(eventPayload, expectedType) {
  const chats = [];
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/create") {
        return { session: { id: "session-x", name: "X", model: null, messages: [] } };
      }
      if (method === "chat/send") {
        const chat = deferred();
        chats.push({ chat, params });
        return chat.promise;
      }
      if (method === "sessions/list") return { sessions: [] };
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });
  harness.send({ type: "send", text: "go" });
  await waitFor(() => chats.length === 1, "chat started");
  const requestId = chats[0].params.request_id;
  client.notify("session/event", { session_id: "session-x", request_id: requestId, event: eventPayload });
  await waitFor(() => messagesOfType(harness.posted, expectedType).length === 1, expectedType);
  const out = messagesOfType(harness.posted, expectedType)[0];
  chats[0].chat.resolve({ status: "complete", session_id: "session-x", response: "" });
  return out;
}

test("planning events post planEvent", async () => {
  const out = await runTypedEventCase(
    {
      event_type: "planning_complete",
      message: "Plan ready",
      data: {
        steps: [
          { id: "s1", description: "gather", status: "done" },
          { title: "analyze", status: "pending" },
        ],
      },
    },
    "planEvent",
  );
  assert.equal(out.plan.phase, "complete");
  assert.equal(out.plan.summary, "Plan ready");
  assert.deepEqual(out.plan.steps.map((s) => [s.id, s.status]), [
    ["s1", "done"],
    ["step-2", "pending"],
  ]);
});

test("step events post planEvent with single step", async () => {
  const out = await runTypedEventCase(
    { event_type: "step_start", message: "running analyze", data: { step_id: "s2" } },
    "planEvent",
  );
  assert.deepEqual(out.plan.steps, [
    { id: "s2", description: "running analyze", status: "running" },
  ]);
});

test("diff-bearing data posts diffEvent", async () => {
  const out = await runTypedEventCase(
    {
      event_type: "tool_result",
      message: "edited",
      data: {
        diff: {
          path: "src/foo.c",
          action: "modify",
          hunks: [{ header: "@@ -1,2 +1,2 @@", lines: [{ kind: "add", text: "+x" }] }],
        },
      },
    },
    "diffEvent",
  );
  assert.equal(out.diff.path, "src/foo.c");
  assert.equal(out.diff.action, "modify");
  assert.equal(out.diff.hunks[0].lines[0].kind, "add");
});

test("citation arrays post citationEvent", async () => {
  const out = await runTypedEventCase(
    {
      event_type: "message",
      message: "knowledge",
      data: {
        citations: [
          { source: "doc-1", title: "Paper A", score: 0.9 },
          { artifact_id: "a2", text: "relevant excerpt text".repeat(30) },
        ],
      },
    },
    "citationEvent",
  );
  assert.equal(out.citations.length, 2);
  assert.equal(out.citations[0].title, "Paper A");
  assert.equal(out.citations[1].source, "a2");
  assert.ok(out.citations[1].excerpt.length <= 200);
});

test("unrecognized payloads still fall back to agentEvent", async () => {
  const chats = [];
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/create") {
        return { session: { id: "session-x", name: "X", model: null, messages: [] } };
      }
      if (method === "chat/send") {
        const chat = deferred();
        chats.push({ chat, params });
        return chat.promise;
      }
      if (method === "sessions/list") return { sessions: [] };
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });
  harness.send({ type: "send", text: "go" });
  await waitFor(() => chats.length === 1, "chat started");
  const requestId = chats[0].params.request_id;
  client.notify("session/event", {
    session_id: "session-x", request_id: requestId,
    event: { event_type: "workflow_complete", message: "done", data: { ok: true } },
  });
  await waitFor(() => messagesOfType(harness.posted, "agentEvent").length === 1, "agentEvent fallback");
  chats[0].chat.resolve({ status: "complete", session_id: "session-x", response: "" });
});

// ── Studio: knowledge/research-loop refresh + run-form parity ────────────

test("runWorkflow forwards parameters, inputFiles, and wait to the agent", async () => {
  let trigger;
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/list") return { sessions: [] };
      if (method === "projects/list") return { projects: [], current: null };
      if (method === "workflows/list") return { workflows: [], count: 0 };
      if (method === "workflows/trigger") {
        trigger = params;
        return { execution_id: "exec-77", status: "queued" };
      }
      // Terminal-status exec so the studio poll timer doesn't re-arm forever.
      if (method === "workflows/status") return { execution_id: "exec-77", status: "completed" };
      if (method === "workflows/detail") return { execution: { results: { steps: {} } } };
      if (method === "knowledge/graph" || method === "research/loops") throw new Error("unconfigured");
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({
    type: "runWorkflow",
    workflowName: "ct-analysis",
    parameters: { threshold: 0.8 },
    inputFiles: ["s3://bucket/scan.tif"],
    wait: true,
  });
  await waitFor(() => trigger !== undefined, "workflows/trigger");

  assert.equal(trigger.workflow_name, "ct-analysis");
  assert.deepEqual(trigger.parameters, { threshold: 0.8 });
  assert.deepEqual(trigger.input_files, ["s3://bucket/scan.tif"]);
  assert.equal(trigger.wait, true);
});

test("refreshKnowledge posts a knowledgeState snapshot for the current project", async () => {
  let knowledgeGraphCalls = 0;
  let researchLoopCalls = 0;
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/list") return { sessions: [] };
      if (method === "projects/list") return { projects: [{ project_id: "proj-9" }], current: "proj-9" };
      if (method === "workflows/list") return { workflows: [], count: 0 };
      if (method === "knowledge/graph") {
        knowledgeGraphCalls += 1;
        return { nodes: [{ id: "H7", type: "hypothesis", name: "H7" }], edges: [] };
      }
      if (method === "research/loops") {
        researchLoopCalls += 1;
        return { loops: [{ loop_id: "loop-1", status: "running" }], detail: null };
      }
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  // Establish the studio state so currentProjectId is known (refreshKnowledge
  // only fires when the studio state carries a current project).
  harness.send({ type: "refreshStudio" });
  await waitFor(() => messagesOfType(harness.posted, "studioState").length === 1, "studioState");
  await waitFor(
    () => knowledgeGraphCalls === 1 && researchLoopCalls === 1,
    "auto knowledge refresh after studio",
  );
  await waitFor(() => messagesOfType(harness.posted, "knowledgeState").length === 1, "knowledgeState");
  const snapshot = messagesOfType(harness.posted, "knowledgeState")[0];
  assert.equal(snapshot.knowledgeGraph.nodes.length, 1);
  assert.equal(snapshot.researchLoops.loops[0].loop_id, "loop-1");

  harness.send({ type: "refreshKnowledge" });
  await waitFor(() => knowledgeGraphCalls === 2, "second knowledge refresh");
  assert.equal(messagesOfType(harness.posted, "knowledgeState").length, 2);
});

test("ready also posts a studioState snapshot so chat surfaces know projects", async () => {
  // Chat↔Studio bridge: the chat tab shows a project picker and receives
  // live knowledge refreshes from agent tool calls. Both need the studio
  // snapshot (projects + current project id) from view-open, not only
  // after the first completed turn or a visit to the Studio tab.
  let knowledgeGraphCalls = 0;
  const client = createClient({
    request: async (method) => {
      if (method === "sessions/list") return { sessions: [] };
      if (method === "projects/list") {
        return { projects: [{ project_id: "proj-1" }], current: "proj-1" };
      }
      if (method === "workflows/list") return { workflows: [], count: 0 };
      if (method === "knowledge/graph") {
        knowledgeGraphCalls += 1;
        return { nodes: [], edges: [] };
      }
      if (method === "research/loops") return { loops: [] };
      if (method === "knowledge/verification") return null;
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "ready" });
  await waitFor(
    () => messagesOfType(harness.posted, "status").some((message) => message.status === "ready"),
    "ready status",
  );
  await waitFor(
    () => messagesOfType(harness.posted, "studioState").length === 1,
    "studioState at init",
  );
  await waitFor(() => knowledgeGraphCalls === 1, "knowledge refresh at init");
  const snapshot = messagesOfType(harness.posted, "studioState")[0];
  assert.equal(snapshot.currentProjectId, "proj-1");
});

test("switchProject refreshes knowledge for the newly active project", async () => {
  // Before this fix, switching the active project left the knowledge graph
  // + loops panels showing the OLD project until something else triggered
  // a knowledge refresh.
  const graphProjects = [];
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/list") return { sessions: [] };
      if (method === "projects/list") {
        return { projects: [{ project_id: "a" }, { project_id: "b" }], current: "a" };
      }
      if (method === "projects/switch") {
        return { projects: [{ project_id: "a" }, { project_id: "b" }], current: params.project_id };
      }
      if (method === "workflows/list") return { workflows: [], count: 0 };
      if (method === "knowledge/graph") {
        graphProjects.push(params.project_id);
        return { nodes: [], edges: [] };
      }
      if (method === "research/loops") return { loops: [] };
      if (method === "knowledge/verification") return null;
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "refreshStudio" });
  await waitFor(() => graphProjects.length === 1, "initial knowledge refresh");
  assert.equal(graphProjects[0], "a");

  harness.send({ type: "switchProject", projectId: "b" });
  await waitFor(() => graphProjects.length === 2, "knowledge refresh after switch");
  assert.equal(graphProjects[1], "b");
});

// ── Knowledge intake flow: ingest / direct-ask / file-pick relay ─────────

test("ingestKnowledge forwards files, sources, and project to knowledge/import", async () => {
  let importCall;
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/list") return { sessions: [] };
      if (method === "knowledge/import") {
        importCall = params;
        return { project_id: params.project_id ?? "proj-1", ingested: params.files.length };
      }
      if (method === "knowledge/graph") throw new Error("unconfigured");
      if (method === "research/loops") throw new Error("unconfigured");
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({
    type: "ingestKnowledge",
    files: ["/papers/a.pdf", "/papers/b.pdf"],
    sources: ["MOF", "CO2 binding"],
    projectId: "proj-9",
  });
  await waitFor(() => importCall !== undefined, "knowledge/import");

  assert.equal(importCall.project_id, "proj-9");
  assert.deepEqual(importCall.files, ["/papers/a.pdf", "/papers/b.pdf"]);
  assert.deepEqual(importCall.sources, ["MOF", "CO2 binding"]);
  await waitFor(() => messagesOfType(harness.posted, "knowledgeIngested").length === 1, "knowledgeIngested");
});

// ── Studio offline snapshot: cache fallback + gatewayOnline flag ─────────

test("knowledge refresh falls back to cache + flags when the gateway is down", async () => {
  const stateDir = mkdtempSync(path.join(os.tmpdir(), "r4-studio-cache-"));
  process.env.R4_STATE_DIR = stateDir;
  const needs = [];
  const gateway = {
    async listEndpoints() {
      return [];
    },
  };
  let projectIdCurrent = "proj-live";
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/list") return { sessions: [] };
      if (method === "projects/list") return { projects: [{ project_id: "proj-live" }], current: "proj-live" };
      if (method === "workflows/list") return { workflows: [], count: 0 };
      if (method === "knowledge/graph") {
        const dir = `${stateDir}/proj-live`;
        fs.mkdirSync(dir, { recursive: true });
        return {
          project_id: "proj-live",
          nodes: [{ id: "n1", node_id: "n1", title: "goal", node_type: "goal" }],
          edges: [],
        };
      }
      if (method === "research/loops") return { loops: [{ loop_id: "loop-9", status: "running" }], detail: null };
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client], gatewayClient: gateway });
  harness.send({ type: "refreshStudio" });
  await waitFor(() => messagesOfType(harness.posted, "studioState").length === 1, "studioState");
  await waitFor(() => messagesOfType(harness.posted, "knowledgeState").length === 1, "good refresh");
  assert.ok(
    fs.existsSync(`${stateDir}/projects/proj-live/kgraph.json`),
    "kgraph cache written",
  );

  // Server drops: fail knowledge/graph and research/loops; extension should
  // surface last-known snapshot with gatewayOnline=false instead of empty.
  for (const c of [client]) {
    const prev = c.request.bind(c);
    c.request = async (method, params) => {
      if (method === "knowledge/graph" || method === "research/loops") throw new Error("gateway down");
      return prev(method, params);
    };
  }
  harness.send({ type: "refreshKnowledge" });
  await waitFor(() => messagesOfType(harness.posted, "knowledgeState").length >= 2, "offline refresh posts second state");
  const state2 = messagesOfType(harness.posted, "knowledgeState").at(-1);
  assert.equal(state2.gatewayOnline, false);
  assert.ok(state2.knowledgeGraph, "mirror returned, not null");
  assert.ok(Array.isArray(state2.knowledgeGraph.nodes) && state2.knowledgeGraph.nodes.length > 0);
  rmSync(stateDir, { recursive: true, force: true });
  delete process.env.R4_STATE_DIR;
});

test("knowledgeState carries registry graphs + constraints (0069 multi-graph)", async () => {
  const client = createClient({
    request: async (method) => {
      if (method === "sessions/list") return { sessions: [] };
      if (method === "projects/list") {
        return { projects: [{ project_id: "proj-9" }], current: "proj-9" };
      }
      if (method === "workflows/list") return { workflows: [], count: 0 };
      if (method === "knowledge/graph") return { nodes: [], edges: [] };
      if (method === "research/loops") return { loops: [] };
      if (method === "knowledge/graphs") {
        return {
          graphs: [
            { graph_id: "g-a", name: "project-a", kind: "project", status: "active" },
            { graph_id: "g-b", name: "experiment-history", kind: "experiment", status: "active" },
          ],
          total: 2,
        };
      }
      if (method === "knowledge/graph_constraints") {
        return { constraints: { include: ["experiment-history"], exclude: ["institutional"], discovery: true } };
      }
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  // Establish the studio state (B6 gate: registry fetches only happen with
  // a selected project — the historic global-census fallback is gone).
  harness.send({ type: "refreshStudio" });
  await waitFor(() => messagesOfType(harness.posted, "studioState").length >= 1, "studioState");
  harness.send({ type: "refreshKnowledge" });
  await waitFor(() => {
    const states = messagesOfType(harness.posted, "knowledgeState");
    return states.length === 1 && states[0].knowledgeGraphs;
  }, "knowledgeState with graphs");

  const snapshot = messagesOfType(harness.posted, "knowledgeState")[0];
  assert.equal(snapshot.knowledgeGraphs.length, 2);
  assert.equal(snapshot.knowledgeGraphs[1].name, "experiment-history");
  assert.deepEqual(snapshot.knowledgeGraphConstraints, {
    include: ["experiment-history"],
    exclude: ["institutional"],
    discovery: true,
  });
});

test("refreshKnowledge issues NO registry fetch when no project is selected (B6)", async () => {
  let graphsCalls = 0;
  let constraintCalls = 0;
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/list") return { sessions: [] };
      if (method === "projects/list") return { projects: [], current: null };
      if (method === "workflows/list") return { workflows: [], count: 0 };
      if (method === "knowledge/graph") return { nodes: [], edges: [] };
      if (method === "research/loops") return { loops: [] };
      if (method === "knowledge/graphs") { graphsCalls += 1; return { graphs: [], total: 0 }; }
      if (method === "knowledge/graph_constraints") { constraintCalls += 1; return { constraints: {} }; }
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "refreshStudio" });
  await waitFor(() => messagesOfType(harness.posted, "studioState").length >= 1, "studioState");
  harness.send({ type: "refreshKnowledge" });
  await waitFor(() => messagesOfType(harness.posted, "knowledgeState").length >= 1, "knowledgeState");

  const snapshot = messagesOfType(harness.posted, "knowledgeState")[0];
  assert.equal(graphsCalls, 0, "no global graphs census without a project");
  assert.equal(constraintCalls, 0, "constraints stay unfetched until a project exists");
  assert.deepEqual(snapshot.knowledgeGraphs, []);
  assert.equal(snapshot.knowledgeGraphConstraints, null);
});

test("setKnowledgeGraphConstraints posts ACP constraints then refreshes", async () => {
  let constraintsCall;
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/list") return { sessions: [] };
      if (method === "projects/list") return { projects: [], current: null };
      if (method === "workflows/list") return { workflows: [], count: 0 };
      if (method === "knowledge/graph") throw new Error("unused");
      if (method === "research/loops") return { loops: [] };
      if (method === "knowledge/graphs") return { graphs: [], total: 0 };
      if (method === "knowledge/graph_constraints") {
        if (params?.include !== undefined) {
          constraintsCall = params;
          return { constraints: params };
        }
        return { constraints: { include: [], exclude: [], discovery: true } };
      }
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({
    type: "setKnowledgeGraphConstraints",
    constraints: { include: ["project-a"], exclude: ["institutional"], discovery: false },
  });
  await waitFor(() => constraintsCall !== undefined, "knowledge/graph_constraints set");

  assert.deepEqual(constraintsCall, {
    include: ["project-a"],
    exclude: ["institutional"],
    discovery: false,
  });
});

test("refreshKnowledge scopes knowledge/graphs to the current project", async () => {
  const graphParamsSeen = [];
  const client = createClient({
    request: async (method, params) => {
      if (method === "sessions/list") return { sessions: [] };
      if (method === "projects/list") return { projects: [{ project_id: "proj-9" }], current: "proj-9" };
      if (method === "workflows/list") return { workflows: [], count: 0 };
      if (method === "knowledge/graph") return { nodes: [], edges: [] };
      if (method === "research/loops") return { loops: [] };
      if (method === "knowledge/graphs") {
        graphParamsSeen.push(params ?? null);
        return { graphs: [{ graph_id: "g-a", kind: "project", project_id: "proj-9", status: "active" }], total: 1 };
      }
      if (method === "knowledge/graph_constraints") return { constraints: { include: [], exclude: [], discovery: false } };
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });

  harness.send({ type: "refreshStudio" });
  await waitFor(() => messagesOfType(harness.posted, "studioState").length >= 1, "studioState");
  harness.send({ type: "refreshKnowledge" });
  await waitFor(() => graphParamsSeen.length >= 1, "knowledge/graphs called");

  assert.deepEqual(graphParamsSeen[0], { project_id: "proj-9" });
});

test("shell_output forwards as shellStream post to webview (W5c)", async () => {
  const chat = deferred();
  const client = createClient({
    request(method, params, options) {
      if (method === "sessions/load") return { session: { id: params.session_id, name: "S" } };
      if (method === "chat/send") return chat.promise;
      if (method === "sessions/list") return { sessions: [] };
      if (method === "editor/tools/register") return {};
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });
  harness.send({ type: "ready" });
  await waitFor(
    () => messagesOfType(harness.posted, "inventory").length === 1,
    "initialize completes (notification listener attached)",
  );
  harness.send({ type: "selectSession", sessionId: "session-1" });
  await waitFor(() => messagesOfType(harness.posted, "sessionSelected").length === 1, "session selected");
  harness.send({ type: "send", text: "x" });
  await waitFor(
    () => client.requests.some((request) => request.method === "chat/send"),
    "chat request in flight",
  );
  const chatRequest = client.requests.find((request) => request.method === "chat/send");
  const requestId = chatRequest.params.request_id;

  // session/event notification carries the agent frame payload
  client.notify("session/event", {
    session_id: "session-1",
    request_id: requestId,
    event: {
      event_type: "shell_output",
      message: "line-a\n",
      data: { stream_id: "shell-ab12cd34" },
    },
  });
  await waitFor(
    () => harness.posted.some(
      (m) =>
        m.type === "shellStream" &&
        m.streamId === "shell-ab12cd34" &&
        m.sessionId === "session-1" &&
        m.chunk === "line-a\n"
    ),
    "shellStream forwarded with stream routing",
  );

  chat.resolve({ status: "complete", session_id: "session-1", response: "Done" });
});

test("shellCancel message routes to shell/cancel and posts shellStreamStatus ack (W5c)", async () => {
  const requestsSeen = [];
  const client = createClient({
    request(method, params, options) {
      if (method === "shell/cancel") {
        requestsSeen.push(params);
        return { stream_id: params.stream_id, status: "cancelled" };
      }
      if (method === "sessions/list") return { sessions: [] };
      if (method === "editor/tools/register") return {};
      throw new Error(`Unexpected request: ${method}`);
    },
  });
  const harness = createHarness({ clients: [client] });
  harness.send({ type: "ready" });
  await waitFor(
    () => messagesOfType(harness.posted, "inventory").length === 1,
    "initialize completes",
  );
  harness.send({ type: "shellCancel", streamId: "shell-zz42", sessionId: "sess-sh" });
  await waitFor(() => requestsSeen.length >= 1, "cancel request made");
  assert.deepEqual(requestsSeen[0], { stream_id: "shell-zz42" });
  await waitFor(
    () =>
      harness.posted.some(
        (m) =>
          m.type === "shellStreamStatus" &&
          m.streamId === "shell-zz42" &&
          m.status === "cancelled"
      ),
    "shellStreamStatus ack posted",
  );
});


