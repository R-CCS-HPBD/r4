/**
 * Typed session errors from the bridge (c3-tui 2026-10-22; the error shape
 * is the c3-session lane's: `error.data.code`). The bridge is stubbed — this
 * base has no Python side for them.
 *
 * session_conflict  the refused turn's text returns to the composer, the
 *                   refused row leaves the transcript, and the card reloads
 *                   the SAME tab (load_session on its bridge). `/sessions
 *                   resume <id>` on the tab that holds the session reloads it
 *                   too — it only said "already open in a tab".
 *                   A conflict met by the END-of-turn save (data.stage
 *                   "after_turn", cycle-3 fix lane) came after the model and
 *                   the tools ran: the rows stay, nothing returns to the
 *                   composer or is re-queued, and the card continues the tab
 *                   in the unsaved copy — never "send again".
 * session_corrupt   the card offers a new session and names
 *                   `/sessions export <id>` for the raw row; from a failed
 *                   open its card sits on the tab the user came from. The
 *                   new session ALWAYS goes in a NEW tab: on the tab that
 *                   holds the corrupt session itself an in-place /new
 *                   cleared the only intact copy and saved "[]" over the
 *                   raw row (cycle 3 verifier round 2).
 */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { actions, activeTab, queueAppendBookkeepingForTests, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { BridgeRequestError, CompanionBridgeClient, setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { sessionErrorCard, sessionErrorOf } from "../src/bridge/session-recovery";
import { fetchSessions, refreshSessionsForTabTitles } from "../src/bridge/session-bridge";
import { resetTabsRegistryForTests } from "../src/tabs/registry";
import type { ChatStreamChunk } from "../src/adapters/types";
import type { ChatMessage } from "../src/types";

const SID = "sess-conflict-0001-aaaa";
const conflict = () =>
  new BridgeRequestError(
    `this session (${SID.slice(0, 8)}) changed in another console — reload (/sessions resume ${SID}) to continue`,
    "close this tab first (/tab close), then /sessions resume",
    "SessionConflictError",
    { code: "session_conflict", session_id: SID, copy_id: "sess-copy-0002" },
  );
const COPY = "sess-copy-0002";
const afterTurn = () =>
  new BridgeRequestError(
    `your turn ran, but this session (${SID.slice(0, 8)}) changed in another console meanwhile, so the turn was not saved to it; the turn and its reply are kept in session ${COPY} (unsaved copy) — do not send it again`,
    `in the console, /sessions resume ${COPY} opens the unsaved copy; /sessions resume ${SID} reloads this session`,
    "SessionConflictError",
    { code: "session_conflict", session_id: SID, copy_id: COPY, stage: "after_turn" },
  );
const corrupt = () =>
  new BridgeRequestError(
    `session ${SID} cannot be opened: its stored messages is corrupt (not JSON); the stored row was left untouched`,
    undefined,
    "SessionCorruptError",
    { code: "session_corrupt", session_id: SID, column: "messages" },
  );

let sent: Array<{ method: string; params: Record<string, unknown> }> = [];
function fakeBridge(reply: (method: string, params: Record<string, unknown>) => unknown) {
  sent = [];
  setCompanionBridgeForTests({
    request: async (method: string, params: Record<string, unknown>) => {
      sent.push({ method, params: params ?? {} });
      return reply(method, params ?? {});
    },
  } as unknown as CompanionBridgeClient);
}

const tick = (ms = 60) => new Promise((resolve) => setTimeout(resolve, ms));
const lastCard = (): ChatMessage | undefined => [...workspace.chatMessages].reverse().find((m) => m.errorCard);

beforeEach(() => {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  resetWorkspace();
  resetTabsRegistryForTests();
  setWorkspace("demoMode", false);
  actions.closeAllOverlays();
});
afterEach(() => {
  setCompanionBridgeForTests(null);
  resetTabsRegistryForTests();
});

describe("the error shape", () => {
  it("dispatchResponse keeps error.data on the rejection", async () => {
    const client = new CompanionBridgeClient();
    const internals = client as unknown as {
      pending: Map<number, unknown>;
      dispatchResponse: (r: Record<string, unknown>) => void;
    };
    const rejected = new Promise<unknown>((resolve) => {
      internals.pending.set(9, { resolve, reject: resolve, timer: null, method: "send_message" });
    });
    internals.dispatchResponse({
      id: 9,
      ok: false,
      error: { message: "changed elsewhere", type: "SessionConflictError", data: { code: "session_conflict", session_id: SID } },
    });
    const info = sessionErrorOf(await rejected);
    expect(info?.code).toBe("session_conflict");
    expect(info?.sessionId).toBe(SID);
  });

  it("only the two typed codes with a session id count", () => {
    expect(sessionErrorOf(new BridgeRequestError("x", undefined, undefined, { code: "other", session_id: SID }))).toBeNull();
    expect(sessionErrorOf(new BridgeRequestError("x", undefined, undefined, { code: "session_corrupt" }))).toBeNull();
    expect(sessionErrorOf(new Error("plain"))).toBeNull();
    expect(sessionErrorOf(corrupt())?.column).toBe("messages");
  });

  it("the corrupt card offers a new session and the raw export", () => {
    const card = sessionErrorCard(sessionErrorOf(corrupt())!);
    expect(card.what).toContain("stored messages is corrupt");
    expect(card.verb).toContain("new session");
    expect(card.alt).toContain(`/sessions export ${SID}`);
  });
});

describe("session_conflict on a turn", () => {
  it("puts the refused text back in the composer, drops its row, and the card reloads the same tab", async () => {
    actions.setSessionId(SID);
    const tabId = workspace.activeChatTabId;
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream(): AsyncGenerator<ChatStreamChunk> {
        // What CompanionChatBackend yields for a typed refusal (failedTurn).
        yield { sessionError: sessionErrorOf(conflict())! };
        yield { done: true };
      },
    });
    // The end-of-turn title refresh fails here, as on a runner with no
    // bridge: it must not post a card over the recovery card (c3 CI red).
    fakeBridge((method) => {
      if (method === "list_sessions") throw new Error("no bridge");
      return {};
    });
    actions.sendChatMessage("my refused turn text");
    await tick(200);
    expect(workspace.pendingChatDraft).toBe("my refused turn text");
    expect(workspace.chatMessages.some((m) => m.role === "user" && m.content === "my refused turn text")).toBe(false);
    const card = lastCard();
    expect(card?.errorCard?.verb).toContain("reload this tab");
    expect(card?.errorCard?.alt).toContain(`/sessions resume ${SID}`);
    // No "[Chat unavailable]" reply with the server's close-the-tab hint.
    expect(workspace.chatMessages.some((m) => m.content.includes("[Chat unavailable]"))).toBe(false);

    fakeBridge((method) => (method === "load_session" ? { session: { id: SID }, messages: [] } : {}));
    actions.activateErrorCard(card!.id);
    await tick();
    expect(sent.find((s) => s.method === "load_session")?.params.session_id).toBe(SID);
    expect(workspace.activeChatTabId).toBe(tabId);
    expect(workspace.chatTabs.length).toBe(1);
  });

  it("CompanionChatBackend turns the rejected send_message into a sessionError chunk", async () => {
    const { CompanionChatBackend } = await import("../src/adapters/companion-chat");
    const bridge = {
      alive: true,
      ensureStarted: async () => {},
      on: () => {},
      off: () => {},
      request: async (method: string) => {
        if (method === "send_message") throw conflict();
        return {};
      },
      abortInFlightSend: () => {},
      cancelTurn: () => {},
    };
    const chunks: ChatStreamChunk[] = [];
    for await (const c of new CompanionChatBackend(bridge as never).chatStream("hi", [], "m")) chunks.push(c);
    expect(chunks.some((c) => c.sessionError?.code === "session_conflict")).toBe(true);
    expect(chunks.some((c) => (c.delta ?? "").includes("close this tab first"))).toBe(false);
  });
});

