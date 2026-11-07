/**
 * Workflow-execution mission overlay pins (2026-09-27): steps from the real
 * record, slurm cost = measured truth only (ADR-067 never-approximate),
 * unmeasured steps counted separately, absent steps honest.
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { WorkflowExecOverlay, mapExecutionSteps, costSummary } from "../src/components/workflow-exec-overlay";
import { actions, resetWorkspace } from "../src/stores";
import { umrsWorkflowStore, setUmrsWorkflowStore } from "../src/stores/umrs-workflow";

describe("mapExecutionSteps / costSummary", () => {
  it("reads the map form with extra.slurm stamps", () => {
    const rows = mapExecutionSteps({
      results: {
        steps: {
          prep: { status: "completed", extra: { slurm: { job_id: "51", elapsed_seconds: 61, gpus: 1, gpu_seconds: 61 } } },
          train: { status: "running" },
        },
      },
    });
    expect(rows.length).toBe(2);
    expect(rows[0]).toMatchObject({ name: "prep", status: "completed", gpuSeconds: 61, jobId: "51" });
    expect(rows[1]).toMatchObject({ name: "train", status: "running" });
    expect(rows[1].gpuSeconds).toBeUndefined(); // honest absence
  });

  it("reads the list form and clips to 32", () => {
    const steps = Array.from({ length: 40 }, (_, i) => ({ name: `s${i}`, status: "completed" }));
    const rows = mapExecutionSteps({ results: { steps } });
    expect(rows.length).toBe(32);
  });

  it("cost sums only measured rows and counts unmeasured completions", () => {
    const cost = costSummary([
      { name: "a", status: "completed", gpuSeconds: 10.5 },
      { name: "b", status: "completed" },
      { name: "c", status: "running" },
    ]);
    expect(cost.measuredGpuSeconds).toBe(10.5);
    expect(cost.unmeasured).toBe(1); // running is not unmeasured-completed
  });

  it("absent steps stay empty — no fabricated rows", () => {
    expect(mapExecutionSteps({})).toEqual([]);
    expect(mapExecutionSteps({ results: {} })).toEqual([]);
  });
});

describe("WorkflowExecOverlay render", () => {
  beforeEach(() => {
    resetWorkspace();
    setUmrsWorkflowStore("activeExecutions", {});
  });
  afterEach(async () => {
    await new Promise((r) => setTimeout(r, 120));
    actions.setExecDetailId(null);
    resetWorkspace();
    setUmrsWorkflowStore("activeExecutions", {});
    setUmrsWorkflowStore("definitions", {});
  });

  it("closed without an id; renders steps + cost when open", async () => {
    setUmrsWorkflowStore("activeExecutions", {
      "exec-1": {
        execution_id: "exec-1",
        workflow_name: "defect-ai",
        status: "running",
        progress: "2/3",
        results: {
          steps: {
            prep: { status: "completed", extra: { slurm: { job_id: "9", elapsed_seconds: 30, gpus: 2, gpu_seconds: 60 } } },
            train: { status: "running" },
          },
        },
      } as never,
    });
    actions.setExecDetailId("exec-1");
    const setup = await testRender(() => <WorkflowExecOverlay />, { width: 90, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("defect-ai");
    expect(frame).toContain("prep · completed");
    expect(frame).toContain("train · running");
    expect(frame).toContain("gpu 60.0s");
    setup.renderer.destroy();
  });

  it("execution without results shows the honest empty note", async () => {
    setUmrsWorkflowStore("activeExecutions", {
      "exec-2": { execution_id: "exec-2", workflow_name: "w", status: "queued" } as never,
    });
    actions.setExecDetailId("exec-2");
    const setup = await testRender(() => <WorkflowExecOverlay />, { width: 90, height: 30 });
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("no step rows on this execution yet");
    setup.renderer.destroy();
  });

  it("live DAG: definition topology drawn with glyph-per-node status + counts", async () => {
    setUmrsWorkflowStore("activeExecutions", {
      "exec-9": {
        execution_id: "exec-9",
        workflow_name: "pipe",
        status: "running",
        results: { steps: { collect: { status: "completed" }, review: { status: "running" } } },
      } as never,
    });
    setUmrsWorkflowStore("definitions", "pipe", {
      workflow_name: "pipe",
      steps: [
        { name: "collect" },
        { name: "review", depends_on: ["collect"] },
        { name: "merge", depends_on: ["review"] },
      ],
    } as never);
    actions.setExecDetailId("exec-9");
    const setup = await testRender(() => <WorkflowExecOverlay />, { width: 90, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("graph (3 steps · ✔ 1 ◐ 1 ○ 1)");
    expect(frame).toContain("[ ✔ collect ]");
    expect(frame).toContain("[ ◐ review ]");
    expect(frame).toContain("[ ○ merge ]"); // not yet reported → pending, never fabricated
    expect(frame).toContain("▼");
    expect(frame).toContain("review · running"); // step detail rows still land
    setup.renderer.destroy();
  });

  it("live DAG without a cached definition: linear chain, honestly labeled", async () => {
    setUmrsWorkflowStore("activeExecutions", {
      "exec-10": {
        execution_id: "exec-10",
        workflow_name: "plain",
        status: "running",
        results: { steps: { prep: { status: "completed" }, train: { status: "running" } } },
      } as never,
    });
    actions.setExecDetailId("exec-10");
    const setup = await testRender(() => <WorkflowExecOverlay />, { width: 90, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("graph (2 steps · ✔ 1 ◐ 1 · linear (no definition))");
    expect(frame).toContain("[ ✔ prep ]");
    expect(frame).toContain("[ ◐ train ]");
    setup.renderer.destroy();
  });
});
