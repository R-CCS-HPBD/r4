/**
 * Research Workspace UI regression test.
 *
 * Guards against the OpenTUI "TextNodeRenderable only accepts strings" crash,
 * which occurred when raw numbers were passed as <text> children (e.g. counts,
 * progress, budget, iteration values from the seeded agent runtime).
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { resetWorkspace, actions } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { knowledgeActions } from "../src/stores/knowledge";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
  knowledgeActions.setLoop(null);
});

describe("Research Workspace UI", () => {
  it("renders the workspace without a TextNodeRenderable error", async () => {
    const setup = await testRender(() => <App />, { width: 160, height: 60 });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();

    actions.setSpace("research");
    actions.openPane("research"); // V4: research lives in the detail pane
    await setup.renderOnce();
    // Let the seeded runtime settle across a couple of frames.
    await new Promise((resolve) => setTimeout(resolve, 50));
    await setup.renderOnce();

    const frame = setup.captureCharFrame();
    expect(frame).not.toContain("Research error");
    expect(frame).not.toContain("TextNodeRenderable");
    expect(frame).toContain("Mission Control");
    expect(frame).not.toContain("Start: [A][R demo][C]");
    expect(frame).not.toContain("[g,1]");
    // Live (no demo visuals): no Run demo button chrome
    expect(frame).not.toContain("[R]un demo");
    expect(frame).toContain("Ops");
    expect(frame).toContain("Strategy");
    expect(frame).toContain("Explain");
    setup.renderer.destroy();
  });

  it("states the loop kind when a live RC1 loop is running (sim vs real)", async () => {
    const setup = await testRender(() => <App />, { width: 160, height: 60 });
    await setup.renderOnce();
    actions.closeAllOverlays();
    actions.setSpace("research");
    actions.openPane("research"); // V4: research lives in the detail pane
    // The loop panel lives under the research "Strategy" bottom tab.
    actions.setResearchBottomTab("strategy");
    await setup.renderOnce();

    knowledgeActions.setLoop({
      loopId: "loop-test-1",
      projectId: "proj-catalyst-x",
      researchGoal: "goal",
      status: "running",
      phase: "experimenting",
      currentIteration: 2,
      maxIterations: 5,
      totalHypothesesTested: 1,
      totalExperimentsRun: 1,
      totalFindings: 0,
      simulated: true,
      iterations: [],
    });
    await setup.renderOnce();

    const frame = setup.captureCharFrame();
    expect(frame).not.toContain("TextNodeRenderable");
    // Loop-kind transparency: simulated RC1 loop must not pose as real science.
    // Flag is front-loaded so it survives narrow panes (V4).
    expect(frame).toContain("[sim]");
    setup.renderer.destroy();

    // Real-mode phrasing is tested at the pure-function level (same string
    // builder) — the panel branch is selected by `simulated` only.
  });
});