describe("/sessions resume <id> on the tab that holds it", () => {
  it("reloads that tab instead of only saying it is already open", async () => {
    actions.setSessionId(SID);
    const tabId = workspace.activeChatTabId;
    actions.tabCreate();
    fakeBridge((method) => (method === "load_session" ? { session: { id: SID }, messages: [] } : {}));
    expect(await actions.tabOpenSession(SID)).toBe(true);
    expect(workspace.activeChatTabId).toBe(tabId);
    expect(workspace.chatTabs.length).toBe(2);
    expect(sent.filter((s) => s.method === "load_session").map((s) => s.params.session_id)).toEqual([SID]);
  });
});

describe("session_corrupt on resume", () => {
  it("drops the opened tab and offers a new session (in a new tab) plus the raw export", async () => {
    fakeBridge((method) => {
      if (method === "load_session") throw corrupt();
      return { session: { id: "sess-new" }, messages: [] };
    });
    expect(await actions.tabOpenSession(SID)).toBe(false);
    expect(workspace.chatTabs.length).toBe(1);
    const card = lastCard();
    expect(card?.errorCard?.what).toContain("cannot be opened");
    expect(card?.errorCard?.alt).toContain(`/sessions export ${SID}`);
    actions.activateErrorCard(card!.id);
    await tick();
    expect(sent.some((s) => s.method === "dispatch_slash_command")).toBe(false);
    expect(workspace.chatTabs.length).toBe(2);
  });
});

