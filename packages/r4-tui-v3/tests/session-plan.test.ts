import { describe, expect, test } from "bun:test";
import {
  planEntities,
  planStepDepths,
  workflowNodesFromCalls,
} from "../src/lib/session-plan";
import type { SessionPlanStep } from "../src/types";
import type { ActivityLine } from "../src/lib/tool-activity";

function step(id: string, status: string, depends_on: string[] = []): SessionPlanStep {
  return { id, title: `t-${id}`, status, progress: 0, depends_on };
}

function call(partial: Partial<ActivityLine>): ActivityLine {
  return { status: "ok", text: "tool", ...partial };
}

describe("planStepDepths", () => {
  test("linear chain → increasing depths in order", () => {
    const steps = [step("a", "done"), step("b", "active", ["a"]), step("c", "pending", ["b"])];
    const depths = planStepDepths(steps);
    expect(depths.get("a")).toBe(0);
    expect(depths.get("b")).toBe(1);
    expect(depths.get("c")).toBe(2);
  });

  test("diamond dependency takes the longest path", () => {
    const steps = [
      step("root", "done"),
      step("left", "done", ["root"]),
      step("right", "done", ["root"]),
      step("join", "pending", ["left", "right"]),
    ];
    const depths = planStepDepths(steps);
    expect(depths.get("join")).toBe(2);
  });

  test("unknown dependency id doesn't raise depth", () => {
    const depths = planStepDepths([step("x", "pending", ["ghost"])]);
    expect(depths.get("x")).toBe(0);
  });

  test("cycle terminates at depth 0 for unreachable roots", () => {
    const steps = [step("p", "pending", ["q"]), step("q", "pending", ["p"])];
    const depths = planStepDepths(steps);
    expect(depths.get("p")).toBe(0);
    expect(depths.get("q")).toBe(0);
  });
});

describe("workflowNodesFromCalls", () => {
  const runResult = JSON.stringify({
    execution_id: "exec-abc123",
    workflow_name: "qmd-recheck",
    status: "running",
  });

  test("workflow_run result becomes a workflow plan node", () => {
    const nodes = workflowNodesFromCalls([
      call({ name: "workflow_run", resultText: runResult }),
    ]);
    expect(nodes).toHaveLength(1);
    expect(nodes[0]).toEqual({
      kind: "workflow",
      id: "exec-abc123",
      label: "qmd-recheck",
      status: "running",
    });
  });

  test("same execution dedupes across rerun rows", () => {
    const nodes = workflowNodesFromCalls([
      call({ name: "workflow_run", resultText: runResult }),
      call({ name: "workflow_status", resultText: runResult }), // status polls are not launches
      call({ name: "workflow_run", resultText: runResult }),
    ]);
    expect(nodes).toHaveLength(1);
  });

  test("non-workflow tools and junk JSON are excluded", () => {
    const nodes = workflowNodesFromCalls([
      call({ name: "shell_exec", resultText: "not json" }),
      call({ name: "workflow_run", resultText: "Error: policy denied" }),
    ]);
    expect(nodes).toEqual([]);
  });
});

describe("planEntities", () => {
  test("steps first, workflow nodes appended", () => {
    const entities = planEntities(
      [step("a", "pending")],
      [
        call({
          name: "workflow_run",
          resultText: JSON.stringify({ execution_id: "e1", workflow_name: "wf" }),
        }),
      ],
    );
    expect(entities).toHaveLength(2);
    expect(entities[0]!.kind).toBe("step");
    expect(entities[1]!.kind).toBe("workflow");
  });
});
