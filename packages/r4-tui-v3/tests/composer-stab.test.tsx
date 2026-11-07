/**
 * Composer stabilization lane (2026-10-21): the keystroke-level findings
 * composer#1/#3/#4/#5/#6/#7/#9/#12, static-sweep#1 and panes-overlays#1,
 * driven through the real ChatPanel. The rule behind most of them: the
 * user sends exactly what they see, and nothing typed or pasted is lost.
 * Runs against a temp R4_CONFIG_HOME (prompt history), never the real config.
 */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { testRender } from "@opentui/solid";
import { createMockMouse } from "@opentui/core/testing";

import { ChatPanel } from "../src/components/chat";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { appendPromptHistory } from "../src/lib/prompt-history";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";

const A = "AAA-1\nAAA-2\nAAA-3";
const B = "BBB-1\nBBB-2\nBBB-3\nBBB-4";

const savedHome = process.env.R4_CONFIG_HOME;
let home = "";

beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), "r4-composer-stab-"));
  process.env.R4_CONFIG_HOME = home;
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  resetWorkspace();
  actions.closeAllOverlays();
});

afterEach(() => {
  resetWorkspace();
  if (savedHome === undefined) delete process.env.R4_CONFIG_HOME;
  else process.env.R4_CONFIG_HOME = savedHome;
  rmSync(home, { recursive: true, force: true });
});

type Setup = Awaited<ReturnType<typeof testRender>>;
const tick = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));

async function mount(): Promise<Setup> {
  const setup = await testRender(() => <ChatPanel />, { width: 120, height: 30 });
  await setup.renderOnce();
  return setup;
}

async function settle(setup: Setup): Promise<void> {
  await tick();
  await setup.renderOnce();
  await setup.renderOnce();
}

async function press(setup: Setup, fn: () => void): Promise<void> {
  fn();
  await settle(setup);
}

const userRows = () => workspace.chatMessages.filter((m) => m.role === "user").map((m) => m.content);
const lastSent = () => userRows().at(-1);

async function enter(setup: Setup): Promise<void> {
  await press(setup, () => setup.mockInput.pressEnter());
}

describe("paste chips expand by identity (composer#1)", () => {
  it("a paste cleared with Esc never replaces the next paste", async () => {
    const setup = await mount();
    await setup.mockInput.pasteBracketedText(A);
    await settle(setup);
    await press(setup, () => setup.mockInput.pressEscape());
    await setup.mockInput.pasteBracketedText(B);
    await settle(setup);
    await enter(setup);
    setup.renderer.destroy();
    expect(lastSent()).toBe(B);
  });

  it("a paste cleared with Ctrl+C (pending-draft clear) never replaces the next paste", async () => {
    const setup = await mount();
    await setup.mockInput.pasteBracketedText(A);
    await settle(setup);
    actions.setPendingChatDraft(""); // what keymap Ctrl+C "clear" does
    await settle(setup);
    await setup.mockInput.pasteBracketedText(B);
    await settle(setup);
    await enter(setup);
    setup.renderer.destroy();
    expect(lastSent()).toBe(B);
  });
});

describe("paste chips are never sent or kept as bare placeholders (composer#5)", () => {
  it("one Backspace removes the whole chip", async () => {
    const setup = await mount();
    await setup.mockInput.typeText("see ");
    await setup.mockInput.pasteBracketedText(B);
    await settle(setup);
    await press(setup, () => setup.mockInput.pressBackspace());
    expect(workspace.chatDraft).toBe("see ");
    expect(setup.captureCharFrame()).not.toContain("Pasted text");
    await setup.mockInput.typeText("x");
    await settle(setup);
    await enter(setup);
    setup.renderer.destroy();
    expect(lastSent()).toBe("see x");
  });

  it("a Ctrl+S stash keeps its paste across an unrelated send", async () => {
    const setup = await mount();
    await setup.mockInput.pasteBracketedText(A);
    await settle(setup);
    await press(setup, () => setup.mockInput.pressKey("s", { ctrl: true }));
    await setup.mockInput.typeText("unrelated");
    await settle(setup);
    await enter(setup);
    await press(setup, () => setup.mockInput.pressKey("s", { ctrl: true }));
    await enter(setup);
    setup.renderer.destroy();
    expect(userRows()).toEqual(["unrelated", A]);
  });

  it("Esc keeps the pasted text in history, and ↑ recalls it", async () => {
    const setup = await mount();
    await setup.mockInput.pasteBracketedText(A);
    await settle(setup);
    await press(setup, () => setup.mockInput.pressEscape());
    await press(setup, () => setup.mockInput.pressArrow("up"));
    expect(workspace.chatDraft).toBe(A);
    await enter(setup);
    setup.renderer.destroy();
    expect(lastSent()).toBe(A);
  });
});