/** A backend whose turn streams `reply`, waits for `release`, then fails with `error`. */
function backendFailingAfter(reply: string, error: () => BridgeRequestError, release: Promise<void> = Promise.resolve()) {
  const calls: string[] = [];
  actions.setChatBackend({
    async chat() {
      return "unused";
    },
    async *chatStream(content: string): AsyncGenerator<ChatStreamChunk> {
      calls.push(content);
      if (reply) yield { delta: reply };
      await release;
      if (reply) yield { roundBreak: true };
      yield { sessionError: sessionErrorOf(error())! };
      yield { done: true };
    },
  });
  return calls;
}

describe("session_conflict after the turn ran (stage after_turn)", () => {
  it("keeps the rows, refills nothing, never asks to send again, and continues in the copy", async () => {
    actions.setSessionId(SID);
    const tabId = workspace.activeChatTabId;
    backendFailingAfter("job 123 submitted", afterTurn);
    actions.sendChatMessage("submit the job");
    await tick(200);
    expect(workspace.chatMessages.some((m) => m.role === "user" && m.content === "submit the job")).toBe(true);
    expect(workspace.chatMessages.some((m) => m.role === "assistant" && m.content.includes("job 123 submitted"))).toBe(true);
    expect(workspace.pendingChatDraft).toBeNull();
    const card = lastCard()!.errorCard!;
    expect(card.what).toContain("your turn ran");
    expect(card.what).toContain("do not send it again");
    expect(card.verb).toContain("continue this tab in the unsaved copy");
    expect(card.verb).not.toContain("send again");
    expect(card.alt).toContain(`/sessions resume ${COPY}`);

    fakeBridge((method) => (method === "load_session" ? { session: { id: COPY }, messages: [] } : {}));
    actions.activateErrorCard(lastCard()!.id);
    await tick();
    expect(sent.filter((s) => s.method === "load_session").map((s) => s.params.session_id)).toEqual([COPY]);
    expect(workspace.activeChatTabId).toBe(tabId);
    expect(workspace.chatTabs.length).toBe(1);
  });

  it("does not pop the queue onto the stale session", async () => {
    actions.setSessionId(SID);
    fakeBridge(() => ({ applied: true, queue: [] }));
    let release!: () => void;
    const calls = backendFailingAfter("partial", afterTurn, new Promise<void>((r) => (release = r)));
    actions.sendChatMessage("first turn");
    await tick();
    actions.sendChatMessage("queued behind it");
    await tick();
    expect(workspace.pendingChatQueue.length).toBe(1);
    release();
    await tick(200);
    expect(calls).toEqual(["first turn"]);
    expect(workspace.pendingChatQueue.length).toBe(1);
    expect(workspace.chatMessages.some((m) => m.queued && m.content === "queued behind it")).toBe(true);
  });
});

