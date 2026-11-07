/**
 * ADR-099 D-8 (M-6): a remote worker's ask_user reaches the user.
 *
 * The worker reports phase `awaiting_user`; the remote-turn watcher reads
 * `session.venue_questions` and opens the existing ask-user overlay; the
 * answer goes to `session.venue_answer` (never the stdin ask_user line).
 * Esc leaves the question pending and this client stops reopening it.
 */
import { test, expect, beforeEach, afterEach } from "bun:test";
import { createRoot } from "solid-js";
import { CompanionChatBackend, setRemotePollMsForTests } from "../src/adapters/companion-chat";
import type { ChatStreamChunk } from "../src/adapters/types";
import { cancelAskUser, respondAskUser } from "../src/bridge/ask-user-bridge";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import {
  questionIdFromDetail,
  resetRemoteQuestionsForTests,
  surfaceRemoteQuestions,
  trackRemoteQuestions,
} from "../src/bridge/venue-bridge";
import { handleWaveIntercepts } from "../src/commands/wave-intercepts";
import { setDemoMode, setDemoVisuals } from "../src/lib/console-mode";
import { mapVenueRuntime, remotePhaseStatus, remoteTurnDone } from "../src/lib/venue-runtime";
import { actions, setWorkspace, workspace } from "../src/stores";

const QUESTION = {
  question_id: "q-1",
  session_id: "sess-1",
  question: "Which dataset?",
  options: ["A", "B"],
  status: "pending",
};

let savedBackendEnv: string | undefined;
beforeEach(() => {
  savedBackendEnv = process.env["R4_TUI_BACKEND"];
  delete process.env["R4_TUI_BACKEND"];
  setDemoMode(false);
  setDemoVisuals(false);
  setRemotePollMsForTests(5);
  setWorkspace("toolApproval", null);
  setWorkspace("askUser", null);
  actions.setVenueRuntime(null);
  actions.setSessionVenue("local");
  resetRemoteQuestionsForTests();
});
afterEach(() => {
  if (savedBackendEnv !== undefined) process.env["R4_TUI_BACKEND"] = savedBackendEnv;
  savedBackendEnv = undefined;
  setRemotePollMsForTests(1_500);
  setCompanionBridgeForTests(null);
  setWorkspace("askUser", null);
  actions.setVenueRuntime(null);
  actions.setSessionVenue("local");
});

type Call = { method: string; params: Record<string, unknown> };

function makeBridge(
  syncs: Array<Record<string, unknown>>,
  questions: Array<Record<string, unknown>>,
  answer: (params: Record<string, unknown>) => unknown = () => ({ question: { ...QUESTION, status: "answered" } }),
) {
  const calls: Call[] = [];
  return {
    calls,
    ensureStarted: async () => {},
    on: () => {},
    off: () => {},
    request: async (method: string, params: Record<string, unknown> = {}) => {
      calls.push({ method, params });
      if (method === "send_message") {
        return { response: "", remote: true, queued: true, state: { messages: [], total_messages: 2, session: { model: "gpt-x" } } };
      }
      if (method === "session.venue_sync") return syncs.length > 1 ? syncs.shift() : syncs[0];
      if (method === "session.venue_questions") return { questions };
      if (method === "session.venue_answer") return answer(params);
      if (method === "session.venue_approvals") return { approvals: [], count: 0 };
      return {};
    },
    abortInFlightSend: () => {},
    cancelTurn: () => {},
    respondAskUser: () => {
      calls.push({ method: "stdin.ask_user_response", params: {} });
    },
  };
}

async function collect(backend: CompanionChatBackend): Promise<ChatStreamChunk[]> {
  const out: ChatStreamChunk[] = [];
  for await (const chunk of backend.chatStream!("hi", [], "gpt-x")) out.push(chunk);
  return out;
}

const AWAITING = { runtime: { phase: "awaiting_user", partial: "", turn_seq: 2, detail: "Which dataset?" }, venue_state: "running", state: { messages: [], total_messages: 3 } };
const FINISHED = {
  adopted: true,
  runtime: { phase: "idle", partial: "", turn_seq: 2 },
  venue_state: "running",
  state: { total_messages: 4, messages: [{ role: "user", content: "hi" }, { role: "assistant", content: "using B" }] },
};

async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 1));
}

