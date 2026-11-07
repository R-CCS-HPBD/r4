import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "vision-proxy.bundle.cjs");
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
    "src/vision-proxy.ts",
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
  catalogKeyMatchesLlmId,
  filterReadyVisionModelIds,
  isVisionCatalogModel,
  llmIdMatchesVisionCatalog,
  messagesHaveImages,
  pickVisionClient,
  resolveVisionProxyMessages,
} = require(bundlePath);
const vscode = require(vscodeStubPath);
rmSync(bundlePath, { force: true });

test("isVisionCatalogModel detects vision catalog entries", () => {
  assert.equal(isVisionCatalogModel({ id: "a", name: "a", scope: "system", model_type: "vision" }), true);
  assert.equal(isVisionCatalogModel({ id: "b", name: "b", scope: "system", model_type: "vlm" }), true);
  assert.equal(
    isVisionCatalogModel({
      id: "c",
      name: "c",
      scope: "system",
      capabilities: { supports_vision: true },
    }),
    true,
  );
  assert.equal(isVisionCatalogModel({ id: "d", name: "d", scope: "system", model_type: "llm" }), false);
});

test("filterReadyVisionModelIds intersects ready LLM models with vision catalog", () => {
  const catalog = [
    { id: "vlm-1", name: "vlm-1", scope: "system", model_type: "vision" },
    { id: "kimi-k2.6", name: "Kimi-K2.6", scope: "system", capabilities: { supports_vision: true } },
    { id: "deepseek-v4-pro", name: "DeepSeek-V4-Pro", scope: "system", model_type: "llm" },
  ];

  assert.deepEqual(filterReadyVisionModelIds(catalog, ["vlm-1", "DeepSeek-V4-Pro"]), ["vlm-1"]);
});

test("filterReadyVisionModelIds excludes primary model and deduplicates", () => {
  const catalog = [
    { id: "vlm-1", name: "vlm-1", scope: "system", model_type: "vision" },
    { id: "kimi-k2.6", name: "Kimi-K2.6", scope: "system", capabilities: { supports_vision: true } },
  ];

  assert.deepEqual(
    filterReadyVisionModelIds(catalog, ["Kimi-K2.6", "vlm-1", "vlm-1"], "Kimi-K2.6"),
    ["vlm-1"],
  );
});

test("catalogKeyMatchesLlmId matches deployment variants and huggingface ids", () => {
  assert.equal(
    catalogKeyMatchesLlmId("kimi-k2.7-code", "kimi-k2.7-code-kimi-k2.7-code-fp8"),
    true,
  );
  assert.equal(catalogKeyMatchesLlmId("kimi-k2.7-code", "moonshotai/kimi-k2.7-code"), false);
  assert.equal(catalogKeyMatchesLlmId("moonshotai/kimi-k2.7-code", "moonshotai/kimi-k2.7-code"), true);
});

test("filterReadyVisionModelIds matches variant picker ids to vision catalog", () => {
  const catalog = [
    {
      id: "kimi-k2.7-code",
      name: "Kimi-K2.7-Code",
      scope: "system",
      huggingface_id: "moonshotai/Kimi-K2.7-Code",
      capabilities: { supports_vision: true },
    },
    { id: "deepseek-v4-pro", name: "DeepSeek-V4-Pro", scope: "system", model_type: "llm" },
  ];

  assert.deepEqual(
    filterReadyVisionModelIds(catalog, [
      "Kimi-K2.7-Code-kimi-k2.7-code-fp8",
      "DeepSeek-V4-Pro",
    ]),
    ["Kimi-K2.7-Code-kimi-k2.7-code-fp8"],
  );
  assert.equal(
    llmIdMatchesVisionCatalog("Kimi-K2.7-Code-kimi-k2.7-code-fp8", [
      "kimi-k2.7-code",
      "moonshotai/kimi-k2.7-code",
    ]),
    true,
  );
});

test("messagesHaveImages detects image data parts", () => {
  const messages = [
    {
      role: vscode.LanguageModelChatMessageRole.User,
      content: [
        new vscode.LanguageModelTextPart("what is this?"),
        new vscode.LanguageModelDataPart(new Uint8Array([1, 2, 3]), "image/png"),
      ],
    },
  ];

  assert.equal(messagesHaveImages(messages), true);
  assert.equal(messagesHaveImages([{ role: vscode.LanguageModelChatMessageRole.User, content: [] }]), false);
});

// ADR-106 D-10 (2026-11-03): a public chat source still gets the platform's
// vision models; the user never picks a service model.
const fakeClient = (name, isPublic) => ({ name, isPublic: () => isPublic });
const SOURCES = [
  { name: "my-openai", url: "https://api.example", token: "k", kind: "public" },
  { name: "rccs-cloud", url: "https://data0.example/rccs", token: "t", kind: "platform" },
];

test("pickVisionClient describes images on the platform when chat is public", () => {
  const made = [];
  const client = pickVisionClient(fakeClient("chat", true), SOURCES, (cfg) => {
    made.push(cfg.name);
    return fakeClient(cfg.name, false);
  });
  assert.equal(client.name, "rccs-cloud");
  assert.deepEqual(made, ["rccs-cloud"]);
});

test("pickVisionClient keeps a platform chat client", () => {
  const chat = fakeClient("chat", false);
  assert.equal(pickVisionClient(chat, SOURCES, () => assert.fail("no new client")), chat);
});

test("pickVisionClient keeps the chat client when no platform source exists", () => {
  const chat = fakeClient("chat", true);
  assert.equal(pickVisionClient(chat, [SOURCES[0]], () => assert.fail("no new client")), chat);
});

test("resolveVisionProxyMessages tells the user which model described the image", async () => {
  // ADR-106 D-10: implicit service models are named to the user.
  const client = {
    listModels: async () => [
      { id: "m1", name: "Qwen3.6-35B-A3B-NVFP4", scope: "system", model_type: "vlm" },
    ],
    listLLMModels: async () => [{ id: "Qwen3.6-35B-A3B-NVFP4" }],
    async *chatCompletionStream() {
      yield { content: "a cat on a desk" };
    },
  };
  const messages = [
    {
      role: vscode.LanguageModelChatMessageRole.User,
      content: [
        new vscode.LanguageModelTextPart("what is this?"),
        new vscode.LanguageModelDataPart(new Uint8Array([1, 2, 3]), "image/png"),
      ],
    },
  ];
  const token = { isCancellationRequested: false, onCancellationRequested: () => ({ dispose() {} }) };
  const resolved = await resolveVisionProxyMessages(client, messages, "my-public-gpt", token);
  assert.equal(resolved.notice, "R4: image described by Qwen3.6-35B-A3B-NVFP4.");
});