describe("session_conflict refused up front", () => {
  it("never overwrites a draft typed meanwhile", async () => {
    actions.setSessionId(SID);
    let release!: () => void;
    backendFailingAfter("", conflict, new Promise<void>((r) => (release = r)));
    actions.sendChatMessage("the refused text");
    await tick();
    actions.setChatDraft("my unsent draft");
    release();
    await tick(200);
    expect(workspace.pendingChatDraft).toBeNull();
    expect(workspace.chatDraft).toBe("my unsent draft");
    expect(workspace.chatMessages.some((m) => m.content.includes("the refused text"))).toBe(true);
    expect(lastCard()?.errorCard?.verb).toContain("reload this tab, then send again");
  });
});

describe("session_corrupt from another tab", () => {
  it("lands on the tab the user came from and never replaces its conversation", async () => {
    const LIVE = "sess-live-xxxx-0001";
    actions.setSessionId(LIVE);
    const tabA = workspace.activeChatTabId;
    fakeBridge((method) => {
      if (method === "load_session") throw corrupt();
      return { session: { id: "sess-new" }, messages: [] };
    });
    expect(await actions.tabOpenSession(SID)).toBe(false);
    expect(workspace.activeChatTabId).toBe(tabA);
    const card = lastCard()!;
    expect(card.errorCard?.what).toContain(`session ${SID.slice(0, 8)} cannot be opened`);
    expect(card.errorCard?.verb).toContain("new tab");
    sent = [];
    expect(actions.resolveLastErrorKey()).toBe(true); // a bare Enter on an empty composer
    await tick();
    expect(sent.some((s) => s.method === "dispatch_slash_command")).toBe(false);
    expect(workspace.chatTabs.length).toBe(2);
    expect(workspace.activeChatTabId).not.toBe(tabA);
    expect(workspace.chatTabs.find((t) => t.id === tabA)?.sessionId).toBe(LIVE);
  });
});

describe("session_corrupt on the tab that holds the corrupt session", () => {
  it("a bare Enter opens a new tab and never clears this tab's intact copy", async () => {
    actions.setSessionId(SID);
    const tabA = workspace.activeChatTabId;
    setWorkspace("chatMessages", [
      { id: "m-precious", role: "user", content: "precious-X-conversation", timestamp: new Date().toISOString() },
    ]);
    fakeBridge((method) => {
      if (method === "load_session") throw corrupt();
      return { session: { id: "sess-new" }, messages: [] };
    });
    // /sessions resume <its own id> reloads this tab, and the row is corrupt.
    expect(await actions.tabOpenSession(SID)).toBe(false);
    expect(workspace.activeChatTabId).toBe(tabA);
    const card = lastCard()!;
    expect(card.errorCard?.what).toContain("cannot be opened");
    expect(card.errorCard?.verb).toContain("new tab");
    expect(workspace.chatMessages.some((m) => m.content === "precious-X-conversation")).toBe(true);
    sent = [];
    expect(actions.resolveLastErrorKey()).toBe(true); // a bare Enter on an empty composer
    await tick();
    // No in-place /new: on the bridge it clears this session and saves "[]".
    expect(sent.some((s) => s.method === "dispatch_slash_command" || s.method === "initialize")).toBe(false);
    expect(workspace.chatTabs.length).toBe(2);
    expect(workspace.activeChatTabId).not.toBe(tabA);
    actions.tabActivate(tabA);
    await tick();
    expect(workspace.chatTabs.find((t) => t.id === tabA)?.sessionId).toBe(SID);
    expect(workspace.chatMessages.some((m) => m.content === "precious-X-conversation")).toBe(true);
  });
});