test("a remote question opens the ask-user overlay and the answer reaches session.venue_answer", async () => {
  const bridge = makeBridge([AWAITING, FINISHED], [QUESTION]);
  await collect(new CompanionChatBackend(bridge as never));
  expect(bridge.calls.some((c) => c.method === "session.venue_questions")).toBe(true);
  expect(workspace.askUser?.id).toBe("remote:q-1");
  expect(workspace.askUser?.question).toBe("Which dataset?");
  expect(workspace.askUser?.options).toEqual(["A", "B"]);

  setCompanionBridgeForTests(bridge as never);
  respondAskUser("B");
  await settle();
  const answer = bridge.calls.find((c) => c.method === "session.venue_answer");
  expect(answer?.params).toEqual({ question_id: "q-1", answer: "B", session_id: "sess-1" });
  expect(bridge.calls.some((c) => c.method === "stdin.ask_user_response")).toBe(false);
  expect(workspace.askUser).toBeNull();
});

test("Esc leaves the question pending and it is not reopened", async () => {
  const bridge = makeBridge([AWAITING], [QUESTION]);
  setCompanionBridgeForTests(bridge as never);
  expect(await surfaceRemoteQuestions(bridge as never)).toBe(1);
  cancelAskUser();
  await settle();
  expect(workspace.askUser).toBeNull();
  expect(bridge.calls.some((c) => c.method === "session.venue_answer")).toBe(false);
  expect(await surfaceRemoteQuestions(bridge as never)).toBe(0);
  expect(workspace.askUser).toBeNull();
});

test("an answer to a question closed elsewhere is a notice, not an error card", async () => {
  const bridge = makeBridge([AWAITING], [QUESTION], () => {
    throw new Error("venue.question_closed: question q-1 is answered");
  });
  setCompanionBridgeForTests(bridge as never);
  const before = workspace.chatMessages.length;
  await surfaceRemoteQuestions(bridge as never);
  respondAskUser("A");
  await settle();
  const added = workspace.chatMessages.slice(before).map((m) => String(m.content ?? ""));
  expect(added.some((text) => text.includes("already closed"))).toBe(true);
});

test("an open card is never covered by a remote question", async () => {
  const bridge = makeBridge([AWAITING], [QUESTION]);
  actions.openAskUser({ id: 7, question: "local?", options: [] });
  expect(await surfaceRemoteQuestions(bridge as never)).toBe(0);
  expect(workspace.askUser?.id).toBe(7);
});

test("awaiting_user keeps the remote turn open and has its own label", () => {
  expect(remoteTurnDone({ baseTotal: 2, total: 4, phase: "awaiting_user", adopted: true })).toBe(false);
  expect(remotePhaseStatus("awaiting_user")).toBe("remote worker is asking you a question");
});

// ── RS-1 review fixes (2026-10-23) ─────────────────────────────────────────

const STALE = { ...QUESTION, question_id: "q-0", question: "stale question of a dead worker" };
const NEWER = { ...QUESTION, question_id: "q-2", question: "newest question" };

function awaitingRuntime(detail: string) {
  return mapVenueRuntime({ state: "running", runtime: { phase: "awaiting_user", detail } });
}

test("A3: the question the worker's heartbeat names is shown, not the oldest", async () => {
  const bridge = makeBridge([AWAITING], [STALE, QUESTION, NEWER]);
  actions.setVenueRuntime(awaitingRuntime("question q-1"));
  expect(await surfaceRemoteQuestions(bridge as never)).toBe(3);
  expect(workspace.askUser?.id).toBe("remote:q-1");
});

test("A3: without a named question the newest is shown", async () => {
  const bridge = makeBridge([AWAITING], [STALE, QUESTION]);
  actions.setVenueRuntime(awaitingRuntime("Which dataset?"));
  await surfaceRemoteQuestions(bridge as never);
  expect(workspace.askUser?.id).toBe("remote:q-1");
  expect(questionIdFromDetail("question q-abc")).toBe("q-abc");
  expect(questionIdFromDetail("Which dataset?")).toBeNull();
});

test("A5: /venue questions reopens a question set aside with Esc", async () => {
  const bridge = makeBridge([AWAITING], [QUESTION]);
  setCompanionBridgeForTests(bridge as never);
  await surfaceRemoteQuestions(bridge as never);
  cancelAskUser();
  await settle();
  expect(await surfaceRemoteQuestions(bridge as never)).toBe(0);
  const result = await handleWaveIntercepts({ name: "venue", args: "questions" });
  expect(result).toEqual({ kind: "handled" });
  expect(workspace.askUser?.id).toBe("remote:q-1");
  respondAskUser("B");
  await settle();
  expect(bridge.calls.find((c) => c.method === "session.venue_answer")?.params.answer).toBe("B");
});

