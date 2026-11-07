import { afterAll, beforeEach, describe, expect, it, mock } from "bun:test";
import { mkdirSync, rmSync, writeFileSync } from "fs";
import { join } from "path";
import { tmpdir } from "os";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { resetTabsRegistryForTests, getRuntime } from "../src/tabs/registry";

/**
 * A1 restore pins (2026-10-05): the persisted layout replaces the fresh
 * first tab, the active session resumes through the same resumeSession a
 * manual open would ride, background tabs mark restoredPending, and the
 * lazy-resume hook in tabActivate fires once and clears the flag.
 */

const home = join(tmpdir(), `r4-tabs-restore-${process.pid}`);

function seedState(payload: unknown) {
  mkdirSync(home, { recursive: true });
  process.env.R4_CONFIG_HOME = home;
  writeFileSync(join(home, "tui-session-state.json"), JSON.stringify(payload), "utf-8");
}

// Restore, never delete: the test preload's scratch home must survive
// this file (r2 #34/#46).
const savedHome = process.env.R4_CONFIG_HOME;
function restoreHome(): void {
  if (savedHome === undefined) delete process.env.R4_CONFIG_HOME;
  else process.env.R4_CONFIG_HOME = savedHome;
}

describe("tabs-restore", () => {
  afterAll(restoreHome);
  beforeEach(() => {
    restoreHome();
    rmSync(home, { recursive: true, force: true });
    resetWorkspace();
    resetTabsRegistryForTests();
  });

  it("restores layout, resumes the active tab, marks pending on the rest", async () => {
    seedState({
      tabs: [
        { title: "main", sessionId: "sess-a111111" },
        { title: "bench", sessionId: "sess-b222222" },
      ],
      activeIndex: 1,
    });
    const resumes: string[] = [];
    mock.module("../src/bridge/session-bridge", () => ({
      resumeSession: async (sessionId: string) => {
        resumes.push(sessionId);
        return true;
      },
    }));
    const { restoreTabsFromState } = await import("../src/bridge/tabs-restore");
    await restoreTabsFromState();

    expect(workspace.chatTabs.map((t) => t.sessionId)).toEqual([
      "sess-a111111",
      "sess-b222222",
    ]);
    expect(workspace.chatTabs[0]!.title).toBe("main");
    const active = workspace.chatTabs.find((t) => t.id === workspace.activeChatTabId);
    expect(active?.sessionId).toBe("sess-b222222");
    expect(resumes).toEqual(["sess-b222222"]); // active resumed eagerly
    const inactive = workspace.chatTabs.find((t) => t.sessionId === "sess-a111111")!;
    expect(getRuntime(inactive.id).restoredPending).toBe(true);
    expect(getRuntime(active!.id).restoredPending).toBe(false);
  });

  it("lazy resume on first activation, once only", async () => {
    seedState({
      tabs: [{ title: "main", sessionId: "sess-a111111" }],
      activeIndex: 0,
    });
    const resumes: string[] = [];
    mock.module("../src/bridge/session-bridge", () => ({
      resumeSession: async (sessionId: string) => {
        resumes.push(sessionId);
        return true;
      },
      refreshSessionsForTabTitles: () => undefined,
    }));
    const { restoreTabsFromState } = await import("../src/bridge/tabs-restore");
    await restoreTabsFromState();

    // second tab (never restored) becomes activation target of the restored one
    const secondId = actions.tabCreate({});
    expect(secondId).not.toBeNull();
    const restored = workspace.chatTabs.find((t) => t.sessionId === "sess-a111111")!;
    actions.tabActivate(restored.id);
    await new Promise((r) => setTimeout(r, 20));
    expect(resumes).toEqual(["sess-a111111"]);
    actions.tabActivate(secondId!);
    actions.tabActivate(restored.id);
    await new Promise((r) => setTimeout(r, 20));
    expect(resumes).toEqual(["sess-a111111"]); // no double resume
  });

  it("a failed active resume is reported honestly, never as resumed (2026-10-21)", async () => {
    // tabs-sessions#5: "resume failed" was followed by "restored 2 tabs
    // (active session resumed; …)" — and, with one tab, "resumed session
    // <the fresh id the bridge opened>".
    seedState({
      tabs: [
        { title: "ghost tab", sessionId: "00000000-dead-beef-0000-000000000000" },
        { title: "real", sessionId: "sess-b222222" },
      ],
      activeIndex: 0,
    });
    mock.module("../src/bridge/session-bridge", () => ({
      resumeSession: async () => false,
      refreshSessionsForTabTitles: () => undefined,
    }));
    const { restoreTabsFromState } = await import("../src/bridge/tabs-restore");
    await restoreTabsFromState();
    const notices = workspace.chatMessages.map((m) => m.content);
    expect(notices.some((n) => n.includes("resumed;") || n.includes("active session resumed"))).toBe(false);
    expect(notices.some((n) => n.includes("session 00000000 could not be resumed"))).toBe(true);
    // The old title named the lost session, not the fresh one now in the tab.
    expect(workspace.chatTabs[0]!.title).toBe("chat 1");
    expect(workspace.chatTabs[1]!.title).toBe("real");
  });

  it("restore notices: one-tab success adds nothing (resumeSession said it)", async () => {
    const { restoreNotice } = await import("../src/bridge/tabs-restore");
    expect(restoreNotice(1, "sess-a111111", true)).toBe("");
    expect(restoreNotice(1, "sess-a111111", false)).toContain("could not be resumed");
    expect(restoreNotice(3, "sess-a111111", true)).toContain("active session resumed");
    expect(restoreNotice(3, "sess-a111111", false)).not.toContain("resumed;");
  });

  it("no state file → fresh start untouched", async () => {
    process.env.R4_CONFIG_HOME = home;
    mkdirSync(home, { recursive: true });
    const { restoreTabsFromState } = await import("../src/bridge/tabs-restore");
    const before = workspace.chatTabs.map((t) => ({ ...t }));
    await restoreTabsFromState();
    expect(workspace.chatTabs.length).toBe(before.length);
  });
});