describe("a queue append refused while the turn ran", () => {
  it("takes its row out of the transcript, returns the text and says why", async () => {
    actions.setSessionId(SID);
    fakeBridge((method) => {
      if (method === "session.queue_remote") throw new Error("journal unreachable");
      if (method === "session.queue_append") throw conflict();
      return {};
    });
    let release!: () => void;
    backendFailingAfter("partial", afterTurn, new Promise<void>((r) => (release = r)));
    actions.sendChatMessage("first turn");
    await tick();
    actions.sendChatMessage("queued behind it");
    await tick(120);
    // Never queued nor stored: as a plain user row it read as sent.
    expect(workspace.chatMessages.some((m) => m.content === "queued behind it")).toBe(false);
    expect(workspace.pendingChatQueue.length).toBe(0);
    expect(workspace.pendingChatDraft).toBe("queued behind it");
    const card = lastCard()!.errorCard!;
    expect(card.what).toContain("back in the composer");
    expect(card.detail).toContain("changed in another console");
    expect(card.detail).not.toContain("retry when connected");
    release();
    await tick(200);
  });
});

/** The append fails only AFTER the running turn finished and the queue
 * drained: popQueuedTurn already took the queued row out and sent its text
 * as its own turn (cycle 3 verifier round 3). Rolling that back returned
 * text that already ran to the composer, and a resend ran its tools twice. */
async function appendFailsAfterTheQueueDrained(draft: string) {
  actions.setSessionId(SID);
  let failRemote!: () => void;
  const remoteGate = new Promise<void>((r) => (failRemote = r));
  fakeBridge(async (method) => {
    if (method === "session.queue_remote") {
      await remoteGate;
      throw new Error("journal unreachable");
    }
    if (method === "session.queue_append") throw conflict();
    return {};
  });
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const calls: string[] = [];
  actions.setChatBackend({
    async chat() {
      return "unused";
    },
    async *chatStream(content: string): AsyncGenerator<ChatStreamChunk> {
      calls.push(content);
      if (calls.length === 1) await gate;
      yield { delta: `reply to ${content}` };
      yield { done: true };
    },
  });
  actions.sendChatMessage("first turn");
  await tick();
  actions.sendChatMessage("queued behind it");
  await tick();
  if (draft) actions.setChatDraft(draft);
  release(); // the first turn ends; the queued row is popped and SENT
  await tick(300);
  expect(calls).toEqual(["first turn", "queued behind it"]);
  failRemote(); // only now does the slow append fail
  await tick(300);
}

describe("a queue append that fails after its row already ran", () => {
  it("never returns the sent text to an empty composer", async () => {
    await appendFailsAfterTheQueueDrained("");
    expect(workspace.pendingChatDraft ?? "").not.toBe("queued behind it");
    const userRows = workspace.chatMessages.filter((m) => m.role === "user").map((m) => m.content);
    expect(userRows).toEqual(["first turn", "queued behind it"]);
    expect(workspace.chatMessages.some((m) => m.errorCard?.what.includes("queue append failed"))).toBe(false);
  });

  it("never posts it as not queued over a draft", async () => {
    await appendFailsAfterTheQueueDrained("my next idea");
    expect(workspace.chatMessages.some((m) => m.content.includes("not queued"))).toBe(false);
    expect(activeTab().chatDraft).toBe("my next idea");
    expect(workspace.chatMessages.some((m) => m.errorCard?.what.includes("queue append failed"))).toBe(false);
  });
});

/** Frontend venue: a remote turn ending idle (its transcript replaces the
 * rows) or an interrupt clears pendingChatQueue WITHOUT sending, and the
 * server never got the remote-only append. The round-3 "still queued" guard
 * then dropped the typed text silently (cycle 3 verifier round 4). */
