import { test, expect } from "bun:test";

import {
  statusIcon,
  wfDescribeRows,
  wfDetailSteps,
  wfListRows,
  wfResultsRows,
  wfStatusRows,
} from "../src/lib/mgmt-card-format";

test("wfListRows caps rows and falls back to count", () => {
  const payload = {
    workflows: [
      { workflow_name: "alpha", description: "first", tags: ["gpu"] },
      { workflow_name: "beta", description: "second redo".repeat(8), tags: [] },
    ],
    count: 2,
  };
  const { rows, total } = wfListRows(payload);
  expect(rows.length).toBe(2);
  expect(rows[0][0]).toBe("alpha");
  expect(rows[0][1]).toBe("gpu");
  expect(total).toBe(2);
});

test("wfStatusRows skips empty fields", () => {
  const rows = wfStatusRows({
    execution_id: "exec-1",
    workflow_name: "example-workflow",
    status: "running",
    progress: 0.4,
  });
  const keys = rows.map((r) => r.key);
  expect(keys).toEqual(["execution", "workflow", "status", "progress"]);
  expect(rows.at(-1)?.value).toBe("40%");
});

test("wfDetailSteps reads the execution header + steps", () => {
  const payload = {
    execution: {
      execution_id: "exec-9",
      workflow_name: "alpha",
      status: "running",
      steps: [
        { name: "import", status: "succeeded", duration_ms: 2100 },
        { name: "analyze", status: "running" },
      ],
    },
  };
  const { header, steps } = wfDetailSteps(payload);
  expect(header.find((r) => r.key === "workflow")?.value).toBe("alpha");
  expect(steps[0].status).toBe("succeeded");
  expect(steps[0].duration).toBe("2s");
  expect(steps[1].status).toBe("running");
});

test("wfResultsRows keeps artifact/output counts", () => {
  const rows = wfResultsRows({
    execution_id: "exec-1",
    status: "succeeded",
    artifacts: ["data:a.csv", "report:r.md"],
    outputs: ["stdout.log"],
  });
  const map = Object.fromEntries(rows.map((r) => [r.key, r.value]));
  expect(map["artifacts"]).toBe("2 item(s)");
  expect(map["outputs"]).toBe("1 item(s)");
});

test("wfDescribeRows flattens scalars and item counts", () => {
  const rows = wfDescribeRows({
    name: "alpha",
    version: 3,
    params: { a: 1 },
    steps: [{}, {}],
  });
  const map = Object.fromEntries(rows.map((r) => [r.key, r.value]));
  expect(map["name"]).toBe("alpha");
  expect(map["version"]).toBe("3");
  expect(map["steps"]).toBe("2 item(s)");
  expect(rows.some((r) => r.key === "params")).toBe(false);
});

test("statusIcon maps states", () => {
  expect(statusIcon("succeeded")).toBe("◆");
  expect(statusIcon("running")).toBe("▶");
  expect(statusIcon("failed")).toBe("✕");
  expect(statusIcon(undefined)).toBe("?");
});
