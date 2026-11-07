import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "store-catalog-scoping.bundle.cjs");

// Bundle ONLY the reducer-exposed surface (store context is React-heavy;
// esbuild bundles it fine even though these tests call only pure reducers).
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  [
    "webview-ui/src/state/store.tsx",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    "--loader:.tsx=tsx",
    `--outfile=${bundlePath}`,
  ],
  { stdio: "inherit", cwd: rootDir },
);

const require = createRequire(import.meta.url);
const { applyCatalogState, applyStudioState } = require(bundlePath);

function baseState() {
  return {
    studio: {
      currentProjectId: "plate-tectonics-acceptance",
      catalogHits: [{ s3_key: "pt/file.h5" }],
      catalogError: null,
      selectedExecutionId: undefined,
      selectedStepName: undefined,
    },
  };
}

test("catalogState from a previous project is discarded", () => {
  const state = baseState();
  const event = {
    type: "catalogState",
    query: "",
    payload: { project_id: "default", results: [] },
  };
  const next = applyCatalogState(state, event);
  assert.equal(next, state, "stale payload must not touch the slice");
  assert.equal(next.studio.catalogHits.length, 1);
});

test("catalogState for the current project updates rows", () => {
  const state = baseState();
  const event = {
    type: "catalogState",
    query: "",
    payload: {
      project_id: "plate-tectonics-acceptance",
      results: [{ s3_key: "plate/new.h5" }],
    },
  };
  const next = applyCatalogState(state, event);
  assert.equal(next.studio.catalogHits.length, 1);
  assert.equal(next.studio.catalogHits[0].s3_key, "plate/new.h5");
});

test("catalog error state clears hits for the current-project query", () => {
  const state = baseState();
  const event = {
    type: "catalogState",
    query: "",
    error: "gateway wedged",
  };
  const next = applyCatalogState(state, event);
  assert.deepEqual(next.studio.catalogHits, []);
  assert.equal(next.studio.catalogError, "gateway wedged");
});

test("project switch clears catalogue rows + error so the card re-arms", () => {
  const state = baseState();
  const event = {
    type: "studioState",
    projects: [],
    currentProjectId: "seismology",
    workflows: [],
    executions: [],
  };
  const next = applyStudioState(state, event);
  assert.equal(next.currentProjectId, "seismology");
  assert.deepEqual(next.catalogHits, []);
  assert.equal(next.catalogError, null);
});

test("same-project studioState keeps catalogue rows", () => {
  const state = baseState();
  const event = {
    type: "studioState",
    projects: [],
    currentProjectId: "plate-tectonics-acceptance",
    workflows: [],
    executions: [],
  };
  const next = applyStudioState(state, event);
  assert.equal(next.catalogHits.length, 1);
});
