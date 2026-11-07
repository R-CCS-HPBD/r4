/**
 * TUI audit 2026-10-17 round 2 (keymap lane) — Esc arbitration through the
 * FULL <App/> (global keymap + focused renderables), not a bare component:
 * the global Esc binding used to consume every Esc (#0), so composer-local
 * Esc behaviour (clear draft, Esc Esc rewind, cursor exit, search cancel,
 * plan keep_planning) never ran; pickers and the tour could not be closed
 * (#1); pane-local inputs lost the whole pane (#8); Ctrl+G during Ctrl+R
 * opened the message cursor (#9).
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import type { CompanionBridgeClient } from "../src/bridge/jsonl-client";

type Setup = Awaited<ReturnType<typeof testRender>>;

const savedBackend = process.env.R4_TUI_BACKEND;

function reset(): void {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
}

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  reset();
});

afterEach(async () => {
  await new Promise((r) => setTimeout(r, 50));
  setCompanionBridgeForTests(null);
  reset();
  if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
  else process.env.R4_TUI_BACKEND = savedBackend;
});

async function settle(setup: Setup, ms = 60): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
  await setup.renderOnce();
}

async function mountApp(): Promise<Setup> {
  const setup = await testRender(() => <App />, { width: 160, height: 50 });
  await setup.renderOnce();
  actions.closeAllOverlays();
  await settle(setup);
  return setup;
}

async function esc(setup: Setup): Promise<void> {
  setup.mockInput.pressEscape();
  await settle(setup, 120);
}

function seedUserMessage(): void {
  setWorkspace("chatMessages", [
    ...workspace.chatMessages,
    { id: "u-r2", role: "user" as const, content: "earlier prompt", timestamp: new Date().toISOString() },
  ]);
}

describe("composer Esc reaches the composer (#0)", () => {
  it("Esc clears a typed draft", async () => {
    const setup = await mountApp();
    await setup.mockInput.typeText("hello");
    await settle(setup);
    expect(workspace.chatDraft).toBe("hello");
    await esc(setup);
    expect(workspace.chatDraft).toBe("");
    setup.renderer.destroy();
  });

  it("Esc during edit-and-resend cancels the edit, not only the draft (2026-10-19)", async () => {
    seedUserMessage();
    const setup = await mountApp();
    expect(actions.beginEditResend(workspace.chatMessages.length - 1)).toBe(true);
    await settle(setup);
    expect(workspace.editResendFromIndex).not.toBeNull();
    // The seeded original is adopted by the composer; Esc clears both the
    // draft and the marker — the marker used to survive and refuse every
    // later Enter, despite the notice's "(Esc cancels the edit)".
    await esc(setup);
    expect(workspace.chatDraft).toBe("");
    expect(workspace.editResendFromIndex).toBeNull();
    setup.renderer.destroy();
  });

  it("Esc Esc on an empty prompt opens the rewind menu", async () => {
    seedUserMessage();
    const setup = await mountApp();
    setup.mockInput.pressEscape();
    await settle(setup, 20);
    setup.mockInput.pressEscape();
    await settle(setup, 120);
    expect(workspace.rewindMenuOpen).toBe(true);
    setup.renderer.destroy();
  });

  it("with a docked unfocused pane, Esc clears the draft first and keeps the pane", async () => {
    const setup = await mountApp();
    actions.openPane("sessions");
    await settle(setup);
    await setup.mockInput.typeText("hello");
    await settle(setup);
    await esc(setup);
    expect(workspace.chatDraft).toBe("");
    expect(workspace.paneView).toBe("sessions");
    await esc(setup);
    expect(workspace.paneView).toBeNull();
    setup.renderer.destroy();
  });

  it("Esc leaves the message cursor (Ctrl+G)", async () => {
    seedUserMessage();
    const setup = await mountApp();
    setup.mockInput.pressKey("g", { ctrl: true });
    await settle(setup);
    expect(workspace.messageCursor).not.toBeNull();
    await esc(setup);
    expect(workspace.messageCursor).toBeNull();
    setup.renderer.destroy();
  });

  it("Esc cancels Ctrl+R history search", async () => {
    seedUserMessage();
    const setup = await mountApp();
    setup.mockInput.pressKey("r", { ctrl: true });
    await settle(setup);
    expect(setup.captureCharFrame()).toContain("reverse-i-search");
    await esc(setup);
    expect(setup.captureCharFrame()).not.toContain("reverse-i-search");
    setup.renderer.destroy();
  });

  it("Esc on an awaiting plan card answers keep_planning", async () => {
    const calls: Array<{ method: string; params: unknown }> = [];
    setCompanionBridgeForTests({
      ensureStarted: async () => {},
      shutdown: async () => {},
      request: async (method: string, params: unknown) => {
        calls.push({ method, params });
        return {};
      },
    } as never);
    const setup = await mountApp();
    setWorkspace("planAwaitingConfirmation", true);
    await settle(setup);
    await esc(setup);
    expect(calls.find((c) => c.method === "confirm_plan")?.params).toEqual({ action: "keep_planning" });
    setup.renderer.destroy();
  });
});

describe("Ctrl+G during Ctrl+R search cancels the search (#9)", () => {
  it("does not open the message cursor", async () => {
    seedUserMessage();
    const setup = await mountApp();
    setup.mockInput.pressKey("r", { ctrl: true });
    await settle(setup);
    setup.mockInput.pressKey("g", { ctrl: true });
    await settle(setup);
    expect(workspace.messageCursor).toBeNull();
    expect(setup.captureCharFrame()).not.toContain("reverse-i-search");
    setup.renderer.destroy();
  });
});

describe("Esc closes the pickers and the tour, one layer only (#1)", () => {
  it("venue picker over the sessions pane: picker closes, pane stays", async () => {
    const setup = await mountApp();
    actions.openPane("sessions");
    actions.openVenuePicker();
    await settle(setup);
    await esc(setup);
    expect(workspace.venuePickerOpen).toBe(false);
    expect(workspace.paneView).toBe("sessions");
    setup.renderer.destroy();
  });

  it("remote-source picker closes on Esc", async () => {
    const setup = await mountApp();
    setWorkspace("remoteSourcePickerOpen", true);
    await settle(setup);
    await esc(setup);
    expect(workspace.remoteSourcePickerOpen).toBe(false);
    setup.renderer.destroy();
  });

  it("remote-source picker: a pending push confirm takes the first Esc, the second closes", async () => {
    const requests: string[] = [];
    setCompanionBridgeForTests({
      alive: true,
      turnInFlight: false,
      shutdown: async () => {},
      request: async (method: string) => {
        requests.push(method);
        if (method !== "source.list_remote") return {};
        return {
          remote_available: true,
          rows: [{ name: "lab-llm", kind: "public", url: "https://llm.example", has_api_key: true, remote: "" }],
        };
      },
    } as unknown as CompanionBridgeClient);
    const setup = await mountApp();
    actions.openRemoteSourcePicker("start");
    await settle(setup, 150);
    setup.mockInput.pressEnter();
    await settle(setup, 120);
    expect(setup.captureCharFrame()).toContain("push its key to the platform");
    await esc(setup);
    expect(workspace.remoteSourcePickerOpen).toBe(true);
    expect(setup.captureCharFrame()).not.toContain("push its key to the platform");
    await esc(setup);
    expect(workspace.remoteSourcePickerOpen).toBe(false);
    expect(requests).not.toContain("source.push");
    setup.renderer.destroy();
  });

  it("tour over the sessions pane: tour closes, pane stays", async () => {
    const setup = await mountApp();
    actions.openPane("sessions");
    setWorkspace("tourOpen", true);
    await settle(setup);
    await esc(setup);
    expect(workspace.tourOpen).toBe(false);
    expect(workspace.paneView).toBe("sessions");
    setup.renderer.destroy();
  });
});

describe("pane-local inputs keep their pane on Esc (#8)", () => {
  it("Live verify: Esc leaves verify, the research pane stays", async () => {
    const setup = await mountApp();
    actions.openPane("research");
    setWorkspace("paneFocus", true);
    actions.setResearchFocus("verify");
    actions.setVerifyInputFocused(true);
    await settle(setup);
    await esc(setup);
    expect(workspace.paneView).toBe("research");
    expect(workspace.verifyInputFocused).toBe(false);
    setup.renderer.destroy();
  });

  it("Knowledge verify: Esc returns to the catalog, the pane stays", async () => {
    const setup = await mountApp();
    actions.openPane("knowledge");
    setWorkspace("paneFocus", true);
    actions.setKnowledgePane("verify");
    await settle(setup);
    await esc(setup);
    expect(workspace.paneView).toBe("knowledge");
    expect(workspace.knowledgePane).toBe("catalog");
    setup.renderer.destroy();
  });
});
