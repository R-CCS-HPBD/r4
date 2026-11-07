/**
 * Chat tabs (2026-09-29) pins:
 * - pure strip math (close/cycle/reopen stack) ported from opencode semantics;
 * - store-level tab lifecycle: create/switch snapshot swap, close activation
 *   rules, last-tab refusal, reopen dedupe;
 * - scoped-field swap: a background tab never leaks into the flat store.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import {
  adjacentTabId,
  CLOSED_TAB_LIMIT,
  MAX_CHAT_TABS,
  nextTabAfterClose,
  pushClosedTab,
  takeClosedTab,
  tabTitle,
  type ClosedChatTab,
} from "../src/tabs/model";
import { actions, workspace, setWorkspace } from "../src/stores";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import { ellipsisSlice, marqueeOffset, marqueeSlice } from "../src/lib/marquee";
import {
  cleanSessionTitle,
  isFallbackTitle,
  isGenericSessionName,
  promptDerivedTitle,
} from "../src/tabs/model";
import { mock, spyOn } from "bun:test";
import { getCompanionBridge } from "../src/bridge/jsonl-client";
import { tabTitleBudget } from "../src/components/chat-tab-strip";

function closedEntry(sessionId: string, index: number): ClosedChatTab {
  return {
    meta: {
      id: `tab-${sessionId}`,
      sessionId,
      title: sessionId,
      busy: false,
      needsAttention: false,
      absorbed: 0,
      createdAt: "",
    },
    snapshot: {},
    index,
  };
}

describe("chat tab strip math (pure)", () => {
  it("close activates the right neighbor, else left, else null", () => {
    const order = ["a", "b", "c"];
    expect(nextTabAfterClose(order, "a")).toBe("b");
    expect(nextTabAfterClose(order, "b")).toBe("c");
    expect(nextTabAfterClose(order, "c")).toBe("b");
    expect(nextTabAfterClose(["solo"], "solo")).toBeNull();
    expect(nextTabAfterClose(order, "ghost")).toBeNull();
  });

  it("cycling wraps both directions and no-ops below two tabs", () => {
    const order = ["a", "b", "c"];
    expect(adjacentTabId(order, "a", 1)).toBe("b");
    expect(adjacentTabId(order, "c", 1)).toBe("a");
    expect(adjacentTabId(order, "a", -1)).toBe("c");
    expect(adjacentTabId(order, "b", -1)).toBe("a");
    expect(adjacentTabId(["a"], "a", 1)).toBeNull();
    expect(adjacentTabId([], "a", 1)).toBeNull();
  });

  it(`reopen stack caps at ${CLOSED_TAB_LIMIT} and skips already-open sessions`, () => {
    let stack: ClosedChatTab[] = [];
    for (let i = 0; i < CLOSED_TAB_LIMIT + 5; i++) {
      stack = pushClosedTab(stack, closedEntry(`s${i}`, i));
    }
    expect(stack.length).toBe(CLOSED_TAB_LIMIT);
    // top is the NEWEST (s-…+4); but it's already open → skipped.
    const open = new Set([`s${CLOSED_TAB_LIMIT + 4}`, `s${CLOSED_TAB_LIMIT + 3}`]);
    const [entry, rest] = takeClosedTab(stack, open);
    expect(entry?.meta.sessionId).toBe(`s${CLOSED_TAB_LIMIT + 2}`);
    expect(rest.length).toBe(CLOSED_TAB_LIMIT - 3);
    // Empty stack → [null, []].
    expect(takeClosedTab([], new Set())[0]).toBeNull();
  });

  it("title falls back to short session id, then ordinal", () => {
    expect(tabTitle({ title: "research", sessionId: "abcdef123" }, 3)).toBe("research");
    expect(tabTitle({ title: " ", sessionId: "abcdef123" }, 3)).toBe("abcdef12");
    expect(tabTitle({ title: "", sessionId: "" }, 3)).toBe("chat 3");
  });
});

describe("chat tab store lifecycle", () => {
  beforeEach(() => {
    resetTabsRegistryForTests();
    actions.resetWorkspace();
  });

  it("boots with exactly one tab marked active", () => {
    expect(workspace.chatTabs.length).toBe(1);
    expect(workspace.activeChatTabId).toBe(workspace.chatTabs[0]!.id);
    expect(typeof workspace.activeChatTabId).toBe("string");
    expect(workspace.activeChatTabId.length).toBeGreaterThan(0);
  });

  it("create appends + activates; the previous tab keeps its transcript", () => {
    const first = workspace.chatTabs[0]!.id;
    actions.sendSystemNotice("hello from tab one");
    const rows = workspace.chatMessages.length;
    const second = actions.tabCreate();
    expect(second).not.toBeNull();
    expect(workspace.activeChatTabId).toBe(second ?? "never");
    expect(workspace.chatTabs.length).toBe(2);
    // New tab: fresh transcript (welcome row only).
    expect(workspace.chatMessages.length).toBeLessThan(rows);
    // Back to tab one: the notice row is still there.
    actions.tabActivate(first);
    expect(workspace.chatMessages.length).toBe(rows);
    expect(workspace.chatMessages.some((m) => m.content.includes("hello from tab one"))).toBe(true);
  });

  it("tab-scoped fields swap, global fields stay put", () => {
    const first = workspace.chatTabs[0]!.id;
    actions.setSessionId("sess-one");
    actions.tabCreate();
    expect(workspace.sessionId).toBe(""); // scoped default on the fresh tab
    actions.setSessionId("sess-two");
    actions.setTabMeta(workspace.activeChatTabId, { title: "two" });
    actions.tabActivate(first);
    expect(workspace.sessionId).toBe("sess-one");
    // Global inventory untouched by the swap.
    expect(workspace.chatTabs.length).toBe(2);
  });

  it("project/identity context is session-shaped: swaps with the tab (2026-09-30 audit)", () => {
    const first = workspace.chatTabs[0]!.id;
    actions.setIdentityState({ projectId: "proj-alpha", profile: "rccs", sourceName: "src-a" });
    actions.setProject({
      id: "proj-alpha",
      name: "Alpha",
      description: "",
      status: "active",
      hypothesis: "",
      createdAt: "",
      updatedAt: "",
      autoApproved: false,
    } as never);
    actions.tabCreate();
    // Fresh tab (2026-10-01 per-tab projects): INHERITS the previous tab's
    // project context — a new tab continues where you were; /project switch
    // then moves only this tab. Everything else still starts from defaults.
    expect(workspace.identityProjectId).toBe("proj-alpha");
    expect(workspace.project?.id).toBe("proj-alpha");
    actions.setIdentityState({ projectId: "proj-beta", profile: "bdrt", sourceName: "src-b" });
    actions.tabActivate(first);
    expect(workspace.identityProjectId).toBe("proj-alpha");
    expect(workspace.identitySource).toBe("src-a");
    expect(workspace.project?.id).toBe("proj-alpha");
    actions.tabCycle(1);
    expect(workspace.identityProjectId).toBe("proj-beta");
  });

  it("close activates the right neighbor and drops its meta", async () => {
    const a = workspace.chatTabs[0]!.id;
    actions.tabCreate();
    const b = workspace.chatTabs[1]!.id;
    actions.tabCreate();
    const c = workspace.chatTabs[2]!.id;
    expect(workspace.activeChatTabId).toBe(c);
    const closed = await actions.tabClose(c);
    expect(closed).toBe(true);
    expect(workspace.chatTabs.map((t) => t.id)).toEqual([a, b]);
    expect(workspace.activeChatTabId).toBe(b);
  });

  it("closing a background tab never re-activates anything", async () => {
    const a = workspace.chatTabs[0]!.id;
    actions.tabCreate();
    const b = workspace.chatTabs[1]!.id;
    actions.tabActivate(a);
    await actions.tabClose(b);
    expect(workspace.activeChatTabId).toBe(a);
    expect(workspace.chatTabs.length).toBe(1);
  });

  it("the last tab refuses to close", async () => {
    const closed = await actions.tabClose(workspace.chatTabs[0]!.id);
    expect(closed).toBe(false);
    expect(workspace.chatTabs.length).toBe(1);
  });

  it(`tab cap is ${MAX_CHAT_TABS}`, () => {
    for (let i = 0; i < MAX_CHAT_TABS + 3; i++) actions.tabCreate();
    expect(workspace.chatTabs.length).toBe(MAX_CHAT_TABS);
  });

  it("reopen restores the closed tab without re-closing it", async () => {
    actions.tabCreate();
    const b = workspace.chatTabs[1]!.id;
    actions.setTabMeta(b, { title: "pinned-work" });
    await actions.tabClose(b);
    expect(workspace.chatTabs.length).toBe(1);
    // reopen ack: entry.meta.sessionId is "" here → no bridge RPC attempted.
    const reopened = await actions.tabReopenClosed();
    expect(reopened).toBe(true);
    expect(workspace.chatTabs.length).toBe(2);
    expect(workspace.chatTabs[1]!.title).toBe("pinned-work");
  });

  it("dedupe: opening an already-open session activates its tab", async () => {
    actions.tabCreate({ sessionId: "sess-dup", title: "dup" });
    const dupTab = workspace.chatTabs[1]!.id;
    actions.tabCreate();
    expect(workspace.chatTabs.length).toBe(3);
    // demoMode default false here — tabOpenSession would RPC; flip demo on.
    actions.setDemoMode(true);
    await actions.tabOpenSession("sess-dup");
    expect(workspace.chatTabs.length).toBe(3);
    expect(workspace.activeChatTabId).toBe(dupTab);
  });

  it("titles follow the SERVER session name; unnamed keeps the fallback", () => {
    actions.tabCreate({ sessionId: "sess-a" });
    const a = workspace.chatTabs[1]!.id;
    actions.tabCreate({ sessionId: "sess-b" });
    const b = workspace.chatTabs[2]!.id;
    setWorkspace("sessionsList", [
      {
        id: "sess-a",
        name: "rank models on swe-bench",
        model: "k",
        created_at: "",
        updated_at: "",
        pinned: false,
        archived_at: "",
        venue: "",
        state: "",
        sync: "",
      },
      {
        id: "sess-b",
        name: "(unnamed)",
        model: "k",
        created_at: "",
        updated_at: "",
        pinned: false,
        archived_at: "",
        venue: "",
        state: "",
        sync: "",
      },
    ]);
    actions.syncChatTabTitles();
    expect(workspace.chatTabs.find((t) => t.id === a)!.title).toBe("rank models on swe-bench");
    // "(unnamed)" never overwrites the readable fallback.
    expect(workspace.chatTabs.find((t) => t.id === b)!.title).toBe("chat 3");
  });
});

describe("marquee (overflow tab titles)", () => {
  const text = "0123456789abcdef"; // len 16, cap 6 → travel 10

  it("short text never scrolls; empty cap renders empty", () => {
    expect(marqueeSlice("hi", 6, 0)).toBe("hi");
    expect(marqueeSlice(text, 0, 1234)).toBe("");
    expect(marqueeOffset(16, 16, 99999)).toBe(0);
  });

  it("dwells at the start, slides to the tail, dwells, then slides back", () => {
    const opts = { dwellMs: 1000, pxPerSec: 10 }; // travel 10 → 1000ms per leg
    // dwell at start
    expect(marqueeOffset(16, 6, 0, opts)).toBe(0);
    expect(marqueeOffset(16, 6, 999, opts)).toBe(0);
    // mid-travel
    expect(marqueeOffset(16, 6, 1500, opts)).toBe(5);
    // maximum at leg end, dwell at the tail
    expect(marqueeOffset(16, 6, 2000, opts)).toBe(10);
    expect(marqueeOffset(16, 6, 2999, opts)).toBe(10);
    // sliding back
    expect(marqueeOffset(16, 6, 3500, opts)).toBe(5);
    // wraps the period cleanly
    expect(marqueeOffset(16, 6, 4000, opts)).toBe(0);
  });

  it("the visible window always shows exactly `cap` columns of the source", () => {
    const opts = { dwellMs: 1000, pxPerSec: 10 };
    expect(marqueeSlice(text, 6, 0, opts)).toBe("012345");
    expect(marqueeSlice(text, 6, 2000, opts)).toBe("abcdef");
    expect(marqueeSlice(text, 6, 1500, opts)).toBe("56789a");
    for (const shown of [marqueeSlice(text, 6, 12345, opts), marqueeSlice(text, 6, 54321, opts)]) {
      expect(shown.length).toBe(6);
      expect(text).toContain(shown);
    }
  });

  it("ellipsisSlice fits and marks the cut", () => {
    expect(ellipsisSlice("short", 6)).toBe("short");
    expect(ellipsisSlice(text, 6)).toBe("01234…");
    expect(ellipsisSlice(text, 6).length).toBe(6);
  });
});

describe("session-name cleaning (wire-context leak)", () => {
  beforeEach(() => {
    resetTabsRegistryForTests();
    actions.resetWorkspace();
  });

  const contextPrefix =
    '[Project context] Active project: r4-dev ("r4-dev"). ' +
    "Local project-store files (F4 / r4 project verify): a.py, b.md. " +
    "Answer questions about THIS project. " +
    "If lab_catalog_search returns no hits, do not retry the same query — that tool is the governed Gateway catalog, a different store. Use knowledge_show or r4 project verify for local RC1 artifacts. " +
    "Do not describe the AI4S platform unless the user asks.";

  it("strips the full [Project context] block, keeps the real prompt", () => {
    expect(cleanSessionTitle(`${contextPrefix} rank models on swe-bench`)).toBe(
      "rank models on swe-bench",
    );
    expect(cleanSessionTitle("plain session name")).toBe("plain session name");
  });

  it("truncated-inside-context names clean to empty (caller falls back)", () => {
    expect(cleanSessionTitle('[Project context] Active project: r4-dev ("r4-dev"). Loca')).toBe("");
  });

  it("collapses whitespace runs", () => {
    expect(cleanSessionTitle("multi\n\n  spaced   name")).toBe("multi spaced name");
  });

  it("provisional title: first line, capped, never multiline", () => {
    expect(promptDerivedTitle("first line\nsecond line")).toBe("first line");
    expect(promptDerivedTitle("   ")).toBe("");
    expect(promptDerivedTitle("x".repeat(100)).length).toBe(60);
  });

  it("isFallbackTitle matches only the makeTabMeta shape", () => {
    expect(isFallbackTitle("chat 3")).toBe(true);
    expect(isFallbackTitle("chat 12")).toBe(true);
    expect(isFallbackTitle("chat x")).toBe(false);
    expect(isFallbackTitle("rank models")).toBe(false);
  });

  it("server name never overrides with an empty cleaned value", () => {
    const first = workspace.chatTabs[0]!.id;
    actions.setSessionId("sess-x");
    actions.noteFirstTabPrompt(first, "keep this title");
    setWorkspace("sessionsList", [
      {
        id: "sess-x",
        name: '[Project context] Active project: r4-dev ("r4-dev"). Loca',
        model: "k",
        created_at: "",
        updated_at: "",
        pinned: false,
        archived_at: "",
        venue: "",
        state: "",
        sync: "",
      },
    ]);
    actions.syncChatTabTitles();
    expect(workspace.chatTabs.find((t) => t.id === first)!.title).toBe("keep this title");
  });
});

describe("tab title adoption from the first prompt", () => {
  beforeEach(() => {
    resetTabsRegistryForTests();
    actions.resetWorkspace();
  });

  it("first typed prompt titles a fallback tab; real titles are sticky", () => {
    const first = workspace.chatTabs[0]!.id;
    actions.noteFirstTabPrompt(first, "what is dpo vs sft?");
    expect(workspace.chatTabs[0]!.title).toBe("what is dpo vs sft?");
    actions.noteFirstTabPrompt(first, "different second prompt");
    expect(workspace.chatTabs[0]!.title).toBe("what is dpo vs sft?");
  });

  it("slash commands never name a tab (verbs, not conversation)", () => {
    const first = workspace.chatTabs[0]!.id;
    actions.noteFirstTabPrompt(first, "/skills");
    actions.noteFirstTabPrompt(first, "  /models kimi-k3");
    expect(workspace.chatTabs[0]!.title).toBe("chat 1");
    // …but the first plain message right after DOES name it.
    actions.noteFirstTabPrompt(first, "summarize the sweep results");
    expect(workspace.chatTabs[0]!.title).toBe("summarize the sweep results");
  });

  it("an armed teamup goal names the tab (the goal IS the first message)", () => {
    actions.setTeamupArmed(true, "code");
    actions.sendChatMessage("audit the slurm wrapper for shell quoting");
    expect(workspace.chatTabs[0]!.title).toBe("audit the slurm wrapper for shell quoting");
  });

  it("generic-name classifier mirrors the server placeholder shape", () => {
    // Server AUTO_SESSION_NAME_RE (session_store.py) — fair game for rename.
    expect(isGenericSessionName("Session 2026-09-30 09:41")).toBe(true);
    expect(isGenericSessionName("(unnamed)")).toBe(true);
    expect(isGenericSessionName("chat 1")).toBe(true);
    expect(isGenericSessionName("New chat")).toBe(true);
    expect(isGenericSessionName("Untitled")).toBe(true);
    // Human intent is NEVER fair game — renaming these is forbidden.
    expect(isGenericSessionName("Session notes")).toBe(false);
    expect(isGenericSessionName("session-review")).toBe(false);
    expect(isGenericSessionName("rank models on swe-bench")).toBe(false);
    expect(isGenericSessionName("Session 2026-09-30 (deep dive)")).toBe(false);
  });

  it("aggressive rename: placeholder gets renamed server-side ONCE from the first prompt", async () => {
    // mock.module the session-bridge: bun overlaps async tests across files
    // (default concurrency 20), so spying the getCompanionBridge singleton
    // races cross-file spy installs/restores. A module mock is local to
    // this file's import evaluation and immune to singleton flips.
    const calls: Array<Record<string, unknown>> = [];
    mock.module("../src/bridge/session-bridge", () => ({
      renameSession: async (sessionId: string, name: string) => {
        calls.push({ session_id: sessionId, name });
        return true;
      },
    }));
    actions.setSessionId("sess-generic");
    // Address the tab BY sessionId — never by position: concurrent files
    // share one store, so chatTabs[0] is not reliably this test's tab.
    const first = () => workspace.chatTabs.find((t) => t.sessionId === "sess-generic")!.id;
    // Transcript truth: first user prompt (typed, no slash).
    setWorkspace("chatMessages", [
      { id: "u1", role: "user", content: "compare sft and dpo on small labs", timestamp: "" },
    ]);
    setWorkspace("sessionsList", [
      {
        id: "sess-generic",
        name: "Session 2026-09-30 09:41",
        model: "k",
        created_at: "",
        updated_at: "",
        pinned: false,
        archived_at: "",
        venue: "",
        state: "",
        sync: "",
      },
    ]);
    actions.syncChatTabTitles();
    // Rename fires through a dynamic import — poll for it (resolution time
    // varies with suite load).
    const deadline = Date.now() + 5000;
    while (calls.length === 0 && Date.now() < deadline) await Bun.sleep(10);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({
      session_id: "sess-generic",
      name: "compare sft and dpo on small labs",
    });
    // Once per run: a second sync does not re-fire.
    actions.syncChatTabTitles();
    const settleDeadline = Date.now() + 300;
    while (Date.now() < settleDeadline) await Bun.sleep(10);
    expect(calls).toHaveLength(1);
    // The placeholder never overwrote the prompt-derived title on the strip.
    expect(workspace.chatTabs.find((t) => t.id === first())!.title).not.toBe(
      "Session 2026-09-30 09:41",
    );
  });

  it("aggressive rename: human names adopt normally and are never renamed", async () => {
    const bridge = spyOn(getCompanionBridge(), "request").mockResolvedValue({});
    try {
      const first = workspace.chatTabs[0]!.id;
      actions.setSessionId("sess-human");
      setWorkspace("sessionsList", [
        {
          id: "sess-human",
          name: "Session notes",
          model: "k",
          created_at: "",
          updated_at: "",
          pinned: false,
          archived_at: "",
          venue: "",
          state: "",
          sync: "",
        },
      ]);
      actions.syncChatTabTitles();
      expect(workspace.chatTabs.find((t) => t.id === first)!.title).toBe("Session notes");
      expect(bridge).not.toHaveBeenCalled();
    } finally {
      bridge.mockRestore();
    }
  });

  it("cell budget reserves chrome and caps at 28 columns", () => {
    const tab = workspace.chatTabs[0]!;
    expect(tabTitleBudget(28, 1, tab)).toBeGreaterThan(0);
    expect(tabTitleBudget(28, 1, tab)).toBe(25); // 28 − "1" − glyph pad − tail pad
    const busy = { ...tab, busy: true, absorbed: 12 };
    expect(tabTitleBudget(28, 1, busy)).toBe(tabTitleBudget(28, 1, tab) - 2 - 3);
    // The gate and the cell use the same budget: a 26-char title at budget 25
    // must scroll (the "not realized" regression guard).
    expect("x".repeat(26).length > tabTitleBudget(28, 1, tab)).toBe(true);
  });
});
