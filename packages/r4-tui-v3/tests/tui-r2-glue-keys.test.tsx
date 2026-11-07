/**
 * TUI audit r2 (2026-10-17), glue lane — keymap/focus deferrals, driven
 * through the FULL <App/> so the global keymap is in play.
 *
 * #2/#12  Shift+R on a paused team in the Work pane resumes (r runs).
 * #15     the Infrastructure Endpoints trap keeps ↑↓ when the Models list
 *         mounts after the endpoint poll lands.
 * #8      Esc in the catalog clears the selected asset before it closes
 *         the pane.
 * #67     with a half-typed draft, Tab still enters the pane, the pane's
 *         letters work, and the draft survives.
 * #9      the composer's Ctrl+R search counts as not idle.
 * #1      every closeAllOverlays caller closes the tour.
 * #23     one project_job_event on a tab bridge posts one notice.
 */
import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { setTeamsState, teamsActions } from "../src/stores/teams";
import { scienceActions, scienceStore } from "../src/stores/science";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { resetTabsRegistryForTests, routeTabEvent } from "../src/tabs/registry";
import { claimKeys } from "../src/keymap/key-claims";
import { letterShortcutsBlocked } from "../src/lib/space-shortcuts";
import { wireChatTabsOnce } from "../src/tabs/wiring";
import { bindProjectJobEvents } from "../src/commands/project-job-events";
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

describe("work pane shifted verbs (#2/#12)", () => {
  it("Shift+R on a paused team resumes it; it never runs it", async () => {
    const calls: string[] = [];
    const saved = { run: teamsActions.run, resume: teamsActions.resume };
    (teamsActions as Record<string, unknown>).run = async (sid: string) => void calls.push(`run:${sid}`);
    (teamsActions as Record<string, unknown>).resume = async (sid: string) => void calls.push(`resume:${sid}`);
    try {
      const setup = await mountApp();
      actions.openPane("work");
      setWorkspace("paneFocus", true);
      await settle(setup, 120);
      // Only the team row: the offline fixtures' endpoints/jobs come first.
      setWorkspace("endpoints", []);
      setWorkspace("jobs", []);
      setWorkspace("workScopeAll", true); // the row carries no project
      setTeamsState("sessions", [{ session_id: "team-9", name: "crew", status: "paused", iteration: 2 } as never]);
      await settle(setup, 120);
      expect(setup.captureCharFrame()).toContain("crew");
      setup.mockInput.pressKey("R");
      await settle(setup);
      expect(calls).toEqual(["resume:team-9"]);
      setup.renderer.destroy();
    } finally {
      Object.assign(teamsActions, saved);
    }
  });
});

describe("infrastructure has one key owner (#15)", () => {
  it("↓ moves the endpoint selection and PgDn pages the models list", async () => {
    const setup = await mountApp(320); // wide enough for the Models column's names
    setWorkspace("jobs", []);
    const many = Array.from({ length: 20 }, (_, i) => endpoint(`ep-${i}`, `model-${String(i).padStart(2, "0")}`));
    setWorkspace("endpoints", many);
    actions.openPane("infrastructure");
    setWorkspace("paneFocus", true);
    await settle(setup, 120);
    expect(workspace.selectedObjectId).toBe("ep-0");
    setup.mockInput.pressArrow("down");
    await settle(setup);
    expect(workspace.selectedObjectId).toBe("ep-1");
    // The models list (14 of 21 rows shown) was unreachable: its own trap
    // lost focus to this one. PgDn now pages it from the pane's trap. (The
    // Endpoints list names model-19 once; the Models list adds the second.)
    const shown = () => setup.captureCharFrame().split("model-19").length - 1;
    const before = shown();
    const PAGE_DOWN = "\u001B[6~"; // not in the mock's KeyCodes table
    setup.mockInput.pressKey(PAGE_DOWN);
    setup.mockInput.pressKey(PAGE_DOWN);
    await settle(setup);
    expect(shown()).toBe(before + 1);
    expect(workspace.selectedObjectId).toBe("ep-1");
    setup.renderer.destroy();
  });
});

describe("catalog Esc order (#8)", () => {
  it("Esc clears the selected asset first; the next Esc closes the pane", async () => {
    const setup = await mountApp();
    actions.openPane("catalog");
    setWorkspace("paneFocus", true);
    await settle(setup, 120);
    scienceActions.selectAsset({ s3Key: "k/a.txt", name: "a.txt" } as never);
    await settle(setup);
    setup.mockInput.pressEscape();
    await settle(setup);
    expect(scienceStore.selectedAsset).toBeNull();
    expect(workspace.paneView).toBe("catalog");
    setup.mockInput.pressEscape();
    await settle(setup);
    expect(workspace.paneView).toBeNull();
    setup.renderer.destroy();
  });
});

describe("a draft does not trap the keys in the composer (#67)", () => {
  it("Tab enters the pane, its letters act, and the draft survives", async () => {
    const setup = await mountApp();
    await setup.mockInput.typeText("half typed");
    await setup.renderOnce();
    expect(workspace.chatDraft).toBe("half typed");
    setWorkspace("endpoints", [endpoint("ep-1", "m-one")]);
    actions.openPane("infrastructure");
    await settle(setup, 120);
    setup.mockInput.pressTab();
    await settle(setup);
    expect(workspace.paneFocus).toBe(true);
    actions.selectObject("ep-1");
    setup.mockInput.pressKey("s");
    await settle(setup);
    expect(workspace.spaceFeedback ?? "").toContain("Stopping endpoint");
    expect(workspace.chatDraft).toBe("half typed");
    setup.renderer.destroy();
  });
});

describe("Ctrl+R search is not idle (#9)", () => {
  it("letterShortcutsBlocked holds while the composer search is active", () => {
    let active = true;
    const unregister = claimKeys("composer-search", ["letters", "tab", "ctrl+g"], () => active);
    try {
      expect(workspace.chatDraft ?? "").toBe("");
      expect(letterShortcutsBlocked()).toBe(true);
      active = false;
      expect(letterShortcutsBlocked()).toBe(false);
    } finally {
      unregister();
    }
  });
});

describe("closeAllOverlays closes the tour (#1)", () => {
  it("tourOpen is cleared by every sweep caller", () => {
    actions.openTour();
    expect(workspace.tourOpen).toBe(true);
    actions.closeAllOverlays();
    expect(workspace.tourOpen).toBe(false);
  });
});

describe("project job events post once (#23)", () => {
  it("one started event on a tab bridge gives one notice", () => {
    wireChatTabsOnce();
    const tabId = workspace.activeChatTabId;
    const bridge = new EventEmitter();
    bridge.on("project_job_event", (payload: unknown) => routeTabEvent(tabId, "project_job_event", payload));
    bindProjectJobEvents(bridge as never, { withExitHandler: false });
    bridge.emit("project_job_event", { jobId: "job-r2-23", operation: "init", phase: "started", message: "init started" });
    const notices = workspace.chatMessages.filter((m) => m.content === "[project init] init started");
    expect(notices).toHaveLength(1);
  });
});
