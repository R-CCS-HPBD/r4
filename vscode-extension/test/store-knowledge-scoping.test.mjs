import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "store-knowledge-scoping.bundle.cjs");

// Bundle ONLY the reducer surface (store context is React-heavy; esbuild
// bundles it fine even though these tests call only pure reducers).
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
const { applyKnowledgeSnapshot, applyStudioState } = require(bundlePath);

function baseState() {
  return {
    studio: {
      currentProjectId: "proj-a",
      catalogHits: [{ s3_key: "a/file.h5" }],
      catalogError: null,
      selectedExecutionId: undefined,
      selectedStepName: undefined,
      knowledgeGraph: { nodes: [{ id: "n1" }], edges: [] },
      knowledgeVerification: { verdict: "ok" },
      knowledgeReconcile: { status: "clean" },
      researchLoops: [{ id: "loop-1" }],
      researchLoopDetail: { id: "loop-1" },
      knowledgeGraphs: [{ graph_id: "g-a" }],
      knowledgeGraphConstraints: { include: ["g-a"], exclude: [], discovery: true },
      selectedGraphContents: [{ graph_id: "g-a", nodes: [] }],
      loopRuntime: { status: "running" },
    },
  };
}

test("applyStudioState clears the whole knowledge surface on project switch (B4)", () => {
  const state = baseState();
  const next = applyStudioState(state, {
    type: "studioState",
    projects: ["proj-a", "proj-b"],
    currentProjectId: "proj-b",
    workflows: [],
    executions: [],
  });
  assert.equal(next.currentProjectId, "proj-b");
  assert.equal(next.knowledgeGraph, null, "old project graph must not render");
  assert.equal(next.knowledgeVerification, null);
  assert.equal(next.knowledgeReconcile, null);
  assert.deepEqual(next.researchLoops, []);
  assert.equal(next.researchLoopDetail, null);
  assert.deepEqual(next.knowledgeGraphs, []);
  assert.deepEqual(next.knowledgeGraphConstraints, {
    include: [],
    exclude: [],
    discovery: false,
  });
  assert.deepEqual(next.selectedGraphContents, []);
  assert.equal(next.loopRuntime, null);
  assert.deepEqual(next.catalogHits, [], "F3 behavior preserved");
  assert.equal(next.catalogError, null);
});

test("applyStudioState preserves knowledge surface when project is unchanged", () => {
  const state = baseState();
  const next = applyStudioState(state, {
    type: "studioState",
    projects: ["proj-a"],
    currentProjectId: "proj-a",
    workflows: [],
    executions: [],
  });
  assert.deepEqual(next.knowledgeGraph, state.studio.knowledgeGraph);
  assert.deepEqual(next.researchLoops, state.studio.researchLoops);
  assert.deepEqual(next.knowledgeGraphs, state.studio.knowledgeGraphs);
});

test("applyKnowledgeSnapshot drops stale-project payloads (B5)", () => {
  const state = baseState();
  const event = {
    type: "knowledgeState",
    projectId: "proj-old",
    knowledgeGraph: { nodes: [{ id: "foreign" }], edges: [] },
    researchLoops: { loops: [{ id: "foreign-loop" }] },
    knowledgeGraphs: [{ graph_id: "g-foreign" }],
  };
  const next = applyKnowledgeSnapshot(state, event);
  assert.equal(next, state, "payload attesting another project must be discarded");
  assert.deepEqual(next.studio.knowledgeGraph, state.studio.knowledgeGraph);
});

test("applyKnowledgeSnapshot applies payloads attesting the current project", () => {
  const state = baseState();
  const event = {
    type: "knowledgeState",
    projectId: "proj-a",
    knowledgeGraph: { nodes: [{ id: "fresh" }], edges: [] },
    researchLoops: { loops: [{ id: "fresh-loop" }] },
  };
  const next = applyKnowledgeSnapshot(state, event);
  assert.notEqual(next, state);
  assert.deepEqual(next.studio.knowledgeGraph.nodes, [{ id: "fresh" }]);
  assert.deepEqual(next.studio.researchLoops, [{ id: "fresh-loop" }]);
});

test("applyKnowledgeSnapshot with no attestation applies when nothing contradicts", () => {
  const state = baseState();
  const event = {
    type: "knowledgeState",
    knowledgeGraph: { nodes: [{ id: "fresh" }], edges: [] },
  };
  const next = applyKnowledgeSnapshot(state, event);
  assert.deepEqual(next.studio.knowledgeGraph.nodes, [{ id: "fresh" }]);
});
