// ADR-113 WV4 (2026-11-09): workflow versions in the sidebar. A workflow
// row reads `name` with `v<default> (+N)` and expands to a child per
// version; the trigger offers the versions default-first and sends
// `version`; an older server (no `versions`) gets the tree and body it had.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const vscodeStubPath = path.join(rootDir, "test", "vscode-stub.cjs");
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-workflow-versions-"));
process.on("exit", () => rmSync(outDir, { recursive: true, force: true }));

const require = createRequire(import.meta.url);
const Module = require("node:module");
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, isMain, options) {
  if (request === "vscode") return vscodeStubPath;
  return originalResolve.call(this, request, parent, isMain, options);
};

function bundle(entry, name) {
  const out = path.join(outDir, `${name}.cjs`);
  execFileSync(
    path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
    [entry, "--bundle", "--platform=node", "--format=cjs", `--outfile=${out}`, "--external:vscode"],
    { stdio: "inherit", cwd: rootDir },
  );
  return require(out);
}

const versions = bundle("src/workflow-versions.ts", "versions");
const views = bundle("src/views/workflows.ts", "views");
const gateway = bundle("src/gateway-client.ts", "gateway");

const RELAX = {
  workflow_id: "relax",
  name: "relax",
  description: "relax a cell",
  version: "1.2.0",
  versions: ["2.0.0-rc.1", "1.2.0", "1.1.0"],
  source_sha256: "3f2a9c1d0b7e55aa66bb77cc88dd99ee00ff11aa22bb33cc44dd55ee66ff7788",
};
const OLD = { workflow_id: "old", name: "old", description: "pre-ADR-113 server" };

test("row spec: name, `v<default> (+N) · description`, expandable with several versions", () => {
  assert.deepEqual(versions.workflowRowSpec(RELAX), {
    label: "relax",
    description: "v1.2.0 (+2) · relax a cell",
    expandable: true,
  });
  assert.deepEqual(versions.workflowRowSpec(OLD), {
    label: "old",
    description: "pre-ADR-113 server",
    expandable: false,
  });
});

test("version children keep server order and mark the default", () => {
  assert.deepEqual(
    versions.versionRowSpecs(RELAX).map((s) => [s.label, s.description]),
    [["v2.0.0-rc.1", ""], ["v1.2.0", "default"], ["v1.1.0", ""]],
  );
  // No `version` from the server: the D-3 rule picks the highest release.
  const noDefault = { ...RELAX, version: undefined };
  assert.equal(versions.defaultVersion(noDefault), "1.2.0");
});

test("quick pick: default first, then the rest; one version asks nothing", () => {
  assert.deepEqual(
    versions.versionPickItems(RELAX).map((i) => [i.label, i.description ?? ""]),
    [["1.2.0", "default"], ["2.0.0-rc.1", ""], ["1.1.0", ""]],
  );
  assert.equal(versions.needsVersionPick(RELAX), true);
  assert.equal(versions.needsVersionPick({ version: "1.0.0", versions: ["1.0.0"] }), false);
  assert.equal(versions.needsVersionPick(OLD), false);
  assert.equal(versions.needsVersionPick(undefined), false);
});

test("run label: name@version and the digest; bare name from an older server", () => {
  assert.equal(
    versions.runLabel({ workflow_name: "relax", workflow_version: "1.1.0", source_sha256: RELAX.source_sha256 }),
    "relax@1.1.0 · 3f2a9c1d0b7e",
  );
  assert.equal(versions.runLabel({ workflow_name: "old" }), "old");
});

function fakeClient(rows) {
  return { isPublic: () => false, listWorkflows: async () => rows };
}

test("tree: a versioned workflow expands to a child per version", async () => {
  const provider = new views.WorkflowTreeProvider(() => fakeClient([RELAX, OLD]));
  const [relax, old] = await provider.getChildren();
  assert.equal(relax.label, "relax");
  assert.equal(relax.description, "v1.2.0 (+2) · relax a cell");
  assert.equal(relax.collapsibleState, 1); // Collapsed
  assert.equal(old.description, "pre-ADR-113 server");
  assert.equal(old.collapsibleState, 0); // None: an older server's row stays a leaf
  const children = await provider.getChildren(relax);
  assert.deepEqual(children.map((c) => [c.label, c.description, c.contextValue, c.workflowVersion]), [
    ["v2.0.0-rc.1", "", "workflow-version", "2.0.0-rc.1"],
    ["v1.2.0", "default", "workflow-version", "1.2.0"],
    ["v1.1.0", "", "workflow-version", "1.1.0"],
  ]);
  assert.equal(children[0].workflowName, "relax");
  assert.deepEqual(await provider.getChildren(children[0]), []);
});

function recordingClient() {
  const client = new gateway.GatewayClient({ baseUrl: "https://gw.example", token: "t", kind: "platform" });
  const calls = [];
  client.request = async (method, url, body) => {
    calls.push({ method, url, body });
    return { execution_id: "exec-1", workflow_id: "relax", status: "queued" };
  };
  return { client, calls };
}

test("trigger: `version` in the body when chosen, none otherwise; name@X is split", async () => {
  const { client, calls } = recordingClient();
  await client.triggerWorkflow("relax", { a: 1 }, "1.1.0");
  await client.triggerWorkflow("relax", undefined);
  await client.triggerWorkflow("relax@1.0.0", {});
  assert.deepEqual(calls, [
    { method: "POST", url: "/gw/workflows/relax/trigger", body: { parameters: { a: 1 }, version: "1.1.0" } },
    { method: "POST", url: "/gw/workflows/relax/trigger", body: { parameters: {} } },
    { method: "POST", url: "/gw/workflows/relax/trigger", body: { parameters: {}, version: "1.0.0" } },
  ]);
});
