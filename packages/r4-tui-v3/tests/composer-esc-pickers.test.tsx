/**
 * w3-tui item 6 (2026-10-21): chat.tsx composerOwnsEsc dropped its explicit
 * `|| venuePickerOpen || remoteSourcePickerOpen` clause — chatInputFocused()
 * derives from the store-claims table, where both pickers own the keyboard.
 * These pins hold the behaviour the clause guarded: with a draft in the
 * composer, Esc on either picker closes the picker and keeps the draft.
 */
import { describe, it, expect } from "bun:test";
import { actions, activeTab, workspace } from "../src/stores";
import { chatInputFocused } from "../src/lib/chat-input-focus";
import { installStabHarness, mountApp, settle } from "./helpers/stab-focus-harness";

installStabHarness();

describe("Esc on a venue picker over a composer draft", () => {
  for (const [label, open, isOpen] of [
    ["venue picker", () => actions.openVenuePicker(), () => workspace.venuePickerOpen],
    ["remote source picker", () => actions.openRemoteSourcePicker("start"), () => workspace.remoteSourcePickerOpen],
  ] as const) {
    it(`${label}: the composer yields Esc, the picker closes, the draft stays`, async () => {
      const setup = await mountApp();
      await setup.mockInput.typeText("keep this draft");
      await settle(setup);
      open();
      await settle(setup, 150);
      expect(chatInputFocused()).toBe(false);
      setup.mockInput.pressEscape();
      await settle(setup, 150);
      expect(isOpen()).toBe(false);
      expect(activeTab().chatDraft).toBe("keep this draft");
      setup.renderer.destroy();
    });
  }
});
