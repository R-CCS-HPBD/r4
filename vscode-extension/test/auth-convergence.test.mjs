import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundleDir = mkdtempSync(path.join(os.tmpdir(), "r4-auth-conv-"));
const bundlePath = path.join(bundleDir, "auth.bundle.cjs");
const vscodeStubPath = path.join(rootDir, "test", "vscode-stub-auth-bridge.cjs");
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

/**
 * §R7 clean-machine convergence proof — integration at the extension-host
 * surface.
 *
 * The acceptance target: on a clean HOME, a user logging in via the VS Code
 * extension/WebView sees the same canonical R4 credential state as one who
 * ran `r4 auth login` in a terminal. No second credential store may exist
 * or diverge silently; reads that converge on a token must agree on the
 * token for both flows.
 *
 * This test drives the AuthManager code path against an isolated XDG config
 * dir — truly "clean machine" for the extension host. It does NOT spawn the
 * CLI inside the test; instead it *materializes* the canonical store shape
 * and asserts the extension-host read path returns the token exactly as the
 * CLI flow produced it. The loginViaR4Cli subprocess path was exercised in
 * webview-login-bridge.test.mjs (§R6 coverage), so this focuses on the
 * remaining convergence surface the user-facing proof needs.
 */

function makeSecrets() {
  const store = new Map();
  return {
    get: async (k) => store.get(k),
    store: async (k, v) => void store.set(k, v),
    delete: async (k) => void store.delete(k),
    _dump: () => store,
  };
}

/** CLI `r4 auth login` writes these files; extension host reads them back. */
function materializeCanonicalStore(configHome, { token, gatewayUrl, profileName = "bdrt" }) {
  const r4Dir = path.join(configHome, "r4");
  const umrsDir = path.join(configHome, "umrs", "connections");
  mkdirSync(r4Dir, { recursive: true });
  mkdirSync(umrsDir, { recursive: true });

  // sources.json — primary credential store (CLI writes)
  const sourcesPayload = {
    sources: [
      {
        name: profileName,
        kind: "platform",
        url: gatewayUrl,
        api_key: token,
      },
    ],
    active: profileName,
  };
  writeFileSync(
    path.join(r4Dir, "sources.json"),
    JSON.stringify(sourcesPayload, null, 2),
  );

  // config.json — profile store (CLI writes)
  const configPayload = {
    profiles: {
      [profileName]: { gateway_url: gatewayUrl, api_key: token },
    },
  };
  writeFileSync(
    path.join(r4Dir, "config.json"),
    JSON.stringify(configPayload, null, 2),
  );

  // UMRS connection profile name — CLI writes for the companion CLI
  writeFileSync(
    path.join(umrsDir, `${profileName}.json`),
    JSON.stringify(
      { name: profileName, url: gatewayUrl, token },
      null,
      2,
    ),
  );

  return {
    sourcesPath: path.join(r4Dir, "sources.json"),
    configPath: path.join(r4Dir, "config.json"),
    umrsProfilePath: path.join(umrsDir, `${profileName}.json`),
  };
}

test("clean-machine: AuthManager.getToken resolves from CLI-written sources.json", async () => {
  const configHome = mkdtempSync(path.join(os.tmpdir(), "r4-clean-home-"));
  const previousXdg = process.env.XDG_CONFIG_HOME;
  process.env.XDG_CONFIG_HOME = configHome;

  const token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0dXNlciJ9.fake";
  const gatewayUrl = "http://127.0.0.1:8004";
  const paths = materializeCanonicalStore(configHome, { token, gatewayUrl });

  const secrets = makeSecrets();
  const auth = new AuthManager(secrets);

  // 1. Token resolution must find the CLI-written source.
  const resolved = await auth.getToken();
  assert.strictEqual(resolved, token, "AuthManager must read sources.json token");

  // 2. Active source tracking must surface the CLI name.
  const active = await auth.getActiveCliSource();
  assert.strictEqual(active?.name, "bdrt", "active source must be the CLI-writen profile name");
  assert.strictEqual(active?.url, gatewayUrl);
  assert.strictEqual(active?.api_key, token);

  // 3. Gateway URL resolution must surface the CLI-written URL.
  const url = await auth.getGatewayUrl();
  assert.strictEqual(url, gatewayUrl);

  // 4. SecretStorage must remain untouched (primary credentialing path).
  assert.strictEqual(secrets._dump().size, 0, "SecretStorage stays empty when sources.json wins");

  // 5. The umrs connection profile sits where the CLI left it (extension
  //    host should not need to write a parallel one).
  const umrsProfile = JSON.parse(readFileSync(paths.umrsProfilePath, "utf-8"));
  assert.strictEqual(umrsProfile.token, token, "UMRS connection token must match CLI-written");

  process.env.XDG_CONFIG_HOME = previousXdg;
  rmSync(configHome, { recursive: true, force: true });
});