describe("pastes land at the caret with their line breaks (composer#4, composer#7)", () => {
  it("a multi-line paste is inserted at the caret, not appended", async () => {
    const setup = await mount();
    await setup.mockInput.typeText("hello world");
    for (let i = 0; i < 5; i += 1) setup.mockInput.pressArrow("left");
    await settle(setup);
    await setup.mockInput.pasteBracketedText("P1\nP2\nP3");
    await settle(setup);
    await enter(setup);
    setup.renderer.destroy();
    expect(lastSent()).toBe("hello P1\nP2\nP3world");
  });

  it("a CR-separated paste (xterm/VTE) keeps its line breaks", async () => {
    const setup = await mount();
    await setup.mockInput.pasteBracketedText("l1\rl2\rl3");
    await settle(setup);
    await enter(setup);
    setup.renderer.destroy();
    expect(lastSent()).toBe("l1\nl2\nl3");
  });
});

describe("history navigation keeps the in-progress draft (composer#3, composer#9)", () => {
  it("↑ then ↓ gives the typed draft back; the first ↑ after a send recalls it", async () => {
    const setup = await mount();
    await setup.mockInput.typeText("older prompt");
    await settle(setup);
    await enter(setup);
    await setup.mockInput.typeText("half written idea");
    await settle(setup);
    await press(setup, () => setup.mockInput.pressArrow("up"));
    expect(workspace.chatDraft).toBe("older prompt");
    await press(setup, () => setup.mockInput.pressArrow("down"));
    expect(workspace.chatDraft).toBe("half written idea");
    await press(setup, () => setup.mockInput.pressArrow("down"));
    expect(workspace.chatDraft).toBe("half written idea");
    setup.renderer.destroy();
  });

  it("a multi-line draft survives a trip through history", async () => {
    const setup = await mount();
    await setup.mockInput.typeText("older prompt");
    await enter(setup);
    await setup.mockInput.typeText("line A");
    await press(setup, () => setup.mockInput.pressKey("j", { ctrl: true }));
    await setup.mockInput.typeText("line B");
    await settle(setup);
    await press(setup, () => setup.mockInput.pressArrow("up"));
    await press(setup, () => setup.mockInput.pressArrow("down"));
    setup.renderer.destroy();
    expect(workspace.chatDraft).toBe("line A\nline B");
  });

  it("a prompt the splash wrote to history is recalled once the splash closes", async () => {
    setWorkspace("splashOpen", true);
    const setup = await mount();
    appendPromptHistory("asked on the splash"); // what the splash submit records
    actions.closeSplash();
    await settle(setup);
    await setup.mockInput.typeText("draft");
    await settle(setup);
    await press(setup, () => setup.mockInput.pressArrow("up"));
    expect(workspace.chatDraft).toBe("asked on the splash");
    await press(setup, () => setup.mockInput.pressArrow("down"));
    setup.renderer.destroy();
    expect(workspace.chatDraft).toBe("draft");
  });
});

describe("Backspace joins a continuation line (composer#12)", () => {
  it("removes a stray newline", async () => {
    const setup = await mount();
    await setup.mockInput.typeText("abc");
    await press(setup, () => setup.mockInput.pressKey("j", { ctrl: true }));
    expect(workspace.chatDraft).toBe("abc\n");
    await press(setup, () => setup.mockInput.pressBackspace());
    expect(workspace.chatDraft).toBe("abc");
    await setup.mockInput.typeText("Z");
    await settle(setup);
    await enter(setup);
    setup.renderer.destroy();
    expect(lastSent()).toBe("abcZ");
  });
});

describe("a stale error card never claims 'l' (static-sweep#1)", () => {
  it("after a non-retryable card, 'list my jobs' is typed whole", async () => {
    const setup = await mount();
    actions.sendErrorCard({ what: "$ make failed", action: "dropped", detail: "exit 2" });
    await settle(setup);
    await setup.mockInput.typeText("list my jobs");
    await settle(setup);
    setup.renderer.destroy();
    expect(workspace.chatDraft).toBe("list my jobs");
    expect(workspace.paneView).not.toBe("logs");
  });

  it("after /clear, a retryable card no longer claims 'l'", async () => {
    const setup = await mount();
    actions.sendErrorCard({ what: "turn failed", action: "needs_you" }, () => {});
    actions.clearChat();
    await settle(setup);
    await setup.mockInput.typeText("load it");
    await settle(setup);
    setup.renderer.destroy();
    expect(workspace.chatDraft).toBe("load it");
    expect(workspace.paneView).not.toBe("logs");
  });
});

describe("a click on the transcript keeps keys on the composer (panes-overlays#1)", () => {
  it("typing after a transcript click still edits the draft", async () => {
    const setup = await mount();
    await setup.mockInput.typeText("one");
    await settle(setup);
    await createMockMouse(setup.renderer).click(20, 4);
    await settle(setup);
    await setup.mockInput.typeText("two");
    await settle(setup);
    setup.renderer.destroy();
    expect(workspace.chatDraft).toBe("onetwo");
  });
});
