/**
 * H-7 stage 3: the write-through funnel (setScopedField) and the flipped
 * `activeTab()` backing.
 *
 * Pins:
 * - a funneled scoped write lands in BOTH the legacy singleton and the
 *   active tab's own store (legacy stays equal until stage 4);
 * - `activeTab()` now reads the active tab's store — a direct tab-store
 *   write is visible through it without touching the singleton;
 * - swap parity survives the flip (deactivate mirrors, reactivating shows
 *   the tab's own values through the accessor);
 * - variadic funnels (path-set, predicate-set, updater) mirror identically.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import {
  getRuntime,
  resetTabsRegistryForTests,
  runtimeOrNull,
} from "../src/tabs/registry";
import { actions, activeTab, setScopedField, workspace } from "../src/stores";
import type { ChatMessage } from "../src/types";

function seedAssistantMessage(id: string): void {
  setScopedField("chatMessages", (list: ChatMessage[]) => [
    ...list,
    {
      id,
      role: "assistant",
      content: "seed",
      streaming: true,
      timestamp: new Date().toISOString(),
    } as ChatMessage,
  ]);
}

describe("stage 3 write-through", () => {
  beforeEach(() => {
    resetTabsRegistryForTests();
    actions.resetWorkspace();
  });

  it("a funneled write lands in both the singleton and the active tab store", () => {
    const id = workspace.chatTabs[0]!.id;
    setScopedField("chatDraft", "hello tab");
    expect(workspace.chatDraft).toBe("hello tab");
    expect(runtimeOrNull(id)?.tabStore?.state.chatDraft).toBe("hello tab");
    expect(activeTab().chatDraft).toBe("hello tab");
  });

  it("the accessor reads the active tab store (backing flipped)", () => {
    const id = workspace.chatTabs[0]!.id;
    const rt = getRuntime(id);
    rt.tabStore!.set("chatDraft" as never, "direct-tab-write" as never);
    expect(activeTab().chatDraft).toBe("direct-tab-write");
  });

  it("swap parity survives the flip", () => {
    const a = actions.tabCreate({})!;
    setScopedField("chatDraft", "draft-A");
    setScopedField("permissionMode", "trust");
    const b = actions.tabCreate({})!;
    setScopedField("chatDraft", "draft-B");
    actions.tabActivate(a);
    expect(activeTab().chatDraft).toBe("draft-A");
    expect(activeTab().permissionMode).toBe("trust");
    expect(workspace.chatDraft).toBe("draft-A");
    expect(runtimeOrNull(b)?.tabStore?.state.chatDraft).toBe("draft-B");
    actions.tabActivate(b);
    expect(activeTab().chatDraft).toBe("draft-B");
    expect(runtimeOrNull(a)?.tabStore?.state.chatDraft).toBe("draft-A");
  });

  it("path-set, predicate-set and updater funnels mirror identically", () => {
    const id = workspace.chatTabs[0]!.id;
    setScopedField("thinking", "expanded", true);
    expect(runtimeOrNull(id)?.tabStore?.state.thinking.expanded).toBe(true);
    expect(workspace.thinking.expanded).toBe(true);

    seedAssistantMessage("m-1");
    setScopedField("chatMessages", (m: ChatMessage) => m.id === "m-1", "streaming", false);
    const viaTab = runtimeOrNull(id)?.tabStore?.state.chatMessages.find((m) => m.id === "m-1");
    expect(viaTab?.streaming).toBe(false);
    expect(workspace.chatMessages.find((m) => m.id === "m-1")?.streaming).toBe(false);

    setScopedField("pendingChatQueue", (ids: string[]) => [...ids, "m-1"]);
    expect(runtimeOrNull(id)?.tabStore?.state.pendingChatQueue).toContain("m-1");
    expect(workspace.pendingChatQueue).toContain("m-1");
  });

  it("a reset reads the fresh tab's defaults through the accessor", () => {
    setScopedField("chatDraft", "before reset");
    actions.resetWorkspace();
    // reconcile mints a new first tab id; its (lazy) runtime holds defaults,
    // and until any funneled write the accessor falls back to the reset
    // singleton — both are the defaults by construction.
    expect(activeTab().chatDraft).not.toBe("before reset");
    expect(activeTab().chatDraft).toBe(workspace.chatDraft);
    setScopedField("chatDraft", "after reset");
    expect(activeTab().chatDraft).toBe("after reset");
    expect(runtimeOrNull(workspace.activeChatTabId)?.tabStore?.state.chatDraft).toBe(
      "after reset",
    );
  });
});