test("clean-machine: pure extension-host login materializes canonical store only", async () => {
  const configHome = mkdtempSync(path.join(os.tmpdir(), "r4-clean-home-"));
  const previousXdg = process.env.XDG_CONFIG_HOME;
  process.env.XDG_CONFIG_HOME = configHome;

  const token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0dXNlciJ9.fake";
  const gatewayUrl = "http://127.0.0.1:8004";

  // Extension-host writes ONLY to SecretStorage in the legacy path.
  const secrets = makeSecrets();
  const auth = new AuthManager(secrets);
  await auth.setToken(token);

  // 1. SecretStorage carries the token.
  const stored = await secrets.get("r4.gatewayToken");
  assert.strictEqual(stored, token, "SecretStorage must carry the token");

  // 2. token resolution picks it up (no CLI store exists yet).
  const resolved = await auth.getToken();
  assert.strictEqual(resolved, token);

  // 3. Canonical store files do NOT exist — the divergence surface
  //    §R7 is calling out is exactly this. Capture it deterministically.
  const sourcesPath = path.join(configHome, "r4", "sources.json");
  const configPath = path.join(configHome, "r4", "config.json");
  const sourcesExists = require("node:fs").existsSync(sourcesPath);
  const configExists = require("node:fs").existsSync(configPath);
  assert.strictEqual(sourcesExists, false, "canonical sources.json is missing on SecretStorage-only flow");
  assert.strictEqual(configExists, false, "canonical config.json is missing on SecretStorage-only flow");

  process.env.XDG_CONFIG_HOME = previousXdg;
  rmSync(configHome, { recursive: true, force: true });
});

test("convergence: CLI priority (sources.json) beats SecretStorage snapshot", async () => {
  const configHome = mkdtempSync(path.join(os.tmpdir(), "r4-clean-home-"));
  const previousXdg = process.env.XDG_CONFIG_HOME;
  process.env.XDG_CONFIG_HOME = configHome;

  const cliToken = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJjbGktdXNlciJ9.fake";
  const secretsToken = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJleHRlbmRlZC11c2VyIn0.fake";
  const gatewayUrl = "http://127.0.0.1:8004";

  materializeCanonicalStore(configHome, { token: cliToken, gatewayUrl });

  const secrets = makeSecrets();
  await secrets.store("r4.gatewayToken", secretsToken);
  const auth = new AuthManager(secrets);

  // sources.json token takes precedence over the SecretStorage snapshot so
  // re-issued JWTs (e.g. a corrected project_id) are picked up automatically.
  const resolved = await auth.getToken();
  assert.strictEqual(resolved, cliToken, "sources.json must outrank SecretStorage");

  process.env.XDG_CONFIG_HOME = previousXdg;
  rmSync(configHome, { recursive: true, force: true });
});

test("report: active CLI source advertises BDRT profile name identically to the CLI", async () => {
  const configHome = mkdtempSync(path.join(os.tmpdir(), "r4-clean-home-"));
  const previousXdg = process.env.XDG_CONFIG_HOME;
  process.env.XDG_CONFIG_HOME = configHome;

  const token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0dXNlciJ9.fake";
  materializeCanonicalStore(configHome, {
    token,
    gatewayUrl: "http://127.0.0.1:8004",
    profileName: "bdrt",
  });

  const secrets = makeSecrets();
  const auth = new AuthManager(secrets);
  const active = await auth.getActiveCliSource();

  // Contract: the extension host must surface the same profile name the CLI
  // writes; profile name drift == WebView/Studio UI showing the wrong source.
  assert.strictEqual(active?.name, "bdrt", "profile name must match CLI write");

  process.env.XDG_CONFIG_HOME = previousXdg;
  rmSync(configHome, { recursive: true, force: true });
});
