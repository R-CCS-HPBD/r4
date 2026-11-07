import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "lm-convert.bundle.cjs");
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
    "src/lm-convert.ts",
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
  convertTools,
  buildThinkingEffortSchema,
  getConfiguredThinkingEffort,
  resolveReasoningEffortConfig,
  resolveModelEffortConfig,
  deriveReasoningEffortFromModelId,
} = require(bundlePath);

test("convertTools: undefined tools -> []", () => {
  assert.deepEqual(convertTools(undefined), []);
});

test("convertTools: empty tools -> []", () => {
  assert.deepEqual(convertTools([]), []);
});

test("convertTools: tool with description is forwarded", () => {
  const tools = [
    {
      name: "read_file",
      description: "Read a file",
      inputSchema: { type: "object", properties: { path: { type: "string" } } },
    },
  ];
  assert.deepEqual(convertTools(tools), [
    {
      type: "function",
      function: {
        name: "read_file",
        description: "Read a file",
        parameters: {
          type: "object",
          properties: { path: { type: "string" } },
        },
      },
    },
  ]);
});

test("convertTools: missing description coerced to empty string", () => {
  // vLLM (0.22.x) rejects tools whose `description` field is omitted with a
  // 400 "1 validation error for ToolDescription\ndescription\n  Input should
  // be a valid string". Forcing `description: ""` makes the field present
  // and valid, allowing the request through.
  const tools = [
    { name: "noop", inputSchema: { type: "object", properties: {} } },
  ];
  assert.deepEqual(convertTools(tools), [
    {
      type: "function",
      function: {
        name: "noop",
        description: "",
        parameters: { type: "object", properties: {} },
      },
    },
  ]);
});

test("convertTools: null description coerced to empty string", () => {
  const tools = [{ name: "x", description: null, inputSchema: {} }];
  const result = convertTools(tools);
  assert.equal(result[0].function.description, "");
});

test("convertTools: missing parameters defaults to object schema", () => {
  const tools = [{ name: "x", description: "y" }];
  const result = convertTools(tools);
  assert.deepEqual(result[0].function.parameters, {
    type: "object",
    properties: {},
  });
});

test("convertTools: JSON.stringify does not omit description", () => {
  // Regression guard: the JSON sent to the gateway must include the
  // `description` field (even if empty) so vLLM's ToolDescription validator
  // accepts the tool.
  const tools = [{ name: "x", inputSchema: {} }];
  const result = convertTools(tools);
  const json = JSON.stringify(result[0]);
  assert.ok(
    json.includes('"description":""'),
    `expected description field in JSON, got: ${json}`,
  );
});

test("buildThinkingEffortSchema: kimi levels", () => {
  const schema = buildThinkingEffortSchema(["none", "high"], "high");
  const prop = schema.properties.reasoningEffort;
  assert.deepEqual(prop.enum, ["none", "high"]);
  assert.deepEqual(prop.enumItemLabels, ["None", "High"]);
  assert.equal(prop.default, "high");
});

test("buildThinkingEffortSchema: llm-jp levels", () => {
  const schema = buildThinkingEffortSchema(["low", "medium", "high"], "medium");
  assert.deepEqual(schema.properties.reasoningEffort.enum, ["low", "medium", "high"]);
});

test("resolveReasoningEffortConfig: valid catalog capabilities", () => {
  const config = resolveReasoningEffortConfig({
    reasoning_effort_levels: ["high", "max"],
    reasoning_effort_default: "high",
  });
  assert.deepEqual(config, { levels: ["high", "max"], default: "high" });
});

test("resolveReasoningEffortConfig: missing default returns undefined", () => {
  assert.equal(
    resolveReasoningEffortConfig({ reasoning_effort_levels: ["high", "max"] }),
    undefined,
  );
});

test("deriveReasoningEffortFromModelId: kimi fallback", () => {
  assert.deepEqual(deriveReasoningEffortFromModelId("Kimi-K2.6"), {
    levels: ["none", "high"],
    default: "high",
  });
  assert.equal(deriveReasoningEffortFromModelId("Kimi-K2.7-Code"), undefined);
});

test("resolveModelEffortConfig: uses catalog when present, else model-id fallback", () => {
  assert.deepEqual(
    resolveModelEffortConfig("Kimi-K2.6", {
      reasoning_effort_levels: ["high", "max"],
      reasoning_effort_default: "high",
    }),
    { levels: ["high", "max"], default: "high" },
  );
  assert.deepEqual(resolveModelEffortConfig("Kimi-K2.6"), {
    levels: ["none", "high"],
    default: "high",
  });
  assert.equal(resolveModelEffortConfig("Kimi-K2.7-Code"), undefined);
});

test("getConfiguredThinkingEffort: clamps stale value to default", () => {
  const effort = getConfiguredThinkingEffort(
    { modelConfiguration: { reasoningEffort: "low" } },
    ["high", "max"],
    "high",
  );
  assert.equal(effort, "high");
});

test("getConfiguredThinkingEffort: reads VS Code model options", () => {
  const effort = getConfiguredThinkingEffort(
    { modelOptions: { reasoningEffort: "max" } },
    ["none", "high", "max"],
    "high",
  );
  assert.equal(effort, "max");
});

test("getConfiguredThinkingEffort: single level returns fixed effort", () => {
  const effort = getConfiguredThinkingEffort({}, ["medium"], "medium");
  assert.equal(effort, "medium");
});

test("getConfiguredThinkingEffort: none is sent explicitly for vLLM", () => {
  const effort = getConfiguredThinkingEffort(
    { modelConfiguration: { reasoningEffort: "none" } },
    ["none", "high"],
    "high",
  );
  assert.equal(effort, "none");
});
