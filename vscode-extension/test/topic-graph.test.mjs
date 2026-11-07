import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "topic-graph.bundle.cjs");

execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  ["src/topic-graph.ts", "--bundle", "--platform=node", "--format=cjs", `--outfile=${bundlePath}`],
  { stdio: "inherit", cwd: rootDir },
);

const require = createRequire(import.meta.url);
const { topicTerms, graphMatchesTopic, rankSatelliteGraphs } = require(bundlePath);

const CATALOG = [
  { graph_id: "exp-hist", name: "experiment-history", kind: "project", project_id: "proj-a", status: "active" },
  { graph_id: "otter", name: "otter-distribution", kind: "domain", project_id: "proj-a", status: "active", description: "wildlife habitat survey" },
  { graph_id: "hmae", name: "hmae-pipeline", kind: "domain", project_id: "proj-a", status: "active", description: "SAXS pipeline notes" },
  { graph_id: "institutional", name: "institutional-memory", kind: "org", project_id: "proj-b", status: "active" },
  { graph_id: "other", name: "unrelated", kind: "domain", project_id: "proj-c", status: "active" },
];

test("topicTerms extracts domain words, drops stopwords and short tokens", () => {
  const terms = topicTerms([
    "show me the otter habitat analysis AND the HMAE pipeline results",
    "what did we learn about SXRD data?",
  ]);
  assert.ok(terms.has("otter"));
  assert.ok(terms.has("habitat"));
  assert.ok(terms.has("analysis"));
  assert.ok(terms.has("hmae"));
  assert.ok(terms.has("sxrd"));
  assert.ok(!terms.has("the"));
  assert.ok(!terms.has("and"));
  assert.ok(!terms.has("me"));
  assert.ok(!terms.has("graph"));
});

test("topicTerms stays empty for stopword-only or short messages", () => {
  assert.equal(topicTerms(["hello", "how are you"]).size, 0);
  assert.equal(topicTerms([]).size, 0);
});

test("graphMatchesTopic hits name and description only on term overlap", () => {
  const terms = topicTerms(["let's review the otter field campaign"]);
  assert.equal(graphMatchesTopic(CATALOG[1], terms), true);
  assert.equal(graphMatchesTopic(CATALOG[2], terms), false);
  assert.equal(graphMatchesTopic(CATALOG[2], new Set()), false);
});

test("rankSatelliteGraphs mounts NOTHING when discovery is off and include is empty", () => {
  const wanted = rankSatelliteGraphs(CATALOG, {
    include: new Set(), exclude: new Set(), discovery: false, projectId: "proj-a",
    terms: topicTerms(["otter campaign"]),
  });
  assert.deepEqual(wanted, []);
});

test("discovery with empty topic mounts kind-scoped satellites (research/domain only)", () => {
  const wanted = rankSatelliteGraphs(CATALOG, {
    include: new Set(), exclude: new Set(), discovery: true, projectId: "proj-a",
    terms: new Set(),
  });
  // project default ('exp-hist') is always skipped; both 'otter' and
  // 'hmae' are domain satellites of proj-a → mount.
  // 'institutional' (org) and 'other' (proj-c !== proj-a) stay off.
  assert.deepEqual(wanted, ["otter", "hmae"]);
});

test("discovery mounts only topic-overlapping same-project graphs", () => {
  const wanted = rankSatelliteGraphs(CATALOG, {
    include: new Set(), exclude: new Set(), discovery: true, projectId: "proj-a",
    terms: topicTerms(["deep dive into the hmae reconstruction pipeline"]),
  });
  assert.deepEqual(wanted, ["hmae"]);
});

test("include wins over discovery+topic; exclude wins over include", () => {
  const wanted = rankSatelliteGraphs(CATALOG, {
    include: new Set(["hmae-pipeline", "institutional"]),
    exclude: new Set(["institutional"]),
    discovery: true,
    projectId: "proj-a",
    terms: topicTerms(["otter habitats"]),
  });
  // gid prefers graph_id (id falls back to name only when id is empty).
  assert.deepEqual(wanted, ["otter", "hmae"]);
});

test("project default graph is never a satellite", () => {
  const wanted = rankSatelliteGraphs(CATALOG, {
    include: new Set(), exclude: new Set(), discovery: true, projectId: "proj-a",
    terms: topicTerms(["experiment tracker history review"]),
  });
  assert.ok(!wanted.includes("exp-hist"));
});

test("include entries unknown to the catalog are attempted by name", () => {
  const wanted = rankSatelliteGraphs(CATALOG, {
    include: new Set(["lab-legacy-notes"]),
    exclude: new Set(),
    discovery: false,
    projectId: "proj-a",
    terms: new Set(),
  });
  assert.deepEqual(wanted, ["lab-legacy-notes"]);
});
