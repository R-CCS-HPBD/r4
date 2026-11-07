/**
 * Ask-user overlay UI regression pins.
 *
 * Bug (report 2026-09-12): the card had no height/flexShrink, so the
 * transcript column shrank it and overflow="hidden" clipped the draft row and
 * footer below the card boundary. Keypresses DID reach the trap — but the
 * typed answer never rendered, so the panel looked like it ignored input.
 * These pins assert the draft row + footer are visible and typing renders.
 */

import { describe, it, expect, beforeEach, afterEach, spyOn } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { resetWorkspace, actions, workspace } from "../src/stores";
import { closeRemoteAskUserElsewhere } from "../src/bridge/ask-user-bridge";
import { getCompanionBridge } from "../src/bridge/jsonl-client";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

afterEach(async () => {
  await new Promise((resolve) => setTimeout(resolve, 200));
  stopBackendPoller();
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("AskUserOverlay UI", () => {
  it("shows the draft row + footer hint, and renders typed free text", async () => {
    const setup = await testRender(() => <App forceOffline={true} />, {
      width: 160,
      height: 60,
    });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();

    actions.openAskUser({
      id: 42,
      question: "Which dataset should I use?",
      options: [" Dataset A ", "Dataset B"],
    });
    await setup.renderOnce();

    let frame = setup.captureCharFrame();
    // Draft row affordance and footer must be visible (clipped pre-fix).
    expect(frame).toContain("type a custom answer");
    expect(frame).toContain("Enter = highlighted option");
    expect(frame).toContain("Esc cancel");

    await setup.mockInput.typeText("custom answer");
    await setup.renderOnce();
    frame = setup.captureCharFrame();
    expect(frame).toContain("custom answer");

    setup.renderer.destroy();
  });

  it("shows an empty draft row with cursor when there are no options", async () => {
    const setup = await testRender(() => <App forceOffline={true} />, {
      width: 160,
      height: 60,
    });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();

    actions.openAskUser({ id: 43, question: "Free-form?", options: [] });
    await setup.renderOnce();

    const frame = setup.captureCharFrame();
    expect(frame).toContain("Enter submit");
    expect(frame).toContain("█");

    setup.renderer.destroy();
  });

  it("Enter submits and closes the overlay after typing", async () => {
    const setup = await testRender(() => <App forceOffline={true} />, {
      width: 160,
      height: 60,
    });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();

    actions.openAskUser({ id: 44, question: "Continue?", options: ["yes", "no"] });
    await setup.renderOnce();
    await setup.mockInput.typeText("typed");
    await setup.renderOnce();
    setup.mockInput.pressEnter();
    await setup.renderOnce();

    expect(workspace.askUser).toBeNull();

    setup.renderer.destroy();
  });

  it("number key jump-selects an option and closes", async () => {
    const setup = await testRender(() => <App forceOffline={true} />, {
      width: 160,
      height: 60,
    });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();

    actions.openAskUser({ id: 45, question: "Pick", options: ["a", "b", "c"] });
    await setup.renderOnce();
    setup.mockInput.pressKey("2");
    await setup.renderOnce();

    expect(workspace.askUser).toBeNull();

    setup.renderer.destroy();
  });

  // Bug (stabilization sweep 2026-10-21, static-sweep#0): the number-key
  // jump-submit ran before the free-text insert and ignored the draft, so
  // typing "use 2 GPUs" sent option 2 the moment "2" was typed.
  it("treats a digit as text once a free-text draft is non-empty", async () => {
    const setup = await testRender(() => <App forceOffline={true} />, {
      width: 160,
      height: 60,
    });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();

    const sent: Array<string | null> = [];
    const bridgeMod = await import("../src/bridge/ask-user-bridge");
    const spy = spyOn(bridgeMod, "respondAskUser").mockImplementation((answer: string | null) => {
      sent.push(answer);
    });
    try {
      actions.openAskUser({ id: 48, question: "How many GPUs?", options: ["1 GPU", "4 GPUs"] });
      await setup.renderOnce();
      await setup.mockInput.typeText("use 2 GPUs");
      await setup.renderOnce();
      expect(sent).toEqual([]);
      expect(workspace.askUser).not.toBeNull();
      expect(setup.captureCharFrame()).toContain("use 2 GPUs");
      setup.mockInput.pressEnter();
      await setup.renderOnce();
      expect(sent).toEqual(["use 2 GPUs"]);
    } finally {
      spy.mockRestore();
      setup.renderer.destroy();
    }
  });

  // Bug (report 2026-10-20): once the draft grew past the window budget,
  // truncate() rewrote the row end and the LAST typed character + the cursor
  // block both disappeared, so typing felt broken. Pin: the draft row always
  // ends with "last char + █", whatever window the text scrolled through.
  it("keeps the last typed character and the cursor visible past the window", async () => {
    const setup = await testRender(() => <App forceOffline={true} />, {
      width: 160,
      height: 60,
    });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();

    actions.openAskUser({ id: 46, question: "Long answer?", options: [] });
    await setup.renderOnce();

    await setup.mockInput.typeText(`${"x".repeat(90)}TAIL-HERE`);
    await setup.renderOnce();

    const frame = setup.captureCharFrame();
    const draftRow = frame.split("\n").find((r) => r.includes("›"));
    expect(draftRow).toBeDefined();
    expect(draftRow).toContain("TAIL-HERE█");

    setup.renderer.destroy();
  });

  it("keeps the last typed character and the cursor visible on a narrow terminal", async () => {
    const setup = await testRender(() => <App forceOffline={true} />, {
      width: 80,
      height: 40,
    });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();

    actions.openAskUser({ id: 47, question: "Long answer?", options: [] });
    await setup.renderOnce();

    await setup.mockInput.typeText(`${"x".repeat(90)}TAIL-HERE`);
    await setup.renderOnce();

    const frame = setup.captureCharFrame();
    const draftRow = frame.split("\n").find((r) => r.includes("›"));
    expect(draftRow).toBeDefined();
    expect(draftRow).toContain("TAIL-HERE█");

    setup.renderer.destroy();
  });

  // RS-1 round-2 finding 5: a remote question closed under the card
  // (answered elsewhere, expired, the session stopped) used to drop the text.
  async function typedThenClosed(id: string | number, closeBy: "elsewhere" | "store" | "esc"): Promise<void> {
    const setup = await testRender(() => <App forceOffline={true} />, { width: 160, height: 60 });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();
    actions.openAskUser({ id, question: "Which dataset?", options: [] });
    await setup.renderOnce();
    await setup.mockInput.typeText("half typed");
    await setup.renderOnce();
    if (closeBy === "elsewhere") closeRemoteAskUserElsewhere();
    else if (closeBy === "store") actions.closeAskUser();
    else {
      setup.mockInput.pressEscape();
      await new Promise((resolve) => setTimeout(resolve, 100)); // the lone-Esc timeout
    }
    await setup.renderOnce();
    expect(workspace.askUser).toBeNull();
    setup.renderer.destroy();
  }

  it("keeps the typed answer in the composer when a remote question closes under it", async () => {
    const seq = workspace.pendingChatDraftSeq;
    await typedThenClosed("remote:q-1", "elsewhere");
    expect(workspace.pendingChatDraft).toBe("half typed");
    expect(workspace.pendingChatDraftSeq).toBe(seq + 1);
  });

  it("R3-N3: leaves a non-empty composer draft alone and names the answer in a notice", async () => {
    actions.setChatDraft("my next prompt");
    try {
      const seq = workspace.pendingChatDraftSeq;
      const before = workspace.chatMessages.length;
      await typedThenClosed("remote:q-9", "elsewhere");
      expect(workspace.pendingChatDraftSeq).toBe(seq);
      const added = workspace.chatMessages.slice(before).map((m) => String(m.content ?? ""));
      expect(added.some((text) => text.includes("half typed"))).toBe(true);
    } finally {
      actions.setChatDraft("");
    }
  });

  it("does not move the draft when the user closes the card or for a local ask", async () => {
    const seq = workspace.pendingChatDraftSeq;
    await typedThenClosed("remote:q-2", "esc");
    await typedThenClosed("remote:q-3", "store");
    await typedThenClosed(48, "store");
    expect(workspace.pendingChatDraftSeq).toBe(seq);
  });

  it("Esc on a remote question sets it aside and sends no stdin answer", async () => {
    const stdin: unknown[] = [];
    const bridge = getCompanionBridge();
    const spy = spyOn(bridge, "respondAskUser").mockImplementation((...args: unknown[]) => {
      stdin.push(args);
    });
    try {
      const before = workspace.chatMessages.length;
      await typedThenClosed("remote:q-4", "esc");
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(stdin).toEqual([]);
      const added = workspace.chatMessages.slice(before).map((m) => String(m.content ?? ""));
      expect(added.some((text) => text.includes("remote question left pending"))).toBe(true);
    } finally {
      spy.mockRestore();
    }
  });
});
