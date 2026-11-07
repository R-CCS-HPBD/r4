/**
 * Tab/session lifecycle pins (stabilization 2026-10-21):
 * - tabs-sessions#3: /sessions new kept the old conversation's tab title, and
 *   the empty new session was then renamed server-side to it.
 * - tabs-sessions#4 / slash-b#3: a failed `/sessions resume <bad id>` left a
 *   stray, active, persisted tab behind (one more per attempt).
 * - tabs-sessions#8: a new tab after a close reused a surviving tab's
 *   `chat N` title.
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import { closedTabCount, resetTabsRegistryForTests } from "../src/tabs/registry";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import type { CompanionBridgeClient } from "../src/bridge/jsonl-client";
import { newSession } from "../src/bridge/session-bridge";

let sent: Array<{ method: string; params: Record<string, unknown> }> = [];

/** One shared fake bridge (no tab wiring: every tab resolves to it). */
function fakeBridge(reply: (method: string, params: Record<string, unknown>) => unknown) {
  sent = [];
  setCompanionBridgeForTests({
    request: async (method: string, params: Record<string, unknown>) => {
      sent.push({ method, params: params ?? {} });
      return reply(method, params ?? {});
    },
  } as unknown as CompanionBridgeClient);
}

const titles = () => workspace.chatTabs.map((t) => t.title);

beforeEach(() => {
  actions.resetWorkspace();
  resetTabsRegistryForTests();
  setWorkspace("demoMode", false);
});
afterEach(() => {
  setCompanionBridgeForTests(null);
  resetTabsRegistryForTests();
});

describe("/sessions new on a titled tab", () => {
  it("drops the old title and never renames the new session to it", async () => {
    const tabA = workspace.activeChatTabId;
    actions.setSessionId("sess-old-0001");
    actions.setTabMeta(tabA, { title: "alpha one" });
    fakeBridge((method) =>
      method === "initialize" ? { session: { id: "sess-new-0002" }, messages: [] } : {},
    );
    expect(await newSession("local")).toBe(true);
    const tab = workspace.chatTabs.find((t) => t.id === tabA)!;
    expect(tab.sessionId).toBe("sess-new-0002");
    expect(tab.title).toBe("chat 1");
    // The sessions refresh that follows (venue picker → fetchSessions):
    actions.setSessionsList([
      { id: "sess-new-0002", name: "Session 2026-10-21 08:59", model: "m", created_at: "", updated_at: "" },
    ] as never);
    actions.syncChatTabTitles();
    await Bun.sleep(10);
    expect(sent.some((c) => c.method === "session.rename")).toBe(false);
  });
});

describe("failed /sessions resume", () => {
  it("leaves the tab layout, focus and reopen stack as they were", async () => {
    const tabA = workspace.activeChatTabId;
    actions.setTabMeta(tabA, { title: "working" });
    fakeBridge((method, params) => {
      if (method === "load_session") {
        throw new Error(`session not found locally or in the journal: ${params.session_id}`);
      }
      return {};
    });
    for (const bogus of ["bogus-sid-1", "bogus-sid-1", "bogus-sid-2"]) {
      expect(await actions.tabOpenSession(bogus)).toBe(false);
    }
    expect(workspace.chatTabs.map((t) => t.id)).toEqual([tabA]);
    expect(workspace.activeChatTabId).toBe(tabA);
    expect(closedTabCount()).toBe(0);
    // The failure is still reported, on the tab the user is back on.
    const cards = workspace.chatMessages.filter((m) => m.errorCard?.what === "resume failed");
    expect(cards.length).toBe(3);
    expect(cards[0]!.errorCard!.detail).toContain("bogus-sid-1");
  });

  it("a successful open keeps its tab", async () => {
    fakeBridge((method, params) =>
      method === "load_session" ? { session: { id: params.session_id }, messages: [] } : {},
    );
    expect(await actions.tabOpenSession("sess-real-0001", "real")).toBe(true);
    expect(workspace.chatTabs.length).toBe(2);
    expect(workspace.chatTabs[1]!.sessionId).toBe("sess-real-0001");
  });
});

describe("fallback tab titles", () => {
  it("a tab created after a close never repeats a surviving title", async () => {
    const first = workspace.activeChatTabId;
    actions.setTabMeta(first, { title: "alpha one" });
    actions.tabCreate();
    actions.tabCreate();
    expect(titles()).toEqual(["alpha one", "chat 2", "chat 3"]);
    await actions.tabClose(first);
    actions.tabCreate();
    expect(titles()).toEqual(["chat 2", "chat 3", "chat 4"]);
    expect(new Set(titles()).size).toBe(titles().length);
  });
});