test("A5: /venue questions says when nothing is pending and names a failure", async () => {
  setCompanionBridgeForTests(makeBridge([AWAITING], []) as never);
  const before = workspace.chatMessages.length;
  expect(await handleWaveIntercepts({ name: "venue", args: "questions" })).toEqual({ kind: "handled" });
  const added = workspace.chatMessages.slice(before).map((m) => String(m.content ?? ""));
  expect(added.some((text) => text.includes("no pending remote questions"))).toBe(true);
  const failing = makeBridge([AWAITING], []);
  failing.request = async () => {
    throw new Error("venue.unavailable: journal down");
  };
  setCompanionBridgeForTests(failing as never);
  const failed = await handleWaveIntercepts({ name: "venue", args: "questions" });
  expect(failed?.kind).toBe("error");
});

test("A5: a failed attach-time read is retried while the session still waits", async () => {
  const bridge = makeBridge([AWAITING], [QUESTION]);
  const original = bridge.request;
  let failures = 1;
  bridge.request = async (method: string, params: Record<string, unknown> = {}) => {
    if (method === "session.venue_questions" && failures-- > 0) throw new Error("bridge starting");
    return original(method, params);
  };
  setCompanionBridgeForTests(bridge as never);
  const dispose = createRoot((done) => {
    trackRemoteQuestions(bridge as never);
    return done;
  });
  try {
    actions.setSessionVenue("frontend");
    actions.setVenueRuntime(awaitingRuntime("question q-1"));
    await settle();
    expect(workspace.askUser).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 2_200));
    await settle();
    expect(workspace.askUser?.id).toBe("remote:q-1");
  } finally {
    dispose();
  }
});

test("A5: attaching a remote session that waits on the user opens its question", async () => {
  const bridge = makeBridge([AWAITING], [QUESTION]);
  setCompanionBridgeForTests(bridge as never);
  const dispose = createRoot((done) => {
    trackRemoteQuestions(bridge as never);
    return done;
  });
  try {
    await settle();
    expect(bridge.calls.some((c) => c.method === "session.venue_questions")).toBe(false);
    // Not under the splash: its Esc would dismiss the question unseen.
    setWorkspace("splashOpen", true);
    actions.setSessionVenue("frontend");
    actions.setVenueRuntime(awaitingRuntime("question q-1"));
    await settle();
    expect(bridge.calls.some((c) => c.method === "session.venue_questions")).toBe(false);
    setWorkspace("splashOpen", false);
    await settle();
    expect(workspace.askUser?.id).toBe("remote:q-1");
  } finally {
    dispose();
    setWorkspace("splashOpen", false);
  }
});

test("A5: an answered question is not reopened while a poll still lists it", async () => {
  const bridge = makeBridge([AWAITING], [QUESTION]);
  setCompanionBridgeForTests(bridge as never);
  await surfaceRemoteQuestions(bridge as never);
  respondAskUser("A");
  await settle();
  expect(await surfaceRemoteQuestions(bridge as never)).toBe(0);
  expect(workspace.askUser).toBeNull();
});

// ── RS-1 round-2 review, finding 5 (2026-10-23) ───────────────────────────

test("R2-5: an answer to a closed question goes back to the composer", async () => {
  const bridge = makeBridge([AWAITING], [QUESTION], () => {
    throw new Error("venue.question_closed: question q-1 is expired");
  });
  setCompanionBridgeForTests(bridge as never);
  const seq = workspace.pendingChatDraftSeq;
  await surfaceRemoteQuestions(bridge as never);
  respondAskUser("use dataset B");
  await settle();
  expect(workspace.pendingChatDraft).toBe("use dataset B");
  expect(workspace.pendingChatDraftSeq).toBe(seq + 1);
});

async function withTracker(bridge: ReturnType<typeof makeBridge>, body: () => Promise<void>): Promise<void> {
  setCompanionBridgeForTests(bridge as never);
  const dispose = createRoot((done) => {
    trackRemoteQuestions(bridge as never);
    return done;
  });
  try {
    actions.setSessionVenue("frontend");
    actions.setVenueRuntime(awaitingRuntime("question q-1"));
    await settle();
    expect(workspace.askUser?.id).toBe("remote:q-1");
    await body();
  } finally {
    dispose();
  }
}

