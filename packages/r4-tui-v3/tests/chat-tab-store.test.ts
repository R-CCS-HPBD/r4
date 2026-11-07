/**
 * Plan H-7 (stages 1–4): one `ChatTabStore` per tab, held by tabs/registry;
 * the store is canonical for its tab. Stage 4 deleted the snapshot swap —
 * activation rehydrates the legacy singleton mirror FROM the tab's store.
 *
 * Pins:
 * - the factory creates independent stores (writing one never moves another);
 * - every runtime created under the real store binding gets its own store
 *   seeded with the scoped defaults;
 * - activation parity: after create/switch cycles, the singleton mirror and
 *   the activated tab's store carry exactly that tab's latest live values —
 *   never a stale captured snapshot.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import {
  CHAT_SCOPED_FIELDS,
  createChatTabStore,
  type ChatTabState,
} from "../src/tabs/chat-tab-store";
import {
  getRuntime,
  resetTabsRegistryForTests,
  runtimeOrNull,
} from "../src/tabs/registry";
import { actions, setWorkspace, workspace } from "../src/stores";

describe("createChatTabStore", () => {
  it("creates independent stores", () => {
    // Minimal valid state: the scoped list drives which keys exist.
    const base = {} as ChatTabState;
    for (const field of CHAT_SCOPED_FIELDS) {
      (base as unknown as Record<string, unknown>)[field] = null;
    }
    const a = createChatTabStore(structuredClone(base));
    const b = createChatTabStore(structuredClone(base));
    a.set("chatDraft" as never, "only-in-a" as never);
    b.set("permissionMode" as never, "trust" as never);
    expect(a.state.chatDraft).toBe("only-in-a");
    expect(b.state.chatDraft).toBeNull();
    expect(b.state.permissionMode).toBe("trust");
    expect(a.state.permissionMode).toBeNull();
  });
});

describe("registry per-tab store (real binding)", () => {
  beforeEach(() => {
    resetTabsRegistryForTests();
    actions.resetWorkspace();
  });

  it("creates one store per runtime, seeded with the scoped defaults", () => {
    const a = actions.tabCreate({});
    expect(a).not.toBeNull();
    const rt = runtimeOrNull(a!);
    expect(rt?.tabStore).toBeTruthy();
    expect(rt?.tabStore?.state.chatDraft).toBe(workspace.chatDraft);
    expect(rt?.tabStore?.state.permissionMode).toBe(workspace.permissionMode);
    // the store is per-tab, not the singleton
    expect(rt?.tabStore?.state).not.toBe(workspace);
  });

  it("activation rehydrates the singleton from the tab's own store (stage 4)", () => {
    const a = actions.tabCreate({})!;
    setWorkspace("chatDraft", "draft-A");
    const b = actions.tabCreate({})!;
    // creating b activated it: a's store holds draft-A, the live mirror
    // shows b's (fresh) draft
    expect(runtimeOrNull(a)?.tabStore?.state.chatDraft).toBe("draft-A");
    expect(workspace.chatDraft).not.toBe("draft-A");

    setWorkspace("chatDraft", "draft-B");
    actions.tabActivate(a);
    expect(workspace.chatDraft).toBe("draft-A");
    expect(runtimeOrNull(b)?.tabStore?.state.chatDraft).toBe("draft-B");
    expect(runtimeOrNull(a)?.tabStore?.state.chatDraft).toBe("draft-A");

    // back to b: a keeps its own value, the mirror follows b's store
    actions.tabActivate(b);
    expect(workspace.chatDraft).toBe("draft-B");
    expect(runtimeOrNull(a)?.tabStore?.state.chatDraft).toBe("draft-A");
  });

  it("re-activation rehydrates from the store — latest live edits win", () => {
    // The store is canonical and current: every scoped write lands on it the
    // moment it happens, so activation never reintroduces an out-of-date
    // captured value (the failure mode of the old snapshot swap).
    const first = workspace.chatTabs[0]!.id;
    setWorkspace("chatDraft", "first-draft");
    actions.tabCreate({}); // switches active to the fresh tab
    actions.tabActivate(first);
    expect(workspace.chatDraft).toBe("first-draft");
    expect(getRuntime(first).tabStore?.state.chatDraft).toBe("first-draft");
  });

  it("a fresh tab never reads another tab's store", () => {
    const a = actions.tabCreate({})!;
    setWorkspace("sessionId", "sess-a");
    setWorkspace("chatDraft", "draft-a");
    const b = actions.tabCreate({})!;
    expect(b).not.toBe(a);
    const bStore = runtimeOrNull(b)?.tabStore;
    expect(bStore).toBeTruthy();
    expect(bStore?.state.chatDraft).not.toBe("draft-a");
    expect(bStore?.state.sessionId).not.toBe("sess-a");
    expect(runtimeOrNull(a)?.tabStore?.state.chatDraft).toBe("draft-a");
    expect(runtimeOrNull(a)?.tabStore?.state.sessionId).toBe("sess-a");
  });
});
