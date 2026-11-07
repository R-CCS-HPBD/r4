/**
 * Workflow-definition card (2026-10-01; run instructions 2026-10-02): meta,
 * how to run, parameters, the step graph, steps and registered code in one
 * scrolling body. Code rows are collapsed by default and expand only on
 * Enter/click; `r` hands the trigger line to the composer; Esc joins the
 * global overlay sweep. Rows come from the seeded store (the bridge fetch is
 * exercised against the real RPC in the r4 suites).
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { WorkflowDefOverlay } from "../src/components/workflow-def-overlay";
import { WorkRoute } from "../src/routes/work";
import { overlayCloseSweep } from "../src/keymap";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setUmrsWorkflowStore } from "../src/stores/umrs-workflow";
import { reconcile } from "solid-js/store";
import { agentActions } from "../src/stores/agent-runtime";
import { setTeamsState } from "../src/stores/teams";
import { setLoopsState } from "../src/stores/loops";

const DEF = {
  workflow_name: "vision-pipe",
  version: "1.0",
  description: "segment then stats",
  scope: "user",
  user_id: "alice",
  execution_mode: "in_process",
  resources: { gpu_count: 1, memory_limit: "8Gi", extra: { partition: "gpu-debug" } },
  parameter_schema: {
    type: "object",
    properties: {
      image: { type: "string", description: "image key" },
      bucket: { type: "string", description: "input bucket" },
    },
    required: ["bucket"],
  },
  steps: [
    { name: "segment", entrypoint: "segment:SegmentStep", config: { threshold: 0.5 }, depends_on: [] },
    { name: "stats", entrypoint: "stats:StatsStep", depends_on: ["segment"] },
  ],
  requirements: ["numpy"],
  source_path: "/srv/workflows/vision-pipe",
  files: [
    { path: "steps/segment.py", bytes: 32, lines: 2, content: "class SegmentStep:\n    pass\n", truncated: false },
  ],
  files_truncated: false,
} as never;

/** No step declares depends_on: the runner chains them in declaration order. */
const CHAIN = {
  workflow_name: "chain",
  steps: [
    { name: "fetch", entrypoint: "fetch:Fetch" },
    { name: "reduce", entrypoint: "reduce:Reduce" },
  ],
  files: [],
} as never;

beforeEach(() => {
  resetWorkspace();
  agentActions.resetAgentRuntime();
  // A store path set to {} merges; reconcile replaces (rows left by other
  // files would sit above the catalog rows the pane test presses Enter on).
  setUmrsWorkflowStore("catalog", []);
  setUmrsWorkflowStore("activeExecutions", reconcile({}));
  setUmrsWorkflowStore("definitions", reconcile({}));
  setUmrsWorkflowStore("definitionErrors", reconcile({}));
  setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
  setLoopsState({ rows: [], error: null });
  // 2026-10-06 focus model: emulate "the pane owns the keys" for the
  // route-under-pin (its trap is paneScoped; the overlay owns keys on top).
  setWorkspace("paneFocus", true);
});

afterEach(async () => {
  await new Promise((r) => setTimeout(r, 120));
  actions.setWorkflowDefId(null);
  resetWorkspace();
});

async function open(def: unknown, name: string, height: number) {
  setUmrsWorkflowStore("definitions", name, def as never);
  actions.setWorkflowDefId(name);
  const setup = await testRender(() => <WorkflowDefOverlay />, { width: 120, height });
  await setup.renderOnce();
  return setup;
}

