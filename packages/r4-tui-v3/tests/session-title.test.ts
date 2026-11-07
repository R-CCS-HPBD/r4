/**
 * Auto session titles (2026-09-29) pins: bridge-announced titles land on the
 * owning tab's strip cell — active or backgrounded, never buffered with the
 * transcript (session_title is a GLOBAL channel).
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { actions, workspace } from "../src/stores";
import { GLOBAL_CHANNELS } from "../src/tabs/registry";
import { TAB_WATCHED_CHANNELS } from "../src/tabs/wiring";

describe("session_title channel", () => {
  beforeEach(() => {
    actions.resetWorkspace();
  });

  it("is global (never buffered) and watched per-bridge", () => {
    expect(GLOBAL_CHANNELS.has("session_title")).toBe(true);
    expect(TAB_WATCHED_CHANNELS).toContain("session_title");
  });
});

describe("tabSetTitleForSession", () => {
  beforeEach(() => {
    actions.resetWorkspace();
  });

  it("patches the title of the tab owning the session", () => {
    const first = actions.tabCreate({ title: "chat 1" })!;
    const second = actions.tabCreate({ title: "chat 2" })!;
    actions.setTabMeta(first, { sessionId: "sess-a" });
    actions.setTabMeta(second, { sessionId: "sess-b" });
    actions.tabSetTitleForSession("sess-b", "Fix the NaN crash");
    const tabA = workspace.chatTabs.find((t) => t.id === first)!;
    const tabB = workspace.chatTabs.find((t) => t.id === second)!;
    expect(tabA.title).toBe("chat 1");
    expect(tabB.title).toBe("Fix the NaN crash");
  });

  it("ignores unknown sessions and blank titles", () => {
    const first = actions.tabCreate({ title: "chat 1" })!;
    actions.setTabMeta(first, { sessionId: "sess-a" });
    actions.tabSetTitleForSession("ghost", "nope");
    actions.tabSetTitleForSession("sess-a", "   ");
    expect(workspace.chatTabs[0].title).toBe("chat 1");
  });

  it("caps overlong titles at 60 chars", () => {
    const first = actions.tabCreate({ title: "chat 1" })!;
    actions.setTabMeta(first, { sessionId: "sess-a" });
    const long_title = `w `.repeat(40) + "tail";
    actions.tabSetTitleForSession("sess-a", long_title);
    expect(workspace.chatTabs[0].title.length).toBeLessThanOrEqual(60);
  });
});
