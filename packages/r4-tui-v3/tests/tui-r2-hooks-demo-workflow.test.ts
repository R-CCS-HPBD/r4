/**
 * TUI audit r2 (2026-10-17), hooks lane — #50: the demo workflow builder's
 * runWorkflow advanced the step index twice per step, so the gate at an odd
 * index was skipped and the run never paused for approval. Review follow-up:
 * approving the gate never advanced past the next step (it re-marked one
 * step completed every 500 ms forever) and reset() could not cancel it.
 */
import { afterAll, beforeEach, describe, expect, it } from "bun:test";
import { workflowActions, workflowStore } from "../src/stores/workflow";
import { isDemoVisuals, setDemoVisuals } from "../src/lib/console-mode";

const savedVisuals = isDemoVisuals();
beforeEach(() => {
  setDemoVisuals(true);
  workflowActions.reset();
});
afterAll(() => {
  workflowActions.reset();
  setDemoVisuals(savedVisuals);
});

describe("demo runWorkflow", () => {
  it("runs steps in order and stops at the gate", async () => {
    workflowActions.addStepsFromTemplate({
      name: "Hypothesis Test",
      types: ["hypothesis", "gate", "experiment", "analysis"],
    });
    expect(workflowActions.assemble().valid).toBe(true);
    workflowActions.runWorkflow();
    expect(workflowStore.steps[0]!.status).toBe("running");
    await new Promise((r) => setTimeout(r, 650));
    expect(workflowStore.steps).toHaveLength(4);
    const statuses = workflowStore.steps.map((s) => s.status);
    expect(statuses[0]).toBe("completed");
    expect(statuses[1]).toBe("waiting");
    expect(workflowStore.pendingGate?.id).toBe(workflowStore.steps[1]!.id);
    expect(statuses[2]).not.toBe("running");
    expect(statuses[2]).not.toBe("completed");
    expect(workflowStore.running).toBe(false);
  });

  it("approving the gate runs the remaining steps to the end", async () => {
    workflowActions.addStepsFromTemplate({
      name: "Hypothesis Test",
      types: ["hypothesis", "gate", "experiment", "analysis"],
    });
    workflowActions.assemble();
    workflowActions.runWorkflow();
    await new Promise((r) => setTimeout(r, 650));
    expect(workflowActions.approveGate(workflowStore.steps[1]!.id)).toBe(true);
    expect(workflowStore.steps[2]!.status).toBe("running");
    await new Promise((r) => setTimeout(r, 1200));
    expect(workflowStore.steps.map((s) => s.status)).toEqual(["completed", "completed", "completed", "completed"]);
    expect(workflowStore.running).toBe(false);
    expect(workflowStore.pendingGate).toBeNull();
  });

  it("reset() cancels the continuation after an approved gate", async () => {
    workflowActions.addStepsFromTemplate({ name: "x", types: ["hypothesis", "gate", "experiment"] });
    workflowActions.assemble();
    workflowActions.runWorkflow();
    await new Promise((r) => setTimeout(r, 650));
    workflowActions.approveGate(workflowStore.steps[1]!.id);
    workflowActions.reset();
    workflowActions.addStepsFromTemplate({ name: "y", types: ["ingest"] });
    await new Promise((r) => setTimeout(r, 650));
    expect(workflowStore.steps.map((s) => s.status)).toEqual(["pending"]);
    expect(workflowStore.running).toBe(false);
  });

  it("reset() clears the steps and stops a running demo", async () => {
    workflowActions.addStepsFromTemplate({ name: "x", types: ["ingest", "extract"] });
    workflowActions.assemble();
    workflowActions.runWorkflow();
    workflowActions.reset();
    expect(workflowStore.steps).toHaveLength(0);
    await new Promise((r) => setTimeout(r, 600));
    expect(workflowStore.steps).toHaveLength(0);
    expect(workflowStore.running).toBe(false);
  });
});