describe("WorkflowDefOverlay", () => {
  it("renders meta, how to run, parameters, the step graph and collapsed code rows", async () => {
    const setup = await open(DEF, "vision-pipe", 80);
    const frame = setup.captureCharFrame();
    expect(frame).toContain("workflow definition — vision-pipe");
    expect(frame).toContain("about     · segment then stats");
    expect(frame).toContain("gpu_count=1");
    expect(frame).toContain("partition=gpu-debug"); // extra folded in
    // How to run: the exact line, required parameters left to fill.
    expect(frame).toContain("how to run");
    expect(frame).toContain("/workflow trigger vision-pipe --param bucket=");
    expect(frame).not.toContain("--param image=");
    // Parameters: required marker, type and description from the schema.
    expect(frame).toContain("parameters (2)");
    expect(frame).toContain("bucket*  string  — input bucket");
    expect(frame).toContain("image  string  — image key");
    // Graph: boxes and an arrow, then the step list with the exact edges.
    expect(frame).toContain("graph (2 steps)");
    expect(frame).toContain("[ segment ]");
    expect(frame).toContain("▼");
    expect(frame).toContain("[ stats ]");
    expect(frame).toContain("● segment");
    expect(frame).toContain("config · threshold=0.5");
    expect(frame).toContain("● stats  (after segment)");
    expect(frame).toContain("↳ stats:StatsStep");
    // Code collapsed by default — the row shows, the content does not.
    expect(frame).toContain("▸ steps/segment.py · 2 lines");
    expect(frame).not.toContain("class SegmentStep");
    setup.renderer.destroy();
  });

  it("scrolls as one body: the code section is below the fold until End", async () => {
    const setup = await open(DEF, "vision-pipe", 30);
    const top = setup.captureCharFrame();
    expect(top).toContain("how to run");
    expect(top).not.toContain("code (1 files)");
    setup.mockInput.pressKey("END");
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("code (1 files)");
    setup.mockInput.pressKey("HOME");
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("how to run");
    setup.renderer.destroy();
  });

  it("Enter expands the selected code file and scrolls to it, Enter again collapses", async () => {
    const setup = await open(DEF, "vision-pipe", 30);
    setup.mockInput.pressEnter();
    await setup.renderOnce();
    const opened = setup.captureCharFrame();
    expect(opened).toContain("▾ steps/segment.py");
    expect(opened).toContain("class SegmentStep");
    setup.mockInput.pressEnter();
    await setup.renderOnce();
    expect(setup.captureCharFrame()).not.toContain("class SegmentStep");
    setup.renderer.destroy();
  });

  it("r puts the trigger line in the composer and hands it the keyboard", async () => {
    actions.openPane("work");
    const setup = await open(DEF, "vision-pipe", 30);
    setup.mockInput.pressKey("r");
    await setup.renderOnce();
    expect(workspace.pendingChatDraft).toBe("/workflow trigger vision-pipe --param bucket=");
    expect(workspace.workflowDefId).toBeNull();
    expect(workspace.paneView).toBeNull(); // the composer only takes keys with the pane closed
    expect(workspace.chatMessages.at(-1)!.content).toContain('fill each value after "="');
    setup.renderer.destroy();
  });

  it("Esc closes the card only, and the work pane takes the keys again", async () => {
    setUmrsWorkflowStore("catalog", [{ workflow_name: "vision-pipe" } as never, { workflow_name: "chain" } as never]);
    setUmrsWorkflowStore("definitions", "vision-pipe", DEF);
    setUmrsWorkflowStore("definitions", "chain", CHAIN);
    actions.openPane("work");
    // 2026-10-06 focus model: openPane lands UNFOCUSED (chat-first); the
    // pin below drives the pane's key grammar, so hand it the keys the way
    // the app does — one explicit focus move in (Tab/click).
    setWorkspace("paneFocus", true);
    const setup = await testRender(() => (
      <>
        <WorkRoute />
        <WorkflowDefOverlay />
      </>
    ), { width: 120, height: 40 });
    await setup.renderOnce();
    setup.mockInput.pressEnter(); // the first catalog row
    await setup.renderOnce();
    expect(workspace.workflowDefId).toBe("vision-pipe");
    overlayCloseSweep(); // what Esc runs
    await setup.renderOnce();
    expect(workspace.workflowDefId).toBeNull();
    expect(workspace.paneView).toBe("work");
    setup.mockInput.pressArrow("down");
    await setup.renderOnce();
    setup.mockInput.pressEnter();
    await setup.renderOnce();
    expect(workspace.workflowDefId).toBe("chain");
    setup.renderer.destroy();
  });

  it("steps without depends_on draw as the declaration-order chain the runner executes", async () => {
    const setup = await open(CHAIN, "chain", 60);
    const frame = setup.captureCharFrame();
    expect(frame).toContain("graph (2 steps) · no depends_on: runs in declaration order");
    expect(frame).toContain("● reduce  (after fetch)");
    expect(frame).toContain("parameters (0)");
    expect(frame).toContain("/workflow trigger chain");
    setup.renderer.destroy();
  });

  it("backend without the route shows the honest error line", async () => {
    setUmrsWorkflowStore("definitionErrors", "vision-pipe", "404 Not Found");
    actions.setWorkflowDefId("vision-pipe");
    const setup = await testRender(() => <WorkflowDefOverlay />, { width: 120, height: 30 });
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("package unavailable: 404 Not Found");
    setup.renderer.destroy();
  });

  it("closeAllOverlays sweeps the card id", async () => {
    actions.setWorkflowDefId("vision-pipe");
    actions.closeAllOverlays();
    expect(workspace.workflowDefId).toBeNull();
  });
});
