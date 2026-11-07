/**
 * Infrastructure pane Tab (TUI audit round 3, 2026-10-17): the global Tab
 * (pane focus toggle) took the key the pane's "Tab switches lists" handler
 * wants, so the Jobs list (c/r/l on jobs) was not keyboard-reachable. With
 * the pane focused, Tab now switches Endpoints <-> Jobs; Esc or a click
 * still leave the pane, as in the research pane.
 */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { setTeamsState } from "../src/stores/teams";
import { scienceActions } from "../src/stores/science";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import type { UiEndpoint } from "../src/types";

type Setup = Awaited<ReturnType<typeof testRender>>;

const savedBackend = process.env.R4_TUI_BACKEND;

function reset(): void {
  stopBackendPoller();
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  resetTabsRegistryForTests();
  setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
  actions.closeAllOverlays();
}

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  reset();
});

afterEach(async () => {
  await new Promise((r) => setTimeout(r, 50));
  scienceActions.reset();
  reset();
  if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
  else process.env.R4_TUI_BACKEND = savedBackend;
});

async function settle(setup: Setup, ms = 60): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
  await setup.renderOnce();
}

async function mountApp(width = 160): Promise<Setup> {
  const setup = await testRender(() => <App />, { width, height: 50 });
  await setup.renderOnce();
  actions.closeAllOverlays();
  await settle(setup);
  return setup;
}

function endpoint(id: string, model: string): UiEndpoint {
  return {
    id,
    model,
    servedModelName: model,
    sourceName: "src-a",
    status: "ready",
    url: "http://x",
    tier: "project",
    backend: "vllm",
    host: "h",
    port: 1,
    createdAt: new Date().toISOString(),
  };
}

describe("infrastructure Tab switches lists", () => {
  it("first Tab enters the pane, the next Tabs switch Endpoints <-> Jobs", async () => {
    const setup = await mountApp();
    setWorkspace("endpoints", [endpoint("ep-1", "m-one")]);
    setWorkspace("jobs", [{ id: "job-1", name: "train", status: "running" } as never]);
    actions.openPane("infrastructure");
    await settle(setup, 120);
    expect(workspace.selectedObjectId).toBe("ep-1");
    setup.mockInput.pressTab();
    await settle(setup);
    expect(workspace.paneFocus).toBe(true);
    setup.mockInput.pressTab();
    await settle(setup);
    expect(workspace.paneFocus).toBe(true);
    expect(workspace.selectedObjectId).toBe("job-1");
    setup.mockInput.pressTab();
    await settle(setup);
    expect(workspace.selectedObjectId).toBe("ep-1");
    setup.renderer.destroy();
  });

  it("an unfocused pane still lets Tab move focus into it", async () => {
    const setup = await mountApp();
    setWorkspace("endpoints", [endpoint("ep-1", "m-one")]);
    actions.openPane("infrastructure");
    await settle(setup, 120);
    expect(workspace.paneFocus).toBe(false);
    setup.mockInput.pressTab();
    await settle(setup);
    expect(workspace.paneFocus).toBe(true);
    expect(workspace.selectedObjectId).toBe("ep-1");
    setup.renderer.destroy();
  });
});
