import { test, expect } from "bun:test";

import {
  knowledgeGraphContentRows,
  knowledgeGraphListRows,
  knowledgeInventoryRows,
  knowledgeStatusRows,
} from "../src/lib/mgmt-card-format";

test("knowledgeStatusRows exposes project/runtime/notes", () => {
  const rows = knowledgeStatusRows({
    available: true,
    nodes: 12,
    edges: 47,
    project_id: "demo",
    runtime: "umrs",
    backends: ["umrs", "local"],
  });
  const map = Object.fromEntries(rows.map((r) => [r.key, r.value]));
  expect(map["available"]).toBe("yes");
  expect(map["nodes"]).toBe("12");
  expect(map["edges"]).toBe("47");
  expect(map["backends"]).toBe("umrs,local");
});

test("knowledgeStatusRows degrades gracefully on digest miss", () => {
  const rows = knowledgeStatusRows({ available: false, reason: "no graph yet" });
  const map = Object.fromEntries(rows.map((r) => [r.key, r.value]));
  expect(map["available"]).toBe("no");
  expect(map["note"]).toBe("no graph yet");
});

test("knowledgeInventoryRows lists counts + backend", () => {
  const rows = knowledgeInventoryRows({
    stats: { nodes: 71, edges: 129, backend: "ks_pickle" },
    patterns: ["p1", "p2"],
    lessons: ["l1"],
  });
  const map = Object.fromEntries(rows.map((r) => [r.key, r.value]));
  expect(map["nodes"]).toBe("71");
  expect(map["edges"]).toBe("129");
  expect(map["patterns"]).toBe("2");
  expect(map["lessons"]).toBe("1");
  expect(map["runtime"]).toBe("ks_pickle");
});

test("knowledgeGraphListRows renders concise rows", () => {
  const rows = knowledgeGraphListRows({
    graphs: [
      {
        graph_id: "demo",
        kind: "project",
        owner: "vm",
        description: "demo graph for example-workflow",
      },
    ],
  });
  expect(rows.length).toBe(1);
  expect(rows[0][0]).toBe("demo");
  expect(rows[0][1]).toBe("project");
});

test("knowledgeGraphListRows falls back to owner_id (GraphRegistry rows)", () => {
  const rows = knowledgeGraphListRows({ graphs: [{ graph_id: "g", kind: "project", owner_id: "alice" }] });
  expect(rows[0][2]).toBe("alice");
});

test("knowledgeGraphContentRows carries counts, owner and node labels", () => {
  const { header, nodes } = knowledgeGraphContentRows({
    graph_id: "g-2",
    kind: "project",
    owner_id: "bob",
    nodes: [{ id: "n1", type: "material" }],
    edges: [],
    truncated: false,
  });
  const map = Object.fromEntries(header.map((r) => [r.key, r.value]));
  expect(map["graph"]).toBe("g-2");
  expect(map["owner"]).toBe("bob");
  expect(map["content"]).toBe("1 nodes, 0 edges");
  expect(nodes).toEqual([["n1", "material"]]);
});