async function frontendAppendFailsAfter(clearQueue: "transcript" | "interrupt") {
  actions.setSessionId(SID);
  actions.setSessionVenue("frontend");
  let failRemote!: () => void;
  const remoteGate = new Promise<void>((r) => (failRemote = r));
  fakeBridge(async (method) => {
    if (method === "session.queue_remote") {
      await remoteGate;
      return { applied: false };
    }
    return {};
  });
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const stamp = () => new Date().toISOString();
  actions.setChatBackend({
    async chat() {
      return "unused";
    },
    async *chatStream(content: string): AsyncGenerator<ChatStreamChunk> {
      await gate;
      yield { delta: `reply to ${content}` };
      const transcript: ChatMessage[] = [
        { id: "u1", role: "user", content, timestamp: stamp() },
        { id: "a1", role: "assistant", content: `reply to ${content}`, timestamp: stamp() },
      ];
      yield { transcript };
      yield { done: true };
    },
  });
  actions.sendChatMessage("first turn");
  await tick();
  actions.sendChatMessage("typed while it ran");
  await tick();
  if (clearQueue === "interrupt") actions.interruptChat();
  release();
  await tick(300);
  expect(workspace.pendingChatQueue).toEqual([]);
  failRemote(); // only now does the remote-only append fail
  await tick(300);
}

describe("a frontend queue append that fails after the queue cleared unsent", () => {
  it("returns the text when the remote transcript replaced the rows", async () => {
    await frontendAppendFailsAfter("transcript");
    expect(workspace.pendingChatDraft).toBe("typed while it ran");
    expect(lastCard()?.errorCard?.what).toContain("queue append failed");
  });

  it("returns the text after an interrupt and never leaves it as a sent row", async () => {
    await frontendAppendFailsAfter("interrupt");
    expect(workspace.chatMessages.some((m) => m.role === "user" && m.content === "typed while it ran")).toBe(false);
    expect(workspace.pendingChatDraft).toBe("typed while it ran");
    expect(lastCard()?.errorCard?.what).toContain("queue append failed");
  });
});

/** Frontend venue: the remote-only append got NO answer (the console's 10 s
 * timeout, or the bridge's own HTTP timeout), yet the server applied it and
 * ran the text. Treating that as a refusal handed the text back with "not
 * queued; retry", inviting a resend that ran twice (cycle 3 verifier round
 * 5). `when` is whether the answer is missed after the turn's transcript or
 * while the row is still queued. */
async function frontendAppendGetsNoAnswer(failure: () => Error, when: "after_transcript" | "still_queued") {
  actions.setSessionId(SID);
  actions.setSessionVenue("frontend");
  let failRemote!: () => void;
  const remoteGate = new Promise<void>((r) => (failRemote = r));
  fakeBridge(async (method) => {
    if (method === "session.queue_remote") {
      await remoteGate;
      throw failure();
    }
    return {};
  });
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  const stamp = () => new Date().toISOString();
  actions.setChatBackend({
    async chat() {
      return "unused";
    },
    async *chatStream(content: string): AsyncGenerator<ChatStreamChunk> {
      await gate;
      yield { delta: `reply to ${content}` };
      const transcript: ChatMessage[] = [
        { id: "u1", role: "user", content, timestamp: stamp() },
        { id: "a1", role: "assistant", content: `reply to ${content}`, timestamp: stamp() },
        { id: "u2", role: "user", content: "typed while it ran", timestamp: stamp() },
        { id: "a2", role: "assistant", content: "reply to typed while it ran", timestamp: stamp() },
      ];
      yield { transcript };
      yield { done: true };
    },
  });
  actions.sendChatMessage("first turn");
  await tick();
  actions.sendChatMessage("typed while it ran");
  await tick();
  if (when === "still_queued") failRemote();
  await tick(when === "still_queued" ? 300 : 0);
  release();
  await tick(300);
  failRemote();
  await tick(300);
}

const consoleTimeout = () => new Error("bridge request timed out: session.queue_remote");
const bridgeHttpTimeout = () =>
  new BridgeRequestError("Request timed out: ReadTimeout.", undefined, "R4TimeoutError");

function expectUnconfirmedCard() {
  expect(workspace.pendingChatDraft ?? "").not.toBe("typed while it ran");
  expect(workspace.chatMessages.some((m) => m.errorCard?.what.includes("queue append failed"))).toBe(false);
  const card = lastCard()?.errorCard;
  expect(card?.what).toContain("unconfirmed");
  expect(card?.what).toContain("check the transcript before resending");
  expect(card?.detail).toContain("typed while it ran"); // the text is never lost
}

