import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { actions, workspace, setWorkspace } from "../src/stores";
import {
  letterShortcutsBlocked,
  handleInfrastructureShortcut,
  handleResearchRunDemo,
  handleResearchStartMenu,
} from "../src/lib/space-shortcuts";
import {
  createStepBlockReason,
  CREATE_STEP_NOT_DEMO,
  CREATE_STEP_FULL,
  workflowActions,
  workflowStore,
} from "../src/stores/workflow";
import { setDemoVisuals } from "../src/lib/console-mode";

describe("letterShortcutsBlocked", () => {
  beforeEach(() => {
    actions.resetWorkspace();
    actions.setChatDraft("");
    actions.closeSlashBar();
    actions.closeCommandPalette();
    actions.closeAllOverlays();
  });

  it("blocks when chat draft is non-empty", () => {
    actions.setChatDraft("hello");
    expect(letterShortcutsBlocked()).toBe(true);
  });

  it("allows when draft empty and no overlays", () => {
    actions.setChatDraft("");
    expect(letterShortcutsBlocked()).toBe(false);
  });

  it("blocks when verify input is focused", () => {
    actions.setVerifyInputFocused(true);
    expect(letterShortcutsBlocked()).toBe(true);
  });

  it("leftover verifyDraft after leave does not block Tab", async () => {
    const { cycleResearchFocus } = await import("../src/lib/research-keyboard");
    actions.setSpace("research");
    actions.setResearchFocus("graph");
    actions.setVerifyInputFocused(false);
    actions.setVerifyDraft("why materials");
    expect(letterShortcutsBlocked()).toBe(false);
    expect(cycleResearchFocus(1)).toBe(true);
    expect(workspace.researchFocus).toBe("agents");
  });

  it("notificationCenterOpen alone does not block letter shortcuts", () => {
    actions.resetWorkspace();
    actions.closeAllOverlays();
    setWorkspace("notificationCenterOpen", true);
    expect(letterShortcutsBlocked()).toBe(false);
    setWorkspace("notificationCenterOpen", false);
  });

  it("clearChatDraft clears chatDraft used by letterShortcutsBlocked", () => {
    actions.setChatDraft("stuck");
    expect(letterShortcutsBlocked()).toBe(true);
    actions.clearChatDraft();
    expect(workspace.chatDraft).toBe("");
    expect(letterShortcutsBlocked()).toBe(false);
  });

  it("focusLiveVerify does not steal keys while already typing in verify", async () => {
    const { focusLiveVerify } = await import("../src/lib/research-keyboard");
    actions.setSpace("research");
    actions.setVerifyInputFocused(true);
    expect(focusLiveVerify()).toBe(false);
    actions.setVerifyInputFocused(false);
    expect(focusLiveVerify()).toBe(true);
    expect(workspace.verifyInputFocused).toBe(true);
  });

  it("camelParamsToSnake maps projectId for KnowledgeClient.verify", async () => {
    const { camelParamsToSnake } = await import("../src/adapters/r4-cli");
    expect(camelParamsToSnake({ projectId: "demo08", question: "why?" })).toEqual({
      project_id: "demo08",
      question: "why?",
    });
  });
});

describe("space shortcuts", () => {
  beforeEach(() => {
    actions.resetWorkspace();
    actions.setChatDraft("");
    actions.setSpace("research");
  });

  it("research run demo notices when no project", () => {
    const before = workspace.chatMessages.length;
    expect(handleResearchRunDemo()).toBe(true);
    expect(workspace.chatMessages.length).toBeGreaterThan(before);
  });

  it("research start menu opens on research space", () => {
    expect(handleResearchStartMenu()).toBe(true);
    expect(workspace.startActionsMenuOpen).toBe(true);
  });

  it("infra shortcut no-ops off infrastructure space", () => {
    actions.setSpace("research");
    expect(handleInfrastructureShortcut("s")).toBe(false);
  });

  it("infra shortcut explains when endpoint already stopped", () => {
    actions.setSpace("infrastructure");
    actions.setChatDraft("");
    setWorkspace("endpoints", [
      {
        id: "ep-1",
        model: "glm",
        servedModelName: "glm",
        status: "stopped",
        url: "",
        tier: "user",
        backend: "hpc",
        host: "",
        port: 0,
        createdAt: new Date().toISOString(),
      },
    ]);
    actions.selectObject("ep-1");
    expect(handleInfrastructureShortcut("s")).toBe(true);
    expect(workspace.spaceFeedback ?? "").toMatch(/already stopped/i);
  });

  it("infra shortcut explains when cancel pressed on endpoint", () => {
    actions.setSpace("infrastructure");
    actions.setChatDraft("");
    setWorkspace("endpoints", [
      {
        id: "ep-2",
        model: "glm",
        servedModelName: "glm",
        status: "ready",
        url: "",
        tier: "user",
        backend: "hpc",
        host: "",
        port: 0,
        createdAt: new Date().toISOString(),
      },
    ]);
    setWorkspace("jobs", []);
    actions.selectObject("ep-2");
    expect(handleInfrastructureShortcut("c")).toBe(true);
    expect(workspace.spaceFeedback ?? "").toMatch(/needs a job/i);
  });

  it("infra shortcut explains when chat draft blocks letters", () => {
    actions.setSpace("infrastructure");
    // setSpace clears draft — set after
    actions.setChatDraft("stuck draft");
    expect(handleInfrastructureShortcut("l")).toBe(true);
    expect(workspace.spaceFeedback ?? "").toMatch(/chat draft/i);
  });
});

describe("createStepBlockReason", () => {
  afterEach(() => {
    workflowActions.reset();
    setDemoVisuals(false);
  });

  beforeEach(() => {
    workflowActions.reset();
    setDemoVisuals(false);
  });

  it("reports not_demo when live visuals off", () => {
    expect(createStepBlockReason()).toBe("not_demo");
    expect(CREATE_STEP_NOT_DEMO).toMatch(/Local demo only/i);
  });

  it("reports full when at max in demo", () => {
    setDemoVisuals(true);
    for (let i = 0; i < workflowStore.maxSteps; i++) {
      expect(workflowActions.createStep("ingest")).not.toBeNull();
    }
    expect(createStepBlockReason()).toBe("full");
    expect(CREATE_STEP_FULL(16)).toMatch(/full/i);
    expect(workflowActions.createStep("extract")).toBeNull();
  });
});
