/**
 * Shift+Enter debug wave 2026-10-19 — "Shift+enter doesn't make a breakline".
 *
 * Root cause chain, now pinned at both ends:
 *  1. Byte layer: Shift+Enter only differs from Enter when the terminal
 *     speaks the kitty keyboard protocol. The production renderer enables
 *     it (cli.tsx useKittyKeyboard disambiguate+alternateKeys) and the full
 *     PTY stack decodes `CSI 13;2u` → draft line appended (probe-verified
 *     before landing this). On a legacy terminal (and tmux/screen by
 *     default) the key arrives as a bare "\r" — the app cannot and must not
 *     treat it as anything but submit.
 *  2. Honesty layer (the fix): the composer footer promised
 *     "Shift/Alt+Enter newline" unconditionally. It now degrades to the
 *     byte-honest "Alt+Enter/Ctrl+J newline" unless the renderer's
 *     capabilities confirm kitty_keyboard.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { setRendererCapabilities } from "@opentui/core/testing";
import { CliRenderEvents } from "@opentui/core";
import { ChatPanel } from "../src/components/chat";
import { resetWorkspace, actions, workspace } from "../src/stores";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { newlineHint } from "../src/lib/kitty-keys";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("shift+enter delivery paths", () => {
  it("kitty path (CSI 13;2u): Shift+Enter appends a draft line, nothing submits", async () => {
    const setup = await testRender(() => <ChatPanel />, {
      width: 80,
      height: 24,
      kittyKeyboard: true,
    });
    await setup.renderOnce();
    await setup.mockInput.typeText("hello");
    setup.mockInput.pressEnter({ shift: true });
    await setup.renderOnce();
    await setup.renderOnce();
    expect(workspace.chatDraft).toBe("hello\n");
    expect(workspace.chatMessages.some((m) => m.role === "user")).toBe(false);
    const frame = setup.captureCharFrame();
    expect(frame).toContain("2-line draft");
    setup.renderer.destroy();
  });

  it("legacy Alt+Enter bytes (ESC+CR → return+meta): appends a line, never submits", async () => {
    // Decoded 2026-10-19: the parser glues ESC+CR into ONE return event with
    // meta=true and option=false. The old claim only honored `option`, and the
    // submit guard only checked shift/option/ctrl — so legacy Alt+Enter sent
    // the draft. On most terminals this is the ONLY Alt+Enter encoding.
    const setup = await testRender(() => <ChatPanel />, { width: 80, height: 24 });
    await setup.renderOnce();
    await setup.mockInput.typeText("hello");
    (setup.renderer as unknown as { stdin: { emit: (a: string, b: unknown) => void } })
      .stdin.emit("data", Buffer.from("\x1b\r"));
    await setup.renderOnce();
    await setup.renderOnce();
    expect(workspace.chatDraft).toBe("hello\n");
    expect(workspace.chatMessages.some((m) => m.role === "user")).toBe(false);
    setup.renderer.destroy();
  });

  it("Ctrl+J arrives as the 'linefeed' key: appends a line", async () => {
    // Decoded 2026-10-19: Ctrl+J IS \n at the byte layer, parsed as a key
    // named linefeed — the old j+ctrl check never fired, so the documented
    // fallback did nothing at all.
    const setup = await testRender(() => <ChatPanel />, { width: 80, height: 24 });
    await setup.renderOnce();
    await setup.mockInput.typeText("hello");
    setup.mockInput.pressKey("j", { ctrl: true });
    await setup.renderOnce();
    await setup.renderOnce();
    expect(workspace.chatDraft).toBe("hello\n");
    expect(workspace.chatMessages.some((m) => m.role === "user")).toBe(false);
    setup.renderer.destroy();
  });

  it("legacy bytes (\r regardless of shift): submits — the byte-layer limitation, not an app claim", async () => {
    const setup = await testRender(() => <ChatPanel />, { width: 80, height: 24 });
    await setup.renderOnce();
    await setup.mockInput.typeText("hello");
    // No kittyKeyboard option: the mock can only emit a plain "\r" for a
    // shifted Enter — exactly what legacy terminals and raw tmux deliver.
    setup.mockInput.pressEnter({ shift: true });
    await setup.renderOnce();
    await setup.renderOnce();
    expect(workspace.chatDraft).toBe("");
    expect(workspace.chatMessages.some((m) => m.role === "user" && m.content === "hello")).toBe(true);
    setup.renderer.destroy();
  });
});

describe("composer hint honesty", () => {
  it("unknown/legacy capabilities degrade the footer to Alt+Enter/Ctrl+J", async () => {
    const setup = await testRender(() => <ChatPanel />, { width: 120, height: 24 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("Alt+Enter/Ctrl+J newline");
    expect(frame).not.toContain("Shift/Alt+Enter");
    setup.renderer.destroy();
  });

  it("footer upgrades to Shift/Alt+Enter once kitty is negotiated", async () => {
    const setup = await testRender(() => <ChatPanel />, { width: 120, height: 24 });
    await setup.renderOnce();
    setRendererCapabilities(setup.renderer, { kitty_keyboard: true });
    setup.renderer.emit(CliRenderEvents.CAPABILITIES, setup.renderer.capabilities);
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("Shift/Alt+Enter newline");
    setup.renderer.destroy();
  });

  it("newlineHint wording", () => {
    expect(newlineHint(true)).toBe("Shift/Alt+Enter");
    expect(newlineHint(false)).toBe("Alt+Enter/Ctrl+J");
  });
});