describe("a frontend queue append that got no answer", () => {
  it("never hands the text back when the console timed out after the transcript", async () => {
    await frontendAppendGetsNoAnswer(consoleTimeout, "after_transcript");
    expectUnconfirmedCard();
  });

  it("never hands the text back when it timed out while the row was still queued", async () => {
    await frontendAppendGetsNoAnswer(consoleTimeout, "still_queued");
    expectUnconfirmedCard();
    const userRows = workspace.chatMessages.filter((m) => m.role === "user").map((m) => m.content);
    expect(userRows).toEqual(["first turn", "typed while it ran"]);
  });

  it("treats the bridge's own HTTP timeout the same way", async () => {
    await frontendAppendGetsNoAnswer(bridgeHttpTimeout, "after_transcript");
    expectUnconfirmedCard();
  });

  it("a definite bridge error still returns the text", async () => {
    const refusal = () => new BridgeRequestError("queue remote requires an active session id", undefined, "ValueError");
    await frontendAppendGetsNoAnswer(refusal, "after_transcript");
    expect(workspace.pendingChatDraft).toBe("typed while it ran");
    expect(lastCard()?.errorCard?.what).toContain("queue append failed");
  });
});

describe("the queue append bookkeeping", () => {
  it("keeps no id once a queued message's append answered first and its row ran", async () => {
    const before = queueAppendBookkeepingForTests();
    actions.setSessionId(SID);
    fakeBridge(async () => ({ applied: true, queue: [] }));
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    let turns = 0;
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream(content: string): AsyncGenerator<ChatStreamChunk> {
        turns += 1;
        if (turns === 1) await gate;
        yield { delta: `reply to ${content}` };
        yield { done: true };
      },
    });
    actions.sendChatMessage("first turn");
    await tick();
    actions.sendChatMessage("queued behind it");
    await tick(); // the append answers before the turn ends
    release(); // popQueuedTurn sends the row
    await tick(300);
    expect(turns).toBe(2);
    expect(queueAppendBookkeepingForTests()).toEqual(before);
  });
});

describe("a local queue append that got no answer", () => {
  it("keeps the row queued so it runs once here, and never hands the text back", async () => {
    actions.setSessionId(SID);
    let failLocal!: () => void;
    const localGate = new Promise<void>((r) => (failLocal = r));
    fakeBridge(async (method) => {
      if (method === "session.queue_remote") throw new Error("journal unreachable");
      if (method === "session.queue_append") {
        await localGate;
        throw new Error("bridge request timed out: session.queue_append");
      }
      return {};
    });
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const calls: string[] = [];
    actions.setChatBackend({
      async chat() {
        return "unused";
      },
      async *chatStream(content: string): AsyncGenerator<ChatStreamChunk> {
        calls.push(content);
        if (calls.length === 1) await gate;
        yield { delta: `reply to ${content}` };
        yield { done: true };
      },
    });
    actions.sendChatMessage("first turn");
    await tick();
    actions.sendChatMessage("queued behind it");
    await tick();
    failLocal(); // no answer while the row is still queued
    await tick(300);
    release();
    await tick(300);
    expect(calls).toEqual(["first turn", "queued behind it"]);
    expect(workspace.pendingChatDraft ?? "").not.toBe("queued behind it");
    expect(lastCard()?.errorCard?.what).toContain("unconfirmed");
    expect(lastCard()?.errorCard?.detail).toContain("stays queued here");
  });
});

describe("a failed session list", () => {
  it("the background title refresh posts no card; a /sessions fetch still does", async () => {
    fakeBridge((method) => {
      if (method === "list_sessions") throw new Error("no bridge");
      return {};
    });
    refreshSessionsForTabTitles(0); // past the throttle
    await tick();
    expect(sent.some((s) => s.method === "list_sessions")).toBe(true);
    expect(lastCard()).toBeUndefined();

    await fetchSessions();
    expect(lastCard()?.errorCard?.what).toBe("session list failed");
  });
});
