import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, rmSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundleDir = mkdtempSync(path.join(os.tmpdir(), "r4-auth-sources-"));
const bundlePath = path.join(bundleDir, "auth.bundle.cjs");
const vscodeStubPath = path.join(rootDir, "test", "vscode-stub.cjs");
const esbuild = path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild");

execFileSync(
  esbuild,
  [
    "src/auth.ts",
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
const { AuthManager } = require(bundlePath);
Module._resolveFilename = originalResolveFilename;
rmSync(bundleDir, { recursive: true, force: true });

/** Ephemeral secrets + isolated XDG config dir per test. */
function makeAuth() {
  const xdg = mkdtempSync(path.join(os.tmpdir(), "r4-xdg-"));
  process.env.XDG_CONFIG_HOME = xdg;
  const store = new Map();
  const secrets = {
    get: async (k) => store.get(k),
    store: async (k, v) => void store.set(k, v),
    delete: async (k) => void store.delete(k),
  };
  const auth = new AuthManager(secrets);
  return {
    auth,
    sourcesPath: path.join(xdg, "r4", "sources.json"),
    cleanup: () => {
      delete process.env.XDG_CONFIG_HOME;
      rmSync(xdg, { recursive: true, force: true });
    },
  };
}

test("upsertCliSource creates sources.json, activates first source, mode 0600", async (t) => {
  const { auth, sourcesPath, cleanup } = makeAuth();
  t.after(cleanup);

  const result = auth.upsertCliSource({
    name: "ollama",
    kind: "public",
    url: "http://localhost:11434",
    provider: "ollama",
    description: "Local Ollama",
  });
  assert.deepEqual(result, { updated: false, activated: true });

  const data = JSON.parse(readFileSync(sourcesPath, "utf-8"));
  assert.equal(data.active, "ollama");
  assert.equal(data.sources.length, 1);
  assert.equal(data.sources[0].kind, "public");
  assert.equal(data.sources[0].provider, "ollama");
  if (process.platform !== "win32") {
    assert.equal(statSync(sourcesPath).mode & 0o777, 0o600);
  }

  // First source becomes the resolvable active LLM source (tokenless public OK).
  const active = await auth.getActiveCliSource();
  assert.equal(active?.name, "ollama");
  assert.equal(auth.listLlmSourceConfigs().length, 1);
  assert.equal(auth.listLlmSourceConfigs()[0].kind, "public");
  assert.equal(auth.listLlmSourceConfigs()[0].token, "");
});

test("upsertCliSource replaces by name and never clobbers existing active", async (t) => {
  const { auth, sourcesPath, cleanup } = makeAuth();
  t.after(cleanup);

  auth.upsertCliSource({ name: "rccs", kind: "platform", url: "http://gw:8000", api_key: "k1" });
  const second = auth.upsertCliSource({ name: "openrouter", kind: "public", url: "https://openrouter.ai/api", api_key: "k2" });
  assert.equal(second.activated, false);

  const replaced = auth.upsertCliSource({ name: "openrouter", kind: "public", url: "https://openrouter.ai/v2", api_key: "k3" });
  assert.equal(replaced.updated, true);

  const data = JSON.parse(readFileSync(sourcesPath, "utf-8"));
  assert.equal(data.active, "rccs");
  const or = data.sources.find((s) => s.name === "openrouter");
  assert.equal(or.url, "https://openrouter.ai/v2");
  assert.equal(or.api_key, "k3");
  assert.equal(data.sources.filter((s) => s.name === "openrouter").length, 1);

  // Both sources resolve as LLM-capable configs.
  const names = auth.listLlmSourceConfigs().map((s) => s.name).sort();
  assert.deepEqual(names, ["openrouter", "rccs"]);
});

test("upsertCliSource survives a missing or corrupt sources.json", (t) => {
  const { auth, sourcesPath, cleanup } = makeAuth();
  t.after(cleanup);

  // Missing file → fresh store (covered above); now corrupt it.
  auth.upsertCliSource({ name: "ollama", kind: "public", url: "http://localhost:11434" });
  chmodSync(sourcesPath, 0o644);
  require("node:fs").writeFileSync(sourcesPath, "{ not json");
  assert.deepEqual(auth.listCliSources(), []);

  const result = auth.upsertCliSource({ name: "ollama", kind: "public", url: "http://localhost:11434" });
  assert.deepEqual(result, { updated: false, activated: true });
  assert.equal(JSON.parse(readFileSync(sourcesPath, "utf-8")).sources.length, 1);
});
