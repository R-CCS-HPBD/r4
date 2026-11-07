import { test, expect } from "bun:test";

import {
  loopIterationRows,
  loopListRows,
  loopStatusRows,
  sessionViewRows,
} from "../src/lib/mgmt-card-format";

test("loopListRows renders loop headers + active count", () => {
  const { rows, active, runtime } = loopListRows({
    loops: [
      {
        loop_id: "video-pipeline",
        status: "running",
        current_iteration: 3,
        max_iterations: 0,
        research_goal: "analyse the example pipeline vidéo 🎬",
      },
      { loop_id: "kg-bundle", status: "paused", current_iteration: 1 },
    ],
    active_count: 1,
    runtime: "umrs",
  });
  expect(rows.length).toBe(2);
  expect(rows[0][0]).toBe("video-pipeline");
  expect(rows[0][1]).toBe("running");
  expect(rows[0][2]).toBe("3/0");
  expect(active).toBe(1);
  expect(runtime).toBe("umrs");
});

test("loopStatusRows skips empty and keeps iterations ratio", () => {
  const rows = loopStatusRows({
    loop_id: "L1",
    status: "running",
    current_iteration: 7,
    max_iterations: 12,
    project_id: "demo",
    runtime: "local",
  });
  const map = Object.fromEntries(rows.map((r) => [r.key, r.value]));
  expect(map["loop"]).toBe("L1");
  expect(map["iterations"]).toBe("7/12");
  expect(map["runtime"]).toBe("local");
});

test("loopIterationRows takes last-N summaries", () => {
  const rows = loopIterationRows({
    recent_iterations: [
      { iteration: 5, hypothesis: "nodes file missing" },
      { iteration: 4, observation: "api key path not found" },
    ],
  });
  expect(rows.length).toBe(2);
  expect(rows[0][0]).toBe("5");
});

test("sessionViewRows sums loops + authority note", () => {
  const rows = sessionViewRows({
    project_id: "p1",
    runtime: "umrs",
    loops: [{ status: "running" }, { status: "done" }],
    sessions: ["s1"],
    knowledge: { nodes: 42, edges: 87 },
    authoritative_runtime: "umrs",
  });
  const map = Object.fromEntries(rows.map((r) => [r.key, r.value]));
  expect(map["loops"]).toBe("2 (1 active)");
  expect(map["knowledge"]).toBe("42 nodes / 87 edges");
  expect(map["authority"]).toBe("umrs");
});

test("loopStatusRows shows the not-found note for an owner-scoped 404 (V-13)", () => {
  const rows = loopStatusRows({
    loop_id: "loop-x",
    found: false,
    status: "not found",
    message: "no such loop, or not yours (loops are owner-scoped)",
  });
  expect(rows.map((r) => r.key)).toEqual(["loop", "status", "note"]);
  expect(rows[2].value).toContain("owner-scoped");
});