test("R2-5: the card closes when the worker stops waiting on its question", async () => {
  const bridge = makeBridge([AWAITING], [QUESTION]);
  await withTracker(bridge, async () => {
    const before = workspace.chatMessages.length;
    actions.setVenueRuntime(mapVenueRuntime({ state: "running", runtime: { phase: "running", detail: "question expired (no answer)" } }));
    await settle();
    expect(workspace.askUser).toBeNull();
    const added = workspace.chatMessages.slice(before).map((m) => String(m.content ?? ""));
    expect(added.some((text) => text.includes("remote question closed"))).toBe(true);
    expect(bridge.calls.some((c) => c.method === "session.venue_answer")).toBe(false);
  });
});

test("R2-5: the card closes when the session stops", async () => {
  await withTracker(makeBridge([AWAITING], [QUESTION]), async () => {
    actions.setVenueRuntime(null);
    await settle();
    expect(workspace.askUser).toBeNull();
  });
});

// RS-1 round 3, N2: umrs stop() and lease expiry change the row state only;
// the runtime keeps the last heartbeat (awaiting_user, the same question) and
// the removed worker sends no final one.
for (const state of ["stopped", "failed"]) {
  test(`R3-N2: the card closes when the session is ${state} with the last heartbeat kept`, async () => {
    const bridge = makeBridge([AWAITING], [QUESTION]);
    await withTracker(bridge, async () => {
      const reads = bridge.calls.filter((c) => c.method === "session.venue_questions").length;
      actions.setVenueRuntime(mapVenueRuntime({ state, runtime: { phase: "awaiting_user", detail: "question q-1" } }));
      await settle();
      expect(workspace.askUser).toBeNull();
      // ...and an ended session's question is not read again and reopened.
      expect(bridge.calls.filter((c) => c.method === "session.venue_questions").length).toBe(reads);
    });
  });
}

// RS-1 round 3, N3: the composer's own draft is never overwritten.
test("R3-N3: an answer to a closed question leaves a non-empty composer draft alone", async () => {
  const bridge = makeBridge([AWAITING], [QUESTION], () => {
    throw new Error("venue.question_closed: question q-1 is expired");
  });
  setCompanionBridgeForTests(bridge as never);
  actions.setChatDraft("my half-written next prompt");
  try {
    const seq = workspace.pendingChatDraftSeq;
    const before = workspace.chatMessages.length;
    await surfaceRemoteQuestions(bridge as never);
    respondAskUser("use dataset B");
    await settle();
    expect(workspace.pendingChatDraftSeq).toBe(seq);
    const added = workspace.chatMessages.slice(before).map((m) => String(m.content ?? ""));
    expect(added.some((text) => text.includes("already closed") && text.includes("use dataset B"))).toBe(true);
  } finally {
    actions.setChatDraft("");
  }
});

test("R2-5: a newer question replaces the stale card", async () => {
  const questions: Array<Record<string, unknown>> = [QUESTION];
  await withTracker(makeBridge([AWAITING], questions), async () => {
    questions.splice(0, questions.length, NEWER);
    actions.setVenueRuntime(awaitingRuntime("question q-2"));
    await settle();
    expect(workspace.askUser?.id).toBe("remote:q-2");
  });
});

test("R2-5: a keep-alive naming the same question leaves the card open", async () => {
  await withTracker(makeBridge([AWAITING], [QUESTION]), async () => {
    actions.setVenueRuntime(awaitingRuntime("question q-1"));
    await settle();
    expect(workspace.askUser?.id).toBe("remote:q-1");
  });
});

test("R2-5: a card opened by /venue questions while idle is not closed by idle polls", async () => {
  const bridge = makeBridge([AWAITING], [QUESTION]);
  setCompanionBridgeForTests(bridge as never);
  const dispose = createRoot((done) => {
    trackRemoteQuestions(bridge as never);
    return done;
  });
  try {
    actions.setSessionVenue("frontend");
    actions.setVenueRuntime(mapVenueRuntime({ state: "running", runtime: { phase: "idle" } }));
    await settle();
    await surfaceRemoteQuestions(bridge as never);
    actions.setVenueRuntime(mapVenueRuntime({ state: "running", runtime: { phase: "idle", detail: "x" } }));
    await settle();
    expect(workspace.askUser?.id).toBe("remote:q-1");
  } finally {
    dispose();
  }
});
